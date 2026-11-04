"""The email worker's health endpoint (TRD §6.16; hosted-demo plan step 3).

Hosted, the worker runs as its own App Service app, and App Service only keeps a container
that answers HTTP: it pings the port at start and then calls the health check path. This is a
deliberately tiny server, no framework: `GET /health` is 200 while the worker's passes keep
completing and 503 once they stop (a stuck worker, a database it can't reach), so the platform
restarts it. Anything else is 404 (or 400 for a request it can't read). It says nothing about
the outbox, the shops or the settings.

Off unless WORKER_HEALTH_PORT is set (locally the worker runs without it).
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# A pass takes seconds (at most 50 sends at 0.12 s plus the provider's replies). Ten minutes
# without one finished means the worker is stuck; the first pass gets the same allowance.
STALE_AFTER = 600.0
REQUEST_TIMEOUT = 5.0
MAX_REQUEST_LINE = 2048


class Heartbeat:
    """When the worker last finished a pass (monotonic clock)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._last: float | None = None

    def beat(self) -> None:
        self._last = self._clock()

    def healthy(self) -> bool:
        since = self._last if self._last is not None else self._started
        return self._clock() - since <= STALE_AFTER


def _response(status: int, reason: str, body: dict) -> bytes:
    data = json.dumps(body).encode()
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + data


def answer(request_line: bytes, heartbeat: Heartbeat) -> bytes:
    parts = request_line.decode("latin-1").strip().split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        return _response(400, "Bad Request", {"detail": "bad request"})
    method, target, _ = parts
    if target.split("?", 1)[0] != "/health":
        return _response(404, "Not Found", {"detail": "not found"})
    if method not in ("GET", "HEAD"):
        return _response(405, "Method Not Allowed", {"detail": "method not allowed"})
    if heartbeat.healthy():
        return _response(200, "OK", {"status": "ok"})
    return _response(503, "Service Unavailable", {"status": "stale"})


async def serve_health(heartbeat: Heartbeat, port: int, host: str = "0.0.0.0") -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT)
            except ValueError:  # longer than the stream's limit: answered as a bad request
                line = b""
            if len(line) > MAX_REQUEST_LINE:
                line = b""
            writer.write(answer(line, heartbeat))
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host, port, limit=MAX_REQUEST_LINE * 2)
    logger.info("worker health endpoint on port %d", port)
    return server
