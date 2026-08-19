"""POST /admin/publish-usage-metric: מגן ע"י מפתח נפרד (require_scheduler_key)
מ-require_api_key הרגיל, כדי ש-Cloud Scheduler לא יצטרך את מפתח האפליקציה.
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings


@pytest.fixture
def client(monkeypatch):
    settings.scheduler_api_key = "sched-test-key"
    settings.backend_api_key = "app-test-key"

    calls: list[bool] = []

    def fake_publish():
        calls.append(True)
        return {
            "billable_seconds_month_to_date": 12345.0,
            "free_tier_limit_seconds": 180_000,
            "percent_of_free_tier": 6.86,
        }

    monkeypatch.setattr(main.compute_usage, "publish_free_tier_usage_metric", fake_publish)
    test_client = TestClient(main.app)
    test_client.calls = calls
    return test_client


def test_rejects_missing_key(client):
    response = client.post("/admin/publish-usage-metric")
    assert response.status_code == 401
    assert client.calls == []


def test_rejects_wrong_key(client):
    response = client.post(
        "/admin/publish-usage-metric", headers={"X-Scheduler-Key": "wrong"}
    )
    assert response.status_code == 401
    assert client.calls == []


def test_rejects_app_api_key(client):
    """מוודא שמפתח האפליקציה הרגיל לא עובד כאן - שני המפתחות נפרדים בכוונה."""
    response = client.post(
        "/admin/publish-usage-metric", headers={"X-Scheduler-Key": "app-test-key"}
    )
    assert response.status_code == 401


def test_publishes_with_correct_key(client):
    response = client.post(
        "/admin/publish-usage-metric", headers={"X-Scheduler-Key": "sched-test-key"}
    )
    assert response.status_code == 200
    assert response.json()["percent_of_free_tier"] == 6.86
    assert client.calls == [True]
