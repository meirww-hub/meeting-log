"""אורכסטרציה של כל שלבי העיבוד: תמלול → זיהוי דוברים → סיכום/TO DO → Drive."""

import datetime

from app.models import MeetingResult, TranscriptSegment
from app.pipeline.speaker_id import identify_speakers
from app.pipeline.summarize import summarize_and_extract_todos
from app.pipeline.transcription import (
    transcribe_single_channel,
    transcribe_with_diarization,
)
from app.services.drive import save_meeting_to_drive
from app.services import firestore_store


def process_recording(
    recording_id: str, user_id: str, audio_path: str, title: str
) -> MeetingResult:
    firestore_store.set_recording_status(recording_id, user_id, "transcribing")
    segments = transcribe_with_diarization(audio_path)

    firestore_store.set_recording_status(recording_id, user_id, "identifying_speakers")
    segments = identify_speakers(segments, user_id)

    return _summarize_and_save(recording_id, user_id, segments, title, audio_path)


def process_call_recording(
    recording_id: str,
    user_id: str,
    uplink_path: str,
    downlink_path: str,
    title: str,
    contact_name: str = "",
) -> MeetingResult:
    """עיבוד שיחת טלפון שהוקלטה בשני ערוצים מבודדים (הצד שלי / הצד השני).

    ההפרדה הפיזית בין הערוצים מייתרת את שלב זיהוי הדוברים: כל ערוץ מתומלל
    בנפרד ומתויג בוודאות, והקטעים ממוזגים חזרה לפי ציר הזמן. התוצאה מדויקת
    יותר מ-diarization רגיל, שמנחש דוברים לפי מאפייני קול.

    contact_name מגיע משם איש הקשר של המספר בהיסטוריית השיחות של הטלפון
    (ראה CallImportWorker.kt) - כשידוע, הוא מחליף את התווית הגנרית "הצד
    השני" מייד, בלי להזדקק לזיהוי מתוך תוכן השיחה.
    """
    other_label = contact_name.strip() or "הצד השני"

    firestore_store.set_recording_status(recording_id, user_id, "transcribing")
    mine = transcribe_single_channel(uplink_path, "אני", 1)
    theirs = transcribe_single_channel(downlink_path, other_label, 2)
    segments = sorted(mine + theirs, key=lambda s: s.start_seconds)

    return _summarize_and_save(
        recording_id,
        user_id,
        segments,
        title,
        uplink_path,
        extra_audio=[(downlink_path, "הקלטה - הצד השני.m4a")],
    )


def _apply_speaker_names(
    segments: list[TranscriptSegment], speaker_names: dict[str, str]
) -> None:
    """מחליף תוויות גנריות ("דובר 1", "הצד השני") בשם האמיתי שזוהה מתוך תוכן
    השיחה עצמה (ראה summarize.py). לעולם לא נוגע ב"אני" - זה תמיד ודאי."""
    for segment in segments:
        if segment.speaker_label == "אני":
            continue
        name = speaker_names.get(segment.speaker_label)
        if name:
            segment.speaker_label = name


def _summarize_and_save(
    recording_id: str,
    user_id: str,
    segments: list[TranscriptSegment],
    title: str,
    audio_path: str,
    extra_audio: list[tuple[str, str]] | None = None,
) -> MeetingResult:
    """השלבים המשותפים לכל סוגי ההקלטות, מרגע שיש תמלול מתויג דוברים."""
    firestore_store.set_recording_status(recording_id, user_id, "summarizing")
    today_iso = datetime.date.today().isoformat()
    suggested_title, summary, todos, speaker_names = summarize_and_extract_todos(
        segments, today_iso
    )
    _apply_speaker_names(segments, speaker_names)
    final_title = title.strip() if title and title.strip() else suggested_title
    speakers = sorted({s.speaker_label for s in segments})
    duration_seconds = max((s.end_seconds for s in segments), default=0.0)

    result = MeetingResult(
        title=final_title,
        date=today_iso,
        transcript=segments,
        summary=summary,
        todos=todos,
        speakers=speakers,
        duration_seconds=duration_seconds,
    )

    firestore_store.set_recording_status(recording_id, user_id, "saving_to_drive")
    links = save_meeting_to_drive(result, audio_path, extra_audio)
    result.drive_folder_id = links["folder_id"]
    result.drive_folder_url = links["folder_url"]
    result.drive_transcript_url = links["transcript_url"]
    result.drive_transcript_doc_id = links["transcript_doc_id"]
    result.drive_summary_url = links["summary_url"]
    result.drive_summary_doc_id = links["summary_doc_id"]
    result.drive_todo_url = links["todo_url"]
    result.drive_audio_url = links["audio_url"]

    firestore_store.set_recording_status(
        recording_id,
        user_id,
        "done",
        title=result.title,
        date=result.date,
        speakers=result.speakers,
        duration_seconds=result.duration_seconds,
        summary=result.summary,
        drive_folder_id=result.drive_folder_id,
        drive_folder_url=result.drive_folder_url,
        drive_transcript_url=result.drive_transcript_url,
        drive_summary_url=result.drive_summary_url,
        drive_summary_doc_id=result.drive_summary_doc_id,
        drive_todo_url=result.drive_todo_url,
        drive_audio_url=result.drive_audio_url,
        attachments=[],
        # התמלול המובנה (עם timestamps) נשמר גם כאן, לא רק כטקסט שטוח
        # ב-Drive - כדי שמסך הצ'אט יוכל לצטט דקה:שנייה מדויקת. ראה
        # pipeline/chat.py.
        transcript=[s.model_dump() for s in segments],
    )

    return result
