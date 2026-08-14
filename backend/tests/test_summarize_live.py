"""בדיקות מול Gemini אמיתי - `pytest --live` בלבד (עולה כסף, דורש רשת).

מודדות את מה שבדיקות יחידה לא יכולות: האם המודל *מציית* לרצפה שחישבנו, האם
המספרים באמת שורדים את הדחיסה, והאם הוא ממציא מספרים. הספים רופפים ביחס
למדידה בפועל כי פלט של מודל משתנה בין הרצות - הם נועדו לתפוס נסיגה אמיתית,
לא תנודה.

נמדד ב-2026-08-11 (gemini-3.1-flash-lite), אחרי התיקונים:
    SHORT: 2 נושאים, 95 מילים (רצפה 61), 11/12 מספרים, 0 מומצאים
    LONG:  7 נושאים, 398 מילים (רצפה 410), 44/55 מספרים, 0 מומצאים
"""

import re

import pytest

from app.pipeline.summarize import (
    UNKNOWN_SPEAKER,
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
    title, summary, todos, _ = summarize_and_extract_todos(segments, "2026-08-11")

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
        "attribution": _attribution_check(summary, segments),
    }


# ---------- דיוק הייחוס ----------
#
# המדד שהמשתמש התלונן עליו: "בסיכום אתה טועה הרבה פעמים בשם של מי אמר מה".
# המדידה נשענת על כך שבתמלול הסינתטי כל נתון מספרי נאמר בפי דובר אחד ידוע -
# אז משפט בסיכום שמזכיר דובר אחד ומצטט נתון של דובר אחר הוא ייחוס שגוי,
# בלי שיפוט סובייקטיבי. משפט שמייחס ל-"אחד הדוברים" אינו טעות: זו בדיוק
# הנסיגה שהתבקשה כשאין ודאות.


def _speakers_in(text: str, known: list[str]) -> set[str]:
    # תחילית עברית ("ודנה", "לדנה") היא עדיין אזכור של אותו אדם, אבל סיומת
    # אות היא כבר מילה אחרת - ולכן החסימה בצד ימין בלבד.
    return {name for name in known if re.search(rf"{re.escape(name)}(?![א-ת])", text)}


def _sentences(summary: str) -> list[str]:
    without_markers = re.sub(r"^\s*\d+\.", "", summary, flags=re.M)
    # נקודה שבין ספרות ("23.8 אחוז") אינה סוף משפט
    return [s for s in re.split(r"(?<!\d)[.!?\n]+", without_markers) if s.strip()]


def _attribution_check(summary: str, segments) -> dict:
    known = list(dict.fromkeys(s.speaker_label for s in segments))

    said_by: dict[str, set[str]] = {}
    for segment in segments:
        for number in _numbers_in(segment.text):
            said_by.setdefault(number, set()).add(segment.speaker_label)
    # רק נתונים שדובר יחיד אמר מזהים בעלים באופן חד-משמעי
    owner_of = {n: next(iter(who)) for n, who in said_by.items() if len(who) == 1}

    checked, wrong = [], []
    for sentence in _sentences(summary):
        named = _speakers_in(sentence, known)
        if len(named) != 1:
            continue
        owners = {owner_of[n] for n in _numbers_in(sentence) if n in owner_of}
        if len(owners) != 1:
            continue
        (attributed,), (actual,) = named, owners
        checked.append(sentence.strip())
        if attributed != actual:
            wrong.append(f"{sentence.strip()!r} - אמר זאת {actual}, לא {attributed}")

    return {
        "checked": checked,
        "wrong": wrong,
        "hedged": len(re.findall(re.escape(UNKNOWN_SPEAKER), summary)),
        # תווית "דובר N" שלא קיימת בתמלול היא ייחוס לאדם שאינו בשיחה, והיא
        # גם לא תתעדכן לעולם כשהמשתמש ישנה שמות (ההחלפה מילולית).
        "stray_labels": sorted(set(re.findall(r"דובר \d+", summary)) - set(known)),
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


class TestAttributionAccuracy:
    """התלונה שהובילה לכלל: "בסיכום אתה טועה הרבה פעמים בשם של מי אמר מה".

    שני הכיוונים נבדקים, כי הריפוי קל מדי לזייף: אפשר לאפס טעויות בכך
    שכותבים "אחד הדוברים" על הכל, וזה סיכום חסר תועלת. לכן גם הדיוק וגם
    הנכונות לנקוב בשם כשיודעים."""

    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_statements_are_attributed_to_whoever_said_them(self, name, request):
        result = request.getfixturevalue(name)["attribution"]
        errors = len(result["wrong"])
        checked = len(result["checked"])
        assert checked, "לא נמצא אף משפט עם ייחוס בר-בדיקה"
        # סף ולא אפס: המדידה נשענת על התאמת נתונים למשפטים, ומשפט שמאחד
        # שני נושאים יכול להיספר כטעות גם כשהייחוס בו נכון.
        assert errors / checked <= 0.15, "\n".join(result["wrong"])

    @pytest.mark.parametrize("name", ["short_result", "long_result"])
    def test_no_speaker_who_is_not_in_the_transcript(self, name, request):
        result = request.getfixturevalue(name)["attribution"]
        assert not result["stray_labels"], f"תוויות שלא בתמלול: {result['stray_labels']}"

    def test_hedging_does_not_replace_naming(self, long_result):
        """"אחד הדוברים" הוא מוצא לספק, לא תחליף לייחוס: בישיבה שבה כל נושא
        מוצג ע"י אדם אחר, רוב הייחוסים חייבים להישאר בשם."""
        attribution = long_result["attribution"]
        assert attribution["hedged"] <= len(attribution["checked"])


class TestTodoOwners:
    """שדה owner מזין את עמודת "אחראי" בגיליון ה-TO DO (ראה drive.py). ההנחיה
    בפרומפט דורשת לזהות אחראי כשהשיחה מאפשרת, אבל לא להמציא - שני הכיוונים
    נבדקים כאן, כי כל אחד מהם נשבר בנפרד."""

    def test_most_todos_in_a_meeting_have_an_owner(self, long_result):
        """בישיבה כל נושא מוצג ע"י מי שאחראי עליו, אז כמעט לכל משימה יש בעלים."""
        todos = long_result["todos"]
        owned = [t for t in todos if t.owner and t.owner.strip()]
        assert len(owned) / len(todos) >= 0.7, (
            f"רק ל-{len(owned)}/{len(todos)} מהמשימות יש אחראי"
        )

    def test_owners_are_people_who_took_part_in_the_meeting(self, long_result):
        """אחראי שאינו אחד מהדוברים הוא שם מומצא - והוא גם לא יתעדכן לעולם
        כשהמשתמש ישנה שם דובר (ההחלפה מילולית, ראה pipeline._apply_speaker_names)."""
        known = ["אני", "דנה", "יוסי", "מירב", "אלון"]
        strays = [
            t.owner
            for t in long_result["todos"]
            if t.owner and not any(name in t.owner for name in known)
        ]
        assert not strays, f"אחראים שלא השתתפו בפגישה: {strays}"

    def test_owner_is_identified_in_a_two_person_call(self, short_result):
        """גם בשיחת טלפון: "תשלח לי את הכתובת" - האחראי הוא הצד השני, לא אני.
        משימה משותפת (הביקור בדירה) מוחזרת כשני הצדדים מופרדים בפסיק, ולכן
        כל אחראי מפוצץ לרכיביו לפני ההשוואה."""
        owners = {
            part.strip()
            for todo in short_result["todos"]
            if todo.owner
            for part in todo.owner.split(",")
        }
        assert owners, "אף משימה בשיחה לא קיבלה אחראי"
        assert owners <= {"אני", "הצד השני"}, f"אחראי שאינו אחד מהצדדים: {owners}"


def test_report(short_result, long_result, capsys):
    """לא בדיקה - הדפסת המדדים להשוואה ידנית אחרי כוונון (`-s`)."""
    with capsys.disabled():
        for name, r in (("SHORT", short_result), ("LONG", long_result)):
            owned = [t for t in r["todos"] if t.owner and t.owner.strip()]
            a = r["attribution"]
            print(
                f"\n{name}: {r['topics']} topics / {r['words']} words "
                f"(floor {r['min_topics']}/{r['min_words']}) | "
                f"numbers {len(r['kept'])}/{len(r['planted'])} "
                f"({len(r['kept'])/len(r['planted']):.0%}) | invented {len(r['invented'])} | "
                f"todos with owner {len(owned)}/{len(r['todos'])} | "
                f"attribution {len(a['checked']) - len(a['wrong'])}/{len(a['checked'])} "
                f"correct, hedged {a['hedged']}"
            )
            for miss in a["wrong"]:
                print(f"    ייחוס שגוי: {miss}")
