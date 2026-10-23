"""Expense receipt routes: one private receipt (image or PDF) per expense.

Roles: upload, register and remove are owner only; viewing is owner and cpa. Staff have no
access to expenses at all. Every database read and write runs in the caller's RLS session.

Upload flow:
1. POST /expenses/{id}/receipt/uploads: the backend signs an upload URL for a pending object
   {tenant_id}/{expense_id}/pending-{upload_id}.{ext} in the private expense-receipts bucket.
   The tenant comes from the verified token; the expense is checked in the caller's session.
   The browser PUTs the file there; clients can't write to the bucket any other way.
2. POST /expenses/{id}/receipt: the pending path is rebuilt from the token (never taken from
   the request), the file is downloaded and checked (services.receipts: real content, size,
   images re-encoded without metadata), written to {tenant_id}/{expense_id}/{receipt_id}.{ext}
   and recorded on the expense. The pending object is always deleted; a replaced receipt's
   file is deleted after the change commits.

Viewing returns a signed URL valid for LINK_SECONDS. The storage path never leaves the server.
"""

import logging
import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.api.v1.expenses import COLUMNS, remove_receipt_files
from app.core import storage
from app.core.errors import NotFoundError
from app.core.security import TokenClaims
from app.db.session import user_session
from app.models.expense import (
    ExpenseOut,
    ReceiptLink,
    ReceiptRegister,
    ReceiptUploadRequest,
    ReceiptUploadTicket,
)
from app.services.receipts import (
    EXTENSIONS,
    MAX_BYTES,
    ReceiptRejected,
    ReceiptType,
    process_receipt_async,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expenses", tags=["expense receipts"])

ReadRoles = Depends(require_role("owner", "cpa"))
OwnerOnly = Depends(require_role("owner"))

LINK_SECONDS = 60


def pending_path(claims: TokenClaims, expense_id: UUID, upload_id: UUID, kind: ReceiptType) -> str:
    """Where an upload waits to be registered. The tenant comes from the verified token only."""
    return f"{claims.tenant_id}/{expense_id}/pending-{upload_id}.{EXTENSIONS[kind]}"


def receipt_path(claims: TokenClaims, expense_id: UUID, receipt_id: UUID, kind: ReceiptType) -> str:
    return f"{claims.tenant_id}/{expense_id}/{receipt_id}.{EXTENSIONS[kind]}"


async def _receipt_of(
    conn: AsyncConnection, claims: TokenClaims, expense_id: UUID, lock: bool = False
) -> dict:
    """The expense's receipt columns (404 if the expense isn't the caller's)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select receipt_path, receipt_content_type from public.expenses "
            f"where id = %s and tenant_id = %s{' for update' if lock else ''}",
            (expense_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Expense not found")
    return row


async def _expense(conn: AsyncConnection, claims: TokenClaims, expense_id: UUID) -> ExpenseOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {COLUMNS} from public.expenses where id = %s and tenant_id = %s",
            (expense_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Expense not found")
    return ExpenseOut.model_validate(row)


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Receipt storage is temporarily unavailable"
    )


async def _remove_quietly(paths: list[str]) -> None:
    try:
        await storage.remove(storage.RECEIPTS_BUCKET, paths)
    except Exception:
        logger.exception("Could not remove %d receipt object(s)", len(paths))


@router.post(
    "/{expense_id}/receipt/uploads",
    response_model=ReceiptUploadTicket,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOnly],
)
async def create_receipt_upload(
    expense_id: UUID, body: ReceiptUploadRequest, claims: CurrentClaims, conn: DbConn
) -> ReceiptUploadTicket:
    await _receipt_of(conn, claims, expense_id)
    upload_id = uuid.uuid4()
    path = pending_path(claims, expense_id, upload_id, body.content_type)
    try:
        signed = await storage.create_signed_upload(storage.RECEIPTS_BUCKET, path)
    except storage.StorageError as exc:
        raise _storage_unavailable() from exc
    return ReceiptUploadTicket(upload_id=upload_id, upload_url=signed.url, max_bytes=MAX_BYTES)


@router.post("/{expense_id}/receipt", response_model=ExpenseOut, dependencies=[OwnerOnly])
async def register_receipt(
    expense_id: UUID,
    body: ReceiptRegister,
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    background: BackgroundTasks,
) -> ExpenseOut:
    pending = pending_path(claims, expense_id, body.upload_id, body.content_type)
    # Short sessions only: no transaction or row lock is held while the file is processed.
    try:
        async with user_session(pool, claims) as conn:
            await _receipt_of(conn, claims, expense_id)
        try:
            data = await storage.download(storage.RECEIPTS_BUCKET, pending, MAX_BYTES)
        except storage.TooLarge:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.",
            ) from None
        except storage.StorageError as exc:
            raise _storage_unavailable() from exc
        if data is None:
            raise NotFoundError("Upload not found. Upload the file first, then register it.")
        try:
            processed = await process_receipt_async(data, body.content_type)
        except ReceiptRejected as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    finally:
        # The pending upload (which may carry GPS metadata) never outlives this request.
        await _remove_quietly([pending])

    final = receipt_path(claims, expense_id, uuid.uuid4(), processed.content_type)
    try:
        await storage.upload(
            storage.RECEIPTS_BUCKET, final, processed.data, processed.content_type.value
        )
    except storage.StorageError as exc:
        raise _storage_unavailable() from exc
    try:
        async with user_session(pool, claims) as conn:
            previous = await _receipt_of(conn, claims, expense_id, lock=True)
            await conn.execute(
                "update public.expenses set receipt_path = %s, receipt_content_type = %s, "
                "receipt_size_bytes = %s, receipt_uploaded_at = now() "
                "where id = %s and tenant_id = %s",
                (
                    final,
                    processed.content_type.value,
                    len(processed.data),
                    expense_id,
                    claims.tenant_id,
                ),
            )
            expense = await _expense(conn, claims, expense_id)
    except BaseException:
        # Nothing was recorded: the stored file would be an orphan.
        await _remove_quietly([final])
        raise
    if previous["receipt_path"]:
        background.add_task(remove_receipt_files, [previous["receipt_path"]])
    return expense


@router.delete("/{expense_id}/receipt", response_model=ExpenseOut, dependencies=[OwnerOnly])
async def remove_receipt(
    expense_id: UUID, claims: CurrentClaims, conn: DbConn, background: BackgroundTasks
) -> ExpenseOut:
    previous = await _receipt_of(conn, claims, expense_id, lock=True)
    if previous["receipt_path"] is None:
        raise NotFoundError("This expense has no receipt")
    await conn.execute(
        "update public.expenses set receipt_path = null, receipt_content_type = null, "
        "receipt_size_bytes = null, receipt_uploaded_at = null "
        "where id = %s and tenant_id = %s",
        (expense_id, claims.tenant_id),
    )
    background.add_task(remove_receipt_files, [previous["receipt_path"]])
    return await _expense(conn, claims, expense_id)


@router.get("/{expense_id}/receipt", response_model=ReceiptLink, dependencies=[ReadRoles])
async def view_receipt(
    expense_id: UUID, claims: CurrentClaims, conn: DbConn, response: Response
) -> ReceiptLink:
    """A link to the receipt that works for LINK_SECONDS. Ask again for each view."""
    row = await _receipt_of(conn, claims, expense_id)
    if row["receipt_path"] is None:
        raise NotFoundError("This expense has no receipt")
    try:
        url = await storage.create_signed_url(
            storage.RECEIPTS_BUCKET, row["receipt_path"], LINK_SECONDS
        )
    except storage.StorageError as exc:
        raise _storage_unavailable() from exc
    response.headers["Cache-Control"] = "no-store"
    return ReceiptLink(
        url=url, expires_in=LINK_SECONDS, content_type=ReceiptType(row["receipt_content_type"])
    )
