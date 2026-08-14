"""בדיקות מהירות (ללא רשת) למדיניות השפה של התמלול.

הרגרסיה שהן שומרות עליה: עד 2026-08-11 הפרומפט הכיל "תמלל בשפה he-IL"
(מתוך הגדרת `TRANSCRIPTION_LANGUAGE`), כלומר ביקש מהמודל במפורש לתרגם כל
הקלטה שאינה בעברית. זה לא התפוצץ רק כי המודל התעלם מההוראה. הבדיקות כאן
מוודאות שההוראה לא חוזרת בדלת האחורית ושהכלל זהה בשני מסלולי התמלול.
"""

import re

from app.config import Settings
from app.pipeline import transcription
from app.pipeline.summarize import _SYSTEM_PROMPT as SUMMARY_SYSTEM_PROMPT


def _diarization_prompt() -> str:
    return transcription._SCHEMA_HINT.format(language_rule=transcription._LANGUAGE_RULE)


def _single_channel_prompt() -> str:
    return transcription._SINGLE_CHANNEL_SCHEMA_HINT.format(
        language_rule=transcription._LANGUAGE_RULE
    )


class TestNoForcedLanguage:
    """אסור שתחזור הוראה שמכתיבה שפת תמלול קבועה."""

    def test_no_bcp47_language_code_in_prompts(self):
        # "he-IL" / "en-US" וכל וריאנט אחר של קוד שפה
        bcp47 = re.compile(r"\b[a-z]{2}-[A-Z]{2}\b")
        for prompt in (_diarization_prompt(), _single_channel_prompt()):
            assert not bcp47.search(prompt), f"קוד שפה קשיח בפרומפט: {prompt}"

    def test_settings_has_no_transcription_language(self):
        assert "transcription_language" not in Settings.model_fields


class TestLanguageRuleReachesBothPaths:
    """שני המסלולים - פגישה (diarization) ושיחה (ערוץ מבודד) - חולקים כלל אחד,
    כדי שתיקון באחד לא ידלג על השני."""

    def test_diarization_prompt_carries_the_rule(self):
        assert transcription._LANGUAGE_RULE in _diarization_prompt()

    def test_single_channel_prompt_carries_the_rule(self):
        assert transcription._LANGUAGE_RULE in _single_channel_prompt()

    def test_rule_forbids_translation_and_transliteration(self):
        rule = transcription._LANGUAGE_RULE
        assert "אל תתרגם" in rule
        assert "לתעתק" in rule

    def test_prompts_still_format_cleanly(self):
        """הכלל מוזרק ב-.format() לתוך תבנית שמכילה JSON עם סוגריים מסולסלים -
        טעות בהכפלת הסוגריים תישבר כאן ולא בפרודקשן."""
        for prompt in (_diarization_prompt(), _single_channel_prompt()):
            assert '"text"' in prompt and "{{" not in prompt


class TestSummaryStaysHebrew:
    """התמלול הולך אחרי שפת הדיבור, אבל הסיכום והמשימות תמיד בעברית -
    דרישה מפורשת של המשתמש (2026-08-11)."""

    def test_system_prompt_demands_hebrew(self):
        assert "בעברית" in SUMMARY_SYSTEM_PROMPT

    def test_system_prompt_covers_non_hebrew_transcripts(self):
        assert "אנגלית" in SUMMARY_SYSTEM_PROMPT
