"""Domain normalization and the onboarding script's target rules. No database needed."""

import importlib.util
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.tenant_domains import DomainRefused, normalize_domain

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "add_tenant_domain.py"
_spec = importlib.util.spec_from_file_location("add_tenant_domain", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

LOCAL_DB = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"
REMOTE_DB = "postgresql://admin:p@db.abcd.supabase.co:5432/postgres"


@pytest.mark.parametrize(
    ("raw", "local", "expected"),
    [
        ("shop.example.com", False, "shop.example.com"),
        ("  Shop.Example.COM.  ", False, "shop.example.com"),
        ("joyería-aurora.example", False, "xn--joyera-aurora-0ib.example"),
        ("my-shop.co.uk", False, "my-shop.co.uk"),
        ("localhost:5174", True, "localhost:5174"),
        ("aurora.localhost:5174", True, "aurora.localhost:5174"),
        ("shop.example.com:8443", True, "shop.example.com:8443"),
    ],
)
def test_domains_are_stored_the_way_browsers_send_them(
    raw: str, local: bool, expected: str
) -> None:
    assert normalize_domain(raw, local=local) == expected


@pytest.mark.parametrize(
    ("raw", "local"),
    [
        ("", False),
        ("https://shop.example.com", False),
        ("shop.example.com/path", False),
        ("user@shop.example.com", False),
        ("shop.example.com:443", False),  # ports only locally
        ("localhost:5174", False),
        ("aurora.localhost", False),
        ("shop", False),
        ("192.168.1.10", False),
        ("-shop.example.com", False),
        ("shop_name.example.com", False),
        ("a" * 64 + ".example.com", False),
        ("shop.example.com:99999", True),
        ("shop.example.com:abc", True),
    ],
)
def test_bad_or_disallowed_domains_are_refused(raw: str, local: bool) -> None:
    with pytest.raises(DomainRefused):
        normalize_domain(raw, local=local)


def _settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, _env_file=None)


def test_the_local_database_is_the_default_target() -> None:
    assert script.database_url({}, _settings(LOCAL_DB), allow_remote=False) == (LOCAL_DB, True)


def test_a_remote_database_needs_the_flag_and_its_own_admin_url() -> None:
    with pytest.raises(DomainRefused, match="--allow-remote"):
        script.database_url({}, _settings(REMOTE_DB), allow_remote=False)
    with pytest.raises(DomainRefused, match="ADMIN_DATABASE_URL"):
        script.database_url({}, _settings(REMOTE_DB), allow_remote=True)
    env = {"ADMIN_DATABASE_URL": REMOTE_DB}
    assert script.database_url(env, _settings(""), allow_remote=True) == (REMOTE_DB, False)


def test_a_bad_tenant_id_is_refused_before_anything_runs(capsys) -> None:
    assert script.main(["--tenant", "not-a-uuid", "--domain", "shop.example.com"]) == 2
    assert "must be a uuid" in capsys.readouterr().err
