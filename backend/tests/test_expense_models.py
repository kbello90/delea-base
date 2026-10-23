"""Unit tests for the expense models (no database)."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.expense import ExpenseCategory, ExpenseCreate, ExpenseUpdate, ReceiptRegister

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20261023000100_expense_categories_receipts.sql"
)
BASE = {"expense_date": "2026-10-01", "description": "Chain", "amount_cents": 100}


def test_categories_match_the_migration_in_order() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    body = re.search(r"create type public\.expense_category as enum \((.*?)\);", sql, re.S)
    assert body is not None
    assert re.findall(r"'([a-z_]+)'", body.group(1)) == [c.value for c in ExpenseCategory]


def test_create_defaults_to_other_and_takes_only_the_list() -> None:
    assert ExpenseCreate(**BASE).category is ExpenseCategory.other
    with pytest.raises(ValidationError):
        ExpenseCreate(**BASE, category="services")


@pytest.mark.parametrize(
    "forged",
    [
        {"receipt_path": "x"},
        {"receipt_content_type": "application/pdf"},
        {"receipt_size_bytes": 1},
        {"receipt_uploaded_at": "2026-10-01T00:00:00Z"},
        {"receipt_url": "https://example.com"},
        {"has_receipt": True},
        {"tenant_id": "11111111-1111-1111-1111-111111111111"},
    ],
)
def test_receipt_fields_are_never_accepted_on_expense_bodies(forged: dict) -> None:
    with pytest.raises(ValidationError):
        ExpenseCreate(**BASE, **forged)
    with pytest.raises(ValidationError):
        ExpenseUpdate(**forged)


def test_register_takes_only_an_upload_id_and_type() -> None:
    with pytest.raises(ValidationError):
        ReceiptRegister(
            upload_id="00000000-0000-4000-8000-000000000000",
            content_type="application/pdf",
            path="11111111-1111-1111-1111-111111111111/x/y.pdf",
        )
    with pytest.raises(ValidationError):
        ReceiptRegister(upload_id="00000000-0000-4000-8000-000000000000", content_type="text/html")
