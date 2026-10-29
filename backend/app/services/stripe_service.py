"""Stripe: Connect onboarding, Checkout Sessions and webhook signatures (TRD §6.14).

Card data never touches DELEA: shoppers pay on Stripe's hosted Checkout page, as a direct
charge on the shop's own connected account, with no platform fee (D20). Test mode only in
Slice B: a live key stops the API at startup, and live-mode events are refused by the database
functions.

The gateway is the only code that talks to Stripe. `LiveStripeGateway` uses the official
library (pinned in pyproject.toml, which pins the API version below); `FakeStripeGateway`
stands in for it in the tests (CI is offline and has no keys) and, with STRIPE_GATEWAY=fake, in
the local end-to-end run. Keys are never logged: errors name the variable, never its value.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import anyio
import stripe

from app.core.config import Settings

logger = logging.getLogger(__name__)

# The API version stripe==16.0.0 pins. Sent on every request; the owner's sandbox uses the
# same default so webhook payloads have the same shape.
STRIPE_API_VERSION = "2026-09-30.endive"
SIGNATURE_TOLERANCE_SECONDS = 300
CHECKOUT_LIFETIME = timedelta(hours=1)


class StripeSettingsError(RuntimeError):
    """The API refuses to start with these Stripe settings."""


def check_stripe_settings(settings: Settings) -> None:
    """Live keys are refused in Slice B (test mode only); the fake gateway is local-only."""
    key = settings.stripe_secret_key.strip()
    if key.startswith(("sk_live_", "rk_live_")):
        raise StripeSettingsError(
            "STRIPE_SECRET_KEY is a live key. This version takes Stripe test keys only "
            "(sk_test_...)."
        )
    gateway = settings.stripe_gateway.strip().lower()
    if gateway not in ("stripe", "fake"):
        raise StripeSettingsError("STRIPE_GATEWAY must be 'stripe' or 'fake'.")
    if gateway == "fake" and not settings.is_dev_environment:
        raise StripeSettingsError("STRIPE_GATEWAY=fake is for ENVIRONMENT=local/test only.")


class StripeUnavailable(Exception):
    """A Stripe call failed (refused, unreachable). The message is safe to log."""


@dataclass(frozen=True)
class AccountState:
    id: str
    charges_enabled: bool
    details_submitted: bool
    livemode: bool


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class CheckoutRequest:
    account_id: str
    amount_cents: int
    currency: str
    description: str
    metadata: dict[str, str]
    success_url: str
    cancel_url: str
    idempotency_key: str
    # The shop's card-only payment method configuration (pmc_...) on its own account.
    card_config_id: str


class StripeGateway(Protocol):
    async def create_account(self, *, tenant_id: str, idempotency_key: str) -> str: ...

    async def account_link(self, account_id: str, *, refresh_url: str, return_url: str) -> str: ...

    async def retrieve_account(self, account_id: str) -> AccountState: ...

    async def create_card_config(self, account_id: str, *, idempotency_key: str) -> str: ...

    async def card_config_usable(self, account_id: str, config_id: str) -> bool: ...

    async def create_checkout_session(self, request: CheckoutRequest) -> CheckoutSession: ...

    async def expire_checkout_session(self, account_id: str, session_id: str) -> None: ...


# The four controller settings that make a connected account the Standard equivalent (K1):
# the shop's own full Stripe Dashboard, the shop pays Stripe's fees, Stripe covers losses and
# collects the shop's details.
STANDARD_EQUIVALENT_CONTROLLER = {
    "stripe_dashboard": {"type": "full"},
    "fees": {"payer": "account"},
    "losses": {"payments": "stripe"},
    "requirement_collection": "stripe",
}

CARD_CONFIG_NAME = "DELEA card only"


def card_config_params(existing: dict[str, Any]) -> dict[str, Any]:
    """A payment method configuration with card on and every other method off. The methods are
    the ones an existing configuration on the account lists (each has a display_preference), so
    a method Stripe adds later is switched off too."""
    params: dict[str, Any] = {"name": CARD_CONFIG_NAME}
    for method, value in sorted(existing.items()):
        if isinstance(value, dict) and "display_preference" in value:
            preference = "on" if method == "card" else "off"
            params[method] = {"display_preference": {"preference": preference}}
    params["card"] = {"display_preference": {"preference": "on"}}
    return params


def checkout_session_params(request: CheckoutRequest, expires_at: datetime) -> dict[str, Any]:
    """The Checkout Session as sent to Stripe. Card only comes from the configuration: API
    version 2026-09-30.endive refuses payment_method_types."""
    return {
        "mode": "payment",
        "payment_method_configuration": request.card_config_id,
        "line_items": [
            {
                "quantity": 1,
                "price_data": {
                    "currency": request.currency.lower(),
                    "unit_amount": request.amount_cents,
                    "product_data": {"name": request.description},
                },
            }
        ],
        "success_url": request.success_url,
        "cancel_url": request.cancel_url,
        "expires_at": int(expires_at.timestamp()),
        "client_reference_id": request.metadata["payment_id"],
        "metadata": request.metadata,
        "payment_intent_data": {"metadata": request.metadata},
    }


class LiveStripeGateway:
    """The real Stripe API (test keys only), called from a worker thread."""

    def __init__(self, secret_key: str) -> None:
        self._client = stripe.StripeClient(
            secret_key, stripe_version=STRIPE_API_VERSION, max_network_retries=2
        )

    async def _call(self, what: str, fn, *args, **kwargs):
        try:
            return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
        except stripe.StripeError as exc:
            # The type and Stripe's request id only: never the request, never a key.
            logger.warning(
                "Stripe %s failed: %s (request %s)", what, type(exc).__name__, exc.request_id
            )
            raise StripeUnavailable(f"Stripe {what} failed") from exc

    async def create_account(self, *, tenant_id: str, idempotency_key: str) -> str:
        account = await self._call(
            "account creation",
            self._client.v1.accounts.create,
            {"controller": STANDARD_EQUIVALENT_CONTROLLER, "metadata": {"tenant_id": tenant_id}},
            {"idempotency_key": idempotency_key},
        )
        return account.id

    async def account_link(self, account_id: str, *, refresh_url: str, return_url: str) -> str:
        link = await self._call(
            "account link",
            self._client.v1.account_links.create,
            {
                "account": account_id,
                "type": "account_onboarding",
                "refresh_url": refresh_url,
                "return_url": return_url,
            },
        )
        return link.url

    async def retrieve_account(self, account_id: str) -> AccountState:
        account = await self._call("account read", self._client.v1.accounts.retrieve, account_id)
        return AccountState(
            account.id,
            bool(account.charges_enabled),
            bool(account.details_submitted),
            bool(getattr(account, "livemode", False)),
        )

    async def create_card_config(self, account_id: str, *, idempotency_key: str) -> str:
        options = {"stripe_account": account_id}
        listed = await self._call(
            "payment method configurations",
            self._client.v1.payment_method_configurations.list,
            {"limit": 100},
            options,
        )
        # The account's own default lists every method it can offer; any other will do.
        configs = sorted(listed.data, key=lambda c: (c.parent is not None, not c.is_default))
        if not configs:
            raise StripeUnavailable("Stripe payment method configurations: none on the account")
        created = await self._call(
            "card-only configuration",
            self._client.v1.payment_method_configurations.create,
            card_config_params(configs[0].to_dict()),
            {**options, "idempotency_key": idempotency_key},
        )
        return created.id

    async def card_config_usable(self, account_id: str, config_id: str) -> bool:
        """False when the configuration is gone, inactive or has card off (the owner changed
        it in their Stripe Dashboard); StripeUnavailable when Stripe can't say."""
        try:
            config = await anyio.to_thread.run_sync(
                lambda: self._client.v1.payment_method_configurations.retrieve(
                    config_id, None, {"stripe_account": account_id}
                )
            )
        except stripe.InvalidRequestError:
            return False
        except stripe.StripeError as exc:
            logger.warning(
                "Stripe configuration read failed: %s (request %s)",
                type(exc).__name__,
                exc.request_id,
            )
            raise StripeUnavailable("Stripe configuration read failed") from exc
        card = (config.to_dict().get("card") or {}).get("display_preference") or {}
        return bool(config.active) and card.get("value") == "on"

    async def create_checkout_session(self, request: CheckoutRequest) -> CheckoutSession:
        expires_at = datetime.now(UTC) + CHECKOUT_LIFETIME
        session = await self._call(
            "checkout",
            self._client.v1.checkout.sessions.create,
            checkout_session_params(request, expires_at),
            {"stripe_account": request.account_id, "idempotency_key": request.idempotency_key},
        )
        return CheckoutSession(
            session.id, session.url, datetime.fromtimestamp(session.expires_at, UTC)
        )

    async def expire_checkout_session(self, account_id: str, session_id: str) -> None:
        await self._call(
            "session expiry",
            self._client.v1.checkout.sessions.expire,
            session_id,
            None,
            {"stripe_account": account_id},
        )


@dataclass
class FakeStripeGateway:
    """In-memory Stripe for the tests and the local end-to-end run. Same idempotency rule as
    Stripe: a repeated idempotency key returns the first result. `fail` makes the next call of
    that name raise StripeUnavailable."""

    checkout_base: str = "https://checkout.stripe.com/c/pay/"
    accounts: dict[str, AccountState] = field(default_factory=dict)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # pmc id -> {"account": acct id, "active": bool}
    card_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)
    # The local end-to-end run: finishing Stripe's form is out of reach, so an onboarding link
    # counts as finished onboarding.
    auto_onboard: bool = False
    _by_key: dict[str, Any] = field(default_factory=dict)

    def _record(self, name: str, **data: Any) -> None:
        self.calls.append((name, data))
        if name in self.fail:
            self.fail.discard(name)
            raise StripeUnavailable(f"Stripe {name} failed")

    async def create_account(self, *, tenant_id: str, idempotency_key: str) -> str:
        self._record("create_account", tenant_id=tenant_id, idempotency_key=idempotency_key)
        if idempotency_key not in self._by_key:
            account_id = "acct_fake" + uuid.uuid4().hex[:16]
            self.accounts[account_id] = AccountState(account_id, False, False, False)
            self._by_key[idempotency_key] = account_id
        return self._by_key[idempotency_key]

    async def account_link(self, account_id: str, *, refresh_url: str, return_url: str) -> str:
        self._record("account_link", account_id=account_id, return_url=return_url)
        if self.auto_onboard:
            self.complete_onboarding(account_id)
        return f"https://connect.stripe.com/setup/fake/{account_id}"

    async def retrieve_account(self, account_id: str) -> AccountState:
        self._record("retrieve_account", account_id=account_id)
        return self.accounts.get(account_id) or AccountState(account_id, False, False, False)

    def complete_onboarding(self, account_id: str) -> None:
        """What the owner finishing Stripe's form does."""
        self.accounts[account_id] = AccountState(account_id, True, True, False)

    async def create_card_config(self, account_id: str, *, idempotency_key: str) -> str:
        self._record("create_card_config", account_id=account_id, idempotency_key=idempotency_key)
        if idempotency_key not in self._by_key:
            config_id = "pmc_fake" + uuid.uuid4().hex[:16]
            self.card_configs[config_id] = {"account": account_id, "active": True}
            self._by_key[idempotency_key] = config_id
        return self._by_key[idempotency_key]

    async def card_config_usable(self, account_id: str, config_id: str) -> bool:
        self._record("card_config_usable", account_id=account_id, config_id=config_id)
        return self._usable(account_id, config_id)

    def _usable(self, account_id: str, config_id: str) -> bool:
        config = self.card_configs.get(config_id)
        return bool(config and config["account"] == account_id and config["active"])

    def deactivate_card_config(self, config_id: str) -> None:
        """What the owner switching the configuration off in their Stripe Dashboard does."""
        self.card_configs[config_id]["active"] = False

    async def create_checkout_session(self, request: CheckoutRequest) -> CheckoutSession:
        self._record("create_checkout_session", request=request)
        # Stripe refuses a configuration that is unknown on the account or inactive.
        if not self._usable(request.account_id, request.card_config_id):
            raise StripeUnavailable("Stripe checkout failed")
        if request.idempotency_key not in self._by_key:
            session_id = "cs_test_fake" + uuid.uuid4().hex[:20]
            session = CheckoutSession(
                session_id,
                self.checkout_base + session_id,
                datetime.now(UTC) + CHECKOUT_LIFETIME,
            )
            self.sessions[session_id] = {"request": request, "status": "open"}
            self._by_key[request.idempotency_key] = session
        return self._by_key[request.idempotency_key]

    async def expire_checkout_session(self, account_id: str, session_id: str) -> None:
        self._record("expire_checkout_session", account_id=account_id, session_id=session_id)
        if session_id in self.sessions:
            self.sessions[session_id]["status"] = "expired"


_fake: FakeStripeGateway | None = None


def get_stripe_gateway(settings: Settings) -> StripeGateway | None:
    """The gateway, or None when Stripe isn't configured (card payments are then off)."""
    global _fake
    if settings.stripe_gateway.strip().lower() == "fake":
        if _fake is None:
            _fake = FakeStripeGateway(auto_onboard=True)
        return _fake
    key = settings.stripe_secret_key.strip()
    if not key:
        return None
    return LiveStripeGateway(key)


def verify_webhook(payload: bytes, signature: str | None, secret: str) -> None:
    """Stripe's signature scheme (HMAC-SHA256 of "<timestamp>.<payload>", 5-minute tolerance).
    Raises stripe.SignatureVerificationError."""
    stripe.WebhookSignature.verify_header(
        payload, signature, secret, tolerance=SIGNATURE_TOLERANCE_SECONDS
    )


def gateway_dependency() -> StripeGateway | None:
    """FastAPI dependency: the configured gateway (tests override it with a fake)."""
    from app.core.config import get_settings

    return get_stripe_gateway(get_settings())
