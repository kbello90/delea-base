"""Unit tests for the database error mapping. No database: errors are constructed locally."""

from types import SimpleNamespace

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors

from app.core.errors import (
    NotFoundError,
    is_trigger_raise,
    map_db_error,
    register_exception_handlers,
)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (errors.UniqueViolation(), 409),
        (errors.ForeignKeyViolation(), 409),
        (errors.CheckViolation(), 422),
        (errors.NotNullViolation(), 422),
        (errors.InsufficientPrivilege(), 403),
        (errors.RaiseException(), 409),
        (errors.NumericValueOutOfRange(), 422),
        (errors.InvalidTextRepresentation(), 422),
        (errors.SyntaxError(), 500),
        (errors.UndefinedTable(), 500),
    ],
)
def test_sqlstate_mapping(exc: psycopg.Error, expected: int) -> None:
    assert map_db_error(exc).status_code == expected


def test_messages_are_generic_without_trigger_context() -> None:
    # A constructed error has no diagnostics, so its text is never surfaced.
    mapped = map_db_error(
        errors.UniqueViolation('duplicate key value violates "products_tenant_id_sku_key"')
    )
    assert mapped.detail == "A record with these values already exists"


def diag(context: str | None, constraint: str | None = None, table: str | None = None):
    return SimpleNamespace(context=context, constraint_name=constraint, table_name=table)


NEXT_NUMBER_FN = "next_document_number(uuid,text,timestamp with time zone)"


@pytest.mark.parametrize(
    "context",
    [
        "PL/pgSQL function invoices_enforce_forward_status() line 3 at RAISE",
        "PL/pgSQL function public.invoices_staff_financial_guard() line 9 at RAISE",
        f"PL/pgSQL function {NEXT_NUMBER_FN} line 26 at RAISE\n"
        "PL/pgSQL function invoices_assign_numbers() line 4 at assignment",
    ],
)
def test_trigger_raise_is_detected(context: str) -> None:
    assert is_trigger_raise(diag(context))


@pytest.mark.parametrize(
    "candidate",
    [
        diag(None),
        diag(""),
        # Postgres-generated error inside a function call (not a RAISE)
        diag(
            'SQL statement "update public.company_settings ..."\n'
            f"PL/pgSQL function {NEXT_NUMBER_FN} line 8 at SQL statement"
        ),
        # RAISE in a non-public schema
        diag("PL/pgSQL function auth.something() line 2 at RAISE"),
        # A constraint violation always names its constraint
        diag(
            "PL/pgSQL function f() line 1 at RAISE",
            constraint="customers_wholesale_requires_company",
        ),
        diag("PL/pgSQL function f() line 1 at RAISE", table="products"),
    ],
)
def test_non_trigger_errors_are_not_surfaced(candidate: SimpleNamespace) -> None:
    assert not is_trigger_raise(candidate)


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/unique")
    def unique() -> None:
        raise errors.UniqueViolation('duplicate key value violates unique constraint "x"')

    @app.get("/rls")
    def rls() -> None:
        raise errors.InsufficientPrivilege(
            'new row violates row-level security policy for table "products"'
        )

    @app.get("/internal")
    def internal() -> None:
        raise errors.SyntaxError('syntax error at or near "selec"')

    @app.get("/missing")
    def missing() -> None:
        raise NotFoundError("Product not found")

    return app


@pytest.mark.parametrize(
    ("path", "status_code", "detail"),
    [
        ("/unique", 409, "A record with these values already exists"),
        ("/rls", 403, "Not allowed"),
        ("/internal", 500, "Internal server error"),
        ("/missing", 404, "Product not found"),
    ],
)
def test_handlers_return_generic_json(path: str, status_code: int, detail: str) -> None:
    response = TestClient(build_app()).get(path)
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    for leaked in ("selec", "row-level security", "constraint", "Traceback"):
        assert leaked not in response.text
