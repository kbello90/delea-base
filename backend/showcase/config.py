"""The loader's configuration from the environment, with its safety rules.

- SHOWCASE_DIR (required): the photo folder. Read only; nothing in it is copied anywhere
  except the processed WebP variants uploaded to Storage.
- Targets: the loader's database (SHOWCASE_DATABASE_URL) and Supabase (SUPABASE_URL, used for
  Storage and Auth). Any host other than this machine is refused unless allow_remote is set.
- On a non-local target: SHOWCASE_DATABASE_URL must be set explicitly (the loader never
  falls back to the API's DATABASE_URL there) and SHOWCASE_PASSWORD is required.
- Locally: the database defaults to DATABASE_URL and the password to the local dev password.
- SHOWCASE_OWNER_EMAIL / SHOWCASE_STAFF_EMAIL / SHOWCASE_CPA_EMAIL: the member logins.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import Settings
from showcase.load import ShowcaseConfig

LOCAL_DEV_PASSWORD = "delea-local-dev"  # same as scripts/create_dev_users.py; local only
DEFAULT_EMAILS = {
    "owner": "owner@showcase.delea.test",
    "staff": "staff@showcase.delea.test",
    "cpa": "cpa@showcase.delea.test",
}
REPO = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = REPO / "showcase" / "products.yaml"


class ConfigRefused(RuntimeError):
    """The configuration is missing something or would target a place it must not."""


def is_local_url(url: str) -> bool:
    """True when the URL's host is this machine (the local Supabase stack)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def build_config(
    env: Mapping[str, str],
    settings: Settings,
    *,
    allow_remote: bool,
    catalog_path: Path = DEFAULT_CATALOG,
    now: datetime | None = None,
) -> ShowcaseConfig:
    photos = env.get("SHOWCASE_DIR", "").strip()
    if not photos:
        raise ConfigRefused("set SHOWCASE_DIR to the folder with the product photo folders")
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ConfigRefused("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

    database_url = env.get("SHOWCASE_DATABASE_URL", "").strip()
    local = is_local_url(settings.supabase_url) and is_local_url(
        database_url or settings.database_url
    )
    if not local:
        if not allow_remote:
            targets = (settings.supabase_url, database_url or settings.database_url)
            hosts = sorted({str(urlsplit(u).hostname) for u in targets if not is_local_url(u)})
            raise ConfigRefused(
                f"refusing a non-local database or Storage target ({', '.join(hosts)}); "
                "pass --allow-remote to load a hosted showcase"
            )
        if not database_url:
            raise ConfigRefused(
                "on a non-local target, set SHOWCASE_DATABASE_URL to the loader's own "
                "administrative connection (the API's DATABASE_URL is never used there)"
            )
    database_url = database_url or settings.database_url
    if not database_url:
        raise ConfigRefused("set SHOWCASE_DATABASE_URL (or DATABASE_URL locally)")

    password = env.get("SHOWCASE_PASSWORD", "")
    if not password:
        if not local:
            raise ConfigRefused(
                "on a non-local target, set SHOWCASE_PASSWORD (it is never printed)"
            )
        password = LOCAL_DEV_PASSWORD

    members = tuple(
        (env.get(f"SHOWCASE_{role.upper()}_EMAIL", "").strip() or default, role)
        for role, default in DEFAULT_EMAILS.items()
    )
    if len({email.lower() for email, _ in members}) != len(members):
        raise ConfigRefused("the owner, staff and cpa emails must be different")

    now = now or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return ShowcaseConfig(
        database_url=database_url,
        settings=settings,
        catalog_path=catalog_path,
        photos_dir=Path(photos),
        members=members,
        password=password,
        now=now,
    )
