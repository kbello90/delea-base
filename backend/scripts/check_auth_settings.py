"""Check a Supabase project's public Auth settings: public sign-up off, email sign-in on.

    python backend/scripts/check_auth_settings.py \
        --url https://<ref>.supabase.co --key <publishable key>

Run it against every hosted project (production and the public demo) after any Auth change,
and before a release. --url can be repeated, with one --key per URL in the same order. The
publishable (anon) key is public; no secret is needed. GET /auth/v1/settings doesn't expose
the minimum password length or the SMTP setup: check those in the dashboard
(infra/scripts/provision-enterprise.md).

Exit code 0 when every project passes, 1 otherwise. The local stack is checked by the backend
tests (tests/test_auth_config.py, tests/integration/test_auth_settings.py).
"""

import argparse
import sys
from typing import Any

import httpx


def settings_problems(settings: dict[str, Any]) -> list[str]:
    """What is wrong with a project's /auth/v1/settings answer (empty: nothing)."""
    problems = []
    if settings.get("disable_signup") is not True:
        problems.append("public sign-up is ON: anyone with the browser key can create an account")
    if (settings.get("external") or {}).get("email") is not True:
        problems.append("the email provider is OFF: password sign-in, invites and resets fail")
    return problems


def fetch_settings(url: str, key: str) -> dict[str, Any]:
    response = httpx.get(f"{url.rstrip('/')}/auth/v1/settings", headers={"apikey": key}, timeout=15)
    response.raise_for_status()
    return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", action="append", required=True, help="project URL")
    parser.add_argument("--key", action="append", required=True, help="publishable key")
    args = parser.parse_args(argv)
    if len(args.url) != len(args.key):
        parser.error("give one --key per --url")

    failed = False
    for url, key in zip(args.url, args.key, strict=True):
        try:
            problems = settings_problems(fetch_settings(url, key))
        except httpx.HTTPError as exc:
            problems = [f"could not read the settings: {exc.__class__.__name__}"]
        if problems:
            failed = True
            print(f"FAIL {url}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {url}: sign-up off, email sign-in on")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
