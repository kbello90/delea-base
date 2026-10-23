"""Expense receipts end to end against the local stack, Storage included: signed upload,
register (real-content check, images re-encoded without metadata, PDFs unchanged), short-lived
view links, replace, remove, deletion with the expense, roles, tenant isolation, and that the
bucket is private.
"""

import io
import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image

from app.core.config import Settings

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

EXPENSES = "/api/v1/expenses"
BUCKET = "expense-receipts"
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def jpeg_with_gps() -> bytes:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (40.0, 44.0, 54.0)
    out = io.BytesIO()
    Image.new("RGB", (800, 600), (240, 240, 230)).save(out, format="JPEG", exif=exif)
    return out.getvalue()


class Storage:
    """Direct Storage access for assertions and cleanup (service key, like the backend)."""

    def __init__(self, settings: Settings) -> None:
        key = settings.supabase_service_role_key
        self.base = f"{settings.supabase_url}/storage/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def names(self, prefix: str) -> list[str]:
        r = httpx.post(
            f"{self.base}/object/list/{BUCKET}",
            headers=self.headers,
            json={"prefix": prefix, "limit": 1000},
        )
        r.raise_for_status()
        return sorted(f"{prefix}{item['name']}" for item in r.json() if item.get("id"))

    def read(self, path: str) -> bytes:
        r = httpx.get(f"{self.base}/object/authenticated/{BUCKET}/{path}", headers=self.headers)
        r.raise_for_status()
        return r.content

    def remove(self, paths: list[str]) -> None:
        if paths:
            httpx.request(
                "DELETE",
                f"{self.base}/object/{BUCKET}",
                headers=self.headers,
                json={"prefixes": paths},
            )


@pytest.fixture
def storage(settings: Settings) -> Iterator[Storage]:
    s = Storage(settings)
    prefixes: list[str] = []
    s.track = prefixes.append  # type: ignore[attr-defined]
    yield s
    for prefix in prefixes:  # whatever the test left behind
        s.remove(s.names(prefix))


def new_expense(client: TestClient, owner: TestUser, storage: Storage, **overrides) -> str:
    body = {
        "expense_date": "2026-10-15",
        "description": f"IT-receipt-{overrides.pop('tag', 'x')}",
        "amount_cents": 1234,
        **overrides,
    }
    r = client.post(EXPENSES, json=body, headers=owner.headers)
    assert r.status_code == 201, r.text
    expense_id = r.json()["id"]
    storage.track(f"{owner.tenant_id}/{expense_id}/")  # type: ignore[attr-defined]
    return expense_id


def sign(client, user: TestUser, expense_id: str, content_type="image/jpeg", **extra):
    return client.post(
        f"{EXPENSES}/{expense_id}/receipt/uploads",
        json={"content_type": content_type, **extra},
        headers=user.headers,
    )


def put(upload_url: str, data: bytes, content_type: str) -> httpx.Response:
    # What the browser does: no app credentials, just the signed URL.
    return httpx.put(upload_url, content=data, headers={"Content-Type": content_type})


def register(client, user, expense_id, upload_id, content_type="image/jpeg", **extra):
    return client.post(
        f"{EXPENSES}/{expense_id}/receipt",
        json={"upload_id": upload_id, "content_type": content_type, **extra},
        headers=user.headers,
    )


def attach(client, owner, expense_id, data: bytes, content_type: str) -> dict:
    ticket = sign(client, owner, expense_id, content_type)
    assert ticket.status_code == 201, ticket.text
    assert put(ticket.json()["upload_url"], data, content_type).status_code == 200
    r = register(client, owner, expense_id, ticket.json()["upload_id"], content_type)
    assert r.status_code == 200, r.text
    return r.json()


def stored_path(db, expense_id: str) -> str | None:
    return db.execute(
        "select receipt_path from public.expenses where id = %s", (expense_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Upload, register, view
# ---------------------------------------------------------------------------
def test_an_image_receipt_is_stored_without_metadata_and_viewed_by_short_link(
    client, users, storage, db
) -> None:
    owner, cpa = users["owner_a"], users["cpa_a"]
    expense_id = new_expense(client, owner, storage)
    ticket = sign(client, owner, expense_id).json()
    assert ticket["max_bytes"] == 10 * 1024 * 1024
    assert put(ticket["upload_url"], jpeg_with_gps(), "image/jpeg").status_code == 200
    pending = f"{TENANT_A}/{expense_id}/pending-{ticket['upload_id']}.jpg"
    assert storage.names(f"{TENANT_A}/{expense_id}/") == [pending]

    expense = register(client, owner, expense_id, ticket["upload_id"]).json()
    assert expense["has_receipt"] is True
    assert expense["receipt_content_type"] == "image/jpeg"
    assert "receipt_path" not in expense  # the path never leaves the server
    path = stored_path(db, expense_id)
    assert re.fullmatch(f"{TENANT_A}/{expense_id}/{UUID}\\.jpg", path)
    assert storage.names(f"{TENANT_A}/{expense_id}/") == [path]  # the pending upload is gone

    stored = Image.open(io.BytesIO(storage.read(path)))
    assert stored.format == "JPEG" and stored.size == (800, 600)
    assert not stored.getexif()  # no GPS, no camera
    assert expense["receipt_size_bytes"] == len(storage.read(path))

    for user in (owner, cpa):
        r = client.get(f"{EXPENSES}/{expense_id}/receipt", headers=user.headers)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        link = r.json()
        assert (link["expires_in"], link["content_type"]) == (60, "image/jpeg")
        assert "/object/sign/" in link["url"] and "token=" in link["url"]
        assert httpx.get(link["url"]).content == storage.read(path)

    # Nothing is public: neither the public nor the plain object URL serves the file.
    base = storage.base
    assert httpx.get(f"{base}/object/public/{BUCKET}/{path}").status_code in (400, 404)
    assert httpx.get(f"{base}/object/{BUCKET}/{path}").status_code in (400, 404)


def test_a_pdf_is_stored_unchanged(client, users, storage, db) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    expense = attach(client, owner, expense_id, PDF, "application/pdf")
    assert (expense["receipt_content_type"], expense["receipt_size_bytes"]) == (
        "application/pdf",
        len(PDF),
    )
    path = stored_path(db, expense_id)
    assert path.endswith(".pdf") and storage.read(path) == PDF


@pytest.mark.parametrize(
    ("data", "content_type", "message"),
    [
        (b"<html><script>alert(1)</script></html>", "application/pdf", "isn't a PDF"),
        (b"MZ\x90\x00 a renamed program", "image/jpeg", "not a valid JPEG"),
        (PDF, "image/png", "not a valid JPEG"),
    ],
)
def test_a_file_that_is_not_what_it_claims_is_refused(
    client, users, storage, db, data, content_type, message
) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    ticket = sign(client, owner, expense_id, content_type).json()
    assert put(ticket["upload_url"], data, content_type).status_code == 200
    r = register(client, owner, expense_id, ticket["upload_id"], content_type)
    assert r.status_code == 422 and message in r.json()["detail"]
    assert stored_path(db, expense_id) is None
    assert storage.names(f"{TENANT_A}/{expense_id}/") == []  # the pending upload is gone too


def test_an_oversize_file_is_refused_by_the_bucket_and_never_recorded(
    client, users, storage, db
) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    ticket = sign(client, owner, expense_id, "application/pdf").json()
    big = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    assert put(ticket["upload_url"], big, "application/pdf").status_code in (400, 413)
    r = register(client, owner, expense_id, ticket["upload_id"], "application/pdf")
    assert r.status_code == 404
    assert stored_path(db, expense_id) is None


def test_register_without_an_upload_is_404(client, users, storage) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    r = register(client, owner, expense_id, "00000000-0000-4000-8000-000000000000")
    assert r.status_code == 404 and "Upload not found" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Replace, remove, delete with the expense
# ---------------------------------------------------------------------------
def test_replacing_and_removing_delete_the_old_file(client, users, storage, db) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    prefix = f"{TENANT_A}/{expense_id}/"
    attach(client, owner, expense_id, jpeg_with_gps(), "image/jpeg")
    first = stored_path(db, expense_id)
    replaced = attach(client, owner, expense_id, PDF, "application/pdf")
    second = stored_path(db, expense_id)
    assert replaced["receipt_content_type"] == "application/pdf" and second != first
    assert storage.names(prefix) == [second]  # the replaced file is gone

    removed = client.delete(f"{EXPENSES}/{expense_id}/receipt", headers=owner.headers)
    assert removed.status_code == 200 and removed.json()["has_receipt"] is False
    assert storage.names(prefix) == []
    again = client.delete(f"{EXPENSES}/{expense_id}/receipt", headers=owner.headers)
    assert again.status_code == 404
    view = client.get(f"{EXPENSES}/{expense_id}/receipt", headers=owner.headers)
    assert view.status_code == 404 and view.json() == {"detail": "This expense has no receipt"}


def test_deleting_an_expense_deletes_its_receipt(client, users, storage, db) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    attach(client, owner, expense_id, PDF, "application/pdf")
    path = stored_path(db, expense_id)
    assert storage.names(f"{TENANT_A}/{expense_id}/") == [path]
    assert client.delete(f"{EXPENSES}/{expense_id}", headers=owner.headers).status_code == 204
    assert storage.names(f"{TENANT_A}/{expense_id}/") == []


# ---------------------------------------------------------------------------
# Roles and tenants
# ---------------------------------------------------------------------------
def test_receipt_role_matrix(client, users, storage) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    attach(client, owner, expense_id, PDF, "application/pdf")
    base = f"{EXPENSES}/{expense_id}/receipt"
    # View: owner and cpa; staff 403; another tenant 404.
    assert_access(client, users, "GET", base, {"owner_a": 200, "cpa_a": 200, "owner_b": 404})
    # Upload, register, remove: owner only.
    assert_access(
        client,
        users,
        "POST",
        f"{base}/uploads",
        {"owner_a": 201, "owner_b": 404},
        json={"content_type": "application/pdf"},
    )
    assert_access(
        client,
        users,
        "POST",
        base,
        {"owner_a": 404, "owner_b": 404},  # no such upload; cpa and staff never get that far
        json={"upload_id": "00000000-0000-4000-8000-000000000000", "content_type": "image/png"},
    )
    assert_access(client, users, "DELETE", base, {"owner_a": 200, "owner_b": 404})


def test_summary_role_matrix(client, users) -> None:
    assert_access(
        client,
        users,
        "GET",
        f"{EXPENSES}/summary",
        {"owner_a": 200, "cpa_a": 200, "owner_b": 200},
    )


def test_another_tenant_can_never_reach_a_receipt(client, users, storage, db) -> None:
    owner_a, owner_b = users["owner_a"], users["owner_b"]
    a_expense = new_expense(client, owner_a, storage)
    attach(client, owner_a, a_expense, PDF, "application/pdf")
    a_path = stored_path(db, a_expense)

    # A pending upload in tenant A can't be registered by tenant B on B's own expense: the
    # path is rebuilt from B's token, so B's request never names A's object.
    ticket = sign(client, owner_a, a_expense, "application/pdf").json()
    assert put(ticket["upload_url"], PDF, "application/pdf").status_code == 200
    b_expense = new_expense(client, owner_b, storage)
    r = register(client, owner_b, b_expense, ticket["upload_id"], "application/pdf")
    assert r.status_code == 404
    assert stored_path(db, b_expense) is None
    assert storage.names(f"{TENANT_B}/{b_expense}/") == []

    # A path or tenant in the body is refused outright.
    for extra in ({"path": a_path}, {"tenant_id": TENANT_A}, {"receipt_path": a_path}):
        assert sign(client, owner_b, b_expense, "application/pdf", **extra).status_code == 422
        assert register(client, owner_b, b_expense, ticket["upload_id"], **extra).status_code == 422
    assert storage.read(a_path) == PDF  # untouched


def test_expense_bodies_never_accept_receipt_fields(client, users, storage, db) -> None:
    owner = users["owner_a"]
    expense_id = new_expense(client, owner, storage)
    url = f"{EXPENSES}/{expense_id}"
    forged_path = f"{TENANT_A}/{expense_id}/00000000-0000-4000-8000-000000000000.pdf"
    for forged in (
        {"receipt_path": forged_path},
        {"receipt_content_type": "application/pdf"},
        {"receipt_size_bytes": 10},
        {"receipt_uploaded_at": "2026-10-01T00:00:00Z"},
        {"has_receipt": True},
        {"receipt_url": "https://example.com/r.pdf"},
    ):
        r = client.patch(url, json={"description": "IT-receipt-x", **forged}, headers=owner.headers)
        assert r.status_code == 422, forged
        body = {"expense_date": "2026-10-15", "description": "IT-receipt-y", "amount_cents": 1}
        assert (
            client.post(EXPENSES, json={**body, **forged}, headers=owner.headers).status_code == 422
        )
    assert stored_path(db, expense_id) is None
