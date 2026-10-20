"""Unit tests for the material models (no database)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.material import MaterialCreate, MaterialOut, MaterialUpdate


def test_quantities_accept_numbers_and_strings_exactly() -> None:
    m = MaterialCreate(
        name="Wire", unit="g", unit_cost_cents=10, qty_on_hand="2.50", reorder_threshold=1.25
    )
    assert m.qty_on_hand == Decimal("2.50")
    assert m.reorder_threshold == Decimal("1.25")


@pytest.mark.parametrize(
    "bad",
    [
        {"qty_on_hand": "1.234"},  # more than 2 decimals
        {"qty_on_hand": "-1"},  # negative stock
        {"qty_on_hand": "12345678901.00"},  # beyond numeric(12,2)
        {"unit_cost_cents": -1},
        {"unit_cost_cents": 1.5},
        {"name": ""},
        {"tenant_id": "11111111-1111-1111-1111-111111111111"},
    ],
)
def test_invalid_create_values_are_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        MaterialCreate(**{"name": "Wire", "unit": "g", "unit_cost_cents": 10, **bad})


def test_update_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MaterialUpdate(tenant_id="11111111-1111-1111-1111-111111111111")


def test_quantities_serialize_as_exact_strings() -> None:
    out = MaterialOut(
        id="a3000000-0000-0000-0000-000000000002",
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="Plating",
        unit="vials",
        unit_cost_cents=6500,
        qty_on_hand=Decimal("3.00"),
        reorder_threshold=Decimal("5.00"),
        is_low=True,
        created_at="2026-10-20T00:00:00Z",
        updated_at="2026-10-20T00:00:00Z",
    )
    data = out.model_dump(mode="json")
    assert data["qty_on_hand"] == "3.00"
    assert data["reorder_threshold"] == "5.00"
