"""הזרמת האודיו של הקלטה לנגן שבתוך האפליקציה.

הנקודה הרגישה כאן היא הגודל: פגישה ארוכה שוקלת עשרות מגה-בייט, ו-Cloud Run
חותך תשובה שאינה chunked מעל 32MiB - בדיוק הקיר שכבר בלע כאן הקלטות שלמות
בכיוון ההעלאה. לכן התשובה נשלחת תמיד בהזרמה, בלי Content-Length, והגודל
המלא נמסר בכותרת נפרדת.
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings

_AUDIO = b"0123456789" * 100


@pytest.fixture
def client(monkeypatch):
    settings.backend_api_key = "test-key"

    store = {
        "rec-1": {
            "user_id": "primary_user",
            "status": "done",
            "drive_audio_file_ids": ["FILE_MAIN", "FILE_OTHER"],
        },
        # הקלטה ישנה: נשמר לה קישור בלבד, בלי רשימת מזהים.
        "rec-old": {
            "user_id": "primary_user",
            "status": "done",
            "drive_audio_url": "https://drive.google.com/file/d/FILE_LEGACY/view",
        },
    }
    served: dict[str, int] = {}

    def fake_stream(file_id, start=0, chunk_size=0):
        served["file_id"] = file_id
        served["start"] = start
        yield _AUDIO[start:]

    monkeypatch.setattr(main.firestore_store, "get_recording", store.get)
    monkeypatch.setattr(main.drive, "file_size", lambda file_id: len(_AUDIO))
    monkeypatch.setattr(main.drive, "stream_file", fake_stream)

    test_client = TestClient(main.app)
    test_client.headers.update({"X-API-Key": "test-key"})
    test_client.served = served
    return test_client


def test_streams_the_whole_file(client):
    response = client.get("/recordings/rec-1/audio")

    assert response.status_code == 200
    assert response.content == _AUDIO
    assert response.headers["X-Audio-Size"] == str(len(_AUDIO))
    assert response.headers["Accept-Ranges"] == "bytes"
    # ללא Content-Length - אחרת Cloud Run חותך פגישה ארוכה.
    assert "content-length" not in response.headers
    assert client.served["file_id"] == "FILE_MAIN"


def test_resumes_from_a_range(client):
    response = client.get("/recordings/rec-1/audio", headers={"Range": "bytes=400-"})

    assert response.status_code == 206
    assert response.content == _AUDIO[400:]
    assert response.headers["Content-Range"] == f"bytes 400-{len(_AUDIO) - 1}/{len(_AUDIO)}"
    assert client.served["start"] == 400


def test_range_past_the_end(client):
    response = client.get(
        "/recordings/rec-1/audio", headers={"Range": f"bytes={len(_AUDIO)}-"}
    )

    assert response.status_code == 416


def test_second_channel_is_the_other_side_of_the_call(client):
    response = client.get("/recordings/rec-1/audio", params={"channel": 1})

    assert response.status_code == 200
    assert client.served["file_id"] == "FILE_OTHER"


def test_missing_channel(client):
    assert client.get("/recordings/rec-1/audio", params={"channel": 2}).status_code == 404


def test_old_recording_falls_back_to_the_saved_link(client):
    response = client.get("/recordings/rec-old/audio")

    assert response.status_code == 200
    assert client.served["file_id"] == "FILE_LEGACY"


def test_unknown_recording(client):
    assert client.get("/recordings/nope/audio").status_code == 404


def test_requires_the_api_key(client):
    client.headers.pop("X-API-Key")
    assert client.get("/recordings/rec-1/audio").status_code == 401
