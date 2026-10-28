"""Customer emails (Slice D2) without the database: what each email says, how to pay, the
escaping of every shop and shopper value, the plain-text version, the footer, and which
event calls for which email."""

import dataclasses

import pytest

from app.services import email_templates as t
from app.worker import customer_templates

TRACK = "https://aurora.example/orders/0b7c1d1e-0000-4000-8000-000000000001"


def order(**changes) -> t.Order:
    base = t.Order(
        shop="Aurora Gems",
        color="#224466",
        ref="W-ABCDEFGH",
        first_name="Sarah",
        currency="USD",
        lines=[
            {
                "description": "Halo Ring",
                "qty": 1,
                "line_total_cents": 14500,
                "personalization": [{"label": "Ring size", "value": "7"}],
            }
        ],
        subtotal_cents=14500,
        shipping_cents=600,
        tax_cents=1160,
        total_cents=16260,
        paid_cents=0,
        balance_cents=16260,
        fulfillment="ship",
        payment_choice="bank_transfer",
        payment_instructions="Bank: First Example\nAccount: 000123",
        pickup_note=None,
        carrier=None,
        tracking_number=None,
        tracking_url=TRACK,
        website_url="https://aurora.example",
        contact_email="hello@aurora.example",
    )
    return dataclasses.replace(base, **changes)


def all_emails(o: t.Order) -> list[t.Rendered]:
    return [t.customer_email(name, o, 5000) for name in t.CUSTOMER_TEMPLATES]


def test_order_received_lists_the_order_and_how_to_pay() -> None:
    out = t.order_received(order())
    assert out.subject == "Order W-ABCDEFGH received"
    for part in (
        "Thank you, Sarah",
        "Halo Ring × 1 (Ring size: 7): $145.00",
        "Shipping: $6.00",
        "Tax: $11.60",
        "Total: $162.60",
        "How to pay $162.60: by bank transfer.",
        "Bank: First Example",
        "Account: 000123",
        f"Track your order: {TRACK}",
    ):
        assert part in out.text, part
    assert f'href="{TRACK}"' in out.html


def test_a_card_order_gets_pay_now_and_a_paid_one_does_not() -> None:
    unpaid = t.order_received(order(payment_choice="card", payment_instructions=None))
    assert f"Pay now: {TRACK}" in unpaid.text  # opens the tracking page; it never charges
    assert "You can pay it by card on your order page." in unpaid.text
    small = t.order_received(order(payment_choice="card", paid_cents=16230, balance_cents=30))
    assert "Pay the rest with the shop." in small.text and "Pay now" not in small.text
    paid = t.order_received(order(payment_choice="card", paid_cents=16260, balance_cents=0))
    assert "It's paid. Thank you." in paid.text and "Pay now" not in paid.text


def test_pickup_orders() -> None:
    o = order(
        fulfillment="pickup",
        shipping_cents=0,
        payment_choice="pay_on_pickup",
        payment_instructions=None,
        pickup_note="Studio, 11-6",
    )
    received = t.order_received(o)
    assert "You pay $162.60 when you pick up your order." in received.text
    assert "Pickup: Studio, 11-6" in received.text and "Shipping" not in received.text
    ready = t.shipped(o)
    assert ready.subject == "Order W-ABCDEFGH is ready for pickup"
    assert "Pickup: Studio, 11-6" in ready.text


def test_deposit_payment_production_and_shipped() -> None:
    deposit = t.deposit_received(order(paid_cents=5000, balance_cents=11260), 5000)
    assert deposit.subject == "Deposit received for W-ABCDEFGH"
    assert "We received your deposit of $50.00 for order W-ABCDEFGH." in deposit.text
    assert "Paid so far: $50.00" in deposit.text and "Left to pay: $112.60" in deposit.text
    paid = t.payment_received(order(paid_cents=16260, balance_cents=0))
    assert "Your order W-ABCDEFGH is paid in full: $162.60." in paid.text
    assert t.in_production(order()).subject == "Order W-ABCDEFGH is being made"
    shipped = t.shipped(order(carrier="USPS", tracking_number="9400 1000"))
    assert shipped.subject == "Order W-ABCDEFGH has shipped"
    assert "Carrier: USPS" in shipped.text and "Tracking number: 9400 1000" in shipped.text
    bare = t.shipped(order())
    assert "Carrier" not in bare.text and "Tracking number" not in bare.text


def test_every_customer_email_has_the_footer_a_text_version_and_no_promotion() -> None:
    for out in all_emails(order(intro="Thanks for shopping small.")):
        assert "You're receiving this because you placed an order with Aurora Gems." in out.text
        assert "Questions? Write to hello@aurora.example." in out.text
        assert "https://aurora.example" in out.text
        assert out.text.endswith("\n") and "<" not in out.text.replace("<notifications", "")
        assert 'name="viewport"' in out.html and "max-width:560px" in out.html
        assert out.text.splitlines()[4] == "Thanks for shopping small."  # the intro comes first
        for word in ("unsubscribe", "sale", "discount", "% off", "coupon"):
            assert word not in out.text.lower(), (out.subject, word)


def test_every_shop_and_shopper_value_is_escaped() -> None:
    evil = "<script>alert(1)</script>"
    o = order(
        shop=evil,
        first_name=evil,
        ref=evil,
        lines=[
            {
                "description": evil,
                "qty": 1,
                "line_total_cents": 100,
                "personalization": [{"label": evil, "value": evil}],
            }
        ],
        payment_instructions=evil,
        pickup_note=evil,
        carrier=evil,
        tracking_number=evil,
        contact_email=evil,
        intro=evil,
        tracking_url='https://x.example/"><script>',
    )
    for out in [*all_emails(o), t.shipped(dataclasses.replace(o, fulfillment="pickup"))]:
        assert "<script>" not in out.html, out.subject
        assert "&lt;script&gt;" in out.html


def test_an_order_has_no_place_for_an_address_or_phone() -> None:
    fields = {f.name for f in dataclasses.fields(t.Order)}
    for forbidden in ("phone", "street", "line1", "address", "city", "postal", "zip", "note"):
        assert not any(forbidden in f for f in fields if f != "pickup_note"), forbidden
    # Whatever else the context holds, only the known fields are read.
    ctx = {
        "order_ref": "W-ABCDEFGH",
        "ship_line1": "1 Main St",
        "phone": "555-0142",
        "lines": [],
        "total_cents": 100,
    }
    o = t.order_from_context("Aurora Gems", None, ctx, TRACK, None, None)
    for out in all_emails(o):
        assert "Main St" not in out.html + out.text and "555-0142" not in out.html + out.text


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        ("order_placed", {"invoice_id": "i"}, ["order_received"]),
        ("deposit_received", {"invoice_id": "i", "payment_id": "p"}, ["deposit_received"]),
        ("production_started", {"invoice_id": "i"}, ["in_production"]),
        (
            "order_status_changed",
            {"invoice_id": "i", "from_status": "quote", "to_status": "paid"},
            ["payment_received"],
        ),
        (
            "order_status_changed",
            {"invoice_id": "i", "from_status": "quote", "to_status": "in_production"},
            ["payment_received", "in_production"],
        ),
        (
            "order_status_changed",
            {"invoice_id": "i", "from_status": "in_production", "to_status": "shipped"},
            ["shipped"],
        ),
        (
            "order_status_changed",
            {"invoice_id": "i", "from_status": "shipped", "to_status": "delivered"},
            [],
        ),
        ("stock_low", {"product_id": "x"}, []),
    ],
)
def test_which_event_sends_which_email(event: str, payload: dict, expected: list[str]) -> None:
    assert [name for name, _ in customer_templates(event, payload)] == expected


def test_one_key_per_order_and_email_and_per_payment_for_a_deposit() -> None:
    started = customer_templates("production_started", {"invoice_id": "i"})
    status = customer_templates(
        "order_status_changed",
        {"invoice_id": "i", "from_status": "paid", "to_status": "in_production"},
    )
    assert started[0][1] == status[0][1] == "customer:in_production:i"
    first = customer_templates("deposit_received", {"invoice_id": "i", "payment_id": "p1"})
    second = customer_templates("deposit_received", {"invoice_id": "i", "payment_id": "p2"})
    assert first[0][1] != second[0][1]
