"""
MCP (Model Context Protocol) Tools endpoint for SmartMart
==========================================================
This blueprint exposes SmartMart's business data as MCP-compatible
tool endpoints. Claude agents (e.g. in Claude Desktop or Kiro) can
call these tools via the MCP protocol to interact with live shop data.

Authentication: Bearer token (MCP_SECRET env var) or admin session.

MCP Tool Registry: GET /mcp/tools
Tool invocation:   POST /mcp/tools/<tool_name>

Available tools
---------------
- get_inventory_status       Check current stock levels
- get_sales_summary          Today / week / month revenue
- search_products            Semantic product search (RAG)
- get_low_stock_products     Products needing reorder
- get_top_products           Best-selling products
- get_customer_profile       Customer purchase history
- create_purchase_order_draft Draft a PO for low-stock items
- get_business_kpis          KPI scorecard
- get_cashflow_forecast      30-day cashflow forecast
- ask_business_advisor       Natural language business Q&A via Claude
- rag_search                 Semantic search over product catalogue
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user
from sqlalchemy import func

from ...extensions import db, limiter

mcp_bp = Blueprint("mcp", __name__, url_prefix="/mcp")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _is_authorized() -> bool:
    """Bearer token (MCP_SECRET) OR an authenticated admin session.

    Fails closed: if MCP_SECRET isn't configured, bearer-token access is
    simply unavailable — there is no "allow all" bypass. Only an
    authenticated admin session can call these tools in that case.

    (Previously, an unset MCP_SECRET made every tool here — sales figures,
    customer profiles, KPIs, purchase-order creation — reachable by anyone
    on the internet with no authentication at all, on any deployment where
    the operator hadn't set MCP_SECRET. This app is deployed publicly, not
    "localhost only".)
    """
    secret = os.environ.get("MCP_SECRET", "")

    if secret:
        bearer = request.headers.get("Authorization", "")
        if bearer.lower().startswith("bearer "):
            bearer = bearer[7:].strip()
        if bearer == secret:
            return True

    try:
        return bool(current_user.is_authenticated and current_user.role == "admin")
    except Exception:
        return False


def mcp_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_authorized():
            return jsonify({"error": "Unauthorized"}), 401
        return view(*args, **kwargs)
    return wrapped


def _ok(data: dict) -> dict:
    return {"ok": True, **data}


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_inventory_status",
        "description": "Get current stock levels for all or specific products. Returns quantity, price, category and stock status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "Filter by product name (optional)"},
                "category":     {"type": "string", "description": "Filter by category (optional)"},
                "low_stock":    {"type": "boolean", "description": "Show only low-stock products"},
                "limit":        {"type": "integer", "description": "Max results (default 50)"},
            },
        },
    },
    {
        "name": "get_sales_summary",
        "description": "Get revenue and sales count for today, this week, and this month. Includes top products.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_products",
        "description": "Semantic search over the product catalogue using natural language. Returns relevant products with prices and availability.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results (default 5)"},
                "in_stock_only": {"type": "boolean", "description": "Return only in-stock products (default true)"},
            },
        },
    },
    {
        "name": "get_low_stock_products",
        "description": "Get products that need reordering (below threshold or out of stock). Returns product details and recommended order quantities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "integer", "description": "Stock threshold (default 10)"},
                "include_out_of_stock": {"type": "boolean", "description": "Include zero-stock items (default true)"},
            },
        },
    },
    {
        "name": "get_top_products",
        "description": "Get best-selling products by quantity sold in a given period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days":  {"type": "integer", "description": "Look-back period in days (default 30)"},
                "limit": {"type": "integer", "description": "Number of products to return (default 10)"},
            },
        },
    },
    {
        "name": "get_customer_profile",
        "description": "Get a customer's purchase history, loyalty tier, CLV, and churn risk.",
        "inputSchema": {
            "type": "object",
            "required": ["customer_name"],
            "properties": {
                "customer_name": {"type": "string", "description": "Customer full name"},
            },
        },
    },
    {
        "name": "create_purchase_order_draft",
        "description": "Automatically create draft purchase orders for low-stock products using demand forecasting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lookback_days": {"type": "integer", "description": "Days of sales history to use (default 30)"},
                "coverage_days": {"type": "integer", "description": "Days of stock to cover (default 14)"},
            },
        },
    },
    {
        "name": "get_business_kpis",
        "description": "Get key performance indicators: revenue, gross margin, net profit, avg order value, customer retention, expense ratio.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_cashflow_forecast",
        "description": "Get 30-day cash flow forecast based on moving averages and day-of-week patterns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Forecast horizon in days (default 30)"},
            },
        },
    },
    {
        "name": "ask_business_advisor",
        "description": "Ask a natural language business question. Returns an AI-generated answer grounded in live shop data.",
        "inputSchema": {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {"type": "string", "description": "Business question in plain English or Nepali"},
            },
        },
    },
    {
        "name": "get_recommendations",
        "description": "Get product recommendations based on co-purchase patterns and collaborative filtering.",
        "inputSchema": {
            "type": "object",
            "required": ["product_id"],
            "properties": {
                "product_id":      {"type": "integer", "description": "Product ID to get recommendations for"},
                "customer_phone":  {"type": "string",  "description": "Customer phone for personalised recs (optional)"},
                "limit":           {"type": "integer",  "description": "Number of recommendations (default 5)"},
            },
        },
    },
    {
        "name": "get_anomaly_report",
        "description": "Detect unusual sales patterns, stock movements, or pricing anomalies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look-back window (default 30)"},
            },
        },
    },
]


@mcp_bp.route("/tools", methods=["GET"])
@mcp_auth
def list_tools():
    """MCP tool listing endpoint."""
    return jsonify({"tools": TOOLS})


@mcp_bp.route("/tools/<tool_name>", methods=["POST"])
@mcp_auth
@limiter.limit("60/minute")
def invoke_tool(tool_name: str):
    """MCP tool invocation endpoint."""
    args = request.get_json(silent=True) or {}

    handlers = {
        "get_inventory_status":       _tool_inventory,
        "get_sales_summary":          _tool_sales_summary,
        "search_products":            _tool_search_products,
        "get_low_stock_products":     _tool_low_stock,
        "get_top_products":           _tool_top_products,
        "get_customer_profile":       _tool_customer_profile,
        "create_purchase_order_draft": _tool_create_po_draft,
        "get_business_kpis":          _tool_kpis,
        "get_cashflow_forecast":      _tool_cashflow,
        "ask_business_advisor":       _tool_ask_advisor,
        "get_recommendations":        _tool_recommendations,
        "get_anomaly_report":         _tool_anomalies,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return _err(f"Unknown tool: {tool_name}", 404)

    try:
        result = handler(args)
        return jsonify(_ok(result))
    except Exception as exc:
        current_app.logger.exception("MCP tool %s failed: %s", tool_name, exc)
        return _err(str(exc), 500)


# ── Tool Implementations ──────────────────────────────────────────────────────

def _tool_inventory(args: dict) -> dict:
    from ...models.product import Product
    q = db.select(Product)
    if args.get("product_name"):
        term = f"%{args['product_name'].lower()}%"
        q = q.where(func.lower(Product.name).like(term))
    if args.get("category"):
        q = q.where(func.lower(Product.category) == args["category"].lower())
    if args.get("low_stock"):
        q = q.where(Product.quantity <= 10)
    limit = min(int(args.get("limit", 50)), 200)
    products = db.session.execute(q.order_by(Product.name).limit(limit)).scalars().all()
    return {
        "total": len(products),
        "products": [
            {
                "id": p.id, "name": p.name, "category": p.category,
                "sku": p.sku, "quantity": p.quantity,
                "selling_price": float(p.selling_price),
                "cost_price": float(p.cost_price),
                "status": "out_of_stock" if p.quantity == 0
                         else "low_stock" if p.quantity <= 10
                         else "ok",
            }
            for p in products
        ],
    }


def _tool_sales_summary(args: dict) -> dict:
    from ...models.sale import Sale, SaleItem
    today = date.today()
    week_start  = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    def _rev(start):
        return float(db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(Sale.sale_date >= start)
        ).scalar() or 0)

    def _cnt(start):
        return int(db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.sale_date >= start)
        ).scalar() or 0)

    from ...models.product import Product
    top = db.session.execute(
        db.select(Product.name, func.sum(SaleItem.quantity).label("qty"),
                  func.sum(SaleItem.subtotal).label("rev"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.sale_date >= month_start)
        .group_by(Product.name)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(5)
    ).all()

    return {
        "today":        {"revenue": _rev(today), "orders": _cnt(today)},
        "this_week":    {"revenue": _rev(week_start), "orders": _cnt(week_start)},
        "this_month":   {"revenue": _rev(month_start), "orders": _cnt(month_start)},
        "top_products": [{"name": r.name, "qty_sold": int(r.qty or 0), "revenue": float(r.rev or 0)} for r in top],
    }


def _tool_search_products(args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"results": []}
    top_k = min(int(args.get("top_k", 5)), 20)
    in_stock = args.get("in_stock_only", True)
    try:
        from ...services.rag_service import rag_search
        results = rag_search(query, top_k=top_k, in_stock_only=in_stock)
    except Exception:
        results = []
    return {"query": query, "results": results}


def _tool_low_stock(args: dict) -> dict:
    from ...models.product import Product
    threshold = int(args.get("threshold", 10))
    include_oos = args.get("include_out_of_stock", True)
    q = db.select(Product).where(Product.is_active.isnot(False))
    if include_oos:
        q = q.where(Product.quantity <= threshold)
    else:
        q = q.where(Product.quantity > 0, Product.quantity <= threshold)
    products = db.session.execute(q.order_by(Product.quantity)).scalars().all()
    return {
        "threshold": threshold,
        "count": len(products),
        "products": [
            {
                "id": p.id, "name": p.name, "category": p.category,
                "quantity": p.quantity,
                "reorder_point": p.reorder_point or threshold,
                "suggested_order_qty": max(50, (p.reorder_point or 50) * 3),
            }
            for p in products
        ],
    }


def _tool_top_products(args: dict) -> dict:
    from ...models.product import Product
    from ...models.sale import Sale, SaleItem
    days  = int(args.get("days", 30))
    limit = min(int(args.get("limit", 10)), 50)
    since = date.today() - timedelta(days=days)
    rows = db.session.execute(
        db.select(Product.id, Product.name, Product.category,
                  func.sum(SaleItem.quantity).label("qty_sold"),
                  func.sum(SaleItem.subtotal).label("revenue"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.sale_date >= since)
        .group_by(Product.id, Product.name, Product.category)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(limit)
    ).all()
    return {
        "period_days": days,
        "products": [
            {"id": r.id, "name": r.name, "category": r.category,
             "qty_sold": int(r.qty_sold or 0), "revenue": float(r.revenue or 0)}
            for r in rows
        ],
    }


def _tool_customer_profile(args: dict) -> dict:
    from ...models.customer import Customer
    from ...models.sale import Sale
    name = (args.get("customer_name") or "").strip()
    if not name:
        return {"error": "customer_name required"}

    c = db.session.execute(
        db.select(Customer).where(func.lower(Customer.name) == name.lower())
    ).scalar_one_or_none()

    sales = db.session.execute(
        db.select(Sale)
        .where(func.lower(Sale.customer_name) == name.lower())
        .order_by(Sale.sale_date.desc())
        .limit(50)
    ).scalars().all()

    total_spent = sum(float(s.total_amount) for s in sales)
    avg_order   = total_spent / len(sales) if sales else 0
    last_sale   = sales[0].sale_date if sales else None
    recency     = (date.today() - last_sale.date()).days if last_sale else None

    return {
        "customer": {
            "name":         c.name if c else name,
            "phone":        c.phone if c else None,
            "loyalty_tier": c.loyalty_tier if c else "unknown",
            "loyalty_points": c.loyalty_points if c else 0,
        } if c else {"name": name},
        "purchase_history": {
            "total_orders": len(sales),
            "total_spent":  round(total_spent, 2),
            "avg_order":    round(avg_order, 2),
            "last_purchase_days_ago": recency,
        },
        "churn_risk": (
            "churned" if recency and recency > 90 else
            "at_risk"  if recency and recency > 45 else
            "active"
        ),
    }


def _tool_create_po_draft(args: dict) -> dict:
    lookback = int(args.get("lookback_days", 30))
    coverage = int(args.get("coverage_days", 14))
    try:
        from ...services.ai_growth_ops import create_auto_draft_purchase_orders
        result = create_auto_draft_purchase_orders(
            user_id=1,  # system user
            lookback_days=lookback,
            coverage_days=coverage,
        )
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _tool_kpis(args: dict) -> dict:
    try:
        from ...services.ai_business_advisor import kpi_scorecard
        return {"kpis": kpi_scorecard()}
    except Exception as exc:
        return {"error": str(exc)}


def _tool_cashflow(args: dict) -> dict:
    days = int(args.get("days", 30))
    try:
        from ...services.ai_cashflow_prediction import predict_cashflow
        return predict_cashflow(days)
    except Exception as exc:
        return {"error": str(exc)}


def _tool_ask_advisor(args: dict) -> dict:
    question = (args.get("question") or "").strip()
    if not question:
        return {"error": "question required"}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"answer": "ANTHROPIC_API_KEY not configured."}

    try:
        from ...blueprints.ai_chat.routes import build_business_context
        context = build_business_context()

        # Also add RAG context
        try:
            from ...services.rag_service import rag_context_for_query
            rag_ctx = rag_context_for_query(question, top_k=5)
            context += f"\n\n{rag_ctx}"
        except Exception:
            pass

        import json as _json, urllib.request as _req
        payload = _json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 800,
            "system": (
                "You are the Goldkernel Business AI, answering questions about a premium dry fruits "
                "retail shop in Nepal. Use the live data provided. Currency is NPR.\n\n"
                f"[LIVE DATA]\n{context}\n[/LIVE DATA]"
            ),
            "messages": [{"role": "user", "content": question}],
        }).encode()

        r = _req.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            method="POST",
        )
        with _req.urlopen(r, timeout=30) as resp:
            data = _json.loads(resp.read())
        return {"question": question, "answer": data["content"][0]["text"].strip()}
    except Exception as exc:
        return {"error": str(exc)}


def _tool_recommendations(args: dict) -> dict:
    product_id = int(args.get("product_id", 0))
    if not product_id:
        return {"error": "product_id required"}
    limit = min(int(args.get("limit", 5)), 10)
    phone = args.get("customer_phone")
    try:
        from ...services.recommendation_service import get_product_recommendations
        recs = get_product_recommendations(product_id, customer_phone=phone, limit=limit)
        return {
            "product_id": product_id,
            "recommendations": [
                {"id": p.id, "name": p.name, "category": p.category,
                 "price": float(p.selling_price), "quantity": p.quantity}
                for p in recs
            ],
        }
    except Exception as exc:
        return {"error": str(exc)}


def _tool_anomalies(args: dict) -> dict:
    days = int(args.get("days", 30))
    try:
        from ...services.ai_anomaly_detection import full_anomaly_report
        return full_anomaly_report(days)
    except Exception as exc:
        return {"error": str(exc)}
