"""Write the FastAPI OpenAPI schema to packages/api-client/openapi.json (no server needed).

    python backend/scripts/dump_openapi.py

The frontend generates its typed client from this file, and CI regenerates it and fails on
any diff, so commit it together with the API change that produced it.
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402

TARGET = BACKEND.parent / "packages" / "api-client" / "openapi.json"


def main() -> int:
    text = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    # newline="\n" keeps the file byte-identical on Windows and Linux.
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
