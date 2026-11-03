"""Write backend/.env for the LOCAL Supabase stack from `supabase status -o env` on stdin.

    npx supabase@2.121.0 status -o env | python backend/scripts/write_local_env.py

Only for the local stack: its keys are the well-known local demo keys. Never point this at a
hosted project. backend/.env is gitignored.

DATABASE_URL is the API's least-privilege login, delea_api (TRD §6.6), with the public local
password supabase/seed.sql gives it; ADMIN_DATABASE_URL is the stack's postgres login, for the
local scripts and the integration tests' setup.
"""

import re
import sys
from pathlib import Path

MAPPING = {
    "SUPABASE_URL": "API_URL",
    "SUPABASE_ANON_KEY": "ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY": "SERVICE_ROLE_KEY",
    "ADMIN_DATABASE_URL": "DB_URL",
}
LOCAL_API_PASSWORD = "delea-api-local"  # supabase/seed.sql; local stack only
LOCAL_HOSTS = (
    "http://127.0.0.1:",
    "http://localhost:",
    "postgresql://postgres:postgres@127.0.0.1:",
)


def main() -> int:
    status = dict(re.findall(r'^([A-Z0-9_]+)="?(.*?)"?\s*$', sys.stdin.read(), re.MULTILINE))
    missing = [source for source in MAPPING.values() if not status.get(source)]
    if missing:
        print(f"supabase status is missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    for target in ("SUPABASE_URL", "ADMIN_DATABASE_URL"):
        if not status[MAPPING[target]].startswith(LOCAL_HOSTS):
            print(f"{target} is not a local stack URL; refusing to write it", file=sys.stderr)
            return 1

    lines = ["# Local stack only (written by scripts/write_local_env.py). Gitignored."]
    lines += [f"{target}={status[source]}" for target, source in MAPPING.items()]
    admin = status[MAPPING["ADMIN_DATABASE_URL"]]
    api = admin.replace("postgres:postgres@", f"delea_api:{LOCAL_API_PASSWORD}@", 1)
    lines.append(f"DATABASE_URL={api}")
    path = Path(__file__).resolve().parent.parent / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(MAPPING) + 1} variables to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
