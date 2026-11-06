"""The API's custom domain (infra/bicep, hosted-demo plan step 7): a redeploy never declares the
live TLS binding without TLS. The binding without TLS exists only for the first bind
(apiHostNameFirstBind, off unless DELEA_API_HOST_FIRST_BIND=true); otherwise every redeploy would
drop TLS on api.deleajewelry.com until the TLS step re-enabled it. Reads the Bicep files as
text: no Azure, no Bicep CLI (CI's Infrastructure job builds and lints them)."""

import re
from pathlib import Path

import pytest

BICEP = Path(__file__).resolve().parents[2] / "infra" / "bicep"
APP_SERVICE = (BICEP / "modules" / "appService.bicep").read_text(encoding="utf-8")
MAIN = (BICEP / "main.bicep").read_text(encoding="utf-8")
PARAMETER_FILES = sorted((BICEP / "parameters").glob("*.bicepparam"))


def _host_name_bindings() -> list[tuple[str, str]]:
    """(condition, body) of every hostNameBindings resource in the apps module."""
    pattern = re.compile(
        r"resource \w+ 'Microsoft\.Web/sites/hostNameBindings@[^']+' = "
        r"(?:if \((.*?)\) )?\{(.*?)\n\}",
        re.S,
    )
    return [(m.group(1) or "", m.group(2)) for m in pattern.finditer(APP_SERVICE)]


def test_the_binding_without_tls_is_declared_only_on_the_first_bind() -> None:
    without_tls = [
        condition
        for condition, body in _host_name_bindings()
        if re.search(r"sslState: 'Disabled'", body)
    ]
    assert len(without_tls) == 1, "expected exactly one binding declared without TLS"
    assert re.fullmatch(r"!empty\(apiHostName\) && apiHostNameFirstBind", without_tls[0])


def test_the_first_bind_is_off_unless_asked_for() -> None:
    assert re.search(r"^param apiHostNameFirstBind bool = false$", APP_SERVICE, re.M)
    assert re.search(r"^param apiHostNameFirstBind bool = false$", MAIN, re.M)
    assert re.search(r"^\s+apiHostNameFirstBind: apiHostNameFirstBind$", MAIN, re.M)


@pytest.mark.parametrize("path", PARAMETER_FILES, ids=lambda p: p.name)
def test_every_parameter_file_reads_the_first_bind_with_a_false_default(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert (
        "param apiHostNameFirstBind = "
        "bool(readEnvironmentVariable('DELEA_API_HOST_FIRST_BIND', 'false'))"
    ) in text


def test_there_are_parameter_files() -> None:
    assert {p.name for p in PARAMETER_FILES} >= {
        "demo.bicepparam",
        "shared.bicepparam",
        "enterprise.template.bicepparam",
    }
