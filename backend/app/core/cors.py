"""CORS for two kinds of caller (TRD §6.4, §6.11).

- Public storefront paths (/api/v1/public/...): each tenant's storefront domains are the
  allowed origins, looked up per request (app.core.storefront_hosts, cached). The same lookup
  picks the tenant, which the endpoints read from the request state. These paths also carry
  the per-address request limit, checked before any database work. Methods GET and POST, the
  Content-Type header only, no credentials.
- Every other path: the admin's static CORS_ORIGINS list (Starlette's CORSMiddleware).

An origin allowed for the admin is not thereby allowed on the public paths, and the other way
round.
"""

import logging
import math

import psycopg
from psycopg_pool import PoolTimeout
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import rate_limit
from app.core.config import get_settings
from app.core.storefront_hosts import resolve_tenant

logger = logging.getLogger(__name__)

PUBLIC_PREFIX = "/api/v1/public/"
PUBLIC_METHODS = ("GET", "POST")
PUBLIC_HEADERS = frozenset({"content-type"})
# request.state key holding the resolved tenant id (None: unknown or disallowed origin).
TENANT_STATE_KEY = "storefront_tenant_id"


class StorefrontCORSMiddleware:
    def __init__(
        self, app: ASGIApp, *, admin_origins: list[str], expose_headers: list[str]
    ) -> None:
        self.app = app
        self.admin = CORSMiddleware(
            app,
            allow_origins=admin_origins,
            # Every method a route uses (tests/test_cors.py checks this against app.routes).
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=expose_headers,
            allow_credentials=False,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PUBLIC_PREFIX):
            await self.admin(scope, receive, send)
            return

        headers = Headers(scope=scope)
        client = scope.get("client")
        retry_after = rate_limit.public_reads.hit(client[0] if client else "unknown")
        if retry_after is not None:
            response = JSONResponse(
                {"detail": "Too many requests. Please wait a minute and try again."},
                status_code=429,
                headers={"Retry-After": str(max(1, math.ceil(retry_after))), "Vary": "Origin"},
            )
            await response(scope, receive, send)
            return

        origin = headers.get("origin")
        tenant_id = None
        pool = getattr(scope["app"].state, "db_pool", None)
        if origin and pool is not None:
            try:
                tenant_id = await resolve_tenant(
                    pool, origin, dev=get_settings().is_dev_environment
                )
            except (psycopg.Error, PoolTimeout):
                logger.exception("Storefront host lookup failed")
                response = JSONResponse(
                    {"detail": "Database is temporarily unavailable"}, status_code=503
                )
                await response(scope, receive, send)
                return

        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
            await self._preflight(scope, receive, send, headers, origin, tenant_id)
            return

        scope.setdefault("state", {})[TENANT_STATE_KEY] = tenant_id

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.add_vary_header("Origin")
                if tenant_id is not None and origin:
                    response_headers["Access-Control-Allow-Origin"] = origin
            await send(message)

        await self.app(scope, receive, send_with_cors)

    @staticmethod
    async def _preflight(
        scope: Scope,
        receive: Receive,
        send: Send,
        headers: Headers,
        origin: str | None,
        tenant_id: str | None,
    ) -> None:
        method = headers["access-control-request-method"].upper()
        requested = {
            h.strip().lower()
            for h in headers.get("access-control-request-headers", "").split(",")
            if h.strip()
        }
        if tenant_id is None or method not in PUBLIC_METHODS or not requested <= PUBLIC_HEADERS:
            response = PlainTextResponse(
                "Disallowed CORS request", status_code=400, headers={"Vary": "Origin"}
            )
        else:
            assert origin
            response = PlainTextResponse(
                "OK",
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": ", ".join(PUBLIC_METHODS),
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
        await response(scope, receive, send)
