"""אורכסטרציה של כל שלבי העיבוד: תמלול → זיהוי דוברים → סיכום/TO DO → Drive."""

import datetime

from google import genai

from app.config import settings
from app.models import MeetingResult, TodoItem, TranscriptSegment
from app.pipeline import diarization, speaker_id
from app.pipeline.speakers import replace_labels, speakers_in_order
from app.pipeline.summarize import summarize_and_extract_todos
from app.pipeline.transcription import (
    transcribe_single_channel,
    transcribe_with_diarization,
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
    segments = transcribe_with_diarization(audio_path)

    firestore_store.set_recording_status(recording_id, user_id, "identifying_speakers")
    # שני שלבים שונים לגמרי, ובסדר הזה דווקא: קודם **כמה** אנשים מדברים כאן
    # ומי אמר כל קטע (אימות אקוסטי מול האודיו, בתוך ההקלטה הזו - ראה
    # diarization.py), ורק אחר כך **מי** הם (התאמה לפרופילים מהקלטות קודמות).
    # הסדר ההפוך היה מנסה להצמיד שם לתווית שהיא בעצמה ערבוב של שני אנשים.
    refined = diarization.refine_speaker_labels(segments, audio_path)
    segments = refined.segments
    profile_ids = speaker_id.identify_speakers(
        segments, user_id, recording_id, audio_path, label_voices=refined.label_voices
    )

    return _summarize_and_save(
        recording_id,
        user_id,
        segments,
        title,
        audio_path,
        speaker_profile_ids=profile_ids,
        measured_duration_seconds=measured_duration_seconds,
    )


def process_call_recording(
    recording_id: str,
    user_id: str,
    uplink_path: str,
    downlink_path: str,
    title: str,
    contact_name: str = "",
    measured_duration_seconds: float = 0.0,
) -> MeetingResult:
    """עיבוד שיחת טלפון שהוקלטה בשני ערוצים מבודדים (הצד שלי / הצד השני).

    ההפרדה הפיזית בין הערוצים מייתרת את שלב זיהוי הדוברים: כל ערוץ מתומלל
    בנפרד ומתויג בוודאות, והקטעים ממוזגים חזרה לפי ציר הזמן. התוצאה מדויקת
    יותר מ-diarization רגיל, שמנחש דוברים לפי מאפייני קול.

    contact_name מגיע מרשימת אנשי הקשר של הטלפון, לפי מספר השיחה (ראה
    CallImportWorker.kt) - כשידוע, הוא מחליף את התווית הגנרית "הצד השני"
    מייד, בלי להזדקק לזיהוי מתוך תוכן השיחה, ומכאן ואילך הוא נעול: שם
    שהמשתמש עצמו שמר באנשי הקשר מדויק יותר מכל שם שיישמע באודיו.

    בשני המקרים, קול "הצד השני" (ערוץ מבודד ונקי - חומר האימון האידאלי)
    נרשם/מותאם מול פרופילי הדוברים (ראה speaker_id.py): כשיש contact_name
    הוא נלמד תחת השם הזה, כדי שאותו אדם יזוהה בעתיד גם בפגישה רגילה בלי
    diarization ודאי; כשאין, הקול הזה עצמו עשוי כבר להיות מוכר (מפגישה או
    משיחה קודמת) ואז השם מוחל כאן מייד.
    """
    other_label = contact_name.strip() or "הצד השני"

    firestore_store.set_recording_status(recording_id, user_id, "transcribing")
    # לקוח אחד משותף לשני הערוצים, לא אחד לכל קריאה: ל-genai.Client אין
    # close() ציבורי, אז שני לקוחות חיים בו-זמנית (אחד עדיין לא שוחרר כש-
    # השני כבר נפתח) הוא מה שחרג ממכסת הזיכרון של Cloud Run והפיל הקלטה
    # שלמה ב-2026-08-16. ראה transcription.py.
    gemini_client = genai.Client(api_key=settings.gemini_api_key)
    mine = transcribe_single_channel(uplink_path, "מאיר", 1, client=gemini_client)
    theirs = transcribe_single_channel(
        downlink_path, other_label, 2, client=gemini_client
    )

    other_channel = 1  # 0 = uplink (audio_path), 1 = extra_audio[0] (downlink) - ראה drive.py:save_meeting_to_drive.
    # נעילת contact_name היא ללא תנאי (גם אם אין קטע ארוך מספיק לטביעת קול -
    # השם עדיין ודאי מאנשי הקשר). ההרשמה/ההתאמה לפי קול היא תוספת, לא תנאי.
    locked = bool(contact_name.strip())
    profile_ids: dict[str, str] = {}

    embedding, sample_segment = speaker_id.representative_embedding(downlink_path, theirs)
    if embedding is not None:
        sample = speaker_id.SpeakerSample(
            recording_id=recording_id,
            channel=other_channel,
            start_seconds=sample_segment.start_seconds,
            end_seconds=sample_segment.end_seconds,
        )
        if contact_name.strip():
            profile_ids[other_label] = speaker_id.enroll_known(
                user_id, other_label, embedding, sample
            )
        else:
            matched_name, profile_id = speaker_id.resolve_or_enroll(
                user_id, embedding, sample
            )
            if matched_name:
                other_label = matched_name
                for segment in theirs:
                    segment.speaker_label = matched_name
                locked = True
            profile_ids[other_label] = profile_id

    segments = sorted(mine + theirs, key=lambda s: s.start_seconds)

    return _summarize_and_save(
        recording_id,
        user_id,
        segments,
        title,
        uplink_path,
        # תווית בלבד - שם הקובץ המלא נבנה ב-drive.py סביב כותרת הפגישה.
        extra_audio=[(downlink_path, "הצד השני")],
        locked_labels={other_label} if locked else set(),
        speaker_profile_ids=profile_ids,
        measured_duration_seconds=measured_duration_seconds,
    )


def _resolve_speaker_names(
    segments: list[TranscriptSegment],
    speaker_names: dict[str, str],
    locked_labels: set[str],
) -> dict[str, str]:
    """מסנן את השמות שזוהו מתוך תוכן השיחה לכאלה שמותר להחיל בפועל.

    "מאיר" (ערוץ ה-uplink בשיחת טלפון) תמיד ודאי, ותווית שהגיעה מרשימת
    אנשי הקשר של הטלפון נעולה גם היא - שתיהן ידועות בוודאות מחוץ לאודיו,
    ולכן ניחוש של המודל מתוך ההקלטה לא מורשה לדרוס אותן.
    """
    blocked = {"מאיר"} | locked_labels
    present = {s.speaker_label for s in segments}
    return {
        label: name
        for label, name in speaker_names.items()
        if label in present and label not in blocked and name and name != label
    }


def _apply_speaker_names(
    segments: list[TranscriptSegment],
    todos: list[TodoItem],
    summary: str,
    renames: dict[str, str],
) -> str:
    """מחיל שם אמיתי שזוהה מתוך השיחה על התמלול, על הסיכום ועל המשימות.

    הסיכום מיוצר מול התמלול הגנרי, ולכן הוא מתייחס לדוברים בתוויות "דובר N"
    ומייחס להם אמירות בשמן (ראה כלל הייחוס ב-summarize.py). בלי ההחלפה כאן
    התמלול היה מציג "יוסי" בזמן שהסיכום ממשיך לדבר על "דובר 2" - אותו אדם
    בשני שמות באותה הקלטה. מחזיר את הסיכום המעודכן.
    """
    for segment in segments:
        name = renames.get(segment.speaker_label)
        if name:
            segment.speaker_label = name
    for todo in todos:
        if todo.owner:
            todo.owner = replace_labels(todo.owner, renames)
        todo.description = replace_labels(todo.description, renames)
    return replace_labels(summary, renames)


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
    locked_labels: set[str] | None = None,
    speaker_profile_ids: dict[str, str] | None = None,
    measured_duration_seconds: float = 0.0,
) -> MeetingResult:
    """השלבים המשותפים לכל סוגי ההקלטות, מרגע שיש תמלול מתויג דוברים."""
    firestore_store.set_recording_status(recording_id, user_id, "summarizing")
    today_iso = datetime.date.today().isoformat()
    suggested_title, summary, todos, speaker_names = summarize_and_extract_todos(
        segments, today_iso
    )
    renames = _resolve_speaker_names(segments, speaker_names, locked_labels or set())
    summary = _apply_speaker_names(segments, todos, summary, renames)
    # התוויות זזו, ומיפוי הפרופילים חייב לזוז איתן - הוא נשמר לפי התווית
    # שתופיע בפועל בהיסטוריה, כי משם מגיע תיקון ידני של שם דובר.
    profile_ids = {
        renames.get(label, label): profile_id
        for label, profile_id in (speaker_profile_ids or {}).items()
    }
    speaker_id.learn_names_from_content(profile_ids, renames)
    final_title = title.strip() if title and title.strip() else suggested_title
    speakers = speakers_in_order(s.speaker_label for s in segments)
    duration_seconds = _duration_of(measured_duration_seconds, segments)

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
        speakers=result.speakers,
        duration_seconds=result.duration_seconds,
        summary=result.summary,
        drive_folder_url=result.drive_folder_url,
        drive_transcript_url=result.drive_transcript_url,
        # בלי המזהה הזה מסמך התמלול ב-Drive לא מתעדכן לעולם בעריכת שם דובר:
        # edit.py קורא drive_transcript_doc_id, וב-65 ההקלטות הראשונות הוא
        # פשוט לא נשמר כאן - כך שהתמלול ב-Firestore הראה "מאיר" בזמן
        # שהמסמך ב-Drive נשאר עם "דובר 1" לתמיד.
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
        # תווית דובר -> פרופיל הקול שממנו היא נגזרה. בלי זה תיקון ידני של שם
        # דובר במסך ההיסטוריה מתקן הקלטה אחת ונשכח: הפרופיל נשאר בלי שם (או
        # עם השם השגוי) וחוזר על אותה טעות בהקלטה הבאה. ראה pipeline/edit.py.
        speaker_profile_ids=profile_ids,
    )

    return result
