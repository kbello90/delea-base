"""Public storefront endpoints: no sign-in (TRD §1.3, §6.11).

The tenant is the one whose storefront domain sent the request's Origin, resolved by the
StorefrontCORSMiddleware (app/core/cors.py) together with the CORS check and the per-address
limit. A request without a known Origin gets 404 "Store not found", as does an unknown,
inactive or switched-off store.

Reads run in storefront_session: a read-only transaction as `storefront_reader`, which can
select only from the storefront views (and run the cart pricing function). Those list public
columns only and filter on the resolved tenant, so these handlers can't reach anything else
even by mistake.

The checkout (POST /public/orders) runs as `storefront_writer`, which can only execute the
storefront checkout functions; the database prices the cart, checks the shop, stock,
personalization and limits, and writes the order (supabase/migrations/20261027000400).
"""

import hashlib
import json
import math
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.api.deps import get_pool
from app.core import rate_limit, turnstile
from app.core.config import get_settings
from app.core.cors import TENANT_STATE_KEY
from app.core.errors import NotFoundError
from app.core.storefront_hosts import origin_host
from app.db.session import checkout_session, storefront_session
from app.models.public import (
    MIN_FILL_MILLISECONDS,
    HeroButton,
    OrderOut,
    OrderRequest,
    PageLink,
    PageSlug,
    PayNowOut,
    PickupOption,
    PublicImageOut,
    PublicPageOut,
    PublicProductOut,
    PublicStoreOut,
    QuoteOut,
    QuoteRequest,
    ShippingOption,
)
from app.services.card_checkout import start_card_payment
from app.services.stripe_service import StripeGateway, gateway_dependency

router = APIRouter(prefix="/public", tags=["public"])

CACHE_CONTROL = "public, max-age=60"
MAX_PRODUCTS = 200

# Footer order. Each page shows only when it has text.
PAGES: dict[PageSlug, tuple[str, str]] = {
    PageSlug.custom_orders: ("custom_orders_info", "How custom orders work"),
    PageSlug.returns: ("return_policy", "Returns"),
    PageSlug.privacy: ("privacy_policy", "Privacy policy"),
    PageSlug.terms: ("terms", "Terms"),
}

STORE_NOT_FOUND = "Store not found"
ORDERING_UNAVAILABLE = "Online ordering is unavailable right now. Please try again later."
TOO_MANY = "Too many requests. Please wait a few minutes and try again."
TRY_AGAIN = "Your order didn't go through. Please check your details and place it again."


def storefront_tenant(request: Request) -> str:
    tenant_id = getattr(request.state, TENANT_STATE_KEY, None)
    if tenant_id is None:
        raise NotFoundError(STORE_NOT_FOUND)
    return tenant_id


Tenant = Annotated[str, Depends(storefront_tenant)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]
Gateway = Annotated[StripeGateway | None, Depends(gateway_dependency)]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limit(limiter: rate_limit.SlidingWindowLimiter, request: Request) -> None:
    retry_after = limiter.hit(_client(request))
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY,
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


# ---------------------------------------------------------------------------
# Store, pages, products
# ---------------------------------------------------------------------------
def _store_out(row: dict[str, Any], *, stripe_ready: bool) -> PublicStoreOut:
    settings = get_settings()
    card = bool(row.pop("card_enabled")) and stripe_ready
    logo = row.pop("logo_path")
    label, url = row.pop("hero_cta_label"), row.pop("hero_cta_url")
    pages = [
        PageLink(slug=slug, title=title)
        for slug, (column, title) in PAGES.items()
        if (row.pop(column) or "").strip()
    ]
    ships, picks = row["ship_enabled"], row["pickup_enabled"]
    payment_choices = []
    if row["bank_transfer_enabled"]:
        payment_choices.append("bank_transfer")
    if row["pay_on_pickup_enabled"] and picks:
        payment_choices.append("pay_on_pickup")
    if card:
        payment_choices.append("card")
    can_receive = ships or picks
    # Paying on pickup needs pickup; bank transfer and card work with either.
    can_pay = bool(payment_choices)
    return PublicStoreOut.model_validate(
        {
            **row,
            "logo_url": f"{settings.brand_assets_public_base}/{logo}" if logo else None,
            "hero_button": HeroButton(label=label, url=url) if label and url else None,
            "pages": pages,
            "checkout_enabled": bool(
                can_receive and can_pay and turnstile.checkout_protected(settings)
            ),
            "shipping": ShippingOption(
                flat_cents=row["ship_flat_cents"], free_over_cents=row["free_ship_over_cents"]
            )
            if ships
            else None,
            "pickup": PickupOption(note=row["pickup_note"]) if picks else None,
            "payment_choices": payment_choices,
            "tax_rate_bps": row["default_tax_rate_bps"],
        }
    )


@router.get("/store", response_model=PublicStoreOut)
async def get_store(
    tenant_id: Tenant, pool: Pool, response: Response, gateway: Gateway
) -> PublicStoreOut:
    async with (
        storefront_session(pool, tenant_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute("select * from storefront.store")
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError(STORE_NOT_FOUND)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return _store_out(row, stripe_ready=gateway is not None)


@router.get("/pages/{slug}", response_model=PublicPageOut)
async def get_page(
    slug: PageSlug, tenant_id: Tenant, pool: Pool, response: Response
) -> PublicPageOut:
    column, title = PAGES[slug]
    async with storefront_session(pool, tenant_id) as conn:
        # `column` comes from the fixed PAGES table above, never from the request.
        cur = await conn.execute(f"select {column} from storefront.store")
        row = await cur.fetchone()
    body = (row[0] if row else None) or ""
    if not body.strip():
        raise NotFoundError("Page not found")
    response.headers["Cache-Control"] = CACHE_CONTROL
    return PublicPageOut(slug=slug, title=title, body=body)


_PRODUCT_SELECT = """
    select p.id, p.name, p.description, p.metal, p.price_cents, p.sale_price_cents,
           p.sale_ends_at, p.availability, p.lead_time_note,
           coalesce((
             select json_agg(json_build_object(
                      'large_path', i.large_path, 'thumb_path', i.thumb_path,
                      'width', i.width, 'height', i.height, 'is_primary', i.is_primary)
                    order by i.position)
               from storefront.product_images i
              where i.product_id = p.id
           ), '[]'::json) as images,
           coalesce((
             select json_agg(json_build_object(
                      'id', f.id, 'label', f.label, 'kind', f.kind, 'required', f.required,
                      'max_length', f.max_length, 'choices', f.choices)
                    order by f.position)
               from storefront.product_personalization f
              where f.product_id = p.id
           ), '[]'::json) as personalization
      from storefront.products p
"""


def _product_out(row: dict[str, Any]) -> PublicProductOut:
    base = get_settings().product_images_public_base
    images = [
        PublicImageOut(
            url=f"{base}/{image['large_path']}",
            thumb_url=f"{base}/{image['thumb_path']}",
            width=image["width"],
            height=image["height"],
            is_primary=image["is_primary"],
        )
        for image in row.pop("images")
    ]
    # The view shows a sale price only while the sale is active.
    unit = row["sale_price_cents"] if row["sale_price_cents"] is not None else row["price_cents"]
    return PublicProductOut.model_validate({**row, "unit_price_cents": unit, "images": images})


async def _products(conn: AsyncConnection, where: str = "", params: tuple = ()) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"{_PRODUCT_SELECT} {where} order by p.sort_order, p.name, p.id limit {MAX_PRODUCTS}",
            params,
        )
        return await cur.fetchall()


@router.get("/products", response_model=list[PublicProductOut])
async def list_products(
    tenant_id: Tenant, pool: Pool, response: Response
) -> list[PublicProductOut]:
    """The published catalog in storefront order (at most 200)."""
    async with storefront_session(pool, tenant_id) as conn:
        rows = await _products(conn)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return [_product_out(row) for row in rows]


@router.get("/products/{product_id}", response_model=PublicProductOut)
async def get_product(
    product_id: UUID, tenant_id: Tenant, pool: Pool, response: Response
) -> PublicProductOut:
    """One published product. Unpublished, missing and other tenants' products are the same
    404, so the answer never tells them apart."""
    async with storefront_session(pool, tenant_id) as conn:
        rows = await _products(conn, "where p.id = %s", (product_id,))
    if not rows:
        raise NotFoundError("Product not found")
    response.headers["Cache-Control"] = CACHE_CONTROL
    return _product_out(rows[0])


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def _priced_error(error: str) -> Exception:
    if error == "closed":
        return NotFoundError(STORE_NOT_FOUND)
    if error == "fulfillment":
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "This shop doesn't offer that delivery option."
        )
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The cart couldn't be read.")


@router.post("/checkout/quote", response_model=QuoteOut)
async def quote(body: QuoteRequest, request: Request, tenant_id: Tenant, pool: Pool) -> QuoteOut:
    """Price a cart exactly as an order would be priced, without writing anything. Lines
    that can't be ordered (unavailable, or personalization to fix) say so."""
    _limit(rate_limit.public_quotes, request)
    cart = body.model_dump(mode="json")["items"]
    async with storefront_session(pool, tenant_id) as conn:
        cur = await conn.execute(
            "select public.storefront_price_cart(%s, %s, %s)",
            (tenant_id, Jsonb(cart), body.fulfillment),
        )
        (priced,) = await cur.fetchone()
    if not priced["ok"]:
        raise _priced_error(priced["error"])
    return QuoteOut.model_validate(priced)


_STATUS = {
    "quote": "awaiting_payment",
    "paid": "paid",
    "in_production": "in_production",
    "shipped": "shipped",
    "delivered": "delivered",
}


async def _tracked_order(pool: AsyncConnectionPool, tenant_id: str, token: UUID) -> OrderOut | None:
    async with (
        storefront_session(pool, tenant_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute("select set_config('app.order_token', %s, true)", (str(token),))
        await cur.execute("select * from storefront.orders")
        order = await cur.fetchone()
        if order is None:
            return None
        await cur.execute(
            "select description, qty, unit_price_cents, line_total_cents, personalization, "
            "lead_time_note from storefront.order_lines order by position"
        )
        lines = await cur.fetchall()
    if order["cancelled"]:
        shopper_status = "cancelled"
    elif order["refunded"]:
        shopper_status = "refunded"
    elif order["order_status"] == "quote" and order["amount_paid_cents"] > 0:
        # A deposit: work may already be under way; paid in full but still a quote means the
        # shop is waiting on stock, which the shopper sees as paid.
        if order["balance_cents"] <= 0:
            shopper_status = "paid"
        elif order["production_started"]:
            shopper_status = "in_production"
        else:
            shopper_status = "deposit_paid"
    else:
        shopper_status = _STATUS[order["order_status"]]
    return OrderOut.model_validate(
        {
            **order,
            "public_token": token,
            "placed_at": order["created_at"],
            "status": shopper_status,
            "lines": lines,
        }
    )


_INVALID = {
    "fulfillment": "This shop doesn't offer that delivery option.",
    "payment_choice": "This shop doesn't take that way to pay for this order.",
    "address": "Check the delivery address.",
    "invalid": "The order couldn't be read.",
}


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": OrderOut, "description": "The same request again: the same order"}},
)
async def place_order(
    body: OrderRequest,
    request: Request,
    response: Response,
    tenant_id: Tenant,
    pool: Pool,
    gateway: Gateway,
) -> OrderOut | JSONResponse:
    """Place an order. Bank transfer and pay on pickup: unpaid, the owner confirms the payment
    in the admin. Card: the response carries `checkout_url`, Stripe's hosted Checkout for the
    total; the webhook records the payment (Slice B2).

    Order of checks: the bot traps; a repeat of an earlier checkout returns that order (before
    Turnstile, so a double click never needs a second token); the per-address attempt limit;
    Turnstile; then the database prices, checks and writes the order in one transaction.
    """
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    if body.website or body.elapsed_ms < MIN_FILL_MILLISECONDS:
        # One answer for both traps, so a bot can't tell which caught it. Nothing is stored, so
        # a person who was just quick places the order again with the same checkout key.
        return JSONResponse(
            {"detail": TRY_AGAIN, "reason": "try_again"},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers={"Cache-Control": "no-store"},
        )
    if not turnstile.checkout_protected(settings):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ORDERING_UNAVAILABLE)
    if body.payment_choice == "card" and gateway is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _INVALID["payment_choice"])

    document = body.order_document()
    fingerprint = _hash(json.dumps(document, sort_keys=True, separators=(",", ":")))
    ip = _client(request)
    allowed = True

    async with checkout_session(pool) as conn:
        cur = await conn.execute(
            "select public.storefront_find_order(%s, %s, %s)",
            (tenant_id, body.checkout_key, fingerprint),
        )
        (previous,) = await cur.fetchone()
        if previous["status"] == "none":
            cur = await conn.execute("select public.storefront_count_attempt(%s)", (_hash(ip),))
            (allowed,) = await cur.fetchone()
    if previous["status"] == "conflict":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This checkout was already used for a different order."
        )
    if previous["status"] == "replayed":
        return await _placed(
            request, pool, gateway, tenant_id, previous["public_token"], response, 200
        )
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)

    host = origin_host(request.headers.get("origin"), dev=settings.is_dev_environment) or ""
    try:
        passed = await turnstile.verify(
            body.turnstile_token,
            remote_ip=ip,
            hostname=host.split(":", 1)[0],
            settings=settings,
        )
    except turnstile.TurnstileUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ORDERING_UNAVAILABLE) from None
    if not passed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The security check didn't pass. Please try again.",
        )

    async with checkout_session(pool) as conn:
        cur = await conn.execute(
            "select public.storefront_place_order(%s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                body.checkout_key,
                fingerprint,
                Jsonb(document),
                _hash(ip),
                _hash(body.contact.email.lower()),
            ),
        )
        (result,) = await cur.fetchone()

    outcome = result["status"]
    if outcome in ("created", "replayed"):
        code = 201 if outcome == "created" else 200
        return await _placed(
            request, pool, gateway, tenant_id, result["public_token"], response, code
        )
    if outcome == "conflict":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This checkout was already used for a different order."
        )
    if outcome == "closed":
        raise NotFoundError(STORE_NOT_FOUND)
    if outcome == "rate_limited":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    if outcome in ("unavailable", "price_changed"):
        detail = (
            "Some items are no longer available or need your choices."
            if outcome == "unavailable"
            else "Prices changed since you opened checkout. Please review your order."
        )
        quote_out = QuoteOut.model_validate(result["quote"]).model_dump(mode="json")
        return JSONResponse(
            {"detail": detail, "reason": outcome, "quote": quote_out},
            status_code=status.HTTP_409_CONFLICT,
            headers={"Cache-Control": "no-store"},
        )
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        _INVALID.get(result.get("reason"), _INVALID["invalid"]),
    )


async def _order_response(
    pool: AsyncConnectionPool, tenant_id: str, token: str, response: Response, code: int
) -> OrderOut:
    order = await _tracked_order(pool, tenant_id, UUID(str(token)))
    if order is None:  # the order was just written in this shop; anything else is a bug
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error")
    response.status_code = code
    return order


async def _placed(
    request: Request,
    pool: AsyncConnectionPool,
    gateway: StripeGateway | None,
    tenant_id: str,
    token: str,
    response: Response,
    code: int,
) -> OrderOut:
    """The placed order; for a card order still owing, with its Checkout link (a double
    submit gets the same open session back)."""
    order = await _order_response(pool, tenant_id, token, response, code)
    if (
        order.payment_choice == "card"
        and order.balance_cents > 0
        and order.status
        not in (
            "cancelled",
            "refunded",
        )
    ):
        started = await start_card_payment(
            pool, gateway, tenant_id, order.public_token, request.headers.get("origin", "")
        )
        order.checkout_url = started.checkout_url
    return order


_PAY_REFUSED = {
    "not_found": (status.HTTP_404_NOT_FOUND, "Order not found"),
    "paid": (status.HTTP_409_CONFLICT, "This order is already paid."),
    "closed": (status.HTTP_409_CONFLICT, "This order can't be paid any more."),
    "card_off": (status.HTTP_409_CONFLICT, "Card payments aren't available for this order."),
    "too_small": (
        status.HTTP_409_CONFLICT,
        "The rest is below the smallest card payment. Please pay it with the shop.",
    ),
    "stripe": (
        status.HTTP_502_BAD_GATEWAY,
        "The card payment couldn't start. Please try again in a minute.",
    ),
}


@router.post("/orders/{public_token}/pay", response_model=PayNowOut)
async def pay_now(
    public_token: UUID,
    request: Request,
    response: Response,
    tenant_id: Tenant,
    pool: Pool,
    gateway: Gateway,
) -> PayNowOut:
    """Pay the order's balance by card: Stripe's hosted Checkout (an open session for the same
    balance is reused). 10 per address per hour."""
    _limit(rate_limit.public_pay, request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    started = await start_card_payment(
        pool, gateway, tenant_id, public_token, request.headers.get("origin", "")
    )
    if started.checkout_url is None:
        code, detail = _PAY_REFUSED[started.refused or "stripe"]
        raise HTTPException(code, detail)
    return PayNowOut(checkout_url=started.checkout_url)


@router.get("/orders/{public_token}", response_model=OrderOut)
async def track_order(
    public_token: UUID, request: Request, response: Response, tenant_id: Tenant, pool: Pool
) -> OrderOut:
    """An order by its tracking link, in its own shop only. An unknown token, or a token of
    another shop, is the same 404."""
    _limit(rate_limit.public_tracking, request)
    order = await _tracked_order(pool, tenant_id, public_token)
    if order is None:
        raise NotFoundError("Order not found")
    response.headers["Cache-Control"] = "no-store"
    # The token is in the URL: never send it on as a referrer.
    response.headers["Referrer-Policy"] = "no-referrer"
    return order
