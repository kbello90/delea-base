"""Product images end to end against the local stack, Storage included: signed upload,
register (validation, variants, raw upload removed), primary, order, delete, limits, roles,
tenant isolation, cleanup on failure, and that clients can't write to the buckets directly.
"""

import io
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.v1 import product_images as routes
from app.core.config import Settings, get_settings

from .support import TENANT_A, TENANT_B, TestUser, assert_access, product_payload

pytestmark = pytest.mark.integration

PRODUCTS = "/api/v1/products"
SEED_A_PRODUCT = "a1000000-0000-0000-0000-000000000001"


def jpeg(
    size: tuple[int, int] = (1600, 1000), colour: tuple[int, int, int] = (150, 60, 70)
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="JPEG")
    return out.getvalue()


class Storage:
    """Direct Storage access for assertions and cleanup (service key, like the backend)."""

    def __init__(self, settings: Settings) -> None:
        key = settings.supabase_service_role_key
        self.base = f"{settings.supabase_url}/storage/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def names(self, bucket: str, prefix: str) -> list[str]:
        r = httpx.post(
            f"{self.base}/object/list/{bucket}",
            headers=self.headers,
            json={"prefix": prefix, "limit": 1000},
        )
        r.raise_for_status()
        return sorted(f"{prefix}{item['name']}" for item in r.json() if item.get("id"))

    def remove(self, bucket: str, paths: list[str]) -> None:
        if paths:
            httpx.request(
                "DELETE",
                f"{self.base}/object/{bucket}",
                headers=self.headers,
                json={"prefixes": paths},
            )


@pytest.fixture
def storage(settings: Settings) -> Iterator[Storage]:
    s = Storage(settings)
    created: list[str] = []
    s.track = created.append  # type: ignore[attr-defined]
    yield s
    # Remove every object the test's products left in either bucket.
    for prefix in created:
        for bucket in ("product-uploads", "product-images"):
            s.remove(bucket, s.names(bucket, prefix))


def new_product(client: TestClient, owner: TestUser, storage: Storage, **overrides) -> str:
    r = client.post(PRODUCTS, json=product_payload(**overrides), headers=owner.headers)
    assert r.status_code == 201, r.text
    product_id = r.json()["id"]
    storage.track(f"{owner.tenant_id}/{product_id}/")  # type: ignore[attr-defined]
    return product_id


def sign(client: TestClient, user: TestUser, product_id: str, content_type="image/jpeg", **extra):
    return client.post(
        f"{PRODUCTS}/{product_id}/images/uploads",
        json={"content_type": content_type, **extra},
        headers=user.headers,
    )


def put(upload_url: str, data: bytes, content_type: str = "image/jpeg") -> httpx.Response:
    # What the browser does: no app credentials, just the signed URL.
    return httpx.put(upload_url, content=data, headers={"Content-Type": content_type})


def register(client, user, product_id, upload_id, content_type="image/jpeg"):
    return client.post(
        f"{PRODUCTS}/{product_id}/images",
        json={"upload_id": upload_id, "content_type": content_type},
        headers=user.headers,
    )


def add_image(client, owner, product_id, data=None) -> dict:
    ticket = sign(client, owner, product_id)
    assert ticket.status_code == 201, ticket.text
    assert put(ticket.json()["upload_url"], data or jpeg()).status_code == 200
    r = register(client, owner, product_id, ticket.json()["upload_id"])
    assert r.status_code == 201, r.text
    return r.json()


def images_of(client, user, product_id) -> list[dict]:
    r = client.get(f"{PRODUCTS}/{product_id}", headers=user.headers)
    assert r.status_code == 200, r.text
    return r.json()["images"]


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------
def test_upload_register_and_serve(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)

    ticket = sign(client, owner, product_id)
    assert ticket.status_code == 201, ticket.text
    body = ticket.json()
    # The upload path is built from the token's tenant and the URL's product, nothing else.
    assert f"/product-uploads/{TENANT_A}/{product_id}/{body['upload_id']}.jpg" in body["upload_url"]
    assert body["max_bytes"] == 5 * 1024 * 1024
    assert put(body["upload_url"], jpeg()).status_code == 200
    raw = f"{TENANT_A}/{product_id}/{body['upload_id']}.jpg"
    assert storage.names("product-uploads", f"{TENANT_A}/{product_id}/") == [raw]

    image = register(client, owner, product_id, body["upload_id"])
    assert image.status_code == 201, image.text
    image = image.json()
    assert (image["position"], image["is_primary"]) == (0, True)
    assert (image["width"], image["height"]) == (1200, 750)
    base = get_settings().product_images_public_base
    assert image["url"] == f"{base}/{TENANT_A}/{product_id}/{image['id']}-1200.webp"
    assert image["thumb_url"] == f"{base}/{TENANT_A}/{product_id}/{image['id']}-400.webp"

    # The variants are public WebP files; the raw upload is gone.
    for url in (image["url"], image["thumb_url"]):
        served = httpx.get(url)
        assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
    assert storage.names("product-uploads", f"{TENANT_A}/{product_id}/") == []
    assert images_of(client, owner, product_id) == [image]
    # Staff see the images too (read-only).
    assert images_of(client, users["staff_a"], product_id) == [image]


def test_primary_order_and_delete(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    first, second, third = (add_image(client, owner, product_id) for _ in range(3))
    assert [(i["position"], i["is_primary"]) for i in (first, second, third)] == [
        (0, True),
        (1, False),
        (2, False),
    ]

    r = client.post(f"{PRODUCTS}/{product_id}/images/{third['id']}/primary", headers=owner.headers)
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["images"] if i["is_primary"]] == [third["id"]]

    order = [third["id"], first["id"], second["id"]]
    r = client.put(
        f"{PRODUCTS}/{product_id}/images/order", json={"image_ids": order}, headers=owner.headers
    )
    assert r.status_code == 200, r.text
    assert [(i["id"], i["position"]) for i in r.json()["images"]] == [
        (order[0], 0),
        (order[1], 1),
        (order[2], 2),
    ]

    # Deleting the primary promotes the next image and closes the gap; its files go.
    r = client.delete(f"{PRODUCTS}/{product_id}/images/{third['id']}", headers=owner.headers)
    assert r.status_code == 200, r.text
    assert [(i["id"], i["position"], i["is_primary"]) for i in r.json()["images"]] == [
        (first["id"], 0, True),
        (second["id"], 1, False),
    ]
    assert httpx.get(third["url"]).status_code in (400, 404)
    assert httpx.get(first["url"]).status_code == 200


def test_reorder_must_list_every_image_exactly_once(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    a, b = add_image(client, owner, product_id), add_image(client, owner, product_id)
    for ids in ([a["id"]], [a["id"], a["id"]], [a["id"], b["id"], SEED_A_PRODUCT]):
        r = client.put(
            f"{PRODUCTS}/{product_id}/images/order", json={"image_ids": ids}, headers=owner.headers
        )
        assert r.status_code == 422, (ids, r.text)


def test_deleting_a_product_removes_its_files(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    image = add_image(client, owner, product_id)
    assert client.delete(f"{PRODUCTS}/{product_id}", headers=owner.headers).status_code == 204
    assert storage.names("product-images", f"{TENANT_A}/{product_id}/") == []
    assert httpx.get(image["url"]).status_code in (400, 404)


# ---------------------------------------------------------------------------
# What gets refused
# ---------------------------------------------------------------------------
def test_tenant_or_path_in_the_request_is_refused(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    for extra in ({"tenant_id": TENANT_B}, {"path": f"{TENANT_B}/x/y.jpg"}):
        assert sign(client, owner, product_id, **extra).status_code == 422
    r = client.post(
        f"{PRODUCTS}/{product_id}/images",
        json={
            "upload_id": "00000000-0000-0000-0000-000000000001",
            "content_type": "image/jpeg",
            "tenant_id": TENANT_B,
        },
        headers=owner.headers,
    )
    assert r.status_code == 422


def test_a_non_image_with_an_image_content_type_is_rejected(client, users, storage, db) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    ticket = sign(client, owner, product_id, content_type="image/png").json()
    html = b"<html><script>alert(1)</script></html>"
    assert (
        put(ticket["upload_url"], html, "image/png").status_code == 200
    )  # Storage trusts the header

    r = register(client, owner, product_id, ticket["upload_id"], "image/png")
    assert r.status_code == 422
    assert r.json()["detail"] == "The file is not a valid JPEG, PNG or WebP image."
    assert storage.names("product-uploads", f"{TENANT_A}/{product_id}/") == []
    assert storage.names("product-images", f"{TENANT_A}/{product_id}/") == []
    assert images_of(client, owner, product_id) == []


def test_register_needs_an_upload_for_that_product(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    other_id = new_product(client, owner, storage)
    assert (
        register(client, owner, product_id, "00000000-0000-0000-0000-000000000001").status_code
        == 404
    )
    # An upload signed for another product can't be registered here.
    ticket = sign(client, owner, other_id).json()
    assert put(ticket["upload_url"], jpeg()).status_code == 200
    assert register(client, owner, product_id, ticket["upload_id"]).status_code == 404


def _fill(db, product_id: str, count: int) -> None:
    for position in range(count):
        db.execute(
            """
            insert into public.product_images
              (id, tenant_id, product_id, position, large_path, thumb_path, width, height)
            select x, %(t)s::uuid, %(p)s::uuid, %(pos)s,
                   %(t)s::text || '/' || %(p)s::text || '/' || x || '-1200.webp',
                   %(t)s::text || '/' || %(p)s::text || '/' || x || '-400.webp', 10, 10
              from gen_random_uuid() x
            """,
            {"t": TENANT_A, "p": product_id, "pos": position},
        )


def test_ten_images_at_most(client, users, storage, db) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    ticket = sign(client, owner, product_id).json()  # signed while there was room
    _fill(db, product_id, 10)

    assert sign(client, owner, product_id).status_code == 409
    assert put(ticket["upload_url"], jpeg()).status_code == 200
    r = register(client, owner, product_id, ticket["upload_id"])
    assert r.status_code == 409
    assert r.json()["detail"] == "A product can have at most 10 images."
    assert storage.names("product-uploads", f"{TENANT_A}/{product_id}/") == []
    assert storage.names("product-images", f"{TENANT_A}/{product_id}/") == []


def test_variants_are_deleted_when_the_insert_fails(client, users, storage, monkeypatch) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    ticket = sign(client, owner, product_id).json()
    assert put(ticket["upload_url"], jpeg()).status_code == 200

    async def failing_insert(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(routes, "_insert_image", failing_insert)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        register(client, owner, product_id, ticket["upload_id"])
    assert storage.names("product-images", f"{TENANT_A}/{product_id}/") == []
    assert storage.names("product-uploads", f"{TENANT_A}/{product_id}/") == []


def test_role_matrix(client, users, storage) -> None:
    owner = users["owner_a"]
    product_id = new_product(client, owner, storage)
    image = add_image(client, owner, product_id)
    base = f"{PRODUCTS}/{product_id}/images"

    # Owner of A succeeds; owner of B can't see A's product (404); everyone else 403.
    assert_access(
        client,
        users,
        "POST",
        f"{base}/uploads",
        {"owner_a": 201, "owner_b": 404},
        json={"content_type": "image/jpeg"},
    )
    assert_access(
        client,
        users,
        "POST",
        base,
        {"owner_a": 404, "owner_b": 404},
        json={"upload_id": "00000000-0000-0000-0000-000000000001", "content_type": "image/jpeg"},
    )
    assert_access(
        client, users, "POST", f"{base}/{image['id']}/primary", {"owner_a": 200, "owner_b": 404}
    )
    assert_access(
        client,
        users,
        "PUT",
        f"{base}/order",
        {"owner_a": 200, "owner_b": 404},
        json={"image_ids": [image["id"]]},
    )
    # Delete last (owner_a's call removes it, so the others see the same image first).
    for name in ("staff_a", "cpa_a", "no_membership"):
        assert (
            client.delete(f"{base}/{image['id']}", headers=users[name].headers).status_code == 403
        )
    assert (
        client.delete(f"{base}/{image['id']}", headers=users["owner_b"].headers).status_code == 404
    )
    assert client.delete(f"{base}/{image['id']}", headers=owner.headers).status_code == 200


@pytest.mark.parametrize("bucket", ["product-uploads", "product-images"])
def test_clients_cannot_write_to_the_buckets_directly(users, settings, bucket) -> None:
    """With the owner's own JWT, straight to Storage: refused by the restrictive policies."""
    owner = users["owner_a"]
    r = httpx.post(
        f"{settings.supabase_url}/storage/v1/object/{bucket}/{TENANT_A}/direct/{bucket}.webp",
        headers={
            **owner.headers,
            "apikey": settings.supabase_anon_key,
            "Content-Type": "image/webp",
        },
        content=b"RIFF....WEBP",
    )
    assert r.status_code in (400, 403), r.text
    assert "row-level security" in r.text


# ---------------------------------------------------------------------------
# Product list filters and fields
# ---------------------------------------------------------------------------
def test_low_stock_and_metal_filters(client, users) -> None:
    owner = users["owner_a"]
    low = client.get(PRODUCTS, params={"low_stock": True}, headers=owner.headers).json()
    assert low and all(p["stock_qty"] < p["low_stock_threshold"] for p in low)
    assert "Engagement Ring" in {p["name"] for p in low}  # seed: 2 < 3
    not_low = client.get(PRODUCTS, params={"low_stock": False}, headers=owner.headers).json()
    assert all(p["stock_qty"] >= p["low_stock_threshold"] for p in not_low)
    assert len(low) + len(not_low) == len(client.get(PRODUCTS, headers=owner.headers).json())

    gold = client.get(PRODUCTS, params={"metal": "gold_14k"}, headers=owner.headers).json()
    assert gold and {p["metal"] for p in gold} == {"gold_14k"}
    assert client.get(PRODUCTS, params={"metal": "tin"}, headers=owner.headers).status_code == 422


def test_image_urls_is_no_longer_a_product_field(client, users) -> None:
    owner = users["owner_a"]
    r = client.post(PRODUCTS, json=product_payload(image_urls=["https://x"]), headers=owner.headers)
    assert r.status_code == 422
    assert (
        "image_urls" not in client.get(f"{PRODUCTS}/{SEED_A_PRODUCT}", headers=owner.headers).json()
    )
