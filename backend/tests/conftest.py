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
