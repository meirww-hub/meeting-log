"""עריכה ומחיקה של הקלטה קיימת ממסך ההיסטוריה באפליקציה.

כל שינוי מקומי (Firestore) משתקף גם ב-Drive, כפי שהמשתמש ביקש: כותרת חדשה
משנה את שם כל קבצי ההקלטה, שינוי שם דובר מעדכן את קובץ התמלול, והערה
נשמרת כ-Google Doc בתיקיית "הערות". ראה PATCH/DELETE /recordings/{id}
ב-main.py.

מאז שקבצי ההקלטה מסודרים ב-Drive לפי סוג ולא לפי פגישה (ראה
services/drive.py), פעולה "על ההקלטה" היא פעולה על רשימת הקבצים שלה -
_recording_file_ids מרכז אותה. הקלטות ישנות, שעדיין יש להן תיקייה משלהן,
ממשיכות לעבוד דרך התיקייה.
"""

import datetime

from app.models import RecordingUpdateRequest
from app.pipeline import speaker_id
from app.pipeline.speakers import display_label, replace_labels, speakers_in_order
from app.services import drive, firestore_store

# הקלטה קצרה מ-5 דקות שלא נערכה נמחקת אוטומטית 48 שעות אחרי יצירתה
# (ראה cleanup_expired_recordings + list_expired_short_recordings, נקרא
# מ-POST /recordings/cleanup בכל טעינה של מסך ההיסטוריה באפליקציה).
#
# הסף כאן גבוה מסף השליחה שבאפליקציה (AudioDuration.MIN_PROCESSING_SECONDS,
# 180 שניות) ובכוונה: הקלטה של 3-5 דקות כן מתעבדת ונכנסת להיסטוריה, ומקבלת
# 48 שעות שבהן די בעריכה אחת (כותרת/דובר/הערה) כדי לפטור אותה מהמחיקה
# לצמיתות. השניים לא כבולים זה לזה - שינוי אחד לא משנה את השני.
_MAX_AUTO_DELETE_DURATION_SECONDS = 300
_AUTO_DELETE_MIN_AGE = datetime.timedelta(hours=48)

# הסטטוסים שביניהם הקלטה עוברת בדרך ל-"done" - ראה recover_stale_recordings.
# משותף עם main.py._RESUMABLE_STATUSES מבחינת השלבים המוקדמים, אבל כולל גם
# "saving_to_drive": שם זה כן שייך - תהליך שנהרג באמצע השלב הזה נשאר תקוע
# בדיוק כמו כל שלב אחר, גם אם ריצה חדשה מאותה נקודה הייתה יוצרת כפילות.
_NONTERMINAL_STATUSES = (
    "queued", "transcribing", "identifying_speakers", "summarizing", "saving_to_drive",
)

# כמה זמן מותר להקלטה להישאר בסטטוס ביניים לפני שרואים בה תקועה.
#
# תהליך שהומת מבחוץ (Cloud Run שחרג ממכסת זיכרון, כפי שקרה בפועל
# ב-2026-08-16 - ראה transcription.py) לא זורק חריגה שמנגנון הניסיון החוזר
# ב-main.py יכול לתפוס; הוא פשוט נעלם, וההקלטה נשארת קפואה לנצח בלי סימן.
# 30 דקות נדיבות בהרבה מזמן העיבוד הרגיל (דקה-שתיים לשיחה, ראה
# RecordingStatusWorker.kt), אבל עדיין קצרות בהרבה ממגבלת ה-timeout של
# הבקשה עצמה בענן (שעה) - כך שהקלטה שעדיין מעבדת פגישה ארוכה וחריגה באמת
# לא תסומן בטעות כתקועה.
_STALE_PROCESSING_THRESHOLD = datetime.timedelta(minutes=30)


def _transcript_to_text(segments: list[dict]) -> str:
    """כמו drive._transcript_to_text, אבל על הקטעים כפי שהם שמורים ב-Firestore
    (dict ולא TranscriptSegment). שתי הגרסאות מוכרחות לייצר את אותו טקסט
    בדיוק, אחרת מסמך התמלול ב-Drive משנה צורה בכל עריכת שם דובר.
    speaker_confident חסר בהקלטות שנשמרו לפני האימות האקוסטי, ושם ברירת
    המחדל היא "ודאי" - בדיוק כמו במודל."""
    return "\n\n".join(
        f"{display_label(s['speaker_label'], s.get('speaker_confident', True))}:\n{s['text']}"
        for s in segments
    )


def _doc_id(recording: dict, id_field: str, url_field: str) -> str | None:
    """מזהה מסמך Drive, עם נפילה לחילוץ מתוך ה-URL השמור.

    ה-URL נשמר תמיד, המזהה לא: drive_transcript_doc_id לא נכתב ל-Firestore
    ב-65 ההקלטות הראשונות (תוקן ב-pipeline.py), ולכן עדכון מסמך התמלול
    בעריכת שם דובר פשוט לא רץ - התמלול באפליקציה התעדכן והמסמך ב-Drive
    נשאר עם התוויות הישנות. החילוץ מה-URL מחזיר גם את ההקלטות הישנות
    לתיקון, בלי מיגרציה.
    """
    return recording.get(id_field) or drive.file_id_from_url(recording.get(url_field))


def audio_file_ids(recording: dict) -> list[str]:
    """קבצי האודיו של ההקלטה ב-Drive, לפי סדר הערוצים.

    ערוץ 0 הוא ההקלטה עצמה; בשיחת טלפון שיובאה מ-cally יש ערוץ 1 נוסף - הצד
    השני של השיחה, בקובץ נפרד (ראה process_call_recording). מלבד העריכה
    והמחיקה, הרשימה משמשת גם את GET /recordings/{id}/audio, שמזרים ערוץ
    מבוקש לנגן שבתוך האפליקציה.

    להקלטות שקדמו ל-drive_audio_file_ids נשמר רק הקישור, ולכן המזהה מחולץ
    ממנו - שם יהיה ערוץ אחד בלבד.
    """
    ids = recording.get("drive_audio_file_ids") or [
        drive.file_id_from_url(recording.get("drive_audio_url"))
    ]
    return [file_id for file_id in ids if file_id]


def _recording_file_ids(recording: dict) -> list[str]:
    """כל קבצי ההקלטה ב-Drive, לשינוי שם ולמחיקה יחד.

    גם כאן יש נפילה מה-URL למזהה (ראה _doc_id): drive_todo_file_id
    ו-drive_audio_file_ids נוספו רק עם המעבר לתיקיות לפי סוג, וההקלטות
    שקדמו להם שמרו רק קישורים. ערוץ אודיו שני של שיחת טלפון לא מופיע
    בקישורים כלל, ולכן להקלטות ישנות הוא לא ייכלל - חוץ מאלה שעברו את
    scripts/migrate_drive_layout.py, שממלא את הרשימה."""
    ids = [
        _doc_id(recording, "drive_transcript_doc_id", "drive_transcript_url"),
        _doc_id(recording, "drive_summary_doc_id", "drive_summary_url"),
        _doc_id(recording, "drive_todo_file_id", "drive_todo_url"),
        recording.get("drive_note_doc_id"),
    ]
    ids += audio_file_ids(recording)
    ids += [
        attachment.get("drive_file_id") or drive.file_id_from_url(attachment.get("drive_url"))
        for attachment in recording.get("attachments") or []
    ]
    return [file_id for file_id in dict.fromkeys(ids) if file_id]


def apply_update(recording_id: str, recording: dict, payload: RecordingUpdateRequest) -> dict:
    updates: dict = {}
    folder_id = recording.get("drive_folder_id")

    old_title = (recording.get("title") or "").strip()
    if payload.title is not None and payload.title.strip():
        new_title = payload.title.strip()
        if new_title == old_title:
            # אותה כותרת בדיוק: אין מה לשנות, ובעיקר אסור לגשת לבדיקת
            # הייחודיות - היא הייתה מוצאת את הקבצים של ההקלטה עצמה וממספרת
            # אותה ל-"... 2" בכל שמירה חוזרת.
            new_title = None
        else:
            # כותרת תפוסה מקבלת מספר, בדיוק כמו בשמירה הראשונה.
            new_title = drive.unique_title(new_title) if not folder_id else new_title

        if new_title:
            updates["title"] = new_title
            if folder_id:
                date = recording.get("date", "")
                drive.rename_folder(folder_id, f"{date} - {new_title}".strip(" -"))
            else:
                drive.retitle_files(_recording_file_ids(recording), old_title, new_title)

    renames = {
        old.strip(): new.strip()
        for old, new in (payload.speaker_renames or {}).items()
        if old.strip() and new.strip() and old.strip() != new.strip()
    }
    if renames:
        # התווית שהמשתמש בחר להחליף מוחלפת **בכל** מופעיה בתמלול, לא רק
        # במופע הראשון: הוא ממלא במסך העריכה שם אחד לכל דובר, לפי סדר
        # הופעתו, ומצפה שכל הקטעים של אותו דובר יקבלו אותו.
        segments = recording.get("transcript") or []
        for segment in segments:
            new_label = renames.get(segment.get("speaker_label"))
            if new_label:
                segment["speaker_label"] = new_label

        # נבנה מהתמלול עצמו ולא ממיפוי רשימת ה-speakers השמורה, כדי שגם
        # הקלטות ישנות (שנשמרו כשהרשימה עוד מוינה א"ב) יעברו לסדר ההופעה.
        updates["transcript"] = segments
        updates["speakers"] = speakers_in_order(
            s.get("speaker_label", "") for s in segments
        ) or speakers_in_order(
            renames.get(s, s) for s in (recording.get("speakers") or [])
        )

        # תיקון ידני של שם דובר הוא העדות האמינה ביותר שיש על זהות הקול -
        # והיא נזרקה עד היום: המשתמש תיקן "דובר 2" ל-"רונית", ההקלטה הזו
        # התעדכנה, ופרופיל הקול נשאר בלי שם וחזר על אותה טעות בהקלטה הבאה.
        # מכאן והלאה התיקון נשמר על הפרופיל עצמו, ולכן חל על כל הקלטה חדשה
        # שבה אותו קול יישמע. ההקלטות שכבר נשמרו לא נסרקות (ראה speaker_id.py).
        profile_ids = dict(recording.get("speaker_profile_ids") or {})
        if profile_ids:
            for old_label, new_label in renames.items():
                profile_id = profile_ids.pop(old_label, None)
                if profile_id:
                    speaker_id.learn_name_from_correction(profile_id, new_label)
                    profile_ids[new_label] = profile_id
            updates["speaker_profile_ids"] = profile_ids

        transcript_doc_id = _doc_id(
            recording, "drive_transcript_doc_id", "drive_transcript_url"
        )
        if transcript_doc_id and segments:
            drive.update_text_doc(transcript_doc_id, _transcript_to_text(segments))

        # הסיכום נשמר כטקסט חופשי (לא מבנה עם הפניה לתוויות), אז שינוי שם
        # דובר דורש חיפוש-והחלפה מילולי בתוכו - גם ב-Firestore וגם במסמך
        # הסיכום ב-Drive - אחרת מסך הסיכום ימשיך להציג "דובר 1" גם אחרי
        # שהמשתמש שינה את השם במסך ההקלטות. זה קריטי במיוחד מאז שהסיכום
        # מייחס אמירות לדובר בשמו ("דובר 1 אמר ש...") - ראה summarize.py.
        summary = recording.get("summary") or ""
        if summary:
            summary = replace_labels(summary, renames)
            updates["summary"] = summary
            summary_doc_id = _doc_id(
                recording, "drive_summary_doc_id", "drive_summary_url"
            )
            if summary_doc_id:
                drive.update_summary_doc(summary_doc_id, summary)

    if payload.note is not None:
        updates["note"] = payload.note
        note_doc_id = recording.get("drive_note_doc_id")
        if note_doc_id:
            drive.update_text_doc(note_doc_id, payload.note)
        elif payload.note.strip():
            title = updates.get("title") or recording.get("title", "")
            note_doc = drive.create_note_doc(title, payload.note)
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
    else:
        drive.trash_files(_recording_file_ids(recording))
    firestore_store.delete_recording(recording_id)


def cleanup_expired_recordings(user_id: str) -> list[str]:
    """מוחק הקלטות קצרות מ-5 דקות שלא נערכו, 48 שעות ומעלה אחרי יצירתן.
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


def recover_stale_recordings(user_id: str) -> list[str]:
    """מסמן כ"error" הקלטות שנתקעו בסטטוס ביניים - ראה _STALE_PROCESSING_THRESHOLD.

    זו הפעולה היחידה שאפשרית מכאן: האודיו המקורי כבר לא קיים בשרת (נמחק אחרי
    ההעלאה, ותיקיית /tmp לא שורדת בין מופעים של Cloud Run בכל מקרה). מעבר
    ל"error" לא משחזר את העיבוד - הוא מה שהופך את ההקלטה לגלויה בהיסטוריה
    (ראה firestore_store._VISIBLE_STATUSES) ומאפשר את כפתור "נסה שוב" הקיים:
    לשיחת טלפון יש עדיין מקור אצל cally (CallImportWorker.retryFromCally),
    להקלטה רגילה אין - ואז הכפתור עצמו כבר יודע להגיד "אין אודיו לנסות שוב".
    מחזיר את רשימת ה-IDs שסומנו.
    """
    stale = firestore_store.list_stale_recordings(
        user_id, _NONTERMINAL_STATUSES, _STALE_PROCESSING_THRESHOLD
    )
    recovered_ids = []
    for candidate in stale:
        recording_id = candidate["recording_id"]
        stuck_status = candidate.get("status", "?")
        firestore_store.set_recording_status(
            recording_id, user_id, "error",
            error=f"העיבוד נעצר באמצע ({stuck_status}) - כנראה עומס זמני בשרת. נסו שוב.",
        )
        recovered_ids.append(recording_id)
    return recovered_ids
