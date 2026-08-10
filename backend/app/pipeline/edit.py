"""עריכה ומחיקה של הקלטה קיימת ממסך ההיסטוריה באפליקציה.

כל שינוי מקומי (Firestore) משתקף גם ב-Drive, כפי שהמשתמש ביקש: כותרת חדשה
משנה את שם התיקייה, שינוי שם דובר מעדכן את קובץ התמלול, והערה נשמרת כקובץ
Google Doc נוסף בתיקיית ההקלטה. ראה PATCH/DELETE /recordings/{id} ב-main.py.
"""

import datetime

from app.models import RecordingUpdateRequest
from app.services import drive, firestore_store

# הקלטה קצרה מ-2 דקות שלא נערכה נמחקת אוטומטית 48 שעות אחרי יצירתה
# (ראה cleanup_expired_recordings + list_expired_short_recordings, נקרא
# מ-POST /recordings/cleanup בכל טעינה של מסך ההיסטוריה באפליקציה).
_MAX_AUTO_DELETE_DURATION_SECONDS = 120
_AUTO_DELETE_MIN_AGE = datetime.timedelta(hours=48)


def _transcript_to_text(segments: list[dict]) -> str:
    return "\n\n".join(f"{s['speaker_label']}:\n{s['text']}" for s in segments)


def apply_update(recording_id: str, recording: dict, payload: RecordingUpdateRequest) -> dict:
    updates: dict = {}
    folder_id = recording.get("drive_folder_id")

    if payload.title is not None and payload.title.strip():
        new_title = payload.title.strip()
        updates["title"] = new_title
        if folder_id:
            date = recording.get("date", "")
            drive.rename_folder(folder_id, f"{date} - {new_title}".strip(" -"))

    if payload.speaker_renames:
        segments = recording.get("transcript") or []
        for segment in segments:
            new_label = payload.speaker_renames.get(segment.get("speaker_label"))
            if new_label:
                segment["speaker_label"] = new_label
        speakers = recording.get("speakers") or []
        new_speakers = sorted({payload.speaker_renames.get(s, s) for s in speakers})

        updates["transcript"] = segments
        updates["speakers"] = new_speakers

        transcript_doc_id = recording.get("drive_transcript_doc_id")
        if transcript_doc_id and segments:
            drive.update_text_doc(transcript_doc_id, _transcript_to_text(segments))

    if payload.note is not None:
        updates["note"] = payload.note
        note_doc_id = recording.get("drive_note_doc_id")
        if note_doc_id:
            drive.update_text_doc(note_doc_id, payload.note)
        elif folder_id and payload.note.strip():
            note_doc = drive.create_text_doc("הערות", payload.note, folder_id)
            updates["drive_note_doc_id"] = note_doc["id"]
            updates["drive_note_url"] = note_doc["webViewLink"]

    if updates:
        # כל עריכה אמיתית פוטרת את ההקלטה לצמיתות מהמחיקה האוטומטית של
        # הקלטות קצרות (ראה cleanup_expired_recordings למטה).
        updates["edited_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        firestore_store.update_recording_fields(recording_id, **updates)
        recording = {**recording, **updates}

    return recording


def delete_recording(recording_id: str, recording: dict) -> None:
    folder_id = recording.get("drive_folder_id")
    if folder_id:
        drive.trash_folder(folder_id)
    firestore_store.delete_recording(recording_id)


def cleanup_expired_recordings(user_id: str) -> list[str]:
    """מוחק הקלטות קצרות מ-2 דקות שלא נערכו, 48 שעות ומעלה אחרי יצירתן.
    מחזיר את רשימת ה-IDs שנמחקו."""
    candidates = firestore_store.list_expired_short_recordings(
        user_id, _MAX_AUTO_DELETE_DURATION_SECONDS, _AUTO_DELETE_MIN_AGE
    )
    deleted_ids = []
    for candidate in candidates:
        recording_id = candidate["recording_id"]
        delete_recording(recording_id, candidate)
        deleted_ids.append(recording_id)
    return deleted_ids
