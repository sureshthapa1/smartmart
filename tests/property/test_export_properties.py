"""Property-based tests for export service.

Property 26: CSV export contains all rows and correct headers
"""
# Feature: smart-mart-inventory

import csv
import io

import pytest
@pytest.mark.slow`nfrom hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.services.exporter import export_report_csv


@pytest.fixture(scope="module")
def app():
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(autouse=True)
def app_ctx(app):
    with app.app_context():
        yield


# ── Property 26: CSV export contains all rows and correct headers ─────────────

@settings(max_examples=100)
@given(
    rows=st.lists(
        st.fixed_dictionaries({
            "name": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"))),
            "quantity": st.integers(min_value=0, max_value=9999),
            "price": st.floats(min_value=0.0, max_value=9999.0, allow_nan=False, allow_infinity=False),
        }),
        min_size=0,
        max_size=50,
    ),
    columns=st.just(["name", "quantity", "price"]),
)
def test_csv_row_count_and_headers(app, rows, columns):
    # Feature: smart-mart-inventory, Property 26: CSV export contains all rows and correct headers
    with app.app_context():
        csv_output = export_report_csv(rows, columns)
        reader = csv.DictReader(io.StringIO(csv_output))
        result_rows = list(reader)
        # Header check
        assert reader.fieldnames is not None
        for col in columns:
            assert col in reader.fieldnames
        # Row count check
        assert len(result_rows) == len(rows)

