"""What DELEA sends to Stripe for a Checkout Session and for the shop's card-only payment method
configuration (TRD §6.14). CI never talks to Stripe and the fake gateway never sees these
parameters, so a parameter Stripe refuses on the pinned API version is caught here or by the
README's manual check, nowhere else."""

from datetime import UTC, datetime

from app.services.stripe_service import (
    CARD_CONFIG_NAME,
    STRIPE_API_VERSION,
    CheckoutRequest,
    card_config_params,
    checkout_session_params,
)

REQUEST = CheckoutRequest(
    account_id="acct_Shop",
    amount_cents=9800,
    currency="USD",
    description="Order W-7DFS3CDZ",
    metadata={"tenant_id": "t", "invoice_id": "i", "payment_id": "p", "order_ref": "W-7DFS3CDZ"},
    success_url="http://shop.localhost:5174/orders/x?paid=1",
    cancel_url="http://shop.localhost:5174/orders/x",
    idempotency_key="delea:checkout:p",
    card_config_id="pmc_CardOnly",
)


def test_the_api_version_is_the_one_the_manual_check_ran_on() -> None:
    assert STRIPE_API_VERSION == "2026-09-30.endive"


def test_a_session_names_the_card_only_configuration_and_never_payment_method_types() -> None:
    params = checkout_session_params(REQUEST, datetime(2026, 10, 29, 12, tzinfo=UTC))
    # 2026-09-30.endive refuses payment_method_types on a Checkout Session.
    assert "payment_method_types" not in params
    assert "excluded_payment_method_types" not in params
    assert params["payment_method_configuration"] == "pmc_CardOnly"
    assert params["mode"] == "payment"
    assert params["line_items"] == [
        {
            "quantity": 1,
            "price_data": {
                "currency": "usd",
                "unit_amount": 9800,
                "product_data": {"name": "Order W-7DFS3CDZ"},
            },
        }
    ]
    assert params["client_reference_id"] == "p"
    assert params["metadata"] == params["payment_intent_data"]["metadata"] == REQUEST.metadata
    assert params["expires_at"] == int(datetime(2026, 10, 29, 12, tzinfo=UTC).timestamp())


def test_the_configuration_has_card_on_and_every_listed_method_off() -> None:
    existing = {
        "id": "pmc_Default",
        "object": "payment_method_configuration",
        "active": True,
        "is_default": True,
        "name": "Default",
        "card": {"available": True, "display_preference": {"preference": "on", "value": "on"}},
        "link": {"available": True, "display_preference": {"preference": "on", "value": "on"}},
        "klarna": {"available": True, "display_preference": {"preference": "on", "value": "on"}},
        "us_bank_account": {
            "available": False,
            "display_preference": {"preference": "none", "value": "off"},
        },
        "apple_pay": {"available": True, "display_preference": {"preference": "on", "value": "on"}},
        "a_method_stripe_adds_later": {"display_preference": {"preference": "on"}},
    }
    params = card_config_params(existing)
    assert params.pop("name") == CARD_CONFIG_NAME
    assert params.pop("card") == {"display_preference": {"preference": "on"}}
    assert set(params) == {
        "link",
        "klarna",
        "us_bank_account",
        "apple_pay",
        "a_method_stripe_adds_later",
    }
    assert all(v == {"display_preference": {"preference": "off"}} for v in params.values())


def test_card_is_on_even_if_the_listed_configuration_had_it_off() -> None:
    params = card_config_params(
        {"card": {"display_preference": {"preference": "off", "value": "off"}}}
    )
    assert params["card"] == {"display_preference": {"preference": "on"}}
