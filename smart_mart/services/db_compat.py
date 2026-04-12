"""Cross-database compatibility helpers for SQLAlchemy queries.

SQLite uses func.strftime(), PostgreSQL uses func.to_char() / extract().
These helpers work on both.
"""
from __future__ import annotations
from sqlalchemy import func, extract, text
from ..extensions import db


def _is_postgres() -> bool:
    """Return True if the current engine is PostgreSQL."""
    try:
        return "postgresql" in str(db.engine.url).lower()
    except Exception:
        return False


def date_format_year_month(col):
    """Format datetime column as 'YYYY-MM'. Works on SQLite and PostgreSQL."""
    if _is_postgres():
        return func.to_char(col, "YYYY-MM")
    return func.strftime("%Y-%m", col)


def date_format_year_week(col):
    """Format datetime column as 'YYYY-WNN'. Works on SQLite and PostgreSQL."""
    if _is_postgres():
        return func.to_char(col, "IYYY-IW")
    return func.strftime("%Y-W%W", col)


def date_format_hour(col):
    """Extract hour (0-23) as string. Works on SQLite and PostgreSQL."""
    if _is_postgres():
        return func.to_char(col, "HH24")
    return func.strftime("%H", col)


def date_format_dow(col):
    """Extract day of week (0=Sunday). Works on SQLite and PostgreSQL."""
    if _is_postgres():
        # PostgreSQL: 0=Sunday via to_char D gives 1=Sunday, adjust
        return func.to_char(col, "D")
    return func.strftime("%w", col)


def hour_extract(col):
    """Extract hour as integer. Works on SQLite and PostgreSQL."""
    if _is_postgres():
        return extract("hour", col)
    return func.strftime("%H", col).cast(db.Integer)
