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
    drive_folder_id: str | None = None
    drive_folder_url: str | None = None
    drive_transcript_url: str | None = None
    drive_transcript_doc_id: str | None = None
    drive_summary_url: str | None = None
    drive_summary_doc_id: str | None = None
    drive_todo_url: str | None = None
    drive_audio_url: str | None = None


class ChatRequest(BaseModel):
    recording_ids: list[str]
    question: str


class Attachment(BaseModel):
    filename: str
    summary: str  # תקציר קצר - זה מה שמתווסף לקובץ הסיכום
    full_text: str  # תוכן מלא/מורחב - לשימוש פנימי בלבד (צ'אט), לא מוצג
    drive_url: str


class RecordingUpdateRequest(BaseModel):
    """גוף בקשת PATCH /recordings/{id} - כל שדה אופציונלי, רק מה שנשלח מתעדכן."""

    title: str | None = None
    # מיפוי תווית ישנה -> חדשה, למשל {"דובר 1": "דני"}. רק תוויות שמופיעות
    # במפתחות המילון משתנות, שאר הדוברים נשארים כפי שהיו.
    speaker_renames: dict[str, str] | None = None
    note: str | None = None
