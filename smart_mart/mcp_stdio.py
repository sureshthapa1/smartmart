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

Upgrades applied
----------------
* Batch JSON-RPC support   — arrays of requests handled atomically
* Argument schema validation before dispatch (per-tool inputSchema)
* Stub handlers for resources/read and prompts/get
* logging/setLevel notification support (dynamic log-level control)
* JSON structured logging to stderr for observability pipelines
* serverInfo.version pulled from smart_mart.__version__
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Logging — JSON structured, stderr-only; stdout is the MCP protocol wire.
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for easy log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    """Set up JSON-structured stderr logging once, only when running as main."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False  # don't double-log if a root handler is also present


# ---------------------------------------------------------------------------
# Package version — pulled from smart_mart.__version__
# ---------------------------------------------------------------------------
def _get_version() -> str:
    try:
        from smart_mart import __version__
        return __version__
    except Exception:
        return "0.0.0"


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
# ---------------------------------------------------------------------------
_handlers: dict[str, Callable[[dict], dict]] | None = None
_TOOLS: list[dict[str, Any]] | None = None


def _get_tools_and_handlers() -> tuple[list[dict[str, Any]], dict[str, Callable[[dict], dict]]]:
    """Import and cache the TOOLS list + handler map exactly once."""
    global _handlers, _TOOLS
    if _handlers is not None:
        return _TOOLS, _handlers  # type: ignore[return-value]

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
# Argument schema validation
# ---------------------------------------------------------------------------

def _validate_arguments(tool_name: str, arguments: dict) -> str | None:
    """
    Validate *arguments* against the tool's inputSchema.
    Returns an error message string on failure, or None on success.

    Only checks required fields for now; full JSON-Schema validation would
    require the ``jsonschema`` library which is not a mandatory dep.
    """
    tools, _ = _get_tools_and_handlers()
    schema: dict | None = None
    for tool in tools:
        if tool.get("name") == tool_name:
            schema = tool.get("inputSchema") or {}
            break

    if schema is None:
        return f"Unknown tool: {tool_name}"

    required: list[str] = schema.get("required") or []
    missing = [f for f in required if f not in arguments]
    if missing:
        return f"Missing required argument(s): {', '.join(missing)}"

    return None  # all good


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _call_tool(name: str, args: dict) -> dict[str, Any]:
    """Validate args, then call a named tool inside a Flask app context."""
    # Validate before touching the DB / Flask
    err = _validate_arguments(name, args)
    if err:
        return {"error": err}

    app = _get_app()
    _, handlers = _get_tools_and_handlers()
    handler = handlers.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}

    with app.app_context():
        try:
            result = handler(args)
        except Exception as exc:
            log.exception("Tool %s raised an exception", name)
            return {"error": str(exc)}

    if not isinstance(result, dict):
        log.warning(
            "Tool %s returned unexpected type %s; wrapping in {'result': ...}",
            name,
            type(result).__name__,
        )
        return {"result": result}
    return result


# ---------------------------------------------------------------------------
# MCP JSON-RPC handlers
# ---------------------------------------------------------------------------

def _handle_initialize(msg: dict) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {
                "tools":     {},
                "resources": {},
                "prompts":   {},
                "logging":   {},
            },
            "serverInfo": {"name": "smartmart", "version": _get_version()},
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
    """No resources exposed — return empty list to satisfy spec."""
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"resources": []},
    }


def _handle_resources_read(msg: dict) -> dict[str, Any]:
    """Stub — no resources available; return a clean error."""
    params = msg.get("params") or {}
    uri = params.get("uri", "<unknown>")
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "error": {
            "code": -32602,
            "message": f"Resource not found: {uri}",
        },
    }


def _handle_prompts_list(msg: dict) -> dict[str, Any]:
    """No prompts exposed — return empty list to satisfy spec."""
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"prompts": []},
    }


def _handle_prompts_get(msg: dict) -> dict[str, Any]:
    """Stub — no prompts available; return a clean error."""
    params = msg.get("params") or {}
    name = params.get("name", "<unknown>")
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "error": {
            "code": -32602,
            "message": f"Prompt not found: {name}",
        },
    }


# Methods that produce a response, keyed by JSON-RPC method name.
_DISPATCH: dict[str, Callable[[dict], dict[str, Any]]] = {
    "initialize":      _handle_initialize,
    "ping":            _handle_ping,
    "tools/list":      _handle_tools_list,
    "tools/call":      _handle_tools_call,
    "resources/list":  _handle_resources_list,
    "resources/read":  _handle_resources_read,
    "prompts/list":    _handle_prompts_list,
    "prompts/get":     _handle_prompts_get,
}

# Notification methods — fire-and-forget, no response sent.
_NOTIFICATIONS = frozenset({"initialized", "notifications/initialized"})


# ---------------------------------------------------------------------------
# logging/setLevel notification handler
# ---------------------------------------------------------------------------

_LOG_LEVEL_MAP: dict[str, int] = {
    "debug":    logging.DEBUG,
    "info":     logging.INFO,
    "warning":  logging.WARNING,
    "warn":     logging.WARNING,
    "error":    logging.ERROR,
    "critical": logging.CRITICAL,
}


def _apply_logging_set_level(msg: dict) -> None:
    """Handle logging/setLevel notification — adjust runtime log level."""
    params = msg.get("params") or {}
    level_str: str = str(params.get("level") or "info").lower()
    level = _LOG_LEVEL_MAP.get(level_str)
    if level is not None:
        log.setLevel(level)
        log.info("Log level changed to %s", level_str.upper())
    else:
        log.warning("Unknown log level in logging/setLevel: %r", level_str)


# ---------------------------------------------------------------------------
# MCP JSON-RPC message handler (single message)
# ---------------------------------------------------------------------------

def _handle(msg: dict) -> dict[str, Any] | None:
    """
    Process one JSON-RPC message and return the response dict, or None for
    notifications that require no reply.
    """
    method: str = msg.get("method", "")
    msg_id = msg.get("id")

    # logging/setLevel is a notification with side-effects
    if method == "logging/setLevel":
        _apply_logging_set_level(msg)
        return None

    # Generic notifications — no response
    if method in _NOTIFICATIONS or method.startswith("notifications/"):
        return None

    handler = _DISPATCH.get(method)
    if handler is not None:
        return handler(msg)

    # Unknown method — reply with error only if a reply is expected (has id)
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


# ---------------------------------------------------------------------------
# Batch JSON-RPC dispatcher
# ---------------------------------------------------------------------------

def _handle_frame(raw_msg: dict | list) -> list[dict[str, Any]]:
    """
    Accept either a single JSON-RPC object or a batch array.
    Always returns a list of zero-or-more response objects.
    """
    if isinstance(raw_msg, list):
        # JSON-RPC 2.0 §6: batch — process each item, collect non-None responses
        if not raw_msg:
            # Empty batch is a protocol error
            return [
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request: empty batch"},
                }
            ]
        responses: list[dict] = []
        for item in raw_msg:
            if not isinstance(item, dict):
                responses.append(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request"},
                    }
                )
                continue
            try:
                resp = _handle(item)
            except Exception as exc:
                log.exception("Unhandled error in batch item")
                resp = {
                    "jsonrpc": "2.0",
                    "id": item.get("id"),
                    "error": {"code": -32603, "message": f"Internal error: {exc}"},
                }
            if resp is not None:
                responses.append(resp)
        return responses

    # Single object
    if not isinstance(raw_msg, dict):
        return [
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }
        ]

    try:
        resp = _handle(raw_msg)
    except Exception as exc:
        log.exception("Unhandled error in _handle")
        resp = {
            "jsonrpc": "2.0",
            "id": raw_msg.get("id"),
            "error": {"code": -32603, "message": f"Internal error: {exc}"},
        }

    return [resp] if resp is not None else []


# ---------------------------------------------------------------------------
# Main stdio loop
# ---------------------------------------------------------------------------

def main() -> None:
    _configure_logging()
    log.info("SmartMart MCP stdio bridge starting  version=%s", _get_version())

    # Eagerly bootstrap the Flask app so the first tool call isn't slow.
    _get_app()

    # Force UTF-8 on stdin/stdout regardless of the platform default.
    # On Windows the console codepage is often cp1252; Nepali (Devanagari)
    # text in product names/descriptions would corrupt silently otherwise.
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

            # Parse — skip malformed frames with a logged warning
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("Skipping malformed JSON frame: %s", exc)
                continue

            # Dispatch (handles both single objects and batch arrays)
            responses = _handle_frame(parsed)

            for response in responses:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            if responses:
                stdout.flush()

    except (EOFError, KeyboardInterrupt):
        log.info("MCP stdio bridge shutting down")


if __name__ == "__main__":
    main()
