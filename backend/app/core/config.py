"""Environment settings.

Every tier reads the same variables; Enterprise deployments differ only in their values
(TRD §2.3). There is no tier branching in code.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environments where development shortcuts are allowed: *.localhost storefront hosts and plain
# http:// storefront origins. Every other value (staging, production, ...) refuses them.
DEV_ENVIRONMENTS = frozenset({"local", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

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

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: str) -> str:
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list exact origins; '*' is not allowed")
        return value

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
