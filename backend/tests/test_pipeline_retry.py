"""עיבוד שנכשל מנסה שוב לבד, ומה שנכשל סופית נראה בהיסטוריה.

עד 2026-08-13 תקלה חולפת אחת הספיקה כדי לאבד הקלטה שלמה: העיבוד רץ פעם
אחת, וכשנפל ההקלטה נכתבה כ-error - סטטוס שמסך ההיסטוריה סינן החוצה. שום
גורם לא ניסה שוב, והאפליקציה כבר מחקה את העותק המקומי שלה.
"""

import pytest

from app import main
from app.services import firestore_store


@pytest.fixture
def store(monkeypatch):
    """Firestore בזיכרון, בלי המתנות בין ניסיונות."""
    saved: dict[str, dict] = {}

    def fake_set_status(recording_id, user_id, status, **extra):
        saved.setdefault(recording_id, {}).update(
            {"user_id": user_id, "status": status, **extra}
        )

    monkeypatch.setattr(main.firestore_store, "set_recording_status", fake_set_status)
    monkeypatch.setattr(
        main.firestore_store, "get_recording", lambda rid: saved.get(rid)
    )
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    return saved


def test_transient_failure_is_retried_and_succeeds(store):
    attempts = []

    def flaky(recording_id, user_id, *args):
        attempts.append(recording_id)
        firestore_store.set_recording_status(recording_id, user_id, "transcribing")
        if len(attempts) < 3:
            raise RuntimeError("Gemini עמוס")

    main._run_recording_pipeline(flaky, "rec-1", "primary_user")

    assert len(attempts) == 3
    assert store["rec-1"]["status"] != "error"


def test_persistent_failure_is_recorded_after_all_attempts(store):
    def always_fails(recording_id, user_id, *args):
        firestore_store.set_recording_status(recording_id, user_id, "transcribing")
        raise RuntimeError("התמלול נקטע")

    main._run_recording_pipeline(always_fails, "rec-2", "primary_user")

    assert store["rec-2"]["status"] == "error"
    assert "התמלול נקטע" in store["rec-2"]["error"]


def test_failure_after_drive_write_is_not_retried(store):
    """ריצה שנייה הייתה יוצרת תיקייה ומסמכים כפולים בדרייב."""
    attempts = []

    def fails_while_saving(recording_id, user_id, *args):
        attempts.append(recording_id)
        firestore_store.set_recording_status(recording_id, user_id, "saving_to_drive")
        raise RuntimeError("Drive מסרב")

    main._run_recording_pipeline(fails_while_saving, "rec-3", "primary_user")

    assert len(attempts) == 1, "אסור לנסות שוב אחרי שהתחילה כתיבה לדרייב"
    assert store["rec-3"]["status"] == "error"


def test_first_attempt_success_runs_once(store):
    attempts = []
    main._run_recording_pipeline(
        lambda rid, uid, *a: attempts.append(rid), "rec-4", "primary_user"
    )
    assert len(attempts) == 1


def test_history_shows_failed_recordings_next_to_finished_ones():
    """הסטטוסים שהרשימה מחזירה - כישלון חייב להיות ביניהם.

    ההתראה על כישלון מפנה את המשתמש להיסטוריה; כשהסינון היה "done" בלבד,
    היא שלחה אותו למקום היחיד שבו ההקלטה לא הופיעה.
    """
    assert "error" in firestore_store._VISIBLE_STATUSES
    assert "done" in firestore_store._VISIBLE_STATUSES
    # הקלטה שעדיין רצה לא מוצגת - היא בדרך, ואין עליה מה להראות.
    assert "transcribing" not in firestore_store._VISIBLE_STATUSES
