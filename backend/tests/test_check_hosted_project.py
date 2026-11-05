"""check_hosted_project.py against a fake Supabase project (no network): each check passes on a
correctly set-up project and fails, with a reason, on each way a hosted project can be wrong.
Nothing it prints contains a key, a token or a password. The local stack run is in
tests/integration/test_check_hosted_project.py."""

import json
import time
from dataclasses import dataclass, field

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from scripts import check_hosted_project as chp

URL = "https://abcdefghij.supabase.co"
PUBLISHABLE = "sb_publishable_pubPUB123"
SECRET = "sb_secret_secSEC456"
EC_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_EC_KEY = ec.generate_private_key(ec.SECP256R1())


def _jwk(private, kid: str) -> dict:
    jwk = json.loads(ECAlgorithm.to_jwk(private.public_key()))
    return {**jwk, "kid": kid, "alg": "ES256", "use": "sig"}


@dataclass
class FakeProject:
    """A correctly set-up project; each test breaks one thing."""

    signup_off: bool = True
    keys: list = field(default_factory=lambda: [_jwk(EC_KEY, "current")])
    rest_open: set = field(default_factory=set)
    graphql_tables: list = field(default_factory=list)
    graphql_on: bool = True
    bearer_secret_refused: bool = False
    secret_refused: bool = False
    buckets: dict = field(default_factory=lambda: dict(chp.BUCKETS))
    token_alg: str = "ES256"
    token_kid: str = "current"
    token_key: object = EC_KEY
    delete_fails: bool = False
    users: dict = field(default_factory=dict)
    seen: list = field(default_factory=list)

    def _server_ok(self, request: httpx.Request) -> bool:
        if self.secret_refused or request.headers.get("apikey") != SECRET:
            return False
        bearer = request.headers.get("authorization")
        return not (bearer and self.bearer_secret_refused)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path == "/auth/v1/settings":
            return httpx.Response(
                200, json={"disable_signup": self.signup_off, "external": {"email": True}}
            )
        if path == "/auth/v1/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": self.keys})
        if path.startswith("/rest/v1/"):
            table = path.rsplit("/", 1)[1]
            if table in self.rest_open:
                return httpx.Response(200, json=[])
            return httpx.Response(404, json={"code": "PGRST205"})
        if path == "/graphql/v1":
            if not self.graphql_on:
                return httpx.Response(200, json={"errors": [{"message": "not enabled"}]})
            fields = [{"name": "node"}] + [{"name": f"{t}Collection"} for t in self.graphql_tables]
            return httpx.Response(
                200, json={"data": {"__schema": {"queryType": {"fields": fields}}}}
            )
        if path.startswith("/auth/v1/admin/users") or path == "/storage/v1/bucket":
            if not self._server_ok(request):
                return httpx.Response(401, json={"message": "Invalid JWT"})
        if path == "/auth/v1/admin/users" and request.method == "GET":
            return httpx.Response(200, json={"users": []})
        if path == "/auth/v1/admin/users" and request.method == "POST":
            body = json.loads(request.content)
            self.users["u1"] = body
            return httpx.Response(200, json={"id": "u1"})
        if path.startswith("/auth/v1/admin/users/") and request.method == "DELETE":
            if self.delete_fails:
                return httpx.Response(500)
            self.users.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(200, json={})
        if path == "/storage/v1/bucket":
            return httpx.Response(
                200, json=[{"id": b, "public": public} for b, public in self.buckets.items()]
            )
        if path == "/auth/v1/token":
            claims = {"sub": "u1", "aud": "authenticated", "exp": int(time.time()) + 600}
            if self.token_alg == "HS256":
                token = jwt.encode(claims, "legacy-shared-secret-of-enough-len", "HS256")
            else:
                token = jwt.encode(claims, self.token_key, "ES256", headers={"kid": self.token_kid})
            self.last_token = token
            return httpx.Response(200, json={"access_token": token})
        return httpx.Response(404)


def _run(fake: FakeProject, capsys, sign_in: bool = True) -> tuple[int, str]:
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        code = chp.run(chp.Project(URL, PUBLISHABLE, SECRET, client), sign_in)
    out = capsys.readouterr().out
    assert PUBLISHABLE not in out and SECRET not in out
    assert getattr(fake, "last_token", "<no token>") not in out
    for user in fake.users.values():
        assert user["password"] not in out
    return code, out


def test_a_correct_project_passes_every_check(capsys) -> None:
    fake = FakeProject()
    code, out = _run(fake, capsys)
    assert code == 0 and out.count("ok   ") == 5 and "FAIL" not in out
    assert fake.users == {}  # the temporary user is gone


def test_the_secret_key_is_sent_on_apikey_only(capsys) -> None:
    fake = FakeProject(bearer_secret_refused=True)
    code, _ = _run(fake, capsys)
    assert code == 0
    server_calls = [r for r in fake.seen if r.headers.get("apikey") == SECRET]
    assert server_calls and not any("authorization" in r.headers for r in server_calls)


@pytest.mark.parametrize(
    ("fake", "reason"),
    [
        (FakeProject(signup_off=False), "public sign-up is ON"),
        (FakeProject(keys=[{"kty": "oct", "kid": "legacy"}]), "no ECC P-256 key"),
        (FakeProject(rest_open={"orders"}), "REST serves orders"),
        (FakeProject(graphql_tables=["products", "tenants"]), "GraphQL exposes 2 tables"),
        (FakeProject(secret_refused=True), "Auth admin refused the secret key: HTTP 401"),
        (FakeProject(buckets={}), "bucket product-images missing"),
        (
            FakeProject(buckets={**chp.BUCKETS, "expense-receipts": True}),
            "bucket expense-receipts should be private",
        ),
        # Migrated but not rotated: the ECC key is published (standby), tokens are still HS256.
        (FakeProject(token_alg="HS256"), "signed with HS256, not ES256"),
        (FakeProject(token_kid="unknown"), "not among the published keys"),
        (FakeProject(token_key=OTHER_EC_KEY), "does not verify"),
        (FakeProject(delete_fails=True), "was not deleted"),
    ],
)
def test_each_misconfiguration_fails_with_its_reason(fake: FakeProject, reason: str, capsys):
    code, out = _run(fake, capsys)
    assert code == 1
    assert reason in out


def test_a_signed_in_user_must_not_read_tables_either(capsys) -> None:
    class OpenToUsers(FakeProject):
        def __call__(self, request):
            if request.url.path.startswith("/rest/v1/") and request.headers.get("authorization"):
                self.seen.append(request)
                return httpx.Response(200, json=[])
            return super().__call__(request)

    code, out = _run(OpenToUsers(), capsys)
    assert code == 1
    assert "FAIL Sign-in" in out and "REST serves products" in out
    assert "ok   Data API closed to the browser key" in out


def test_the_temporary_user_is_deleted_even_when_sign_in_fails(capsys) -> None:
    class NoSignIn(FakeProject):
        def __call__(self, request):
            if request.url.path == "/auth/v1/token":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return super().__call__(request)

    fake = NoSignIn()
    code, out = _run(fake, capsys)
    assert code == 1 and "could not sign in: HTTP 400" in out
    assert fake.users == {}


def test_without_sign_in_nothing_is_written(capsys) -> None:
    fake = FakeProject()
    code, out = _run(fake, capsys, sign_in=False)
    assert code == 0 and out.count("ok   ") == 4
    assert all(r.method in ("GET", "POST") for r in fake.seen)
    writes = [r for r in fake.seen if r.method == "POST" and r.url.path != "/graphql/v1"]
    assert writes == []


def test_keys_come_from_hidden_prompts_and_wrong_kinds_are_refused(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    asked: list[str] = []

    def swapped(prompt: str) -> str:
        asked.append(prompt)
        return SECRET  # the secret key typed where the publishable one was asked for

    with pytest.raises(SystemExit) as stopped:
        chp.main(["--url", URL], ask=swapped)
    assert asked == ["Publishable key (hidden): "]
    assert SECRET not in str(stopped.value) and "sb_publishable_" in str(stopped.value)


@pytest.mark.parametrize(
    "url", ["http://abcdefghij.supabase.co", "http://127.0.0.1:55321", "http://10.0.0.5:8000"]
)
def test_only_https_unless_local_is_allowed_for_the_tests(url: str) -> None:
    with pytest.raises(SystemExit):
        chp.main(["--url", url], ask=lambda _: "")


def test_allow_local_accepts_only_the_loopback(monkeypatch) -> None:
    with pytest.raises(SystemExit) as stopped:
        chp.main(["--url", "http://10.0.0.5:8000", "--allow-local"], ask=lambda _: "")
    assert stopped.value.code == 2  # argparse error, before any key is asked for
