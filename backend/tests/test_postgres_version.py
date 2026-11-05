"""The local stack (and CI, which starts it from the same config) runs the Postgres major
version the hosted projects run, so migrations and tests are proven on what production runs
(hosted-demo plan step 5). tests/integration/test_postgres_version.py checks the running stack."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOSTED_MAJOR_VERSION = 17  # new Supabase projects; check the project's Infrastructure page


def test_the_local_stack_is_configured_for_the_hosted_major_version() -> None:
    config = tomllib.loads((ROOT / "supabase" / "config.toml").read_text(encoding="utf-8"))
    assert config["db"]["major_version"] == HOSTED_MAJOR_VERSION
