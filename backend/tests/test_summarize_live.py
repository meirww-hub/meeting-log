"""בדיקות מול Gemini אמיתי - `pytest --live` בלבד (עולה כסף, דורש רשת).

מודדות את מה שבדיקות יחידה לא יכולות: האם המודל *מציית* לרצפה שחישבנו, האם
המספרים באמת שורדים את הדחיסה, והאם הוא ממציא מספרים. הספים רופפים ביחס
למדידה בפועל כי פלט של מודל משתנה בין הרצות - הם נועדו לתפוס נסיגה אמיתית,
לא תנודה.

התמלול הוא זרם שטוח בלי תיוג דוברים - אין כאן בדיקת ייחוס אמירה לדובר.
"""

import re

import pytest

from app.pipeline.summarize import (
    _figure_count,
    _summary_budget,
    _word_count,
    summarize_and_extract_todos,
)
from tests.transcripts import LONG, LONG_NUMS, SHORT, SHORT_NUMS

pytestmark = pytest.mark.live

# ':' ו-'-' נכללים בתוך הטוקן כדי ש-"17:30" ו-"052-3319847" לא יתפצלו לשברים
# שייחשבו בטעות למספרים שהומצאו.
_NUM_RE = re.compile(r"\d[\d,:\-\.]*\d|\d")


def _numbers_in(text: str, strip_list_markers: bool = False) -> set[str]:
    if strip_list_markers:
        # "3. נושא" - מספור הסעיפים אינו נתון מהשיחה
        text = re.sub(r"^\s*\d+\.", "", text, flags=re.M)
    found = set()
    for match in _NUM_RE.finditer(text):
        token = match.group().replace(",", "").rstrip(".")
        parts = [p for p in re.split(r"[:\-]", token) if p]
        # טווח כמו "24000-31000" מפורק לרכיביו: ניסוח "בין X ל-Y" כ-"X-Y"
        # הוא ניסוח לגיטימי, לא מספר חדש
        found.update(parts if len(parts) > 1 else [token])
    return found


@pytest.fixture(scope="module")
def short_result():
    return _summarize(SHORT, SHORT_NUMS)


@pytest.fixture(scope="module")
def long_result():
    return _summarize(LONG, LONG_NUMS)


def _summarize(segments, planted):
    words = _word_count(segments)
    figures = _figure_count(segments)
    min_topics, min_words = _summary_budget(words, figures)
    title, summary, todos = summarize_and_extract_todos(segments, "2026-08-11")

    summary_numbers = _numbers_in(summary, strip_list_markers=True)
    transcript_numbers = _numbers_in(" ".join(s.text for s in segments))
    return {
        "title": title,
        "summary": summary,
        "todos": todos,
        "min_topics": min_topics,
        "min_words": min_words,
        "topics": len(re.findall(r"^\s*\d+\.", summary, re.M)),
        "words": len(summary.split()),
        "kept": [n for n in planted if n in summary_numbers or n in summary],
        "planted": planted,
        "invented": sorted(summary_numbers - transcript_numbers),
    }


class TestNoHallucination:
    """התכונה הקריטית: מספר שלא נאמר בשיחה לא יופיע בסיכום."""

    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_no_invented_numbers(self, name, request):
        result = request.getfixturevalue(name)
        assert result["invented"] == [], f"מספרים שלא בתמלול: {result['invented']}"


class TestNumberRetention:
    def test_short_keeps_almost_every_number(self, short_result):
        kept = len(short_result["kept"]) / len(short_result["planted"])
        assert kept >= 0.75, f"נשמרו {kept:.0%} מהמספרים"

    def test_long_dense_meeting_keeps_most_numbers(self, long_result):
        """הרגרסיה שהמדד הזה שומר עליה: עם תקציב שנגזר מאורך בלבד זה היה 27%."""
        kept = len(long_result["kept"]) / len(long_result["planted"])
        assert kept >= 0.60, f"נשמרו {kept:.0%} מהמספרים"


class TestFloorIsRespected:
    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_meets_topic_floor(self, name, request):
        result = request.getfixturevalue(name)
        assert result["topics"] >= result["min_topics"]

    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_roughly_meets_word_floor(self, name, request):
        """רצפה רכה: המודל נוטה לקצר, ואנחנו רוצים לתפוס קריסה ולא תנודה."""
        result = request.getfixturevalue(name)
        assert result["words"] >= result["min_words"] * 0.7, (
            f"{result['words']} מילים מול רצפה {result['min_words']}"
        )


class TestProportionality:
    def test_long_summary_is_substantially_longer(self, short_result, long_result):
        assert long_result["words"] > short_result["words"] * 2.5

    def test_long_summary_has_more_topics(self, short_result, long_result):
        assert long_result["topics"] > short_result["topics"]


class TestStructure:
    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_summary_is_numbered(self, name, request):
        assert request.getfixturevalue(name)["topics"] >= 2

    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_has_title_and_todos(self, name, request):
        result = request.getfixturevalue(name)
        assert result["title"].strip() and result["todos"]


def test_report(short_result, long_result, capsys):
    """לא בדיקה - הדפסת המדדים להשוואה ידנית אחרי כוונון (`-s`)."""
    with capsys.disabled():
        for name, r in (("SHORT", short_result), ("LONG", long_result)):
            owned = [t for t in r["todos"] if t.owner and t.owner.strip()]
            print(
                f"\n{name}: {r['topics']} topics / {r['words']} words "
                f"(floor {r['min_topics']}/{r['min_words']}) | "
                f"numbers {len(r['kept'])}/{len(r['planted'])} "
                f"({len(r['kept'])/len(r['planted']):.0%}) | invented {len(r['invented'])} | "
                f"todos with owner {len(owned)}/{len(r['todos'])}"
            )
