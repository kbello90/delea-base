"""Check a hosted Supabase project before the API is pointed at it (hosted-demo plan step 5).

    python backend/scripts/check_hosted_project.py --url https://<ref>.supabase.co [--sign-in]

It asks for the project's publishable key and secret key with hidden input (or reads
SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY from the environment). It prints only "ok" or
"FAIL" lines: never a key, a token or a password.

Checks:
- Auth settings: public sign-up off, email sign-in on (as check_auth_settings.py).
- Signing key: the project publishes an ES256 (ECC P-256) key; the API accepts no other.
- Data API closed: the REST API serves no table, and GraphQL exposes no table, to the browser key.
- Server key: Auth admin and Storage accept the secret key sent the way the API sends it
  (app/core/supabase_keys.py), and the four buckets exist with the right public/private setting.
- With --sign-in: creates a temporary confirmed user (no email is sent), signs in, and checks
  that the access token is signed with ES256 and verifies against the published keys (a key
  that is only on standby doesn't pass), and that the signed-in user gets no table data from
  REST or GraphQL. The user is deleted at the end, also when a check fails. That user is the
  only thing it writes; without --sign-in it only reads.

Exit code 0 when everything passes, 1 otherwise. --allow-local accepts the local stack
(http://127.0.0.1), for the tests.
"""

import argparse
import getpass
import ipaddress
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.supabase_keys import (  # noqa: E402
    PUBLISHABLE_KEY_PREFIX,
    SECRET_KEY_PREFIX,
    server_key_headers,
)
from scripts.check_auth_settings import settings_problems  # noqa: E402

# Created by the migrations; True = public.
BUCKETS = {
    "product-uploads": False,
    "product-images": True,
    "expense-receipts": False,
    "brand-assets": True,
}
# Tables the REST probe asks for: the catalogue and the most sensitive ones.
REST_PROBES = ("products", "tenants", "orders", "customers")
GRAPHQL_FIELDS = "{ __schema { queryType { fields { name } } } }"

Check = tuple[str, list[str]]


class Project:
    def __init__(self, url: str, publishable: str, secret: str, client: httpx.Client) -> None:
        self.url = url.rstrip("/")
        self.publishable = publishable
        self.secret = secret
        self.http = client

    def public(self, token: str | None = None) -> dict[str, str]:
        """Headers of a browser: the publishable key, and a user's token when signed in."""
        headers = {"apikey": self.publishable}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def server(self) -> dict[str, str]:
        return server_key_headers(self.secret)


def check_auth_settings(p: Project) -> Check:
    r = p.http.get(f"{p.url}/auth/v1/settings", headers=p.public())
    if r.status_code != 200:
        return "Auth settings", [f"could not read them: HTTP {r.status_code}"]
    return "Auth settings: sign-up off, email sign-in on", settings_problems(r.json())


def _jwks(p: Project) -> list[dict]:
    r = p.http.get(f"{p.url}/auth/v1/.well-known/jwks.json")
    if r.status_code != 200:
        return []
    return r.json().get("keys", [])


def check_signing_key(p: Project) -> Check:
    name = "Signing key: ES256 (ECC P-256) published"
    keys = _jwks(p)
    if not any(k.get("kty") == "EC" and k.get("crv") == "P-256" for k in keys):
        return name, [
            "no ECC P-256 key: the API only accepts ES256 tokens, so every sign-in would fail "
            "(Settings > JWT Signing Keys)"
        ]
    return name, []


def data_api_problems(p: Project, token: str | None = None) -> list[str]:
    problems = []
    for table in REST_PROBES:
        r = p.http.get(
            f"{p.url}/rest/v1/{table}",
            params={"select": "*", "limit": "1"},
            headers=p.public(token),
        )
        if r.status_code == 200:
            problems.append(f"REST serves {table}: remove public from the exposed schemas")
    r = p.http.post(f"{p.url}/graphql/v1", json={"query": GRAPHQL_FIELDS}, headers=p.public(token))
    if r.status_code == 200:
        query_type = (((r.json().get("data") or {}).get("__schema") or {}).get("queryType")) or {}
        tables = sorted(
            f["name"]
            for f in query_type.get("fields") or []
            if f.get("name", "").endswith("Collection")
        )
        if tables:
            problems.append(
                f"GraphQL exposes {len(tables)} tables (e.g. {tables[0]}): remove public from "
                "the exposed schemas and the extra search path"
            )
    return problems


def check_data_api(p: Project) -> Check:
    return "Data API closed to the browser key", data_api_problems(p)


def check_server_key(p: Project) -> Check:
    name = "Server key: Auth admin and Storage accept it as the API sends it; buckets in place"
    r = p.http.get(f"{p.url}/auth/v1/admin/users", params={"per_page": "1"}, headers=p.server())
    if r.status_code != 200:
        return name, [f"Auth admin refused the secret key: HTTP {r.status_code}"]
    r = p.http.get(f"{p.url}/storage/v1/bucket", headers=p.server())
    if r.status_code != 200:
        return name, [f"Storage refused the secret key: HTTP {r.status_code}"]
    found = {b.get("id"): b.get("public") for b in r.json()}
    problems = []
    for bucket, public in BUCKETS.items():
        if bucket not in found:
            problems.append(f"bucket {bucket} missing (are the migrations pushed?)")
        elif found[bucket] is not public:
            problems.append(f"bucket {bucket} should be {'public' if public else 'private'}")
    return name, problems


def check_sign_in(p: Project) -> Check:
    name = "Sign-in: ES256 token that verifies; no table data for a signed-in user"
    email = f"delea-check-{secrets.token_hex(6)}@deleajewelry.com"
    password = secrets.token_urlsafe(24)
    r = p.http.post(
        f"{p.url}/auth/v1/admin/users",
        json={"email": email, "password": password, "email_confirm": True},
        headers=p.server(),
    )
    if r.status_code not in (200, 201):
        return name, [f"could not create the temporary user: HTTP {r.status_code}"]
    user_id = r.json()["id"]
    try:
        problems = _signed_in_problems(p, email, password)
    finally:
        r = p.http.delete(f"{p.url}/auth/v1/admin/users/{user_id}", headers=p.server())
        if r.status_code not in (200, 204):
            print(f"FAIL could not delete the temporary user {email}: delete it in the dashboard")
    if r.status_code not in (200, 204):
        problems.append(f"the temporary user {email} was not deleted")
    return name, problems


def _signed_in_problems(p: Project, email: str, password: str) -> list[str]:
    r = p.http.post(
        f"{p.url}/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers=p.public(),
    )
    if r.status_code != 200:
        return [f"the temporary user could not sign in: HTTP {r.status_code}"]
    token = r.json()["access_token"]
    return token_problems(token, _jwks(p)) + data_api_problems(p, token)


def token_problems(token: str, keys: list[dict]) -> list[str]:
    """The API's rule (app/core/security.py): ES256, verified against the published keys."""
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "ES256":
        return [
            f"tokens are signed with {header.get('alg')}, not ES256: rotate to the ECC key "
            "(Settings > JWT Signing Keys > Rotate keys)"
        ]
    key = next((k for k in keys if k.get("kid") == header.get("kid")), None)
    if key is None:
        return ["the token's signing key is not among the published keys"]
    try:
        jwt.decode(
            token,
            jwt.PyJWK(key).key,
            algorithms=["ES256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        return [f"the token does not verify: {exc.__class__.__name__}"]
    return []


def _is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def read_key(env: str, prompt: str, prefix: str, ask: Callable[[str], str]) -> str:
    value = os.environ.get(env) or ask(prompt)
    value = value.strip()
    if not value.startswith(prefix):
        raise SystemExit(f"that is not a {prompt.split(' (')[0].lower()} (it must start {prefix})")
    return value


def run(project: Project, sign_in: bool) -> int:
    checks = [check_auth_settings, check_signing_key, check_data_api, check_server_key]
    if sign_in:
        checks.append(check_sign_in)
    failed = False
    for check in checks:
        try:
            name, problems = check(project)
        except httpx.HTTPError as exc:
            name, problems = check.__name__, [f"request failed: {exc.__class__.__name__}"]
        if problems:
            failed = True
            print(f"FAIL {name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {name}")
    return 1 if failed else 0


def main(argv: list[str] | None = None, ask: Callable[[str], str] = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="https://<ref>.supabase.co")
    parser.add_argument("--sign-in", action="store_true", help="also check a real sign-in")
    parser.add_argument("--allow-local", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    local = args.allow_local and _is_loopback(args.url)
    if urlsplit(args.url).scheme != "https" and not local:
        parser.error("--url must be the project's https:// address")
    publishable = read_key(
        "SUPABASE_PUBLISHABLE_KEY", "Publishable key (hidden): ", PUBLISHABLE_KEY_PREFIX, ask
    )
    secret = read_key("SUPABASE_SECRET_KEY", "Secret key (hidden): ", SECRET_KEY_PREFIX, ask)
    with httpx.Client(timeout=20) as client:
        return run(Project(args.url, publishable, secret, client), args.sign_in)


if __name__ == "__main__":
    sys.exit(main())
