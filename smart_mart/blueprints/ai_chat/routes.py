"""
AI Chat blueprint — Gemini-powered Business Advisor with:
 • SSE streaming responses (no waiting for full reply)
 • Persistent conversation history in DB (ChatConversation / ChatMessage)
 • RAG-grounded context (live business data + product catalogue)
 • Full conversation management (list, load, delete, rename)
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta, datetime, timezone

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context
from flask_login import current_user
from sqlalchemy import func

from ...extensions import db
from ...models.expense import Expense
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ...models.ai_memory import ChatConversation, ChatMessage
from ...services.decorators import login_required
from ...services.gemini_client import gemini_available, gemini_generate
from ...utils.nepali_date import bs_month_name

logger = logging.getLogger(__name__)

ai_chat_bp = Blueprint("ai_chat", __name__, url_prefix="/ai/chat")

SYSTEM_PROMPT = """You are the Goldkernel Business Advisor, an AI assistant built into the Goldkernel
retail management system for Goldkernel Dryfruits and Treats, a premium dry fruits shop
in Dhangadhi, Kailali, Nepal.

Your role:
- Answer questions about sales performance, inventory, profit, and customer trends
- Give practical business advice for Nepal's retail market
- Reference the live shop data provided when answering data questions
- Mention Nepali festivals (Dashain, Tihar) and seasonal patterns when relevant
- Currency is NPR. Weights are in grams.
- Be concise, friendly, and action-oriented (3-5 sentences unless asked for more)
- Use bullet points when listing items

[LIVE DATA]
{injected_business_context}
[/LIVE DATA]
"""


# ── Pages ─────────────────────────────────────────────────────────────────────

@ai_chat_bp.route("/")
@login_required
def index():
    conversations = db.session.execute(
        db.select(ChatConversation)
        .where(ChatConversation.user_id == current_user.id,
               ChatConversation.is_archived == False)
        .order_by(ChatConversation.updated_at.desc())
        .limit(20)
    ).scalars().all()
    return render_template(
        "ai_chat/index.html",
        api_key_configured=gemini_available(),
        conversations=conversations,
    )


# ── Non-streaming ask (legacy / fallback) ─────────────────────────────────────

@ai_chat_bp.route("/ask", methods=["POST"])
@login_required
def ask():
    if not gemini_available():
        return jsonify({"error": "GEMINI_API_KEY not configured."}), 500

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    conv_id = payload.get("conversation_id")
    history = _load_history(conv_id, current_user.id, fallback=payload.get("history") or [])

    # Build Gemini-format history
    gemini_history = []
    for m in history:
        role = m.get("role", "user")
        if role == "assistant":
            role = "model"
        if role in ("user", "model"):
            gemini_history.append({"role": role, "parts": [{"text": m.get("content", "")}]})

    system = SYSTEM_PROMPT.format(injected_business_context=build_business_context())
    reply = gemini_generate(message, system=system, max_tokens=1024, history=gemini_history)

    if not reply:
        return jsonify({"error": "AI Advisor unavailable."}), 502

    conv_id = _save_turn(conv_id, current_user.id, message, reply)
    return jsonify({"reply": reply, "conversation_id": conv_id})


# ── SSE Streaming ask ─────────────────────────────────────────────────────────

@ai_chat_bp.route("/stream", methods=["POST"])
@login_required
def stream():
    """
    Server-Sent Events streaming endpoint.
    Uses Gemini API (non-streaming) and sends the reply in word-chunks via SSE
    so the UI displays it progressively without blocking.

    POST body: {message, conversation_id (optional)}

    SSE events:
      data: {"token": "..."}                              — partial text chunk
      data: {"done": true, "conversation_id": N}          — stream finished
      data: {"error": "..."}                              — error occurred
    """
    if not gemini_available():
        return jsonify({"error": "GEMINI_API_KEY not configured."}), 500

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    conv_id = payload.get("conversation_id")
    history = _load_history(conv_id, current_user.id, fallback=payload.get("history") or [])

    # Build Gemini-format history
    gemini_history = []
    for m in history:
        role = m.get("role", "user")
        if role == "assistant":
            role = "model"
        if role in ("user", "model"):
            gemini_history.append({"role": role, "parts": [{"text": m.get("content", "")}]})

    context = build_business_context()
    system  = SYSTEM_PROMPT.format(injected_business_context=context)

    user_id    = current_user.id
    conv_id_in = conv_id
    user_msg   = message

    def generate():
        # Call Gemini (blocking — response arrives all at once)
        reply = gemini_generate(message, system=system, max_tokens=1024, history=gemini_history)

        if not reply:
            yield f"data: {json.dumps({'error': 'AI Advisor unavailable'})}\n\n"
            return

        # Stream word-by-word so the UI feels responsive
        words = reply.split(" ")
        chunk = []
        for i, word in enumerate(words):
            chunk.append(word)
            # Send every 4 words as a token chunk
            if len(chunk) >= 4 or i == len(words) - 1:
                token = " ".join(chunk) + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'token': token})}\n\n"
                chunk = []

        # Persist to DB
        from flask import current_app
        try:
            with current_app.app_context():
                saved_id = _save_turn(conv_id_in, user_id, user_msg, reply)
        except Exception as exc:
            logger.warning("_save_turn failed in stream: %s", exc)
            saved_id = conv_id_in

        yield f"data: {json.dumps({'done': True, 'conversation_id': saved_id})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Conversation management ───────────────────────────────────────────────────

@ai_chat_bp.route("/conversations", methods=["GET"])
@login_required
def list_conversations():
    """List all conversations for the current user."""
    convs = db.session.execute(
        db.select(ChatConversation)
        .where(ChatConversation.user_id == current_user.id,
               ChatConversation.is_archived == False)
        .order_by(ChatConversation.updated_at.desc())
        .limit(50)
    ).scalars().all()
    return jsonify([{
        "id": c.id,
        "title": c.title or "Untitled conversation",
        "message_count": len(c.messages),
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    } for c in convs])


@ai_chat_bp.route("/conversations/<int:conv_id>", methods=["GET"])
@login_required
def get_conversation(conv_id: int):
    """Load a specific conversation with all messages."""
    conv = db.session.get(ChatConversation, conv_id)
    if not conv or conv.user_id != current_user.id:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": conv.id,
        "title": conv.title or "Untitled",
        "messages": [{"role": m.role, "content": m.content,
                      "created_at": m.created_at.isoformat()} for m in conv.messages],
    })


@ai_chat_bp.route("/conversations/<int:conv_id>", methods=["DELETE"])
@login_required
def delete_conversation(conv_id: int):
    """Delete a conversation."""
    conv = db.session.get(ChatConversation, conv_id)
    if not conv or conv.user_id != current_user.id:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(conv)
    db.session.commit()
    return jsonify({"ok": True})


@ai_chat_bp.route("/conversations/<int:conv_id>/rename", methods=["POST"])
@login_required
def rename_conversation(conv_id: int):
    """Rename a conversation."""
    conv = db.session.get(ChatConversation, conv_id)
    if not conv or conv.user_id != current_user.id:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:100]
    if title:
        conv.title = title
        db.session.commit()
    return jsonify({"ok": True, "title": conv.title})


@ai_chat_bp.route("/conversations/new", methods=["POST"])
@login_required
def new_conversation():
    """Create a new empty conversation and return its ID."""
    conv = ChatConversation(user_id=current_user.id, title=None)
    db.session.add(conv)
    db.session.commit()
    return jsonify({"conversation_id": conv.id})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_history(conv_id, user_id: int, fallback: list) -> list:
    """Load conversation history from DB or fall back to client-provided list."""
    if conv_id:
        try:
            conv = db.session.get(ChatConversation, int(conv_id))
            if conv and conv.user_id == user_id:
                return [{"role": m.role, "content": m.content} for m in conv.messages[-20:]]
        except Exception:
            pass
    return fallback[-10:]


def _save_turn(conv_id, user_id: int, user_msg: str, assistant_msg: str) -> int:
    """Save a user+assistant turn to DB. Creates conversation if conv_id is None."""
    try:
        if conv_id:
            conv = db.session.get(ChatConversation, int(conv_id))
            if not conv or conv.user_id != user_id:
                conv_id = None
        if not conv_id:
            # Auto-generate title from first message (first 60 chars)
            title = user_msg[:60].rstrip() + ("…" if len(user_msg) > 60 else "")
            conv = ChatConversation(user_id=user_id, title=title)
            db.session.add(conv)
            db.session.flush()  # get ID without committing
        else:
            conv = db.session.get(ChatConversation, int(conv_id))

        db.session.add(ChatMessage(conversation_id=conv.id, role="user",    content=user_msg))
        db.session.add(ChatMessage(conversation_id=conv.id, role="assistant", content=assistant_msg))
        conv.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return conv.id
    except Exception as exc:
        db.session.rollback()
        import logging
        logging.getLogger(__name__).warning("_save_turn failed: %s", exc)
        return conv_id or 0


def build_business_context() -> str:
    """Build live business context string for Claude's system prompt."""
    today       = date.today()
    week_start  = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_row = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0), func.count(Sale.id))
        .where(Sale.sale_date.between(today, today))
    ).one()
    week_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.sale_date >= week_start)
    ).scalar() or 0
    month_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.sale_date >= month_start)
    ).scalar() or 0
    month_expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= month_start)
    ).scalar() or 0
    top_products = db.session.execute(
        db.select(
            Product.name,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("qty"),
            func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.sale_date >= month_start)
        .group_by(Product.name)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(5)
    ).all()
    low_stock = db.session.execute(
        db.select(Product)
        .where(Product.is_active == True)
        .where(Product.quantity < 500)
        .order_by(Product.quantity.asc())
        .limit(10)
    ).scalars().all()
    customers = db.session.execute(
        db.select(Sale.customer_name, func.count(Sale.id).label("visits"),
                  func.sum(Sale.total_amount).label("spent"))
        .where(Sale.customer_name.isnot(None))
        .where(Sale.sale_date >= month_start)
        .group_by(Sale.customer_name)
        .order_by(func.count(Sale.id).desc())
        .limit(5)
    ).all()

    top_lines = [
        f"- {r.name}: {int(r.qty or 0)}g sold, NPR {float(r.revenue or 0):,.2f}"
        for r in top_products
    ] or ["- No product sales yet this month."]
    low_lines = [f"- {p.name}: {p.quantity}g" for p in low_stock] or ["- No products under 500g."]
    cust_lines = [
        f"- {r.customer_name}: {r.visits} visits, NPR {float(r.spent or 0):,.2f}"
        for r in customers
    ] or ["- No repeat customer data this month."]

    return "\n".join([
        f"Date: {today.isoformat()} AD; BS month: {bs_month_name(today)}",
        f"Today revenue: NPR {float(today_row[0] or 0):,.2f}; sales: {today_row[1] or 0}",
        f"This week revenue: NPR {float(week_revenue):,.2f}",
        f"This month revenue: NPR {float(month_revenue):,.2f}",
        f"This month profit estimate: NPR {float(month_revenue) - float(month_expenses):,.2f}",
        "Top 5 selling products this month:",
        *top_lines,
        "Low stock alerts under 500g:",
        *low_lines,
        "Frequent customers this month:",
        *cust_lines,
    ])
