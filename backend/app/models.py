from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    speaker_label: str  # "דובר 1" בשלב 1, שם אמיתי משלב 2 ואילך
    speaker_tag: int  # התג הגולמי שהחזיר מנוע התמלול
    text: str
    start_seconds: float
    end_seconds: float


class TodoItem(BaseModel):
    description: str
    owner: str | None = None
    due_date: str | None = None  # ISO 8601 (YYYY-MM-DD), None אם לא צוין תאריך


class MeetingResult(BaseModel):
    title: str
    date: str  # ISO 8601
    transcript: list[TranscriptSegment]
    summary: str
    todos: list[TodoItem]
    speakers: list[str] = []
    duration_seconds: float = 0.0
    # drive_folder_id קיים רק בהקלטות מלפני המעבר לתיקיות לפי סוג, שלהן
    # עדיין יש תיקייה משלהן ב-Drive. drive_folder_url מצביע היום על ספריית
    # ה-Meeting Log כולה. ראה services/drive.py.
    drive_folder_id: str | None = None
    drive_folder_url: str | None = None
    drive_transcript_url: str | None = None
    drive_transcript_doc_id: str | None = None
    drive_summary_url: str | None = None
    drive_summary_doc_id: str | None = None
    drive_todo_url: str | None = None
    drive_todo_file_id: str | None = None
    drive_audio_url: str | None = None
    # כל קבצי האודיו של ההקלטה (שיחת טלפון = שני ערוצים), לשינוי שם ומחיקה.
    drive_audio_file_ids: list[str] = []


class ChatRequest(BaseModel):
    recording_ids: list[str]
    question: str


class Attachment(BaseModel):
    """מסמך תיעוד בלבד - הרשומה בפועל נשמרת כ-dict גמיש ב-Firestore (ראה
    firestore_store.add_attachments/update_attachment/remove_attachment),
    כדי ששלב "processing" יוכל להיכתב לפני שיש summary/full_text בכלל."""

    attachment_id: str
    filename: str
    mime_type: str
    status: str  # "processing" | "done" | "error"
    error: str | None = None
    summary: str | None = None  # תקציר קצר - זה מה שמשולב לתוך הסיכום
    full_text: str | None = None  # תוכן מלא/מורחב - לשימוש פנימי (צ'אט), לא מוצג
    drive_file_id: str | None = None
    drive_url: str | None = None


class RecordingUpdateRequest(BaseModel):
    """גוף בקשת PATCH /recordings/{id} - כל שדה אופציונלי, רק מה שנשלח מתעדכן."""

    title: str | None = None
    # מיפוי תווית ישנה -> חדשה, למשל {"דובר 1": "דני"}. רק תוויות שמופיעות
    # במפתחות המילון משתנות, שאר הדוברים נשארים כפי שהיו.
    speaker_renames: dict[str, str] | None = None
    note: str | None = None


class SpeakerProfileUpdateRequest(BaseModel):
    """גוף בקשת PATCH /speaker-profiles/{id} - תיוג פרופיל דובר לא-מזוהה
    בשם, ממסך "דוברים לא מזוהים" באפליקציה (ראה pipeline/speaker_id.py)."""

    name: str
