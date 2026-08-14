"""ייחוס אמירות לדובר בסיכום: ניסוח הספק "אחד הדוברים" וההגנות סביבו.

המשתמש דיווח (2026-08-13) שהסיכום טועה לא מעט בשם של מי אמר מה, וביקש
שכשאין ודאות ייכתב "אחד הדוברים" במקום שם. ייחוס שגוי גרוע מייחוס חסר -
הוא מספר למשתמש שאדם אחד אמר משהו שאדם אחר אמר - ולכן הפרומפט מעדיף את
הניסוח הזה על ניחוש (ראה summarize._TASK_SCHEMA_HINT).

הצד שתלוי במודל - האם הוא באמת מדייק ובאמת נסוג לניסוח הזה - נמדד ב-
test_summarize_live. כאן נבדק כל מה שדטרמיניסטי, ושלושת המקומות שיכולים
להישבר בנפרד:
  1. ההנחיה עצמה מגיעה לפרומפט.
  2. הניסוח לא דולף מגוף הסיכום לשדות שבהם הוא חסר משמעות (אחראי משימה,
     רשימת הדוברים).
  3. שינוי שם דובר בדיעבד לא הופך אותו לשם של מישהו.
"""

import re
from types import SimpleNamespace

import pytest

from app.models import RecordingUpdateRequest, TodoItem
from app.pipeline import summarize
from app.pipeline.edit import apply_update
from app.pipeline.speakers import replace_labels
from app.pipeline.summarize import (
    _MeetingSummary,
    _SpeakerName,
    _summary_budget,
    _TASK_SCHEMA_HINT,
    UNKNOWN_SPEAKER,
)
from tests.transcripts import _segs


def _prompt() -> str:
    topics, words = _summary_budget(3900, 156)
    return _TASK_SCHEMA_HINT.format(
        today="2026-08-13",
        word_count=3900,
        figure_count=156,
        min_topics=topics,
        min_words=words,
    )


class TestPromptOffersTheEscapeFromGuessing:
    def test_the_exact_fallback_phrase_reaches_the_prompt(self):
        """הניסוח מוכתב מילה במילה: המשתמש ביקש דווקא אותו, והבדיקות למטה
        (ובדיקת הדיוק החיה) מזהות אותו כמחרוזת."""
        assert UNKNOWN_SPEAKER in _prompt()

    def test_prompt_ties_the_phrase_to_lack_of_certainty(self):
        """בלי התנאי המפורש המודל מתייחס לזה כאל ניסוח סגנוני חלופי ומפזר
        אותו גם כשהוא כן יודע מי דיבר."""
        out = _prompt()
        assert "אינכם בטוחים" in out
        assert re.search(rf"אינכם בטוחים.{{0,80}}{re.escape(UNKNOWN_SPEAKER)}", out, re.S)

    def test_prompt_states_that_a_wrong_name_is_worse_than_no_name(self):
        """זו הנחיית סדר העדיפויות שמחליפה את הלחץ לנקוב בשם בכל מחיר."""
        assert "דיוק הייחוס חשוב יותר מהייחוס עצמו" in _prompt()

    def test_prompt_anchors_attribution_to_the_transcript_label(self):
        assert "התווית שבתחילת השורה בתמלול" in _prompt()

    def test_prompt_still_demands_a_name_when_it_is_known(self):
        """הכיוון ההפוך: "אחד הדוברים" לא אמור להפוך לברירת מחדל נוחה
        שמייתרת את הבדיקה בתמלול."""
        assert "לא כברירת מחדל" in _prompt()

    def test_prompt_forbids_the_phrase_as_a_todo_owner(self):
        out = _prompt()
        assert "בשדה owner של משימה אין לכתוב אותו" in out

    def test_phrase_is_not_a_speaker_label_in_the_transcript(self):
        """אילו זו הייתה תווית, החלפת שם דובר הייתה בולעת אותה - ראה
        TestRenamingLeavesThePhraseAlone."""
        assert not re.fullmatch(r"דובר \d+", UNKNOWN_SPEAKER)


class TestPhraseDoesNotLeakOutOfTheSummary:
    """גוף הסיכום הוא המקום היחיד שבו "אחד הדוברים" אומר משהו. בעמודת
    "אחראי" של גיליון המשימות הוא רעש - אי אפשר להטיל משימה על "אחד
    הדוברים" - וברשימת הדוברים הוא היה מופיע במסך "עריכת דוברים" כאילו הוא
    אדם שצריך למלא לו שם."""

    @pytest.fixture
    def summarize_returning(self, monkeypatch):
        def _run(parsed: _MeetingSummary):
            fake_client = SimpleNamespace(
                models=SimpleNamespace(generate_content=lambda **kwargs: None)
            )
            # מוחלף בשם שבתוך summarize בלבד ולא ב-google.genai עצמו: אותו
            # מודול משמש גם את transcription.py, ותיקון גלובלי היה דולף לשם.
            monkeypatch.setattr(
                summarize, "genai", SimpleNamespace(Client=lambda **kwargs: fake_client)
            )
            monkeypatch.setattr(
                summarize,
                "call_with_retry",
                lambda fn, **kwargs: SimpleNamespace(parsed=parsed, text=""),
            )
            return summarize.summarize_and_extract_todos(
                _segs([("דובר 1", "שלום"), ("דובר 2", "היי")]), "2026-08-13"
            )

        return _run

    def _summary(self, **kwargs) -> _MeetingSummary:
        return _MeetingSummary(title="פגישה", summary="1. נושא: ...", **kwargs)

    def test_phrase_as_owner_becomes_no_owner(self, summarize_returning):
        todos = [TodoItem(description="לשלוח חוזה", owner=UNKNOWN_SPEAKER)]
        _, _, todos, _ = summarize_returning(self._summary(todos=todos))
        assert todos[0].owner is None

    def test_phrase_with_stray_whitespace_is_caught_too(self, summarize_returning):
        todos = [TodoItem(description="לשלוח חוזה", owner=f"  {UNKNOWN_SPEAKER} ")]
        _, _, todos, _ = summarize_returning(self._summary(todos=todos))
        assert todos[0].owner is None

    def test_a_real_owner_is_left_alone(self, summarize_returning):
        todos = [
            TodoItem(description="לשלוח חוזה", owner="דובר 2"),
            TodoItem(description="להזמין ציוד", owner=UNKNOWN_SPEAKER),
            TodoItem(description="לבדוק מול הספק"),
        ]
        _, _, todos, _ = summarize_returning(self._summary(todos=todos))
        assert [t.owner for t in todos] == ["דובר 2", None, None]

    def test_phrase_is_not_accepted_as_a_speaker_name(self, summarize_returning):
        names = [_SpeakerName(label="דובר 1", name=UNKNOWN_SPEAKER)]
        *_, speaker_names = summarize_returning(self._summary(speaker_names=names))
        assert speaker_names == {}

    def test_phrase_is_not_accepted_as_a_speaker_label(self, summarize_returning):
        names = [_SpeakerName(label=UNKNOWN_SPEAKER, name="דנה")]
        *_, speaker_names = summarize_returning(self._summary(speaker_names=names))
        assert speaker_names == {}

    def test_genuine_speaker_names_still_come_through(self, summarize_returning):
        names = [
            _SpeakerName(label="דובר 1", name="דנה"),
            _SpeakerName(label="דובר 2", name=UNKNOWN_SPEAKER),
        ]
        *_, speaker_names = summarize_returning(self._summary(speaker_names=names))
        assert speaker_names == {"דובר 1": "דנה"}

    def test_summary_text_keeps_the_phrase(self, summarize_returning):
        """הסינון למעלה נוגע בשדות בלבד - בסיכום עצמו הניסוח הוא התוצר
        המבוקש ואסור לגעת בו."""
        text = f"1. תקציב: {UNKNOWN_SPEAKER} ביקש לדחות את ההחלטה."
        parsed = _MeetingSummary(title="פגישה", summary=text)
        _, summary, _, _ = summarize_returning(parsed)
        assert summary == text


class TestRenamingLeavesThePhraseAlone:
    """כשהמשתמש ממלא שמות במסך "עריכת דוברים", כל תווית בסיכום מוחלפת
    בחיפוש-והחלפה מילולי (ראה speakers.replace_labels). "אחד הדוברים" לא
    אמור להיסחף איתם - אחרת ההודאה בחוסר ודאות הייתה הופכת בדיוק לייחוס
    השגוי שהיא באה למנוע."""

    def test_numbered_labels_do_not_match_inside_the_phrase(self):
        text = f"1. תקציב: {UNKNOWN_SPEAKER} ביקש דחייה, ודובר 1 הסכים."
        out = replace_labels(text, {"דובר 1": "מאיר", "דובר 2": "דנה"})
        assert out == f"1. תקציב: {UNKNOWN_SPEAKER} ביקש דחייה, ומאיר הסכים."

    def test_call_labels_do_not_match_inside_the_phrase(self):
        text = f"{UNKNOWN_SPEAKER} ביקש את הכתובת, והצד השני הבטיח לשלוח."
        out = replace_labels(text, {"הצד השני": "דנה", "אני": "מאיר"})
        assert out == f"{UNKNOWN_SPEAKER} ביקש את הכתובת, ודנה הבטיח לשלוח."

    def test_editing_a_recording_preserves_the_phrase_in_the_summary(self, monkeypatch):
        """המסלול המלא של מסך ההיסטוריה, ולא רק פונקציית ההחלפה."""
        import app.pipeline.edit as edit

        monkeypatch.setattr(edit.firestore_store, "update_recording_fields", lambda *a, **k: None)
        for name in ("rename_folder", "update_text_doc", "update_summary_doc"):
            monkeypatch.setattr(edit.drive, name, lambda *a, **k: None)

        recording = {
            "transcript": [{"speaker_label": "דובר 1", "text": "שלום"}],
            "summary": f"1. פתיחה: {UNKNOWN_SPEAKER} העלה את הנושא. דובר 1 השיב.",
        }
        result = apply_update(
            "rec1", recording, RecordingUpdateRequest(speaker_renames={"דובר 1": "מאיר"})
        )
        assert UNKNOWN_SPEAKER in result["summary"]
        assert "מאיר השיב" in result["summary"]
