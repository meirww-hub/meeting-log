"""אורכסטרציה של כל שלבי העיבוד: תמלול → סיכום/TO DO → Drive."""

import datetime

from google import genai

from app.config import settings
from app.models import MeetingResult, TranscriptSegment
from app.pipeline.summarize import summarize_and_extract_todos
from app.pipeline.transcription import (
    transcribe_meeting,
    transcribe_single_channel,
)
from app.services.drive import save_meeting_to_drive
from app.services import firestore_store


def process_recording(
    recording_id: str,
    user_id: str,
    audio_path: str,
    title: str,
    measured_duration_seconds: float = 0.0,
) -> MeetingResult:
    firestore_store.set_recording_status(recording_id, user_id, "transcribing")
    segments = transcribe_meeting(audio_path)

    return _summarize_and_save(
        recording_id,
        user_id,
        segments,
        title,
        audio_path,
        measured_duration_seconds=measured_duration_seconds,
    )


def process_call_recording(
    recording_id: str,
    user_id: str,
    uplink_path: str,
    downlink_path: str,
    title: str,
    measured_duration_seconds: float = 0.0,
) -> MeetingResult:
    """עיבוד שיחת טלפון שהוקלטה בשני ערוצים מבודדים (הצד שלי / הצד השני).

    כל ערוץ מתומלל בנפרד (איכות/בידוד שמע טוב יותר מאשר לחלץ צד אחד מתוך
    הקלטה ממוזגת), והקטעים ממוזגים חזרה לזרם אחד לפי ציר הזמן - בלי שום
    תיוג של מקור הקטע.
    """
    firestore_store.set_recording_status(recording_id, user_id, "transcribing")
    # לקוח אחד משותף לשני הערוצים, לא אחד לכל קריאה: ל-genai.Client אין
    # close() ציבורי, אז שני לקוחות חיים בו-זמנית (אחד עדיין לא שוחרר כש-
    # השני כבר נפתח) הוא מה שחרג ממכסת הזיכרון של Cloud Run והפיל הקלטה
    # שלמה ב-2026-08-16. ראה transcription.py.
    gemini_client = genai.Client(api_key=settings.gemini_api_key)
    mine = transcribe_single_channel(uplink_path, client=gemini_client)
    theirs = transcribe_single_channel(downlink_path, client=gemini_client)

    segments = sorted(mine + theirs, key=lambda s: s.start_seconds)

    return _summarize_and_save(
        recording_id,
        user_id,
        segments,
        title,
        uplink_path,
        # תווית בלבד - שם הקובץ המלא נבנה ב-drive.py סביב כותרת הפגישה.
        extra_audio=[(downlink_path, "הצד השני")],
        measured_duration_seconds=measured_duration_seconds,
    )


def _duration_of(
    measured_seconds: float, segments: list[TranscriptSegment]
) -> float:
    """אורך ההקלטה: מה שנמדד מקובץ האודיו, ורק אם אין - סוף הדיבור האחרון.

    המדידה מגיעה מהאפליקציה, שממילא מודדת את הקובץ לפני ההעלאה (ראה
    AudioDuration.kt). סוף הדיבור האחרון הוא קירוב גרוע: שיחה של 4 דקות
    שרובה המתנה למוקד נרשמה כ-125 שניות - התג באפליקציה הראה אורך שגוי,
    והגרוע יותר, הניקוי האוטומטי (שמוחק הקלטות קצרות) ראה אותה כקצרה
    ועמד למחוק אותה. נשמר כגיבוי בלבד, להקלטות ישנות ולגרסאות אפליקציה
    שלא שולחות את המדידה.
    """
    if measured_seconds > 0:
        return measured_seconds
    return max((s.end_seconds for s in segments), default=0.0)


def _summarize_and_save(
    recording_id: str,
    user_id: str,
    segments: list[TranscriptSegment],
    title: str,
    audio_path: str,
    extra_audio: list[tuple[str, str]] | None = None,
    measured_duration_seconds: float = 0.0,
) -> MeetingResult:
    """השלבים המשותפים לכל סוגי ההקלטות, מרגע שיש תמלול."""
    firestore_store.set_recording_status(recording_id, user_id, "summarizing")
    today_iso = datetime.date.today().isoformat()
    suggested_title, summary, todos = summarize_and_extract_todos(
        segments, today_iso
    )
    final_title = title.strip() if title and title.strip() else suggested_title
    duration_seconds = _duration_of(measured_duration_seconds, segments)

    result = MeetingResult(
        title=final_title,
        date=today_iso,
        transcript=segments,
        summary=summary,
        todos=todos,
        duration_seconds=duration_seconds,
    )

    firestore_store.set_recording_status(recording_id, user_id, "saving_to_drive")
    links = save_meeting_to_drive(result, audio_path, extra_audio)
    # הכותרת עשויה לחזור ממוספרת ("... 2") אם כבר הייתה פגישה בשם הזה. היא
    # נשמרת כאן חזרה כדי שהכותרת באפליקציה תהיה זהה לזו שעל הקבצים בדרייב -
    # אחרת שתי הקלטות היו נראות זהות במסך ההיסטוריה.
    result.title = links["title"]
    result.drive_folder_url = links["folder_url"]
    result.drive_transcript_url = links["transcript_url"]
    result.drive_transcript_doc_id = links["transcript_doc_id"]
    result.drive_summary_url = links["summary_url"]
    result.drive_summary_doc_id = links["summary_doc_id"]
    result.drive_todo_url = links["todo_url"]
    result.drive_todo_file_id = links["todo_file_id"]
    result.drive_audio_url = links["audio_url"]
    result.drive_audio_file_ids = links["audio_file_ids"]

    firestore_store.set_recording_status(
        recording_id,
        user_id,
        "done",
        title=result.title,
        date=result.date,
        duration_seconds=result.duration_seconds,
        summary=result.summary,
        drive_folder_url=result.drive_folder_url,
        drive_transcript_url=result.drive_transcript_url,
        drive_transcript_doc_id=result.drive_transcript_doc_id,
        drive_summary_url=result.drive_summary_url,
        drive_summary_doc_id=result.drive_summary_doc_id,
        drive_todo_url=result.drive_todo_url,
        # מזהי הקבצים (ולא רק הקישורים) נדרשים לשינוי שם ולמחיקה: מאז
        # שהקבצים יושבים בתיקיות לפי סוג, אין תיקייה אחת לפגישה שאפשר
        # לשנות/למחוק במקומם. ראה pipeline/edit.py.
        drive_todo_file_id=result.drive_todo_file_id,
        drive_audio_url=result.drive_audio_url,
        drive_audio_file_ids=result.drive_audio_file_ids,
        attachments=[],
        # התמלול המובנה (עם timestamps) נשמר גם כאן, לא רק כטקסט שטוח
        # ב-Drive - כדי שמסך הצ'אט יוכל לצטט דקה:שנייה מדויקת. ראה
        # pipeline/chat.py.
        transcript=[s.model_dump() for s in segments],
    )

    return result
