"""בדיקות מהירות (ללא רשת) לחישוב היקף הסיכום ולבניית הפרומפט.

מקבעות את התכונות של העקומה, לא ערכים שרירותיים: מונוטוניות, רגישות לצפיפות
נתונים, והרצפות/תקרות שמגינות מהמקרים הקיצוניים. כוונון המקדמים מותר - שבירת
התכונות האלה לא.
"""

import re

import pytest

from app.pipeline.summarize import (
    _TASK_SCHEMA_HINT,
    _figure_count,
    _summary_budget,
    _word_count,
)
from tests.transcripts import LONG, SHORT, _segs

# ~130 מילות דיבור לדקה בעברית, ~4% מהן עם ספרה (צפיפות בינונית)
def _minutes(m: float) -> tuple[int, int]:
    words = int(m * 130)
    return words, round(words * 0.04)


class TestProportionality:
    def test_longer_conversation_gets_longer_summary(self):
        """הדרישה המרכזית: הסיכום גדל עם אורך השיחה."""
        prev = 0
        for m in (2, 10, 30, 60, 120):
            _, words = _summary_budget(*_minutes(m))
            assert words > prev, f"{m} דק' לא קיבלה יותר מהאורך הקודם"
            prev = words

    def test_more_topics_for_longer_conversation(self):
        assert _summary_budget(*_minutes(2))[0] < _summary_budget(*_minutes(60))[0]

    def test_growth_is_sublinear(self):
        """שיחה ארוכה פי 10 לא מקבלת סיכום ארוך פי 10 - יחס הדחיסה גדל."""
        short_w, short_f = _minutes(3)
        long_w, long_f = _minutes(30)
        ratio = _summary_budget(long_w, long_f)[1] / _summary_budget(short_w, short_f)[1]
        assert 1 < ratio < (long_w / short_w), "הגידול חייב להיות תת-לינארי אך חיובי"

    def test_compression_ratio_tightens_with_length(self):
        r2 = _summary_budget(*_minutes(2))[1] / _minutes(2)[0]
        r60 = _summary_budget(*_minutes(60))[1] / _minutes(60)[0]
        assert r60 < r2, "שיחה ארוכה צריכה להידחס חזק יותר, לא פחות"


class TestDataDensity:
    def test_denser_conversation_gets_more_room(self):
        """אותו אורך, יותר נתונים -> יותר מקום. זה מה שאורך לבדו מפספס."""
        sparse = _summary_budget(3900, 20)[1]
        dense = _summary_budget(3900, 320)[1]
        assert dense > sparse * 1.4

    def test_density_discriminates_across_realistic_range(self):
        """התוספת לא נחסמת מיד בתקרה - אחרת היא מפסיקה להבדיל."""
        vals = [_summary_budget(3900, round(3900 * p))[1] for p in (0.005, 0.02, 0.04, 0.08)]
        assert vals == sorted(vals) and len(set(vals)) == len(vals)


class TestGuardrails:
    def test_tiny_recording_summary_never_exceeds_transcript(self):
        """הקלטה של חצי דקה לא תקבל יעד ארוך מהתמלול עצמו - זו הזמנה לריפוד."""
        for words in (10, 40, 65, 120, 175):
            _, target = _summary_budget(words, round(words * 0.05))
            assert target <= max(words, 25), f"{words} מילים -> יעד {target}"

    def test_empty_transcript_does_not_crash(self):
        topics, words = _summary_budget(0, 0)
        assert topics >= 2 and words > 0

    def test_topics_stay_in_sane_bounds(self):
        for m in (0.1, 1, 30, 120, 600):
            topics, _ = _summary_budget(*_minutes(m))
            assert 2 <= topics <= 16


class TestCounters:
    def test_word_count_ignores_speaker_labels(self):
        segs = _segs([("דובר מספר אחד", "שלום מה נשמע"), ("אני", "הכל טוב")])
        assert _word_count(segs) == 5

    def test_figure_count_counts_words_containing_digits(self):
        segs = _segs([("אני", "המחיר הוא 4,200 שקל ב-15 בספטמבר")])
        assert _figure_count(segs) == 2

    def test_figure_count_zero_without_numbers(self):
        assert _figure_count(_segs([("אני", "שיחה בלי שום נתון מספרי")])) == 0


class TestPromptRendering:
    def _render(self, word_count=3900, figure_count=156):
        topics, words = _summary_budget(word_count, figure_count)
        return _TASK_SCHEMA_HINT.format(
            today="2026-08-11",
            word_count=word_count,
            figure_count=figure_count,
            min_topics=topics,
            min_words=words,
        )

    def test_no_unfilled_placeholders(self):
        assert not re.findall(r"(?<!\{)\{[a-z_]+\}", self._render())

    def test_json_schema_braces_survive_format(self):
        """הסכמה בפרומפט משתמשת ב-{{ }} - קל לשבור אותה בטעות בעריכה."""
        out = self._render()
        assert out.count("{") == out.count("}")
        assert '"title"' in out and '"speaker_names"' in out

    def test_attribution_rule_demands_the_exact_transcript_label(self):
        """הסיכום מייחס אמירות בשם הדובר, והשם מוחלף אחר כך בחיפוש מילולי -
        אז הפרומפט חייב לדרוש את הכתיב המדויק של התווית (ראה edit.py)."""
        out = self._render()
        assert "אמר" in out and "בדיוק כפי שהוא מופיע בתמלול" in out

    def test_computed_numbers_reach_the_prompt(self):
        topics, words = _summary_budget(3900, 156)
        out = self._render()
        assert str(words) in out and str(topics) in out

    @pytest.mark.parametrize("segments", [SHORT, LONG])
    def test_renders_for_real_transcripts(self, segments):
        out = self._render(_word_count(segments), _figure_count(segments))
        assert not re.findall(r"(?<!\{)\{[a-z_]+\}", out)
