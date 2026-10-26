"""Loads scripts/add_tenant_domain.py as a module (scripts/ is not a package)."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "add_tenant_domain.py"
_spec = importlib.util.spec_from_file_location("add_tenant_domain_it", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)
