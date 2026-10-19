"""
smart_mart/mcp_stdio.py — MCP stdio transport bridge
=====================================================
Implements the Model Context Protocol (MCP) stdio transport so Claude
Desktop, Kiro, and other MCP clients can connect to SmartMart's tools
without running a separate server.

Usage (from project root):
    python -m smart_mart.mcp_stdio

The client sends JSON-RPC messages on stdin; responses go to stdout.

Protocol: MCP 2025-03-26 (JSON-RPC 2.0 subset)
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Logging — must go to stderr only; stdout is the MCP protocol wire.
# Configure lazily so importing this module (e.g. in tests) does NOT call
# basicConfig and hijack the root logger of the host application.
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Set up stderr logging once, only when running as the main process."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [mcp_stdio] %(levelname)s %(message)s")
    )
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False  # don't double-log if a root handler is also present


# ---------------------------------------------------------------------------
# Flask app — created lazily inside main() so importing this module is cheap.
# ---------------------------------------------------------------------------
_app = None


def _get_app():
    global _app
    if _app is None:
        os.environ.setdefault("FLASK_ENV", "development")
        from smart_mart.app import create_app
        _app = create_app(os.environ.get("FLASK_ENV", "development"))
    return _app


# ---------------------------------------------------------------------------
# Tool handler cache — populated on first tools/list or tools/call.
# Handlers are plain module-level functions; they do not require an active
# app context to be *imported*, only to be *called*.
# ---------------------------------------------------------------------------
_handlers: dict[str, Callable[[dict], dict]] | None = None
_TOOLS: list[dict[str, Any]] | None = None


def _get_tools_and_handlers() -> tuple[list[dict[str, Any]], dict[str, Callable[[dict], dict]]]:
    """Import and cache the TOOLS list + handler map exactly once."""
    global _handlers, _TOOLS
    if _handlers is not None:
        return _TOOLS, _handlers  # type: ignore[return-value]

    # Import the blueprint module.  TOOLS is a plain list (no DB access),
    # so no app context is needed for the import itself.
    from smart_mart.blueprints.mcp.routes import (
        TOOLS,
        _tool_anomalies,
        _tool_ask_advisor,
        _tool_cashflow,
        _tool_create_po_draft,
        _tool_customer_profile,
        _tool_inventory,
        _tool_kpis,
        _tool_low_stock,
        _tool_recommendations,
        _tool_sales_summary,
        _tool_search_products,
        _tool_top_products,
    )

    _TOOLS = TOOLS
    _handlers = {
        "get_inventory_status":        _tool_inventory,
        "get_sales_summary":           _tool_sales_summary,
        "search_products":             _tool_search_products,
        "get_low_stock_products":      _tool_low_stock,
        "get_top_products":            _tool_top_products,
        "get_customer_profile":        _tool_customer_profile,
        "create_purchase_order_draft": _tool_create_po_draft,
        "get_business_kpis":           _tool_kpis,
        "get_cashflow_forecast":       _tool_cashflow,
        "ask_business_advisor":        _tool_ask_advisor,
        "get_recommendations":         _tool_recommendations,
        "get_anomaly_report":          _tool_anomalies,
    }
    return _TOOLS, _handlers


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _call_tool(name: str, args: dict) -> dict[str, Any]:
    """Call a named tool and always return a dict."""
    app = _get_app()
    _, handlers = _get_tools_and_handlers()

    handler = handlers.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}

    # All tool handlers use DB models — they must run inside an app context.
    with app.app_context():
        try:
            result = handler(args)
        except Exception as exc:
            log.exception("Tool %s raised an exception", name)
            return {"error": str(exc)}

    # Guarantee the return type is always a dict so callers can safely .get()
    if not isinstance(result, dict):
        log.warning(
            "Tool %s returned unexpected type %s; wrapping in {'result': ...}",
            name,
            type(result).__name__,
        )
        return {"result": result}
    return result


# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatch table
# ---------------------------------------------------------------------------

def _handle_initialize(msg: dict) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
                "logging": {},
            },
            "serverInfo": {"name": "smartmart", "version": "1.0.0"},
        },
    }


def _handle_ping(msg: dict) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}


def _handle_tools_list(msg: dict) -> dict[str, Any]:
    tools, _ = _get_tools_and_handlers()
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"tools": tools},
    }


def _handle_tools_call(msg: dict) -> dict[str, Any]:
    params = msg.get("params") or {}
    tool_name: str = params.get("name") or ""
    # "arguments" may be explicitly null — default to empty dict
    arguments: dict = params.get("arguments") or {}

    log.info("tools/call  name=%s", tool_name)

    result = _call_tool(tool_name, arguments)
    is_error = bool(result.get("error"))

    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }
            ],
            "isError": is_error,
        },
    }


def _handle_resources_list(msg: dict) -> dict[str, Any]:
    """Stub — no resources exposed; avoids noisy 'method not found' in clients."""
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"resources": []},
    }


def _handle_prompts_list(msg: dict) -> dict[str, Any]:
    """Stub — no prompts exposed; avoids noisy 'method not found' in clients."""
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"prompts": []},
    }


# Methods that produce a response, keyed by JSON-RPC method name.
_DISPATCH: dict[str, Callable[[dict], dict[str, Any]]] = {
    "initialize":     _handle_initialize,
    "ping":           _handle_ping,
    "tools/list":     _handle_tools_list,
    "tools/call":     _handle_tools_call,
    "resources/list": _handle_resources_list,
    "prompts/list":   _handle_prompts_list,
}

# Notification methods — fire-and-forget, no response sent.
_NOTIFICATIONS = frozenset({"initialized"})


# ---------------------------------------------------------------------------
# MCP JSON-RPC message handler
# ---------------------------------------------------------------------------

def _handle(msg: dict) -> dict[str, Any] | None:
    """
    Process one JSON-RPC message and return the response dict, or None for
    notifications that require no reply.
    """
    method: str = msg.get("method", "")
    msg_id = msg.get("id")

    # Notifications: "initialized", "notifications/*" — no response.
    if method in _NOTIFICATIONS or method.startswith("notifications/"):
        return None

    handler = _DISPATCH.get(method)
    if handler is not None:
        return handler(msg)

    # Unknown method — reply with JSON-RPC error only if a reply is expected.
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


# ---------------------------------------------------------------------------
# Main stdio loop
# ---------------------------------------------------------------------------

def main() -> None:
    _configure_logging()
    log.info("SmartMart MCP stdio bridge starting")

    # Eagerly bootstrap the Flask app so the first tool call isn't slow.
    _get_app()

    # Force UTF-8 on stdin/stdout regardless of the platform default.
    # sys.stdin/stdout.reconfigure() is the preferred approach on Python 3.7+
    # because it avoids double-wrapping the already-open text streams.
    # On Windows the console codepage is often cp1252; product names and
    # descriptions contain Nepali (Devanagari) text that would corrupt silently.
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # type: ignore[attr-defined]
        stdin, stdout = sys.stdin, sys.stdout
    except AttributeError:
        # Fallback for environments that don't support reconfigure (e.g. IDLE).
        stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")
        stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

    log.info("MCP stdio bridge ready — waiting for client messages")

    try:
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue

            # Parse — skip malformed frames
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("Skipping malformed JSON frame: %s", exc)
                continue

            # Guard against non-object JSON values (arrays, strings, numbers…)
            if not isinstance(msg, dict):
                log.warning("Skipping non-object JSON frame: %r", line[:120])
                continue

            # Dispatch
            try:
                response = _handle(msg)
            except Exception as exc:
                log.exception("Unhandled error in _handle")
                response = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32603, "message": f"Internal error: {exc}"},
                }

            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()

    except (EOFError, KeyboardInterrupt):
        # Client closed the pipe or the process received SIGINT — clean exit.
        log.info("MCP stdio bridge shutting down")


if __name__ == "__main__":
    main()
