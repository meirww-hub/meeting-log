"""הקלטה שהתהליך שמעבד אותה נהרג באמצע (למשל Cloud Run שחרג ממכסת זיכרון,
כפי שקרה בפועל ב-2026-08-16) לא זורקת חריגה שהניסיון החוזר הרגיל תופס - היא
פשוט נעלמת, וההקלטה נשארת קפואה בסטטוס האחרון שלה לנצח בלי סימן. הבדיקות
כאן מכסות את מנגנון השחזור: זיהוי הקלטות תקועות והפיכתן ל"error" גלוי.
"""

import datetime

import pytest

from app import main
from app.pipeline import edit as recording_edit
from app.services import firestore_store


def test_recover_stale_recordings_marks_them_as_error(monkeypatch):
    stale = [
        {"recording_id": "rec-1", "status": "transcribing"},
        {"recording_id": "rec-2", "status": "saving_to_drive"},
    ]
    recorded_calls = []

    monkeypatch.setattr(
        firestore_store, "list_stale_recordings", lambda *a, **k: stale
    )
    monkeypatch.setattr(
        firestore_store,
        "set_recording_status",
        lambda recording_id, user_id, status, **extra: recorded_calls.append(
            (recording_id, user_id, status, extra)
        ),
    )

    recovered = recording_edit.recover_stale_recordings("primary_user")

    assert recovered == ["rec-1", "rec-2"]
    assert [c[0] for c in recorded_calls] == ["rec-1", "rec-2"]
    assert all(c[2] == "error" for c in recorded_calls)
    # ההודעה כוללת את השלב שבו זה נתקע - עוזר לאבחון בלי לחפור בלוגים.
    assert "transcribing" in recorded_calls[0][3]["error"]
    assert "saving_to_drive" in recorded_calls[1][3]["error"]


def test_recover_stale_recordings_does_nothing_when_none_are_stale(monkeypatch):
    monkeypatch.setattr(firestore_store, "list_stale_recordings", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(
        firestore_store, "set_recording_status", lambda *a, **k: calls.append(a)
    )

    assert recording_edit.recover_stale_recordings("primary_user") == []
    assert calls == []


def test_recover_stale_recordings_queries_the_right_statuses_and_threshold(monkeypatch):
    """saving_to_drive חייב להיכלל: תהליך שנהרג בשלב הזה תקוע באותה מידה,
    גם אם ריצה חדשה מאותה נקודה הייתה יוצרת כפילות - ראה main.py._RESUMABLE_STATUSES
    לעומת זאת."""
    captured = {}

    def fake_list(user_id, nonterminal_statuses, min_age):
        captured["user_id"] = user_id
        captured["statuses"] = nonterminal_statuses
        captured["min_age"] = min_age
        return []

    monkeypatch.setattr(firestore_store, "list_stale_recordings", fake_list)

    recording_edit.recover_stale_recordings("primary_user")

    assert captured["user_id"] == "primary_user"
    assert "saving_to_drive" in captured["statuses"]
    assert "transcribing" in captured["statuses"]
    assert "done" not in captured["statuses"]
    assert "error" not in captured["statuses"]
    assert captured["min_age"] == datetime.timedelta(minutes=30)


def test_list_stale_recordings_filters_by_age_and_status(monkeypatch):
    """בדיקה ישירה על שכבת Firestore: רק הקלטות ישנות מספיק בסטטוס ביניים
    חוזרות - לא הקלטות טריות ולא הקלטות done/error שכבר נכנסו ל-where."""
    now = datetime.datetime.now(datetime.timezone.utc)
    old_enough = now - datetime.timedelta(minutes=45)
    too_fresh = now - datetime.timedelta(minutes=5)

    class FakeDoc:
        def __init__(self, doc_id, data):
            self.id = doc_id
            self._data = data

        def to_dict(self):
            return dict(self._data)

    docs = [
        FakeDoc("stuck", {"status": "transcribing", "created_at": old_enough}),
        FakeDoc("fresh", {"status": "transcribing", "created_at": too_fresh}),
        FakeDoc("no_timestamp", {"status": "transcribing"}),
    ]

    class FakeQuery:
        def where(self, *a, **k):
            return self

        def stream(self):
            return iter(docs)

    class FakeClient:
        def collection(self, name):
            return FakeQuery()

    monkeypatch.setattr(firestore_store, "_client", lambda: FakeClient())
    monkeypatch.setattr(firestore_store.usage_tracker, "record", lambda *a, **k: None)

    result = firestore_store.list_stale_recordings(
        "primary_user", ("transcribing",), datetime.timedelta(minutes=30)
    )

    assert [r["recording_id"] for r in result] == ["stuck"]


@pytest.fixture
def cleanup_client(monkeypatch):
    settings_backup = main.settings.backend_api_key
    main.settings.backend_api_key = "test-key"

    monkeypatch.setattr(main.recording_edit, "cleanup_expired_recordings", lambda uid: ["deleted-1"])
    monkeypatch.setattr(main.recording_edit, "recover_stale_recordings", lambda uid: ["recovered-1"])

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    client.headers.update({"X-API-Key": "test-key"})
    yield client
    main.settings.backend_api_key = settings_backup


def test_cleanup_endpoint_returns_both_deleted_and_recovered(cleanup_client):
    response = cleanup_client.post("/recordings/cleanup", params={"user_id": "primary_user"})

    assert response.status_code == 200
    body = response.json()
    assert body == {"deleted": ["deleted-1"], "recovered": ["recovered-1"]}
