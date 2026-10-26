"""Storefront Origin parsing, the host cache and the request limiter. No database needed."""

import pytest

from app.core.rate_limit import SlidingWindowLimiter
from app.core.storefront_hosts import HostCache, origin_host


@pytest.mark.parametrize(
    ("origin", "dev", "expected"),
    [
        ("https://shop.example.com", False, "shop.example.com"),
        ("https://Shop.Example.com", False, "shop.example.com"),
        ("https://shop.example.com:8443", False, "shop.example.com:8443"),
        ("http://localhost:5174", True, "localhost:5174"),
        ("http://aurora.localhost:5174", True, "aurora.localhost:5174"),
        # development shortcuts outside development
        ("http://localhost:5174", False, None),
        ("https://localhost:5174", False, None),
        ("https://aurora.localhost", False, None),
        ("http://shop.example.com", False, None),
        # not an origin
        (None, True, None),
        ("", True, None),
        ("null", True, None),
        ("https://shop.example.com/", True, None),
        ("https://shop.example.com/path", True, None),
        ("https://shop.example.com?x=1", True, None),
        ("https://user@shop.example.com", True, None),
        ("https://shop.example.com:99999", True, None),
        ("ftp://shop.example.com", True, None),
        ("javascript:alert(1)", True, None),
        ("https://", True, None),
        ("https://" + "a" * 300 + ".com", True, None),
    ],
)
def test_origin_host(origin: str | None, dev: bool, expected: str | None) -> None:
    assert origin_host(origin, dev=dev) == expected


def test_host_cache_expires_and_remembers_unknown_hosts(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("app.core.storefront_hosts.time.monotonic", lambda: now[0])
    cache = HostCache(ttl=60)
    assert cache.get("a.example") == (False, None)
    cache.put("a.example", "tenant-a")
    cache.put("unknown.example", None)
    assert cache.get("a.example") == (True, "tenant-a")
    assert cache.get("unknown.example") == (True, None)
    now[0] += 61
    assert cache.get("a.example") == (False, None)


def test_host_cache_is_bounded() -> None:
    cache = HostCache(max_entries=3)
    for i in range(10):
        cache.put(f"h{i}.example", None)
    assert len(cache._entries) <= 3


def test_limiter_allows_the_limit_then_refuses_until_the_window_passes(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: now[0])
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert [limiter.hit("ip") for _ in range(3)] == [None, None, None]
    assert limiter.hit("ip") == pytest.approx(60)
    assert limiter.hit("other-ip") is None  # each address has its own budget
    now[0] = 30
    assert limiter.hit("ip") == pytest.approx(30)
    now[0] = 60.5
    assert limiter.hit("ip") is None


def test_limiter_memory_is_bounded(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: now[0])
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, max_keys=5)
    for i in range(50):
        limiter.hit(f"ip-{i}")
    assert len(limiter._hits) <= 5
