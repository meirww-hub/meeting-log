"""מטא-דאטה של הקלטות ופרופילי דוברים (שם + embedding קול).

שלד לשלב 2. כרגע חושף רק רישום סטטוס הקלטה (pending/done/error) כדי
שה-endpoint יוכל לדווח התקדמות לאפליקציה.
"""

import datetime

from google.cloud import firestore

from app.config import settings
from app.services import usage_tracker
from app.services.google_credentials import get_service_account_credentials

_RECORDINGS_COLLECTION = "recordings"
_SPEAKER_PROFILES_COLLECTION = "speaker_profiles"


def _client() -> firestore.Client:
    return firestore.Client(
        project=settings.google_cloud_project,
        database=settings.firestore_database_id,
        credentials=get_service_account_credentials(),
    )


def set_recording_status(recording_id: str, user_id: str, status: str, **extra) -> None:
    data = {"user_id": user_id, "status": status, **extra}
    if status == "queued":
        data["created_at"] = firestore.SERVER_TIMESTAMP
    doc_ref = _client().collection(_RECORDINGS_COLLECTION).document(recording_id)
    doc_ref.set(data, merge=True)
    usage_tracker.record("writes")


def get_recording(recording_id: str) -> dict | None:
    doc = _client().collection(_RECORDINGS_COLLECTION).document(recording_id).get()
    usage_tracker.record("reads")
    return doc.to_dict() if doc.exists else None


def update_recording_fields(recording_id: str, **fields) -> None:
    """עדכון חלקי (merge) של הקלטה קיימת - לעריכת כותרת/דוברים/הערה
    מהאפליקציה (ראה PATCH /recordings/{id} ו-pipeline/edit.py). בשונה
    מ-set_recording_status, לא נוגע בשדה status."""
    doc_ref = _client().collection(_RECORDINGS_COLLECTION).document(recording_id)
    doc_ref.set(fields, merge=True)
    usage_tracker.record("writes")


def delete_recording(recording_id: str) -> None:
    _client().collection(_RECORDINGS_COLLECTION).document(recording_id).delete()
    usage_tracker.record("deletes")


# --- קבצים מצורפים -----------------------------------------------------
#
# רשימת המצורפים היא מערך בתוך מסמך ההקלטה, ולכן כל שינוי בה הוא
# קרא-שנה-כתוב. שלוש הפונקציות שלמטה עוטפות אותו בטרנזקציה של Firestore
# במקום לקרוא ולכתוב בנפרד: צירוף קובץ, סיום העיבוד שלו והמחיקה שלו יכולים
# לרוץ במקביל (המשתמש מצרף שני קבצים ומיד מוחק אחד), וכתיבה של המערך כולו
# מתוך עותק ישן הייתה מוחקת בשקט את מה שהכתיבה השנייה הספיקה להוסיף.


def add_attachments(recording_id: str, entries: list[dict]) -> None:
    """מוסיף רשומות מצורף חדשות לסוף הרשימה."""
    client = _client()
    doc_ref = client.collection(_RECORDINGS_COLLECTION).document(recording_id)

    @firestore.transactional
    def _append(transaction) -> None:
        snapshot = doc_ref.get(transaction=transaction)
        current = (snapshot.to_dict() or {}).get("attachments") or []
        transaction.update(doc_ref, {"attachments": current + entries})

    _append(client.transaction())
    usage_tracker.record("writes")


def update_attachment(recording_id: str, attachment_id: str, **fields) -> None:
    """מעדכן שדות ברשומת מצורף אחת, לפי attachment_id."""
    client = _client()
    doc_ref = client.collection(_RECORDINGS_COLLECTION).document(recording_id)

    @firestore.transactional
    def _patch(transaction) -> None:
        snapshot = doc_ref.get(transaction=transaction)
        attachments = (snapshot.to_dict() or {}).get("attachments") or []
        for entry in attachments:
            if entry.get("attachment_id") == attachment_id:
                entry.update(fields)
        transaction.update(doc_ref, {"attachments": attachments})

    _patch(client.transaction())
    usage_tracker.record("writes")


def remove_attachment(recording_id: str, attachment_id: str) -> dict | None:
    """מסיר רשומת מצורף ומחזיר אותה, כדי שהקורא יוכל למחוק את הקובץ
    ב-Drive לפי המזהה ששמור בה. מחזיר None אם לא נמצאה."""
    client = _client()
    doc_ref = client.collection(_RECORDINGS_COLLECTION).document(recording_id)

    @firestore.transactional
    def _remove(transaction) -> dict | None:
        snapshot = doc_ref.get(transaction=transaction)
        attachments = (snapshot.to_dict() or {}).get("attachments") or []
        removed = next(
            (e for e in attachments if e.get("attachment_id") == attachment_id), None
        )
        if removed is None:
            return None
        transaction.update(
            doc_ref,
            {"attachments": [e for e in attachments if e is not removed]},
        )
        return removed

    removed = _remove(client.transaction())
    usage_tracker.record("writes")
    return removed


def update_recording_field_with(recording_id: str, field: str, updater) -> object:
    """עדכון טרנזקציוני של שדה בודד: קורא את הערך הנוכחי, מפעיל עליו
    updater(current) -> new_value, וכותב את התוצאה. מחזיר את הערך החדש.

    נחוץ לשדות שכמה קריאות עלולות לעדכן במקביל - למשל summary, שכל מצורף
    משלב לתוכו את התוכן שלו (ראה pipeline/attachments.py:integrate_into_summary).
    כתיבה נאיבית של "קרא, שנה בקוד, כתוב" הייתה מאבדת בשקט את השילוב של
    אחד המצורפים אם שניים מהם מסתיימים בו-זמנית."""
    client = _client()
    doc_ref = client.collection(_RECORDINGS_COLLECTION).document(recording_id)
    result = {}

    @firestore.transactional
    def _update(transaction) -> None:
        snapshot = doc_ref.get(transaction=transaction)
        current = (snapshot.to_dict() or {}).get(field)
        new_value = updater(current)
        result["value"] = new_value
        transaction.update(doc_ref, {field: new_value})

    _update(client.transaction())
    usage_tracker.record("writes")
    return result["value"]


# הסטטוסים שמופיעים בהיסטוריה: מה שהושלם, ומה שנכשל.
#
# **הכישלונות חייבים להיות כאן.** עד 2026-08-13 הרשימה החזירה "done" בלבד,
# ולכן הקלטה שהעיבוד שלה נפל פשוט נעלמה מהמסך - ההתראה על הכישלון אפילו
# הפנתה את המשתמש "לבדוק בהיסטוריה", המקום היחיד שבו היא לא הייתה. כך שיחה
# בת 28 דקות אבדה בלי שאיש ידע שהיא בכלל הגיעה לשרת.
_VISIBLE_STATUSES = ("done", "error")


def list_recordings(user_id: str) -> list[dict]:
    """ההקלטות שהושלמו ואלה שנכשלו, מהחדשה לישנה - לצורך היסטוריה/סינון
    באפליקציה (ראה GET /recordings ב-main.py). הקלטה שעדיין בעיבוד לא
    מופיעה: היא בדרך, ואין עליה עדיין מה להציג."""
    docs = (
        _client()
        .collection(_RECORDINGS_COLLECTION)
        .where("user_id", "==", user_id)
        .where("status", "in", list(_VISIBLE_STATUSES))
        .stream()
    )
    recordings = []
    for doc in docs:
        data = doc.to_dict()
        data["recording_id"] = doc.id
        created_at = data.get("created_at")
        data["created_at"] = created_at.isoformat() if created_at else None
        recordings.append(data)
    usage_tracker.record("reads", count=len(recordings))
    recordings.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return recordings


def list_expired_short_recordings(
    user_id: str, max_duration_seconds: float, min_age: datetime.timedelta
) -> list[dict]:
    """הקלטות "done" קצרות מ-max_duration_seconds, שנוצרו לפני min_age
    ומעלה, ושמעולם לא נערכו (אין להן edited_at) - מועמדות למחיקה אוטומטית.
    ראה pipeline/edit.py:cleanup_expired_recordings, שקורא לפונקציה הזו
    ומוחק כל מועמדת עם delete_recording הרגיל."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - min_age
    docs = (
        _client()
        .collection(_RECORDINGS_COLLECTION)
        .where("user_id", "==", user_id)
        .where("status", "==", "done")
        .stream()
    )
    candidates = []
    read_count = 0
    for doc in docs:
        read_count += 1
        data = doc.to_dict()
        created_at = data.get("created_at")
        if not created_at or created_at > cutoff:
            continue
        if data.get("edited_at"):
            continue
        # הקלטות ישנות מלפני תכונה זו לא נשמר להן duration_seconds כלל -
        # אין להתייחס להיעדר השדה כ"אורך 0" ולמחוק אותן בטעות.
        duration = data.get("duration_seconds")
        if duration is None or duration >= max_duration_seconds:
            continue
        data["recording_id"] = doc.id
        candidates.append(data)
    usage_tracker.record("reads", count=read_count)
    return candidates


def get_speaker_profiles(user_id: str) -> list[dict]:
    """מחזיר את פרופילי הדוברים השמורים של המשתמש: [{"name", "embedding"}].

    ייעודי לשלב 2 (identify_speakers ב-pipeline/speaker_id.py).
    """
    docs = (
        _client()
        .collection(_SPEAKER_PROFILES_COLLECTION)
        .where("user_id", "==", user_id)
        .stream()
    )
    profiles = [doc.to_dict() for doc in docs]
    usage_tracker.record("reads", count=len(profiles))
    return profiles


def save_speaker_profile(user_id: str, name: str, embedding: list[float]) -> None:
    _client().collection(_SPEAKER_PROFILES_COLLECTION).add(
        {"user_id": user_id, "name": name, "embedding": embedding}
    )
    usage_tracker.record("writes")
