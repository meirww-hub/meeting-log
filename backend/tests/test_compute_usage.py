"""compute_usage.py: שליפת צריכת Cloud Run מ-Monitoring וכתיבת ה-custom metric.

התקלה שהבדיקה הזו קמה בגללה: הפילטר `resource.label."service_name"="..."`
(עם מרכאות סביב שם השדה) הוא syntax לא תקין ומחזיר 403 מ-Monitoring - התגלה
רק אחרי דיפלוי, כי אין קריאה אמיתית ל-API בבדיקות היחידה. הבדיקה כאן נועלת
את ה-syntax הנכון (resource.labels.<key>, בלי מרכאות על השם) כדי שרגרסיה
דומה תיתפס לפני הפריסה.
"""

import datetime

import pytest

from app.services import compute_usage


class _FakeCredentials:
    def __init__(self):
        self.token = "fake-token"
        self.refresh_calls = 0

    def refresh(self, request):
        self.refresh_calls += 1


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise compute_usage.requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json


@pytest.fixture
def fake_auth(monkeypatch):
    monkeypatch.setattr(
        compute_usage.google.auth, "default", lambda scopes=None: (_FakeCredentials(), None)
    )


def test_month_to_date_query_uses_correct_filter_syntax(monkeypatch, fake_auth):
    """הרגרסיה שגילינו: מרכאות סביב שם השדה (resource.label."service_name")
    לא תקינות - השדה חייב להיות resource.labels.<key> בלי מרכאות עליו."""
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["filter"] = params["filter"]
        return _FakeResponse({"timeSeries": []})

    monkeypatch.setattr(compute_usage.requests, "get", fake_get)
    monkeypatch.setattr(compute_usage.requests, "post", lambda *a, **k: _FakeResponse({}))

    compute_usage.publish_free_tier_usage_metric()

    assert 'resource.labels.service_name="meeting-log-backend"' in captured["filter"]
    assert 'resource.label."service_name"' not in captured["filter"]


def test_sums_points_across_series_and_computes_percent(monkeypatch, fake_auth):
    monkeypatch.setattr(
        compute_usage.requests,
        "get",
        lambda *a, **k: _FakeResponse(
            {
                "timeSeries": [
                    {"points": [{"value": {"doubleValue": 1000.0}}, {"value": {"doubleValue": 500.0}}]},
                    {"points": [{"value": {"doubleValue": 2500.0}}]},
                ]
            }
        ),
    )
    written = {}

    def fake_post(url, headers, json, timeout):
        written["payload"] = json
        return _FakeResponse({})

    monkeypatch.setattr(compute_usage.requests, "post", fake_post)

    result = compute_usage.publish_free_tier_usage_metric()

    assert result["billable_seconds_month_to_date"] == 4000.0
    assert result["percent_of_free_tier"] == round(100 * 4000.0 / 180_000, 2)
    point = written["payload"]["timeSeries"][0]["points"][0]
    assert point["value"]["doubleValue"] == result["percent_of_free_tier"]
    assert written["payload"]["timeSeries"][0]["metric"]["type"] == (
        "custom.googleapis.com/meeting_log/free_tier_usage_pct"
    )


def test_raises_on_monitoring_error_response(monkeypatch, fake_auth):
    monkeypatch.setattr(
        compute_usage.requests,
        "get",
        lambda *a, **k: _FakeResponse({"error": {"message": "denied"}}, status_code=403),
    )

    with pytest.raises(RuntimeError, match="denied"):
        compute_usage.publish_free_tier_usage_metric()


def test_month_start_is_first_of_month_midnight_utc(fake_auth):
    start = compute_usage._month_start_utc()
    now = datetime.datetime.now(datetime.timezone.utc)
    assert start.year == now.year
    assert start.month == now.month
    assert start.day == 1
    assert start.hour == 0 and start.minute == 0 and start.second == 0
