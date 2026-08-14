"""נקודות הקצה של קבצים מצורפים: /attachments, /attachments/{id}/retry,
DELETE /attachments/{id}.

הבדיקות כאן נועלות את מה שמסך ההיסטוריה באפליקציה צריך כדי להציג מצורף
לפני שהעיבוד שלו הסתיים (status="processing" נכתב מיד, לא רק אחרי
שהרקע סיים - ראה main.py:upload_attachments), ואת שרשרת retry/delete
שמאפשרת להתאושש מכישלון בלי לצרף את הקובץ מחדש.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings


@pytest.fixture
def client(monkeypatch):
    settings.backend_api_key = "test-key"

    recordings: dict[str, dict] = {"rec-1": {"title": "ישיבת צוות", "attachments": []}}
    added: list[list[dict]] = []
    attachment_updates: list[dict] = []
    removed_ids: list[str] = []
    scheduled_process: list[tuple] = []
    scheduled_retry: list[tuple] = []
    trashed: list[list[str]] = []

    def fake_get(recording_id):
        return recordings.get(recording_id)

    def fake_add_attachments(recording_id, entries):
        recordings[recording_id].setdefault("attachments", []).extend(entries)
        added.append(entries)

    def fake_update_attachment(recording_id, attachment_id, **fields):
        attachment_updates.append({"attachment_id": attachment_id, **fields})
        for entry in recordings[recording_id].get("attachments", []):
            if entry.get("attachment_id") == attachment_id:
                entry.update(fields)

    def fake_remove_attachment(recording_id, attachment_id):
        removed_ids.append(attachment_id)
        attachments = recordings[recording_id].get("attachments", [])
        removed = next((a for a in attachments if a.get("attachment_id") == attachment_id), None)
        if removed is not None:
            recordings[recording_id]["attachments"] = [a for a in attachments if a is not removed]
        return removed

    def fake_process_attachment(*args, **kwargs):
        scheduled_process.append(args)

    def fake_retry_attachment(*args, **kwargs):
        scheduled_retry.append(args)

    def fake_trash_files(file_ids):
        trashed.append(file_ids)

    monkeypatch.setattr(main.firestore_store, "get_recording", fake_get)
    monkeypatch.setattr(main.firestore_store, "add_attachments", fake_add_attachments)
    monkeypatch.setattr(main.firestore_store, "update_attachment", fake_update_attachment)
    monkeypatch.setattr(main.firestore_store, "remove_attachment", fake_remove_attachment)
    monkeypatch.setattr(main, "process_attachment", fake_process_attachment)
    monkeypatch.setattr(main, "retry_attachment", fake_retry_attachment)
    monkeypatch.setattr(main.drive, "trash_files", fake_trash_files)

    test_client = TestClient(main.app)
    test_client.recordings = recordings
    test_client.added = added
    test_client.attachment_updates = attachment_updates
    test_client.removed_ids = removed_ids
    test_client.scheduled_process = scheduled_process
    test_client.scheduled_retry = scheduled_retry
    test_client.trashed = trashed
    return test_client


def _upload(client, filename="invoice.pdf", content=b"pdf-bytes", content_type="application/pdf"):
    return client.post(
        "/recordings/rec-1/attachments",
        headers={"X-API-Key": "test-key"},
        files={"files": (filename, io.BytesIO(content), content_type)},
    )


# ---------- POST /attachments ----------


def test_upload_writes_a_processing_entry_before_background_work_runs(client):
    response = _upload(client)

    assert response.status_code == 200
    [entry] = client.recordings["rec-1"]["attachments"]
    assert entry["status"] == "processing"
    assert entry["filename"] == "invoice.pdf"
    assert entry["mime_type"] == "application/pdf"
    assert "attachment_id" in entry


def test_upload_schedules_background_processing_with_the_same_attachment_id(client):
    response = _upload(client)

    [entry] = client.recordings["rec-1"]["attachments"]
    [scheduled_args] = client.scheduled_process
    assert scheduled_args[0] == "rec-1"
    assert scheduled_args[1] == entry["attachment_id"]
    assert scheduled_args[3] == "invoice.pdf"


def test_upload_to_missing_recording_returns_404(client):
    response = client.post(
        "/recordings/does-not-exist/attachments",
        headers={"X-API-Key": "test-key"},
        files={"files": ("x.pdf", io.BytesIO(b"x"), "application/pdf")},
    )

    assert response.status_code == 404


def test_oversized_file_is_marked_as_error_without_scheduling_background_work(client):
    huge = b"x" * (main._MAX_ATTACHMENT_UPLOAD_BYTES + 1)

    response = _upload(client, filename="huge.pdf", content=huge)

    assert response.status_code == 200
    [entry] = client.recordings["rec-1"]["attachments"]
    assert entry["status"] == "error"
    assert "MB" in entry["error"]
    assert client.scheduled_process == []


def test_multiple_files_in_one_request_each_get_their_own_entry(client):
    client.post(
        "/recordings/rec-1/attachments",
        headers={"X-API-Key": "test-key"},
        files=[
            ("files", ("a.pdf", io.BytesIO(b"a"), "application/pdf")),
            ("files", ("b.pdf", io.BytesIO(b"b"), "application/pdf")),
        ],
    )

    assert len(client.recordings["rec-1"]["attachments"]) == 2
    assert len(client.scheduled_process) == 2


# ---------- POST /attachments/{id}/retry ----------


def test_retry_schedules_with_the_stored_drive_file_id_and_mime_type(client):
    client.recordings["rec-1"]["attachments"] = [
        {
            "attachment_id": "att-1",
            "filename": "note.pdf",
            "mime_type": "application/pdf",
            "status": "error",
            "error": "boom",
            "drive_file_id": "FILE1",
            "drive_url": "https://drive.google.com/file/d/FILE1/view",
        }
    ]

    response = client.post(
        "/recordings/rec-1/attachments/att-1/retry", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 200
    [args] = client.scheduled_retry
    assert args == ("rec-1", "att-1", "FILE1", "https://drive.google.com/file/d/FILE1/view", "note.pdf", "application/pdf")
    # מצב תיאום מיידי, לפני שהעיבוד ברקע בכלל התחיל.
    processing_update = [u for u in client.attachment_updates if u["attachment_id"] == "att-1"][0]
    assert processing_update["status"] == "processing"
    assert processing_update["error"] is None


def test_retry_404_when_attachment_not_found(client):
    response = client.post(
        "/recordings/rec-1/attachments/does-not-exist/retry", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 404


def test_retry_409_when_the_original_file_was_never_uploaded(client):
    """אם ה-upload הראשוני לא הצליח בכלל, אין קובץ ב-Drive להוריד -
    retry לא יכול לעזור, המשתמש חייב לצרף מחדש."""
    client.recordings["rec-1"]["attachments"] = [
        {
            "attachment_id": "att-1",
            "filename": "huge.pdf",
            "mime_type": "application/pdf",
            "status": "error",
            "error": "הקובץ גדול מ-25MB",
        }
    ]

    response = client.post(
        "/recordings/rec-1/attachments/att-1/retry", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 409
    assert client.scheduled_retry == []


# ---------- DELETE /attachments/{id} ----------


def test_delete_removes_the_entry_and_trashes_the_drive_file(client):
    client.recordings["rec-1"]["attachments"] = [
        {"attachment_id": "att-1", "drive_file_id": "FILE1", "status": "done"}
    ]

    response = client.delete(
        "/recordings/rec-1/attachments/att-1", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 200
    assert client.removed_ids == ["att-1"]
    assert client.trashed == [["FILE1"]]


def test_delete_a_still_processing_attachment_does_not_touch_drive(client):
    """מצורף שנכשל לפני שהספיק לעלות ל-Drive - אין קובץ למחוק שם."""
    client.recordings["rec-1"]["attachments"] = [{"attachment_id": "att-1", "status": "error"}]

    response = client.delete(
        "/recordings/rec-1/attachments/att-1", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 200
    assert client.trashed == []


def test_delete_404_when_attachment_not_found(client):
    response = client.delete(
        "/recordings/rec-1/attachments/does-not-exist", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 404
