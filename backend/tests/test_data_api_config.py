"""The Supabase Data API must not expose application schemas (no database needed).

`public` must be absent from both the exposed schemas (REST) and the extra search path:
pg_graphql reflects every schema on the search path, so leaving `public` there would expose
public tables through /graphql/v1 even when REST doesn't serve them.
"""

import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "supabase" / "config.toml"


def test_public_and_ai_are_not_exposed() -> None:
    api = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["api"]
    assert "public" not in api["schemas"]
    assert "ai" not in api["schemas"]
    assert "public" not in api.get("extra_search_path", [])
