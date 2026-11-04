"""The real worker loop serves its health endpoint (hosted-demo plan step 3): the hosted
worker is an App Service app, which must answer HTTP. Against the local stack, with email off
(nothing is sent) and other tests' events closed first."""

import asyncio
import socket

import pytest

from app import worker as worker_module
from app.core.config import get_settings

from . import test_email as d1

_quiet_queue = d1._quiet_queue

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _status(port: int) -> int | None:
    """The endpoint's status code, or None when nothing answers (a refused connection takes
    about 2 s on Windows, hence the timeout)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=0.5
        )
    except (OSError, TimeoutError):
        return None
    writer.write(b"GET /health HTTP/1.1\r\n\r\n")
    await writer.drain()
    data = await reader.read()
    writer.close()
    return int(data.split(b" ", 2)[1]) if data else None


@pytest.mark.anyio
async def test_the_worker_answers_health_while_it_runs(monkeypatch) -> None:
    port = _free_port()
    settings = get_settings().model_copy(
        update={"worker_health_port": port, "email_provider": "off", "resend_api_key": ""}
    )
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    passes = 0
    real_beat = worker_module.Heartbeat.beat

    def counting_beat(self) -> None:
        nonlocal passes
        passes += 1
        real_beat(self)

    monkeypatch.setattr(worker_module.Heartbeat, "beat", counting_beat)
    task = asyncio.create_task(worker_module.main(once=False))
    try:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            if passes and await _status(port) == 200:
                break
            await asyncio.sleep(0.1)
        assert passes >= 1, "the worker finished no pass"
        assert await _status(port) == 200
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0.1)
    assert await _status(port) is None  # the endpoint stops with the worker


@pytest.mark.anyio
async def test_a_single_pass_starts_no_endpoint(monkeypatch) -> None:
    port = _free_port()
    settings = get_settings().model_copy(
        update={"worker_health_port": port, "email_provider": "off", "resend_api_key": ""}
    )
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    await worker_module.main(once=True)
    assert await _status(port) is None
