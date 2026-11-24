"""האודיו המקורי נשמר ב-Drive *לפני* העיבוד, ונמחק רק אחרי שהעיבוד הצליח.

למה: עד 2026-11-24 האפליקציה מחקה את העותק שלה ברגע שהשרת ענה "התקבל", והשרת
שמר אודיו ל-Drive רק בסוף עיבוד מוצלח. עיבוד שנכשל (Gemini עמוס) איבד את
ההקלטה לגמרי - אין עותק בטלפון, וגם לא בשרת (/tmp לא שורד בין מופעים).

עכשיו: ההקלטה נשמרת בתיקיית "ממתינות לעיבוד" לפני שהשרת עונה לטלפון
(נכשלה השמירה - השרת עונה 503 והטלפון שומר את הקובץ וינסה שוב). עיבוד
שנכשל מתוזמן לניסיון חוזר בעוד שעה, מאותו אודיו שנשמר.

המידע נשמר בשדה `stash` של מסמך ההקלטה:
    {"files": [{"id": <מזהה ב-Drive>, "label": ""|"הצד השני"}],
     "kind": "single"|"call", "title": ..., "duration": ...}
ובשדות `retry_at` (מתי לנסות) ו-`auto_retries` (כמה ניסיונות אוטומטיים כבר היו).
"""

import datetime
import logging

from google.cloud import firestore

from app.services import drive, firestore_store

log = logging.getLogger(__name__)

# כמה זמן מחכים בין כישלון לניסיון החוזר. עומס בצד Gemini נמשך לפעמים דקות
# ארוכות, וניסיון חוזר מיידי רק נכשל שוב.
RETRY_DELAY = datetime.timedelta(hours=1)

# אחרי כמה ניסיונות אוטומטיים מפסיקים (עדיין אפשר ידנית מכפתור "נסה שוב").
MAX_AUTO_RETRIES = 6

# הכישלון קרה לפני שנכתב משהו ל-Drive, ולכן ריצה חוזרת לא תיצור כפילויות.
SAFE_RETRY_STATUSES = frozenset({"queued", "transcribing", "summarizing"})


def save(
    recording_id: str,
    files: list[tuple[str, str]],
    kind: str,
    title: str,
    duration_seconds: float,
) -> dict:
    """שומר את האודיו ומחזיר את התיאור לשמירה בשדה `stash`."""
    ids = drive.stash_audio(recording_id, files)
    return {
        "files": [
            {"id": file_id, "label": label}
            for file_id, (_path, label) in zip(ids, files)
        ],
        "kind": kind,
        "title": title,
        "duration": duration_seconds,
    }


def stash_file_ids(record: dict) -> list[str]:
    stash = record.get("stash") or {}
    return [f["id"] for f in stash.get("files", []) if f.get("id")]


def release_files(record: dict) -> None:
    """מעביר את הקבצים השמורים לאשפה. כישלון כאן לא מפיל כלום: הקבצים נשארים
    בתיקייה הפרטית, וזה רק עניין של סדר."""
    ids = stash_file_ids(record)
    if not ids:
        return
    try:
        drive.trash_files(ids)
    except Exception:
        log.exception("could not trash stashed audio %s", ids)


def release(recording_id: str, record: dict) -> None:
    """העיבוד הצליח: האודיו כבר יושב בתיקיית ההקלטות, והעותק הזמני מיותר."""
    if not record.get("stash"):
        return
    release_files(record)
    firestore_store.update_recording_fields(
        recording_id,
        stash=firestore.DELETE_FIELD,
        retry_at=firestore.DELETE_FIELD,
        auto_retries=firestore.DELETE_FIELD,
    )


def schedule_retry(
    recording_id: str, record: dict, now: datetime.datetime | None = None
) -> bool:
    """קובע ניסיון חוזר בעוד [RETRY_DELAY]. False אם אין אודיו שמור או שנגמרו
    הניסיונות האוטומטיים."""
    if not record.get("stash"):
        return False
    if int(record.get("auto_retries") or 0) >= MAX_AUTO_RETRIES:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    firestore_store.update_recording_fields(recording_id, retry_at=now + RETRY_DELAY)
    return True
