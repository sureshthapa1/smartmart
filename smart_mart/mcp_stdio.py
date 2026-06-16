"""
smart_mart/mcp_stdio.py — MCP stdio transport bridge
=====================================================
Implements the Model Context Protocol (MCP) stdio transport so Claude
Desktop, Kiro, and other MCP clients can connect to SmartMart's tools
without running a separate server.

Usage (from project root):
    python -m smart_mart.mcp_stdio

The client sends JSON-RPC messages on stdin; responses go to stdout.

Protocol: MCP 1.0 (JSON-RPC 2.0 subset)
"""

from __future__ import annotations

import json
import os
import sys

# Bootstrap Flask app so models and DB are available
os.environ.setdefault("FLASK_ENV", "development")

from smart_mart.app import create_app

_app = create_app(os.environ.get("FLASK_ENV", "development"))


# ── Tool implementations (thin wrappers around mcp/routes.py logic) ──────────

def _call_tool(name: str, args: dict) -> dict:
    with _app.app_context():
        from smart_mart.blueprints.mcp.routes import (
            _tool_inventory, _tool_sales_summary, _tool_search_products,
            _tool_low_stock, _tool_top_products, _tool_customer_profile,
            _tool_create_po_draft, _tool_kpis, _tool_cashflow,
            _tool_ask_advisor, _tool_recommendations, _tool_anomalies,
            TOOLS,
        )
        handlers = {
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
        handler = handlers.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}
        return handler(args)


# ── MCP JSON-RPC message handling ─────────────────────────────────────────────

def _handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    # Initialise handshake
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "smartmart",
                    "version": "1.0.0",
                },
            },
        }

    if method == "initialized":
        return None  # notification, no response

    if method == "tools/list":
        with _app.app_context():
            from smart_mart.blueprints.mcp.routes import TOOLS
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            }

    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = _call_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                    "isError": "error" in result,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    # Unknown method
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


# ── Main stdio loop ───────────────────────────────────────────────────────────

def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            response = _handle(msg)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
