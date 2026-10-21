"""זמן ההקלטה בפועל נשמר בנפרד מזמן ההגעה לשרת (created_at)."""

import io

from fastapi.testclient import TestClient

from app import main
from app.config import settings


def _upload(monkeypatch, **form):
    settings.backend_api_key = "test-key"
    statuses: list[tuple] = []
    monkeypatch.setattr(
        main.firestore_store, "set_recording_status",
        lambda *a, **k: statuses.append((a, k)),
    )
    monkeypatch.setattr(main.firestore_store, "get_recording", lambda *a, **k: None)
    monkeypatch.setattr(main, "process_recording", lambda *a: None)
    TestClient(main.app).post(
        "/recordings",
        headers={"X-API-Key": "test-key"},
        data={"user_id": "primary_user", **form},
        files={"file": ("a.m4a", io.BytesIO(b"x"), "audio/mp4")},
    )
    return statuses


def test_recorded_at_is_stored_with_the_queued_record(monkeypatch):
    statuses = _upload(monkeypatch, recorded_at="2026-10-19T08:15:00+03:00")
    (args, kwargs) = statuses[0]
    assert args[2] == "queued"
    assert kwargs["recorded_at"] == "2026-10-19T08:15:00+03:00"


def test_old_app_without_recorded_at_still_uploads(monkeypatch):
    statuses = _upload(monkeypatch)
    assert "recorded_at" not in statuses[0][1]
