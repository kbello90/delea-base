"""How the API sends its server key to Supabase (the Auth admin API and Storage).

Supabase has two kinds of server key:
- the new secret key, "sb_secret_...": not a JWT. Supabase takes it on the `apikey` header only
  ("Send publishable and secret keys on the apikey header, not on Authorization: Bearer",
  https://supabase.com/docs/guides/api/api-keys); its gateway then acts as service_role.
- the legacy service_role key: a JWT, sent on both headers. The local stack still accepts it.
  Supabase retires the legacy keys by the end of 2026; hosted projects use the secret key.

A publishable key ("sb_publishable_...") is never a server key: it is public and grants nothing.
"""

SECRET_KEY_PREFIX = "sb_secret_"
PUBLISHABLE_KEY_PREFIX = "sb_publishable_"


def server_key_headers(key: str) -> dict[str, str]:
    """The headers that authenticate the API to Auth admin and Storage with its server key."""
    if key.startswith(PUBLISHABLE_KEY_PREFIX):
        raise ValueError("a publishable key is not a server key")
    if key.startswith(SECRET_KEY_PREFIX):
        return {"apikey": key}
    return {"apikey": key, "Authorization": f"Bearer {key}"}
