"""מעקב שימוש יומי ב-Firestore, כדי לדעת כמה קרובים למכסה החינמית.

המכסה החינמית (מתאפסת סביב חצות שעון פסיפיק): 50,000 קריאות, 20,000 כתיבות,
20,000 מחיקות ליום. המונים כאן הם ספירה עצמית בקוד שלנו (לא נתון רשמי
מ-Google Cloud Monitoring) - אבל מכיוון שהאפליקציה הזו היא הצרכן היחיד של
מסד הנתונים הזה, הספירה אמורה להיות מדויקת כמעט לחלוטין.
"""

import datetime

from google.cloud import firestore

from app.config import settings
from app.services.google_credentials import get_service_account_credentials

_USAGE_COLLECTION = "_usage_counters"

_FREE_TIER_LIMITS = {"reads": 50_000, "writes": 20_000, "deletes": 20_000}


def _client() -> firestore.Client:
    return firestore.Client(
        project=settings.google_cloud_project,
        database=settings.firestore_database_id,
        credentials=get_service_account_credentials(),
    )


def _today_doc_id() -> str:
    # שעון פסיפיק הוא שם המכסה החינמית מתאפסת בפועל; UTC-8 קירוב סביר
    pacific_now = datetime.datetime.utcnow() - datetime.timedelta(hours=8)
    return pacific_now.strftime("%Y-%m-%d")


def record(op: str, count: int = 1) -> None:
    """op: 'reads' | 'writes' | 'deletes'. נקרא מ-firestore_store.py אחרי כל פעולה."""
    if count <= 0:
        return
    doc_ref = _client().collection(_USAGE_COLLECTION).document(_today_doc_id())
    doc_ref.set({op: firestore.Increment(count)}, merge=True)


def get_today_usage() -> dict:
    doc = _client().collection(_USAGE_COLLECTION).document(_today_doc_id()).get()
    data = doc.to_dict() or {}
    return {
        op: {
            "count": data.get(op, 0),
            "limit": limit,
            "percent_of_free_tier": round(100 * data.get(op, 0) / limit, 1),
        }
        for op, limit in _FREE_TIER_LIMITS.items()
    }
