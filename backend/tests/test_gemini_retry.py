"""call_with_retry: עומס זמני (503) מחכה כמה דקות, מכסה (429) פחות."""

import pytest
from google.genai import errors

from app.pipeline import _retry


def _server_error():
    return errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})


def _rate_limit():
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)


def _flaky(error_factory, failures):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= failures:
            raise error_factory()
        return "ok"

    return fn, calls


def test_server_overload_survives_five_failures():
    fn, calls = _flaky(_server_error, 5)
    assert _retry.call_with_retry(fn) == "ok"
    assert calls["n"] == 6


def test_server_overload_gives_up_after_six_attempts():
    fn, calls = _flaky(_server_error, 99)
    with pytest.raises(errors.ServerError):
        _retry.call_with_retry(fn)
    assert calls["n"] == 6


def test_rate_limit_gives_up_after_four_attempts():
    fn, calls = _flaky(_rate_limit, 99)
    with pytest.raises(errors.ClientError):
        _retry.call_with_retry(fn)
    assert calls["n"] == 4
