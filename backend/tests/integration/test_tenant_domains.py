"""The domain onboarding script against the local stack (throwaway tenants only)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.storefront_hosts import host_cache

from .support import TENANT_A
from .test_tenant_domains_helpers import script

pytestmark = pytest.mark.integration


def _domain() -> str:
    return f"it-{uuid.uuid4().hex[:8]}.localhost:5174"


def _rows(db, domain: str) -> list[tuple]:
    return db.execute(
        "select tenant_id::text, kind from public.tenant_domains where domain = %s", (domain,)
    ).fetchall()


def test_a_domain_is_added_once_and_serves_the_store(
    client: TestClient, make_tenant, db, capsys
) -> None:
    tenant = make_tenant()
    domain = _domain()

    assert script.main(["--tenant", tenant.id, "--domain", domain, "--dry-run"]) == 0
    assert _rows(db, domain) == []  # a dry run writes nothing

    assert script.main(["--tenant", tenant.id, "--domain", domain.upper()]) == 0
    out = capsys.readouterr().out
    assert f"Added {domain}" in out
    assert "switched off" in out  # provisioned with the storefront off
    assert _rows(db, domain) == [(tenant.id, "storefront")]

    assert script.main(["--tenant", tenant.id, "--domain", domain]) == 0
    assert "already set" in capsys.readouterr().out
    assert _rows(db, domain) == [(tenant.id, "storefront")]

    db.execute(
        "update public.storefront_settings set storefront_enabled = true where tenant_id = %s",
        (tenant.id,),
    )
    host_cache.clear()
    store = client.get("/api/v1/public/store", headers={"Origin": f"http://{domain}"})
    assert store.status_code == 200
    assert store.json()["business_name"] == tenant.business_name


def test_another_tenants_domain_is_refused(make_tenant, db, capsys) -> None:
    tenant = make_tenant()
    assert script.main(["--tenant", tenant.id, "--domain", "localhost:5174"]) == 2
    assert "already used" in capsys.readouterr().err
    assert _rows(db, "localhost:5174") == [(TENANT_A, "storefront")]


def test_an_unknown_or_inactive_tenant_is_refused(make_tenant, db, capsys) -> None:
    domain = _domain()
    assert script.main(["--tenant", str(uuid.uuid4()), "--domain", domain]) == 2
    assert "no tenant" in capsys.readouterr().err
    tenant = make_tenant()
    db.execute("update public.tenants set is_active = false where id = %s", (tenant.id,))
    assert script.main(["--tenant", tenant.id, "--domain", domain]) == 2
    assert "not active" in capsys.readouterr().err
    assert _rows(db, domain) == []
