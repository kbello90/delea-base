"""The brand logo end to end against the local stack, Storage included: signed upload,
register (real-content check, metadata stripped, one WebP of at most 512 px in the public
brand-assets bucket, raw upload removed), replace, remove, roles and tenant isolation."""

import io
import re
from collections.abc import Iterator

import httpx
import pytest
from PIL import ExifTags, Image

from app.core.config import Settings, get_settings

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

LOGO = "/api/v1/settings/brand/logo"
BRAND = "/api/v1/settings/brand"
UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def png_with_gps(size=(1200, 600)) -> bytes:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] = "N"
    out = io.BytesIO()
    Image.new("RGBA", size, (107, 39, 55, 200)).save(out, format="PNG", exif=exif)
    return out.getvalue()


class Storage:
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


def logos(storage: Storage, tenant: str) -> list[str]:
    return [n for n in storage.names("brand-assets", f"{tenant}/") if "/logo-" in n]


@pytest.fixture
def storage(settings: Settings, db) -> Iterator[Storage]:
    """Put each tenant's logo back as it was and remove what the test uploaded."""
    s = Storage(settings)
    saved = dict(
        db.execute(
            "select tenant_id::text, logo_path from public.brand_config where tenant_id = any(%s)",
            ([TENANT_A, TENANT_B],),
        ).fetchall()
    )
    before = {t: logos(s, t) for t in (TENANT_A, TENANT_B)}
    yield s
    for tenant, path in saved.items():
        db.execute(
            "update public.brand_config set logo_path = %s where tenant_id = %s", (path, tenant)
        )
        s.remove("brand-assets", [p for p in logos(s, tenant) if p not in before[tenant]])
        s.remove("product-uploads", s.names("product-uploads", f"{tenant}/brand/"))


def sign(client, user: TestUser, content_type="image/png", **extra):
    return client.post(
        f"{LOGO}/uploads", json={"content_type": content_type, **extra}, headers=user.headers
    )


def put(url: str, data: bytes, content_type: str) -> httpx.Response:
    return httpx.put(url, content=data, headers={"Content-Type": content_type})


def register(client, user, upload_id, content_type="image/png", **extra):
    return client.post(
        LOGO,
        json={"upload_id": upload_id, "content_type": content_type, **extra},
        headers=user.headers,
    )


def upload_logo(client, owner, data: bytes, content_type="image/png") -> dict:
    ticket = sign(client, owner, content_type)
    assert ticket.status_code == 201, ticket.text
    assert put(ticket.json()["upload_url"], data, content_type).status_code == 200
    r = register(client, owner, ticket.json()["upload_id"], content_type)
    assert r.status_code == 200, r.text
    return r.json()


def stored_logo(db, tenant: str) -> str | None:
    return db.execute(
        "select logo_path from public.brand_config where tenant_id = %s", (tenant,)
    ).fetchone()[0]


def test_a_logo_is_processed_stored_publicly_and_shown_to_owner_and_staff(
    client, users, storage, db
) -> None:
    owner = users["owner_a"]
    ticket = sign(client, owner).json()
    assert ticket["max_bytes"] == 2 * 1024 * 1024
    assert put(ticket["upload_url"], png_with_gps(), "image/png").status_code == 200
    raw = f"{TENANT_A}/brand/{ticket['upload_id']}.png"
    assert storage.names("product-uploads", f"{TENANT_A}/brand/") == [raw]

    brand = register(client, owner, ticket["upload_id"]).json()
    path = stored_logo(db, TENANT_A)
    assert re.fullmatch(f"{TENANT_A}/logo-{UUID}\\.webp", path)
    assert brand["logo_url"] == f"{get_settings().brand_assets_public_base}/{path}"
    assert storage.names("product-uploads", f"{TENANT_A}/brand/") == []  # raw upload removed

    served = httpx.get(brand["logo_url"])  # public, no credentials
    assert served.status_code == 200
    image = Image.open(io.BytesIO(served.content))
    assert image.format == "WEBP" and image.size == (512, 256)  # longest side 512
    assert image.mode == "RGBA"  # transparency kept
    assert not image.getexif()
    assert (
        client.get(BRAND, headers=users["staff_a"].headers).json()["logo_url"]
        == (brand["logo_url"])
    )


def test_a_small_logo_is_not_upscaled(client, users, storage) -> None:
    brand = upload_logo(client, users["owner_a"], png_with_gps((200, 100)))
    assert Image.open(io.BytesIO(httpx.get(brand["logo_url"]).content)).size == (200, 100)


def test_replacing_and_removing_delete_the_old_file(client, users, storage, db) -> None:
    owner = users["owner_a"]
    upload_logo(client, owner, png_with_gps())
    first = stored_logo(db, TENANT_A)
    second_brand = upload_logo(client, owner, png_with_gps((300, 300)))
    second = stored_logo(db, TENANT_A)
    assert second != first and first not in logos(storage, TENANT_A)
    assert second in logos(storage, TENANT_A)

    removed = client.delete(LOGO, headers=owner.headers)
    assert removed.status_code == 200 and removed.json()["logo_url"] is None
    assert second not in logos(storage, TENANT_A)
    assert httpx.get(second_brand["logo_url"]).status_code in (400, 404)
    assert client.delete(LOGO, headers=owner.headers).status_code == 404


@pytest.mark.parametrize(
    ("data", "content_type", "message"),
    [
        (b"MZ\x90\x00 a renamed program", "image/png", "not a valid JPEG"),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "image/png",
            "not a valid JPEG",
        ),
        (b"%PDF-1.7 not an image", "image/jpeg", "not a valid JPEG"),
    ],
)
def test_files_that_are_not_images_are_refused(
    client, users, storage, db, data, content_type, message
) -> None:
    owner = users["owner_a"]
    before = stored_logo(db, TENANT_A)
    ticket = sign(client, owner, content_type).json()
    assert put(ticket["upload_url"], data, content_type).status_code == 200
    r = register(client, owner, ticket["upload_id"], content_type)
    assert r.status_code == 422 and message in r.json()["detail"]
    assert stored_logo(db, TENANT_A) == before
    assert storage.names("product-uploads", f"{TENANT_A}/brand/") == []


def test_svg_and_other_types_are_refused_before_signing(client, users, storage) -> None:
    for content_type in ("image/svg+xml", "image/gif", "application/pdf"):
        assert sign(client, users["owner_a"], content_type).status_code == 422, content_type


def test_a_logo_over_2_mb_is_refused(client, users, storage, db) -> None:
    owner = users["owner_a"]
    noise = Image.effect_noise((1400, 1400), 120).convert("RGB")
    out = io.BytesIO()
    noise.save(out, format="PNG")
    big = out.getvalue()
    assert 2 * 1024 * 1024 < len(big) < 5 * 1024 * 1024  # under the raw bucket's own limit
    ticket = sign(client, owner).json()
    assert put(ticket["upload_url"], big, "image/png").status_code == 200
    r = register(client, owner, ticket["upload_id"])
    assert r.status_code == 422 and "larger than 2 MB" in r.json()["detail"]
    assert storage.names("product-uploads", f"{TENANT_A}/brand/") == []


def test_logo_role_matrix(client, users, storage) -> None:
    assert_access(
        client,
        users,
        "POST",
        f"{LOGO}/uploads",
        {"owner_a": 201, "owner_b": 201},  # each owner signs for their own tenant only
        json={"content_type": "image/png"},
    )
    assert_access(
        client,
        users,
        "POST",
        LOGO,
        {"owner_a": 404, "owner_b": 404},  # no such upload; staff and cpa never get that far
        json={"upload_id": "00000000-0000-4000-8000-000000000000", "content_type": "image/png"},
    )
    upload_logo(client, users["owner_a"], png_with_gps((100, 100)))
    assert_access(client, users, "DELETE", LOGO, {"owner_a": 200, "owner_b": 404})


def test_another_tenant_never_reaches_a_logo_upload(client, users, storage, db) -> None:
    owner_a, owner_b = users["owner_a"], users["owner_b"]
    ticket = sign(client, owner_a).json()
    assert put(ticket["upload_url"], png_with_gps(), "image/png").status_code == 200
    # B's request is rebuilt from B's token: it can't name A's upload.
    assert register(client, owner_b, ticket["upload_id"]).status_code == 404
    assert stored_logo(db, TENANT_B) is None
    a_raw = f"{TENANT_A}/brand/{ticket['upload_id']}.png"
    for extra in ({"path": a_raw}, {"tenant_id": TENANT_A}, {"logo_path": a_raw}):
        assert sign(client, owner_b, **extra).status_code == 422
        assert register(client, owner_b, ticket["upload_id"], **extra).status_code == 422
    assert storage.names("product-uploads", f"{TENANT_A}/brand/") == [a_raw]  # untouched
