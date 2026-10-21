"""Central mapping from database errors to HTTP responses.

Responses are always `{"detail": "..."}` with a fixed, generic message. SQL text, constraint
and table names, and stack traces never reach the client. The one exception is an error
raised by one of our own PL/pgSQL triggers (`RAISE` in a `public` function): those messages
are written for humans and are returned with the mapped status.
"""

import logging
import re
from dataclasses import dataclass

import psycopg
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

logger = logging.getLogger(__name__)


class NotFoundError(Exception):
    """No row matched, or the row is hidden from the caller by RLS."""

    def __init__(self, detail: str = "Not found") -> None:
        super().__init__(detail)
        self.detail = detail


class ConflictError(Exception):
    """The request conflicts with existing data (409). The message is shown to the client."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class MappedError:
    status_code: int
    detail: str


# SQLSTATE -> (status, generic message)
_BY_SQLSTATE: dict[str, tuple[int, str]] = {
    "23505": (status.HTTP_409_CONFLICT, "A record with these values already exists"),
    "23503": (status.HTTP_409_CONFLICT, "The request references a missing or in-use record"),
    "23514": (status.HTTP_422_UNPROCESSABLE_CONTENT, "The request violates a data rule"),
    "23502": (status.HTTP_422_UNPROCESSABLE_CONTENT, "A required value is missing"),
    "42501": (status.HTTP_403_FORBIDDEN, "Not allowed"),
    "P0001": (status.HTTP_409_CONFLICT, "The request conflicts with a business rule"),
}
_DATA_ERROR = (status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid value")
_UNAVAILABLE = (status.HTTP_503_SERVICE_UNAVAILABLE, "Database is temporarily unavailable")
_INTERNAL = (status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error")

# Innermost context line of a PL/pgSQL RAISE. Postgres prints the schema only when the
# function is not on the search_path, so "public." is optional; any other schema is rejected.
_RAISE_CONTEXT = re.compile(
    r"^PL/pgSQL function (public\.)?[a-z_][a-z0-9_]*\(.*\) line \d+ at RAISE$"
)


def is_trigger_raise(diag: psycopg.errors.Diagnostic) -> bool:
    """True when the error came from a RAISE inside one of our public PL/pgSQL functions.

    Postgres-generated errors (constraint violations, RLS checks, permission checks) carry no
    "at RAISE" context, so their messages are never surfaced.
    """
    context = diag.context or ""
    if not context:
        return False
    innermost = context.splitlines()[0]
    return (
        _RAISE_CONTEXT.match(innermost) is not None
        and diag.constraint_name is None
        and diag.table_name is None
    )


def map_db_error(exc: psycopg.Error) -> MappedError:
    sqlstate = exc.sqlstate or ""
    if sqlstate in _BY_SQLSTATE:
        code, detail = _BY_SQLSTATE[sqlstate]
    elif sqlstate.startswith("22"):
        code, detail = _DATA_ERROR
    elif isinstance(exc, psycopg.OperationalError) and not sqlstate:
        code, detail = _UNAVAILABLE
    else:
        code, detail = _INTERNAL

    if code != status.HTTP_500_INTERNAL_SERVER_ERROR and is_trigger_raise(exc.diag):
        detail = exc.diag.message_primary or detail
    return MappedError(code, detail)


async def _db_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, psycopg.Error)
    mapped = map_db_error(exc)
    if mapped.status_code >= 500:
        logger.exception("Database error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": mapped.detail}, status_code=mapped.status_code)


async def _pool_timeout_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("Database pool timeout on %s %s", request.method, request.url.path)
    code, detail = _UNAVAILABLE
    return JSONResponse({"detail": detail}, status_code=code)


async def _conflict_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ConflictError)
    return JSONResponse({"detail": exc.detail}, status_code=status.HTTP_409_CONFLICT)


async def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, NotFoundError)
    return JSONResponse({"detail": exc.detail}, status_code=status.HTTP_404_NOT_FOUND)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(psycopg.Error, _db_error_handler)
    app.add_exception_handler(PoolTimeout, _pool_timeout_handler)
    app.add_exception_handler(NotFoundError, _not_found_handler)
    app.add_exception_handler(ConflictError, _conflict_handler)
