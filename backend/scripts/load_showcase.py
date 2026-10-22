"""Load (or reload) the DELEA Showcase demo tenant. See showcase/README.md.

    SHOWCASE_DIR="C:/path/to/photos" python backend/scripts/load_showcase.py [--dry-run]

Local by default: any non-local database or Storage URL is refused unless --allow-remote is
given (and then SHOWCASE_DATABASE_URL and SHOWCASE_PASSWORD are required). Run it from a
workstation on the loader's own administrative connection, never from the deployed API.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from showcase.catalog import CatalogError  # noqa: E402
from showcase.config import ConfigRefused, build_config  # noqa: E402
from showcase.load import load_showcase, prepare  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-local database or Storage target (hosted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate products.yaml and process every photo; write nothing",
    )
    args = parser.parse_args(argv)

    try:
        config = build_config(os.environ, get_settings(), allow_remote=args.allow_remote)
        if args.dry_run:
            entries, processed = prepare(config)
            print(f"OK: {len(entries)} products, {len(processed)} images; nothing was written")
            return 0
        report = _run(load_showcase(config))
    except (ConfigRefused, CatalogError) as exc:
        print(exc, file=sys.stderr)
        return 2

    print(
        f"Showcase loaded (tenant {report.tenant_id}): {report.products} products, "
        f"{report.images} images, {report.materials} materials, {report.customers} customers, "
        f"{report.invoices} invoices, {report.expenses} expenses; "
        f"{report.removed_objects} old Storage objects removed."
    )
    print("Sign in as: " + ", ".join(f"{email} ({role})" for email, role in config.members))
    return 0


def _run(coro):
    # psycopg's async mode needs a selector event loop (Windows defaults to Proactor).
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


if __name__ == "__main__":
    sys.exit(main())
