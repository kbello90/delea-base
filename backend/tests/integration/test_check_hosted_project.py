"""check_hosted_project.py against the running local stack, with the stack's new-style keys
(sb_publishable_/sb_secret_, read from the pinned CLI's `supabase status`): every check passes,
including a real sign-in, and the temporary user is gone afterwards. The local stack has no
pg_graphql, so the GraphQL probe is covered by tests/test_check_hosted_project.py."""

import json
import subprocess

import httpx
import pytest

from scripts import check_hosted_project as chp

pytestmark = pytest.mark.integration

CLI = ["npx", "-y", "supabase@2.121.0", "status", "-o", "json"]


@pytest.fixture(scope="module")
def local_keys(settings) -> dict[str, str]:
    result = subprocess.run(
        " ".join(CLI), shell=True, capture_output=True, text=True, timeout=120, check=True
    )
    status = json.loads(result.stdout[result.stdout.index("{") :])
    assert status["API_URL"].rstrip("/") == settings.supabase_url.rstrip("/")
    return {"publishable": status["PUBLISHABLE_KEY"], "secret": status["SECRET_KEY"]}


def _users(settings, keys) -> set[str]:
    r = httpx.get(
        f"{settings.supabase_url}/auth/v1/admin/users",
        params={"per_page": "1000"},
        headers={"apikey": keys["secret"]},
        timeout=10,
    )
    r.raise_for_status()
    return {u["email"] for u in r.json()["users"]}


def test_every_check_passes_on_the_local_stack(settings, local_keys, monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", local_keys["publishable"])
    monkeypatch.setenv("SUPABASE_SECRET_KEY", local_keys["secret"])
    before = _users(settings, local_keys)

    code = chp.main(["--url", settings.supabase_url, "--allow-local", "--sign-in"])

    out = capsys.readouterr().out
    assert code == 0, out
    assert out.count("ok   ") == 5 and "FAIL" not in out
    assert local_keys["publishable"] not in out and local_keys["secret"] not in out
    assert _users(settings, local_keys) == before  # the temporary user was deleted


def test_a_wrong_secret_key_is_refused_by_the_real_auth_server(settings, local_keys, capsys):
    with httpx.Client(timeout=20) as client:
        project = chp.Project(
            settings.supabase_url, local_keys["publishable"], local_keys["secret"], client
        )
        project.secret = "sb_secret_not-the-real-one"
        code = chp.run(project, sign_in=False)
    out = capsys.readouterr().out
    assert code == 1 and "Auth admin refused the secret key" in out
