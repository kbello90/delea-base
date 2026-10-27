"""FastAPI app entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.cors import StorefrontCORSMiddleware
from app.core.errors import register_exception_handlers
from app.core.security import ACCESS_HEADER
from app.db.session import close_pool, create_pool, open_pool
from app.services.stripe_service import check_stripe_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    check_stripe_settings(settings)  # a live key, or the fake outside local/test: no start
    app.state.db_pool = None
    if settings.database_url:
        app.state.db_pool = create_pool(settings)
        await open_pool(app.state.db_pool)
    else:
        logger.warning("DATABASE_URL is not set; database endpoints will return 503")
    try:
        yield
    finally:
        if app.state.db_pool is not None:
            await close_pool(app.state.db_pool)


app = FastAPI(title="DELEA API", version="0.0.0", lifespan=lifespan)

app.add_middleware(
    StorefrontCORSMiddleware,
    # Admin paths: these exact origins. Public storefront paths: each tenant's storefront
    # domains, looked up per request (app/core/cors.py).
    admin_origins=get_settings().cors_origin_list,
    # The browser reads it to tell "no business membership" from other 403s.
    expose_headers=[ACCESS_HEADER],
)

register_exception_handlers(app)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    # Liveness only: never touches the database.
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def health_ready(request: Request) -> JSONResponse:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return JSONResponse(
            {"status": "unavailable", "detail": "Database is not configured"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        async with pool.connection(timeout=5) as conn:
            await conn.execute("select 1")
    except (psycopg.Error, PoolTimeout):
        return JSONResponse(
            {"status": "unavailable", "detail": "Database is unreachable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse({"status": "ready"})


app.include_router(api_router, prefix="/api/v1")
