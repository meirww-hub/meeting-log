"""הגדרות משותפות לבדיקות.

בדיקות מסומנות `live` פונות ל-Gemini באמת (עולה כסף, דורש רשת ומפתח API),
ולכן הן מדולגות כברירת מחדל ורצות רק עם `--live` מפורש:

    pytest                # מהיר, ללא רשת - להרצה על כל שינוי
    pytest --live         # כולל הבדיקות מול המודל
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="הרץ גם בדיקות שפונות ל-Gemini API בפועל (עולה כסף)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: פונה ל-Gemini API אמיתי")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="דורש --live (פונה ל-Gemini API אמיתי)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _never_touch_real_drive(monkeypatch):
    """שמירת האודיו המקדימה (pipeline/stash.py) פונה ל-Drive. בבדיקות היא
    מוחלפת בכפיל, אחרת כל העלאה בבדיקה הייתה משאירה קבצים אמיתיים בדרייב."""
    from app.services import drive

    monkeypatch.setattr(
        drive, "stash_audio", lambda recording_id, files: [f"fake-{i}" for i, _ in enumerate(files)]
    )


@pytest.fixture(autouse=True)
def _no_real_retry_lookup(monkeypatch):
    """GET/POST שמריצים ניסיונות חוזרים שואלים את Firestore מי מועמד לכך."""
    from app.services import firestore_store

    monkeypatch.setattr(firestore_store, "list_retry_due", lambda now, user_id=None: [])
