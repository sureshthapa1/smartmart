"""AI Smart Search — Natural language search using Claude API.

Allows staff to search using plain English queries like:
  "show me sales over 5000 yesterday"
  "products running low on stock"
  "customers who haven't bought in 30 days"
  "top selling item this week"

Falls back to keyword search when API key is not set.
"""

from __future__ import annotations
import os
import re
import json
from datetime import date, timedelta
from sqlalchemy import func
from ..extensions import db


def _parse_query_with_claude(query: str) -> dict | None:
    """Use Claude to convert natural language to a structured search intent."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request
        today = date.today().isoformat()
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 200,
            "system": (
                f"Today is {today}. You are a search parser for a Nepal retail shop system. "
                "Convert the user's query into a JSON search intent. "
                "Reply ONLY with a JSON object (no markdown) with these keys: "
                "type (one of: product, sale, customer, expense, supplier, report), "
                "filters (object with optional keys: min_amount, max_amount, days_back, "
                "status, category, keyword), "
                "sort (string: 'recent', 'amount_desc', 'amount_asc', 'name'). "
                "Example: {\"type\": \"sale\", \"filters\": {\"min_amount\": 5000, \"days_back\": 1}, \"sort\": \"amount_desc\"}"
            ),
            "messages": [{"role": "user", "content": query}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read())
        text = result["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("Smart search parse failed: %s", exc)
        return None


def smart_search(query: str) -> dict:
    """Execute a natural language search and return structured results.

    For product queries, RAG semantic search is tried first (more accurate),
    then Claude NLP intent parsing, then keyword fallback.
    """
    from ..models.product import Product
    from ..models.sale import Sale
    from ..models.customer import Customer
    from ..models.expense import Expense
    from ..models.supplier import Supplier

    # ── RAG-powered product search ────────────────────────────────────────
    # Try RAG first for short-to-medium product queries
    if len(query.split()) <= 8:
        try:
            from .rag_service import rag_search
            rag_results = rag_search(query, top_k=5, in_stock_only=False)
            if rag_results:
                results = []
                for r in rag_results:
                    results.append({
                        "type": "product", "icon": "bi-box-seam", "color": "#6366f1",
                        "label": r["name"],
                        "sub": f"{r['category']} | NPR {r['price']:,.0f} | "
                               f"{'In stock' if r['in_stock'] else 'Out of stock'}",
                        "url": f"/inventory/{r['product_id']}/edit",
                        "score": r["score"],
                    })
                if results:
                    return {"results": results, "intent": {"type": "product", "method": "rag"}, "query": query}
        except Exception:
            pass  # fall through to intent-based search

    results = []
    intent = _parse_query_with_claude(query)

    if not intent:
        # Fallback: simple keyword search across all entities
        term = f"%{query.lower()}%"
        products = db.session.execute(
            db.select(Product).where(
                db.or_(func.lower(Product.name).like(term),
                       func.lower(Product.sku).like(term))
            ).limit(5)
        ).scalars().all()
        for p in products:
            results.append({"type": "product", "icon": "bi-box-seam", "color": "#6366f1",
                            "label": p.name, "sub": f"SKU: {p.sku} | Stock: {p.quantity}",
                            "url": f"/inventory/{p.id}/edit"})

        sales = db.session.execute(
            db.select(Sale).where(
                db.or_(func.lower(Sale.invoice_number).like(term),
                       func.lower(Sale.customer_name).like(term))
            ).order_by(Sale.sale_date.desc()).limit(5)
        ).scalars().all()
        for s in sales:
            results.append({"type": "sale", "icon": "bi-receipt", "color": "#10b981",
                            "label": s.invoice_number or f"Sale #{s.id}",
                            "sub": f"{s.customer_name or 'Walk-in'} | NPR {float(s.total_amount):,.0f}",
                            "url": f"/sales/{s.id}"})
        return {"results": results, "intent": None, "query": query}

    search_type = intent.get("type", "product")
    filters = intent.get("filters", {})
    sort = intent.get("sort", "recent")

    if search_type == "product":
        q = db.select(Product).where(Product.is_active == True)
        if filters.get("keyword"):
            term = f"%{filters['keyword'].lower()}%"
            q = q.where(db.or_(func.lower(Product.name).like(term),
                                func.lower(Product.category).like(term)))
        if filters.get("category"):
            q = q.where(func.lower(Product.category) == filters["category"].lower())
        if filters.get("max_quantity") is not None:
            q = q.where(Product.quantity <= int(filters["max_quantity"]))
        if sort == "name":
            q = q.order_by(Product.name)
        rows = db.session.execute(q.limit(10)).scalars().all()
        for p in rows:
            results.append({"type": "product", "icon": "bi-box-seam", "color": "#6366f1",
                            "label": p.name,
                            "sub": f"SKU: {p.sku} | Stock: {p.quantity} | NPR {float(p.selling_price):,.0f}",
                            "url": f"/inventory/{p.id}/edit"})

    elif search_type == "sale":
        q = db.select(Sale)
        if filters.get("days_back"):
            since = date.today() - timedelta(days=int(filters["days_back"]))
            q = q.where(Sale.sale_date >= since)
        if filters.get("min_amount"):
            q = q.where(Sale.total_amount >= float(filters["min_amount"]))
        if filters.get("max_amount"):
            q = q.where(Sale.total_amount <= float(filters["max_amount"]))
        if sort == "amount_desc":
            q = q.order_by(Sale.total_amount.desc())
        else:
            q = q.order_by(Sale.sale_date.desc())
        rows = db.session.execute(q.limit(10)).scalars().all()
        for s in rows:
            results.append({"type": "sale", "icon": "bi-receipt", "color": "#10b981",
                            "label": s.invoice_number or f"Sale #{s.id}",
                            "sub": f"{s.customer_name or 'Walk-in'} | NPR {float(s.total_amount):,.0f} | {str(s.sale_date)[:10]}",
                            "url": f"/sales/{s.id}"})

    elif search_type == "customer":
        q = db.select(Customer)
        if filters.get("keyword"):
            term = f"%{filters['keyword'].lower()}%"
            q = q.where(db.or_(func.lower(Customer.name).like(term),
                                func.lower(Customer.phone).like(term)))
        rows = db.session.execute(q.limit(10)).scalars().all()
        for c in rows:
            results.append({"type": "customer", "icon": "bi-person", "color": "#f59e0b",
                            "label": c.name,
                            "sub": f"Phone: {c.phone or '-'} | Tier: {c.loyalty_tier or 'bronze'}",
                            "url": f"/customers/{c.id}"})

    elif search_type == "expense":
        q = db.select(Expense)
        if filters.get("days_back"):
            since = date.today() - timedelta(days=int(filters["days_back"]))
            q = q.where(Expense.expense_date >= since)
        if filters.get("min_amount"):
            q = q.where(Expense.amount >= float(filters["min_amount"]))
        q = q.order_by(Expense.expense_date.desc())
        rows = db.session.execute(q.limit(10)).scalars().all()
        for e in rows:
            results.append({"type": "expense", "icon": "bi-receipt-cutoff", "color": "#ef4444",
                            "label": e.description or e.category,
                            "sub": f"NPR {float(e.amount):,.0f} | {str(e.expense_date)[:10]}",
                            "url": "/expenses/"})

    elif search_type == "supplier":
        q = db.select(Supplier)
        if filters.get("keyword"):
            term = f"%{filters['keyword'].lower()}%"
            q = q.where(func.lower(Supplier.name).like(term))
        rows = db.session.execute(q.limit(8)).scalars().all()
        for s in rows:
            results.append({"type": "supplier", "icon": "bi-truck", "color": "#3b82f6",
                            "label": s.name, "sub": f"Contact: {s.contact or '-'}",
                            "url": f"/purchases/suppliers/{s.id}/edit"})

    return {"results": results, "intent": intent, "query": query}
