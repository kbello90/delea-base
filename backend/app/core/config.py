"""Environment settings.

Every tier reads the same variables; Enterprise deployments differ only in their values
(TRD §2.3). There is no tier branching in code.
"""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.supabase_keys import PUBLISHABLE_KEY_PREFIX, SECRET_KEY_PREFIX

# Environments where development shortcuts are allowed: *.localhost storefront hosts and plain
# http:// storefront origins. Every other value (staging, production, ...) refuses them.
DEV_ENVIRONMENTS = frozenset({"local", "test"})

# How App Service writes a Key Vault reference it could not resolve (a missing secret, or no
# permission to read it): the app would otherwise receive the reference text as the value.
UNRESOLVED_KEY_VAULT_REFERENCE = "@microsoft.keyvault("


class Settings(BaseSettings):
    # hide_input_in_errors: a settings error names the setting, never the values (pydantic would
    # otherwise print part of the raw settings, keys and the database password included).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    environment: str = "local"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    # The API's own login: delea_api, least privilege (TRD §6.6, migration 20261103000100).
    database_url: str = ""
    # An administrative login (postgres) for local scripts and the integration tests' setup;
    # never used by the API. Empty: DATABASE_URL (an older local .env).
    admin_database_url: str = ""
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # Stripe (Slice B2, test mode only). Never commit a key; never log one. A live key
    # (sk_live_ / rk_live_) stops the API at startup (app/services/stripe_service.py).
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # "stripe": the real API. "fake": an in-memory stand-in for the local end-to-end run only;
    # refused outside ENVIRONMENT=local/test.
    stripe_gateway: str = "stripe"
    # Where Stripe-hosted onboarding sends the owner back: the admin app.
    admin_app_url: str = "http://localhost:5173"

    # Email (Slice D, TRD §6.16). EMAIL_PROVIDER: "auto" (default) is Resend when RESEND_API_KEY
    # is set, the local mail catcher (Mailpit, from the Supabase stack) in local/test, and off
    # everywhere else. "mailpit" is refused outside local/test. Never commit a key.
    email_provider: str = "auto"
    email_from_domain: str = "mail.deleajewelry.com"
    mailpit_url: str = "http://127.0.0.1:55324"
    resend_api_key: str = ""
    resend_webhook_secret: str = ""
    # Supabase Auth's Send Email Hook (TRD §6.6, app/api/v1/auth_email_hook.py): the secret
    # Supabase generates for the hook ("v1,whsec_..."). Empty: the hook endpoint answers 404 and
    # Auth sends its own emails (the built-in mailer, or Mailpit locally). Never commit it.
    send_email_hook_secret: str = ""

    llm_api_key: str = ""

    # Cloudflare Turnstile for the storefront checkout (app/core/turnstile.py). Empty outside
    # local/test means no checkout (fails closed). Never commit a real key.
    turnstile_secret_key: str = ""

    jwt_audience: str = "authenticated"

    # Public base URL of the product-images bucket; image URLs are this plus the stored path.
    # Hosted, set it to the project's (or a CDN's) public URL for the bucket. Empty: derived
    # from SUPABASE_URL, which is right for the local stack.
    product_images_base_url: str = ""
    # Public base URL of the brand-assets bucket (logos), the same way.
    brand_assets_base_url: str = ""

    # Comma-separated list of exact origins. A wildcard is never allowed.
    cors_origins: str = "http://localhost:5173,http://localhost:5174"

    # uvicorn trusts X-Forwarded-For only from these peers (it reads the same variable; TRD
    # §6.10). Read here only to refuse "*": then the client could choose its own address and
    # dodge the per-address limits. Hosted, it is the App Service front end's addresses.
    forwarded_allow_ips: str = ""

    @field_validator("forwarded_allow_ips")
    @classmethod
    def _no_wildcard_proxy(cls, value: str) -> str:
        if "*" in value:
            raise ValueError(
                "FORWARDED_ALLOW_IPS must list the proxy's addresses; '*' would let any client "
                "set its own address"
            )
        return value

    # The worker's health endpoint (GET /health on this port; app/worker_health.py). 0: off,
    # as locally. Hosted, the worker runs as its own App Service app, which must answer HTTP.
    worker_health_port: int = 0

    @model_validator(mode="after")
    def _no_unresolved_key_vault_reference(self) -> "Settings":
        """Refuse to start when App Service passed a Key Vault reference through unresolved:
        the reference text would be used as a key or a connection string. The error names the
        setting, never its value."""
        unresolved = sorted(
            name.upper()
            for name, value in self.__dict__.items()
            if isinstance(value, str)
            and value.strip().lower().startswith(UNRESOLVED_KEY_VAULT_REFERENCE)
        )
        if unresolved:
            raise ValueError(
                "unresolved Key Vault reference in "
                + ", ".join(unresolved)
                + ": the secret is missing or the app's identity can't read it"
            )
        return self

    @model_validator(mode="after")
    def _keys_in_the_right_settings(self) -> "Settings":
        """Refuse to start with Supabase's two new key kinds swapped: a publishable key can't
        act as the server key, and the secret key must never sit in the public key's setting
        (it would ship to browsers). Names the setting, never the value."""
        if self.supabase_service_role_key.startswith(PUBLISHABLE_KEY_PREFIX):
            raise ValueError(
                "SUPABASE_SERVICE_ROLE_KEY holds a publishable key; it needs the secret key"
            )
        if self.supabase_anon_key.startswith(SECRET_KEY_PREFIX):
            raise ValueError(
                "SUPABASE_ANON_KEY holds a secret key; it takes the publishable key only"
            )
        return self

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: str) -> str:
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list exact origins; '*' is not allowed")
        return value

    @property
    def admin_dsn(self) -> str:
        """The administrative connection for scripts and test setup (never the API's pool)."""
        return self.admin_database_url or self.database_url

    @property
    def is_dev_environment(self) -> bool:
        return self.environment.strip().lower() in DEV_ENVIRONMENTS

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def product_images_public_base(self) -> str:
        base = self.product_images_base_url or (
            f"{self.supabase_url.rstrip('/')}/storage/v1/object/public/product-images"
        )
        return base.rstrip("/")

    @property
    def brand_assets_public_base(self) -> str:
        base = self.brand_assets_base_url or (
            f"{self.supabase_url.rstrip('/')}/storage/v1/object/public/brand-assets"
        )
        return base.rstrip("/")

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
