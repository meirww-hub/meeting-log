"""העלאה חוזרת של אותו מקור לא יוצרת הקלטה שנייה.

זה השומר האחרון מפני כפילויות: גם אם האפליקציה תשלח את אותה שיחה פעמיים
(סריקות שרצו יחד, או ניסיון חוזר אחרי שהתשובה על ההעלאה לא הגיעה), השרת
מזהה את המקור לפי client_upload_id ומחזיר את ההקלטה הקיימת.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings


@pytest.fixture
def client(monkeypatch):
    """שרת בזיכרון: Firestore והעיבוד עצמו מוחלפים בכפילים."""
    settings.backend_api_key = "test-key"

    store: dict[str, dict] = {}
    processed: list[str] = []

    def fake_set_status(recording_id, user_id, status, **extra):
        store[recording_id] = {"user_id": user_id, "status": status, **extra}

    def fake_get(recording_id):
        return store.get(recording_id)

    monkeypatch.setattr(main.firestore_store, "set_recording_status", fake_set_status)
    monkeypatch.setattr(main.firestore_store, "get_recording", fake_get)
    monkeypatch.setattr(
        main, "process_recording", lambda rid, *a, **k: processed.append(rid)
    )
    monkeypatch.setattr(
        main, "process_call_recording", lambda rid, *a, **k: processed.append(rid)
    )

    test_client = TestClient(main.app)
    test_client.store = store
    test_client.processed = processed
    return test_client


def upload(client, *, client_upload_id="call-1", user_id="primary_user"):
    data = {"user_id": user_id, "title": ""}
    if client_upload_id is not None:
        data["client_upload_id"] = client_upload_id
    return client.post(
        "/recordings",
        headers={"X-API-Key": "test-key"},
        data=data,
        files={"file": ("call.m4a", io.BytesIO(b"audio-bytes"), "audio/mp4")},
    )


def test_same_source_uploaded_twice_creates_one_recording(client):
    first = upload(client)
    second = upload(client)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["recording_id"] == second.json()["recording_id"]
    assert second.json()["duplicate"] is True
    assert len(client.store) == 1
    # העיבוד היקר (תמלול + סיכום + דרייב) רץ פעם אחת בלבד.
    assert client.processed == [first.json()["recording_id"]]


def test_different_sources_stay_separate(client):
    first = upload(client, client_upload_id="call-1")
    second = upload(client, client_upload_id="call-2")

    assert first.json()["recording_id"] != second.json()["recording_id"]
    assert len(client.store) == 2


def test_same_source_for_different_users_stays_separate(client):
    first = upload(client, client_upload_id="call-1", user_id="a")
    second = upload(client, client_upload_id="call-1", user_id="b")

    assert first.json()["recording_id"] != second.json()["recording_id"]
    assert len(client.store) == 2


def test_upload_without_client_id_still_works(client):
    """שיתוף מגרסה ישנה של האפליקציה, בלי המזהה - נקלט כרגיל."""
    first = upload(client, client_upload_id=None)
    second = upload(client, client_upload_id=None)

    assert first.json()["recording_id"] != second.json()["recording_id"]
    assert len(client.store) == 2


def test_failed_recording_can_be_sent_again(client):
    """הקלטה שהעיבוד שלה נכשל היא היחידה שמותר להריץ מחדש.

    בלי זה שיחה שנפלה פעם אחת תקועה ב-error לנצח: המסמך שלה קיים ולכן כל
    העלאה חוזרת נענית ב-"duplicate", והיא גם לא מופיעה בהיסטוריה (שמציגה
    רק "done") - כלומר היא פשוט נעלמת. זה מה שקרה לשיחה בת 28 הדקות
    ב-2026-08-13.
    """
    first = upload(client)
    recording_id = first.json()["recording_id"]
    client.store[recording_id] = {
        "user_id": "primary_user",
        "status": "error",
        "error": "Unterminated string starting at: line 576 column 13",
    }

    again = upload(client)

    assert again.json()["recording_id"] == recording_id, "אסור שייווצר מזהה שני"
    assert again.json().get("duplicate") is not True
    assert client.processed == [recording_id, recording_id], "העיבוד לא רץ מחדש"
    assert len(client.store) == 1


def test_recording_still_processing_is_not_restarted(client):
    """רק error מריץ מחדש - הקלטה שעדיין באמצע עיבוד לא נוגעים בה."""
    first = upload(client)
    recording_id = first.json()["recording_id"]
    client.store[recording_id] = {"user_id": "primary_user", "status": "transcribing"}

    again = upload(client)

    assert again.json()["duplicate"] is True
    assert client.processed == [recording_id]


def test_recording_id_is_stable_across_processes():
    """המזהה נגזר מהמקור בלבד, ולכן זהה גם אחרי הפעלה מחדש של השרת."""
    first = main.recording_id_for_upload("primary_user", "2026-08-12_09-17-19__9WFSD")
    second = main.recording_id_for_upload("primary_user", "2026-08-12_09-17-19__9WFSD")

    assert first == second
    assert first != main.recording_id_for_upload("primary_user", "other-call")
