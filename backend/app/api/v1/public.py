"""Public storefront endpoints: no sign-in (TRD §1.3, §6.11).

The tenant is the one whose storefront domain sent the request's Origin, resolved by the
StorefrontCORSMiddleware (app/core/cors.py) together with the CORS check and the per-address
limit. A request without a known Origin gets 404 "Store not found", as does an unknown,
inactive or switched-off store.

Reads run in storefront_session: a read-only transaction as `storefront_reader`, which can
select only from the storefront views. Those list public columns only and filter on the
resolved tenant, so these handlers can't reach anything else even by mistake.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import get_pool
from app.core.config import get_settings
from app.core.cors import TENANT_STATE_KEY
from app.core.errors import NotFoundError
from app.db.session import storefront_session
from app.models.public import (
    HeroButton,
    PageLink,
    PageSlug,
    PublicImageOut,
    PublicPageOut,
    PublicProductOut,
    PublicStoreOut,
)

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


def storefront_tenant(request: Request) -> str:
    tenant_id = getattr(request.state, TENANT_STATE_KEY, None)
    if tenant_id is None:
        raise NotFoundError("Store not found")
    return tenant_id


Tenant = Annotated[str, Depends(storefront_tenant)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


def _store_out(row: dict[str, Any]) -> PublicStoreOut:
    settings = get_settings()
    logo = row.pop("logo_path")
    label, url = row.pop("hero_cta_label"), row.pop("hero_cta_url")
    pages = [
        PageLink(slug=slug, title=title)
        for slug, (column, title) in PAGES.items()
        if (row.pop(column) or "").strip()
    ]
    return PublicStoreOut.model_validate(
        {
            **row,
            "logo_url": f"{settings.brand_assets_public_base}/{logo}" if logo else None,
            "hero_button": HeroButton(label=label, url=url) if label and url else None,
            "pages": pages,
            "checkout_enabled": False,
        }
    )


@router.get("/store", response_model=PublicStoreOut)
async def get_store(tenant_id: Tenant, pool: Pool, response: Response) -> PublicStoreOut:
    async with (
        storefront_session(pool, tenant_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute("select * from storefront.store")
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Store not found")
    response.headers["Cache-Control"] = CACHE_CONTROL
    return _store_out(row)


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
           ), '[]'::json) as images
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
