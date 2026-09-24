"""ריטריי על עומס בצד גוגל (503) חייב לחכות דקות, לא שניות.

הקלטת שיחה אמיתית נפלה ל-error על "503 - the model is currently
experiencing high demand" בזמן שהאפליקציה לא הייתה בשימוש קודם לכן - כלומר
לא הייתה תקלה אצל המשתמש, רק עומס זמני אצל גוגל שנמשך יותר מהחלון הקצר
(35 שניות) שהריטריי חיכה עד היום. הבדיקה נועלת שגם 503 מקבל את אותה חכייה
הארוכה שחריגת מכסה (429) כבר מקבלת.
"""

from google.genai import errors

from app.pipeline import _retry


def _server_error(code=503):
    return errors.ServerError(code, {"message": "high demand"})


def _rate_limit_error():
    return errors.ClientError(429, {"message": "quota exceeded"})


def _bad_request_error():
    return errors.ClientError(400, {"message": "invalid argument"})


def test_server_overload_gets_long_backoff(monkeypatch):
    delays = []
    monkeypatch.setattr(_retry.time, "sleep", delays.append)

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < _retry._MAX_ATTEMPTS:
            raise _server_error()
        return "ok"

    assert _retry.call_with_retry(flaky) == "ok"
    assert len(calls) == _retry._MAX_ATTEMPTS
    assert delays == [
        _retry._LONG_BACKOFF_BASE_DELAY_SECONDS * (2**i)
        for i in range(_retry._MAX_ATTEMPTS - 1)
    ]


def test_persistent_server_overload_raises_after_all_attempts(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda _seconds: None)

    def always_overloaded():
        raise _server_error()

    import pytest

    with pytest.raises(errors.ServerError):
        _retry.call_with_retry(always_overloaded)


def test_rate_limit_still_gets_same_long_backoff(monkeypatch):
    delays = []
    monkeypatch.setattr(_retry.time, "sleep", delays.append)

    def always_rate_limited():
        raise _rate_limit_error()

    import pytest

    with pytest.raises(errors.ClientError):
        _retry.call_with_retry(always_rate_limited)
    assert delays == [
        _retry._LONG_BACKOFF_BASE_DELAY_SECONDS * (2**i)
        for i in range(_retry._MAX_ATTEMPTS - 1)
    ]


def test_real_client_error_is_not_retried(monkeypatch):
    calls = []

    def bad_request():
        calls.append(1)
        raise _bad_request_error()

    import pytest

    with pytest.raises(errors.ClientError):
        _retry.call_with_retry(bad_request)
    assert len(calls) == 1
