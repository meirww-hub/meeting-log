"""האודיו נשמר לפני העיבוד, ועיבוד שנכשל נוסה שוב אחרי שעה.

ב-2026-11-24 שתי הקלטות (פגישה ושיחה) נכשלו בגלל עומס אצל Gemini, והפגישה
אבדה לגמרי: הטלפון מחק את העותק שלו ברגע שהשרת ענה "התקבל", והשרת שמר אודיו
רק בסוף עיבוד מוצלח.
"""

import datetime
import io

import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore

from app import main
from app.config import settings
from app.pipeline import stash
from app.services import firestore_store


@pytest.fixture
def env(monkeypatch):
    settings.backend_api_key = "test-key"
    store: dict[str, dict] = {}
    trashed: list[str] = []

    def set_status(rid, uid, status, **extra):
        store.setdefault(rid, {}).update({"user_id": uid, "status": status, **extra})

    def update_fields(rid, **fields):
        doc = store.setdefault(rid, {})
        for key, value in fields.items():
            if value is firestore.DELETE_FIELD:
                doc.pop(key, None)
            else:
                doc[key] = value

    monkeypatch.setattr(firestore_store, "set_recording_status", set_status)
    monkeypatch.setattr(firestore_store, "get_recording", lambda rid: store.get(rid))
    monkeypatch.setattr(firestore_store, "update_recording_fields", update_fields)
    monkeypatch.setattr(main.time, "sleep", lambda _s: None)
    monkeypatch.setattr(stash.drive, "trash_files", lambda ids: trashed.extend(ids))
    monkeypatch.setattr(main.drive, "download_file", lambda file_id: b"audio:" + file_id.encode())

    class Env:
        pass

    e = Env()
    e.store, e.trashed = store, trashed
    return e


def _stash_spec(kind="single"):
    files = [{"id": "f0", "label": ""}]
    if kind == "call":
        files.append({"id": "f1", "label": "הצד השני"})
    return {"files": files, "kind": kind, "title": "כותרת", "duration": 120.0}


def _always_fail(*_a, **_k):
    raise RuntimeError("503 UNAVAILABLE")


def test_final_failure_schedules_retry_in_one_hour(env, monkeypatch):
    env.store["r1"] = {"user_id": "u", "status": "queued", "stash": _stash_spec()}
    before = datetime.datetime.now(datetime.timezone.utc)

    main._run_recording_pipeline(_always_fail, "r1", "u")

    rec = env.store["r1"]
    assert rec["status"] == "error"
    delay = rec["retry_at"] - before
    assert datetime.timedelta(minutes=59) < delay < datetime.timedelta(minutes=61)


def test_failure_without_stash_schedules_nothing(env):
    env.store["r2"] = {"user_id": "u", "status": "queued"}
    main._run_recording_pipeline(_always_fail, "r2", "u")
    assert "retry_at" not in env.store["r2"]


def test_failure_after_drive_write_is_not_scheduled(env):
    """ריצה חוזרת אחרי שנכתבו מסמכים הייתה יוצרת כפילויות."""
    env.store["r3"] = {"user_id": "u", "status": "queued", "stash": _stash_spec()}

    def fails_while_saving(rid, uid, *a):
        firestore_store.set_recording_status(rid, uid, "saving_to_drive")
        raise RuntimeError("drive down")

    main._run_recording_pipeline(fails_while_saving, "r3", "u")

    assert env.store["r3"]["status"] == "error"
    assert "retry_at" not in env.store["r3"]


def test_success_releases_the_stash(env):
    env.store["r4"] = {"user_id": "u", "status": "queued", "stash": _stash_spec("call")}

    def ok(rid, uid, *a):
        firestore_store.set_recording_status(rid, uid, "done")

    main._run_recording_pipeline(ok, "r4", "u")

    assert "stash" not in env.store["r4"]
    assert env.trashed == ["f0", "f1"]


def test_auto_retries_are_capped(env):
    env.store["r5"] = {
        "user_id": "u", "status": "error", "stash": _stash_spec(),
        "auto_retries": stash.MAX_AUTO_RETRIES,
    }
    assert stash.schedule_retry("r5", env.store["r5"]) is False
    assert "retry_at" not in env.store["r5"]


def test_retry_runs_pipeline_from_stored_audio(env, monkeypatch):
    env.store["r6"] = {
        "user_id": "u", "status": "error", "error": "x",
        "stash": _stash_spec(), "retry_at": datetime.datetime.now(datetime.timezone.utc),
    }
    seen = {}

    def fake_process(rid, uid, path, title, duration):
        seen.update(rid=rid, uid=uid, audio=open(path, "rb").read(), title=title, duration=duration)
        firestore_store.set_recording_status(rid, uid, "done")

    monkeypatch.setattr(main, "process_recording", fake_process)

    main._retry_recording("r6", automatic=True)

    assert seen == {"rid": "r6", "uid": "u", "audio": b"audio:f0", "title": "כותרת", "duration": 120.0}
    rec = env.store["r6"]
    assert rec["status"] == "done"
    assert "stash" not in rec and "error" not in rec
    assert env.trashed == ["f0"]


def test_retry_of_a_call_uses_both_channels(env, monkeypatch):
    env.store["r7"] = {"user_id": "u", "status": "error", "stash": _stash_spec("call")}
    seen = {}

    def fake_call(rid, uid, up, down, title, duration):
        seen["audio"] = (open(up, "rb").read(), open(down, "rb").read())
        firestore_store.set_recording_status(rid, uid, "done")

    monkeypatch.setattr(main, "process_call_recording", fake_call)
    main._retry_recording("r7", automatic=False)
    assert seen["audio"] == (b"audio:f0", b"audio:f1")


def test_retry_skips_a_recording_that_is_not_in_error(env, monkeypatch):
    env.store["r8"] = {"user_id": "u", "status": "transcribing", "stash": _stash_spec()}
    monkeypatch.setattr(main, "process_recording", _always_fail)
    main._retry_recording("r8", automatic=True)
    assert env.store["r8"]["status"] == "transcribing"


def test_automatic_retry_counts_but_manual_does_not(env, monkeypatch):
    counts = {}

    def capture(rid, uid, *a):
        counts[rid] = env.store[rid]["auto_retries"]

    monkeypatch.setattr(main, "process_recording", capture)
    env.store["a"] = {"user_id": "u", "status": "error", "stash": _stash_spec(), "auto_retries": 2}
    env.store["m"] = {"user_id": "u", "status": "error", "stash": _stash_spec(), "auto_retries": 2}
    main._retry_recording("a", automatic=True)
    main._retry_recording("m", automatic=False)
    assert counts == {"a": 3, "m": 2}


def test_upload_is_stored_before_it_is_acknowledged(env, monkeypatch):
    calls = []

    def fake_save(recording_id, files, kind, title, duration_seconds):
        calls.append((recording_id, [label for _p, label in files], kind))
        return _stash_spec(kind)

    monkeypatch.setattr(stash, "save", fake_save)
    seen = {}

    def capture(rid, uid, *a):
        seen["stash"] = env.store[rid].get("stash")

    monkeypatch.setattr(main, "process_call_recording", capture)
    client = TestClient(main.app)

    response = client.post(
        "/recordings",
        headers={"X-API-Key": "test-key"},
        data={"user_id": "u", "client_upload_id": "call-9", "duration_seconds": "30"},
        files={
            "file": ("up.m4a", io.BytesIO(b"1"), "audio/mp4"),
            "file_downlink": ("down.m4a", io.BytesIO(b"2"), "audio/mp4"),
        },
    )

    assert response.status_code == 200
    rid = response.json()["recording_id"]
    assert calls == [(rid, ["", "הצד השני"], "call")]
    assert seen["stash"]["kind"] == "call"


def test_upload_is_refused_when_audio_cannot_be_stored(env, monkeypatch):
    """הטלפון מוחק את הקובץ רק על 200 - לכן בלי שמירה חייבים לא לענות 200."""

    def broken_save(*_a, **_k):
        raise RuntimeError("Drive down")

    monkeypatch.setattr(stash, "save", broken_save)
    client = TestClient(main.app)

    response = client.post(
        "/recordings",
        headers={"X-API-Key": "test-key"},
        data={"user_id": "u", "client_upload_id": "m-1"},
        files={"file": ("m.m4a", io.BytesIO(b"1"), "audio/mp4")},
    )

    assert response.status_code == 503
    assert env.store == {}


def test_manual_retry_endpoint(env, monkeypatch):
    ran = []
    monkeypatch.setattr(main, "_retry_recording", lambda rid, automatic: ran.append((rid, automatic)))
    client = TestClient(main.app)
    headers = {"X-API-Key": "test-key"}

    env.store["e1"] = {"user_id": "u", "status": "error", "stash": _stash_spec()}
    env.store["e2"] = {"user_id": "u", "status": "error"}
    env.store["e3"] = {"user_id": "u", "status": "done", "stash": _stash_spec()}

    assert client.post("/recordings/e1/retry", headers=headers).status_code == 200
    assert ran == [("e1", False)]
    assert client.post("/recordings/e2/retry", headers=headers).status_code == 409
    assert client.post("/recordings/e3/retry", headers=headers).status_code == 409
    assert client.post("/recordings/nope/retry", headers=headers).status_code == 404
