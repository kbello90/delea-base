"""The worker's health endpoint (hosted-demo plan step 3): 200 while passes keep finishing,
503 once they stop, nothing else answered; and the settings guard that refuses an unresolved
Key Vault reference. No database."""

import asyncio

import pytest

from app.core.config import Settings
from app.worker_health import STALE_AFTER, Heartbeat, answer, serve_health


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def status(response: bytes) -> int:
    return int(response.split(b" ", 2)[1])


def test_healthy_while_passes_keep_finishing() -> None:
    clock = Clock()
    beat = Heartbeat(clock)
    assert beat.healthy()  # starting: the first pass gets the same allowance
    clock.now += STALE_AFTER + 1
    assert not beat.healthy()  # no pass ever finished
    beat.beat()
    assert beat.healthy()
    clock.now += STALE_AFTER
    assert beat.healthy()
    clock.now += 1
    assert not beat.healthy()  # stuck: the platform restarts it


def test_only_get_health_is_answered() -> None:
    clock = Clock()
    beat = Heartbeat(clock)
    ok = answer(b"GET /health HTTP/1.1\r\n", beat)
    assert status(ok) == 200 and ok.endswith(b'{"status": "ok"}')
    assert b"Cache-Control: no-store" in ok
    assert status(answer(b"HEAD /health?probe=1 HTTP/1.1\r\n", beat)) == 200
    assert status(answer(b"POST /health HTTP/1.1\r\n", beat)) == 405
    assert status(answer(b"GET / HTTP/1.1\r\n", beat)) == 404
    assert status(answer(b"GET /health/../outbox HTTP/1.1\r\n", beat)) == 404
    assert status(answer(b"garbage\r\n", beat)) == 400
    assert status(answer(b"", beat)) == 400
    clock.now += STALE_AFTER + 1
    stale = answer(b"GET /health HTTP/1.1\r\n", beat)
    assert status(stale) == 503 and b"stale" in stale


def test_the_server_answers_over_tcp() -> None:
    async def run() -> list[int]:
        clock = Clock()
        beat = Heartbeat(clock)
        server = await serve_health(beat, 0, host="127.0.0.1")
        port = server.sockets[0].getsockname()[1]

        async def get(line: bytes) -> int:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line)
            await writer.drain()
            data = await reader.read()
            writer.close()
            return status(data)

        try:
            out = [await get(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")]
            clock.now += STALE_AFTER + 1
            out.append(await get(b"GET /health HTTP/1.1\r\n\r\n"))
            out.append(await get(b"GET /nope HTTP/1.1\r\n\r\n"))
            out.append(await get(b"X" * 5000 + b"\r\n"))  # an oversized request line
            return out
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(run()) == [200, 503, 404, 400]


def test_an_unresolved_key_vault_reference_stops_startup() -> None:
    ref = "@Microsoft.KeyVault(VaultName=kv-delea-demo;SecretName=stripe-secret-key)"
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, stripe_secret_key=ref, database_url=ref.replace("stripe", "db"))
    message = str(exc.value)
    assert "unresolved Key Vault reference in DATABASE_URL, STRIPE_SECRET_KEY" in message
    assert "kv-delea-demo" not in message and "SecretName" not in message  # never the value
    # A resolved value (what the app gets when the secret exists) is fine.
    assert Settings(_env_file=None, stripe_secret_key="sk_test_x").stripe_secret_key == "sk_test_x"


def test_a_wildcard_proxy_list_stops_startup() -> None:
    for bad in ("*", "10.0.0.1,*"):
        with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS must list the proxy"):
            Settings(_env_file=None, forwarded_allow_ips=bad)
    assert Settings(_env_file=None, forwarded_allow_ips="169.254.0.0/16").forwarded_allow_ips
