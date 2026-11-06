"""The hosted apps' settings (infra/bicep, hosted-demo plan step 3) and the code's settings
(app/core/config.py) can't drift apart: every setting the code reads is set in Bicep for the
API or the worker, or is listed below with the reason it isn't; every secret is a Key Vault
reference, never a template parameter; the hosted ENVIRONMENT can't be local or test; the
proxy list has no default. Reads the Bicep files as text: no Azure, no Bicep CLI."""

import re
from pathlib import Path

from app.core.config import Settings

BICEP = Path(__file__).resolve().parents[2] / "infra" / "bicep"
APP_SERVICE = (BICEP / "modules" / "appService.bicep").read_text(encoding="utf-8")
MAIN = (BICEP / "main.bicep").read_text(encoding="utf-8")

# Settings the hosted apps deliberately don't set, and why.
NOT_HOSTED = {
    "ADMIN_DATABASE_URL": "scripts and integration-test setup only; the API never uses it",
    "MAILPIT_URL": "the local mail catcher; refused outside local/test",
    "LLM_API_KEY": "read by nothing yet",
    "JWT_AUDIENCE": "the default, authenticated, is what Supabase issues",
    "PRODUCT_IMAGES_BASE_URL": "derived from SUPABASE_URL; set only in front of a CDN",
    "BRAND_ASSETS_BASE_URL": "derived from SUPABASE_URL; set only in front of a CDN",
}
# Read by the platform or by uvicorn, not by Settings.
PLATFORM = {"WEBSITES_PORT"}
SECRETS = {
    "SUPABASE_SERVICE_ROLE_KEY",
    "DATABASE_URL",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "RESEND_API_KEY",
    "RESEND_WEBHOOK_SECRET",
    "TURNSTILE_SECRET_KEY",
    "SEND_EMAIL_HOOK_SECRET",
}


def _settings(var: str) -> dict[str, str]:
    block = re.search(rf"var {var} = (?:emailHookOn \? )?\[\n(.*?)\n\]", APP_SERVICE, re.S)
    assert block, f"{var} not found in appService.bicep"
    entries = re.findall(r"\{ name: '([A-Z_]+)', value: (.+?) \}$", block.group(1), re.M)
    assert len(entries) == len(block.group(1).strip().splitlines()), f"unparsed line in {var}"
    names = [name for name, _ in entries]
    assert len(names) == len(set(names)), f"duplicate setting in {var}"
    return dict(entries)


# The Send Email Hook's secret joins the API's settings only when emailHookOn is true (it can
# only exist after the hook is configured in Supabase; an unresolved reference stops the API).
HOOK = _settings("emailHookSettings")
API = {**_settings("apiSettings"), **HOOK}
WORKER = _settings("workerSettings")
CODE = {name.upper() for name in Settings.model_fields}


def test_every_setting_the_code_reads_is_hosted_or_explained() -> None:
    hosted = set(API) | set(WORKER)
    missing = CODE - hosted - set(NOT_HOSTED)
    assert not missing, (
        f"set these in infra/bicep/modules/appService.bicep or NOT_HOSTED: {missing}"
    )
    unknown = hosted - CODE - PLATFORM
    assert not unknown, f"Bicep sets settings the code doesn't read: {unknown}"
    assert set(NOT_HOSTED) <= CODE, "NOT_HOSTED lists a setting the code no longer has"
    assert not hosted & set(NOT_HOSTED), "a setting is both hosted and listed as not hosted"


def test_secrets_are_key_vault_references_only() -> None:
    for app, settings in (("api", API), ("worker", WORKER)):
        for name, value in settings.items():
            uses_vault = value.startswith("kvRef(vaultName, '")
            assert uses_vault == (name in SECRETS), f"{app}: {name} = {value}"
    assert SECRETS <= set(API)
    # Secrets never pass through a template parameter (they would show in the deployment).
    for path in BICEP.rglob("*.bicep*"):
        text = path.read_text(encoding="utf-8")
        assert "@secure()" not in text, path.name
        for name in SECRETS:
            assert f"readEnvironmentVariable('{name}'" not in text, (path.name, name)


def test_the_worker_runs_the_worker_and_answers_health() -> None:
    assert "appCommandLine: 'python -m app.worker'" in APP_SERVICE
    assert WORKER["WORKER_HEALTH_PORT"] == WORKER["WEBSITES_PORT"] == "'8000'"
    assert "WORKER_HEALTH_PORT" not in API
    for name in ("DATABASE_URL", "ENVIRONMENT", "EMAIL_PROVIDER", "RESEND_API_KEY"):
        assert name in WORKER, name
    # The worker holds no key it doesn't use.
    assert not {
        "STRIPE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "TURNSTILE_SECRET_KEY",
        "SEND_EMAIL_HOOK_SECRET",
    } & set(WORKER)


def test_the_hosted_environment_is_never_a_development_one() -> None:
    allowed = re.search(r"@allowed\(\[\n((?:\s+'[a-z]+'\n)+)\]\)\nparam environment string", MAIN)
    assert allowed, "param environment must have an @allowed list"
    values = set(re.findall(r"'([a-z]+)'", allowed.group(1)))
    assert values == {"demo", "production"}
    assert API["ENVIRONMENT"] == WORKER["ENVIRONMENT"] == "environment"
    assert API["EMAIL_PROVIDER"] == "'resend'" and API["STRIPE_GATEWAY"] == "'stripe'"


def test_the_proxy_list_must_be_given_and_is_never_a_wildcard() -> None:
    assert re.search(r"^param forwardedAllowIps string$", MAIN, re.M), "no default allowed"
    for path in (BICEP / "parameters").glob("*.bicepparam"):
        line = re.search(r"^param forwardedAllowIps = (.+)$", path.read_text(), re.M)
        assert line and "*" not in line.group(1), path.name
    assert API["FORWARDED_ALLOW_IPS"] == "forwardedAllowIps"


def test_both_apps_may_pull_the_image_and_read_the_secrets() -> None:
    """Without AcrPull the app can't start (no image); without Key Vault Secrets User every
    secret stays an unresolved reference and the app refuses to start. Both roles, for both."""
    names = re.search(r"var appNames = \[\n(.*?)\n\]", MAIN, re.S)
    assert names and re.findall(r"'\$\{namePrefix\}-(\w+)'", names.group(1)) == ["api", "worker"]
    for role, scope in (("AcrPull", "registry"), ("KeyVaultSecretsUser", "vault")):
        block = re.search(
            rf"resource \w+ 'Microsoft.Authorization/roleAssignments@[\d-]+' = \[\n"
            rf"  for \(app, i\) in appNames: if \(deployApps\) \{{\n"
            rf"    name: guid\({scope}\.id, app, '{role}'\)\n"
            rf"    scope: {scope}\n",
            MAIN,
        )
        assert block, f"{role} on the {scope} for every app"
    assert "principalId: deployApps ? apps!.outputs.principalIds[i] : ''" in MAIN
    assert "output principalIds array = [\n  api.identity.principalId\n  worker.identity" in (
        APP_SERVICE
    )
    assert APP_SERVICE.count("acrUseManagedIdentityCreds: true") == 1  # in the shared siteConfig
    assert APP_SERVICE.count("keyVaultReferenceIdentity: 'SystemAssigned'") == 2


def test_the_email_hook_secret_is_api_only_and_added_only_when_the_hook_is_on() -> None:
    assert HOOK == {"SEND_EMAIL_HOOK_SECRET": "kvRef(vaultName, 'send-email-hook-secret')"}
    assert "var emailHookSettings = emailHookOn ? [" in APP_SERVICE and "] : []" in APP_SERVICE
    assert "appSettings: concat(apiSettings, emailHookSettings)" in APP_SERVICE
    assert "SEND_EMAIL_HOOK_SECRET" not in WORKER
    assert re.search(r"^param emailHookOn bool = false$", MAIN, re.M), "off unless asked"
    switch = "param emailHookOn = bool(readEnvironmentVariable('DELEA_EMAIL_HOOK', 'false'))"
    for path in (BICEP / "parameters").glob("*.bicepparam"):
        assert switch in path.read_text(encoding="utf-8"), path.name


# The Supabase session pooler holds one server connection per client connection, and the
# demo project's pool is 15 (Micro compute). Both apps' pools together stay at most 7, so an
# old and a new instance of each fit side by side during a restart or a deploy.
SESSION_POOLER_POOL_SIZE = 15
MAX_CONNECTIONS_BOTH_APPS = 7


def test_the_two_apps_pools_fit_the_session_pooler_twice() -> None:
    for app, settings in (("api", API), ("worker", WORKER)):
        assert settings["DB_POOL_MIN_SIZE"] == "'1'", f"{app}: DB_POOL_MIN_SIZE"
    total = int(API["DB_POOL_MAX_SIZE"].strip("'")) + int(WORKER["DB_POOL_MAX_SIZE"].strip("'"))
    assert total <= MAX_CONNECTIONS_BOTH_APPS, f"the two pools' maximums total {total}"
    assert 2 * MAX_CONNECTIONS_BOTH_APPS <= SESSION_POOLER_POOL_SIZE
