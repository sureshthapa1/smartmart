"""MCP (Model Context Protocol) blueprint for SmartMart.

Exposes business data and actions as MCP-compatible tool endpoints.
These endpoints are called by Claude agents via the MCP protocol.
"""
from .routes import mcp_bp  # noqa: F401
