"""בדיקות שפה מול Gemini אמיתי - `pytest --live` בלבד (עולה כסף, דורש רשת).

מייצרות אודיו סינתטי (ראה tests/audio_fixtures.py) ומריצות עליו את פונקציות
התמלול האמיתיות. זה מה שבדיקת הפרומפט הטקסטואלית לא יכולה לתפוס: אם גרסת
מודל חדשה תתחיל לתרגם פגישה באנגלית לעברית, רק בדיקה על אודיו אמיתי תיפול.

נמדד ב-2026-08-11 (gemini-3.1-flash-lite): אנגלית נשארה אנגלית במלואה;
בשיחה מעורבת נשמרו deployment / production / downtime / invite / roadmap /
invoice / purchase order באותיות לטיניות. חריג ידוע: מילת שאולה שנהגית
בעברית ("budget") עדיין מתועתקת לפעמים ל"באדג'ט" - ולכן הבדיקות דורשות רוב
מוחלט של מונחים ששרדו, לא 100%.
"""

import pytest

from app.pipeline.summarize import summarize_and_extract_todos
from app.pipeline.transcription import (
    transcribe_single_channel,
    transcribe_with_diarization,
)
from tests import audio_fixtures

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not audio_fixtures.credentials_available(),
        reason="דורש backend/service-account.json לייצור האודיו (Cloud TTS)",
    ),
]

def _has_hebrew(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in text)


def _has_latin(text: str) -> bool:
    return any(("a" <= ch <= "z") or ("A" <= ch <= "Z") for ch in text)


def _transcript_text(segments) -> str:
    return " ".join(s.text for s in segments)


@pytest.fixture(scope="module")
def english_segments(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("audio") / "english.wav")
    audio_fixtures.synthesize(audio_fixtures.ENGLISH_MEETING, path)
    return transcribe_with_diarization(path)


@pytest.fixture(scope="module")
def mixed_segments(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("audio") / "mixed.wav")
    audio_fixtures.synthesize(audio_fixtures.MIXED_MEETING, path)
    return transcribe_with_diarization(path)


@pytest.fixture(scope="module")
def mixed_call_segments(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("audio") / "call.wav")
    audio_fixtures.synthesize(audio_fixtures.MIXED_CALL_CHANNEL, path)
    return transcribe_single_channel(path, "אני", 1)


class TestEnglishStaysEnglish:
    def test_transcript_is_not_translated_to_hebrew(self, english_segments):
        text = _transcript_text(english_segments)
        assert not _has_hebrew(text), f"התמלול תורגם/תועתק לעברית: {text}"

    def test_transcript_is_not_empty(self, english_segments):
        assert len(_transcript_text(english_segments).split()) >= 30

    def test_content_actually_transcribed(self, english_segments):
        text = _transcript_text(english_segments).lower()
        assert "contractor" in text and "47,200" in text.replace(" ", "")

    def test_speaker_labels_are_generic_before_naming(self, english_segments):
        """התיוג בשלב התמלול הוא "דובר N" בכל שפה - השם האמיתי מגיע רק
        משלב הסיכום (ראה pipeline._apply_speaker_names)."""
        assert {s.speaker_label for s in english_segments} == {"דובר 1", "דובר 2"}


class TestMixedStaysMixed:
    def test_transcript_contains_both_scripts(self, mixed_segments):
        text = _transcript_text(mixed_segments)
        assert _has_hebrew(text), "העברית נעלמה מהתמלול"
        assert _has_latin(text), "האנגלית תועתקה לעברית או תורגמה"

    def test_english_terms_keep_latin_script(self, mixed_segments):
        text = _transcript_text(mixed_segments).lower()
        terms = ["deployment", "production", "downtime", "invite", "roadmap"]
        kept = [t for t in terms if t in text]
        assert len(kept) >= 4, f"רק {kept} שרדו באותיות לטיניות מתוך {terms}"

    def test_full_english_sentence_is_not_translated(self, mixed_segments):
        text = _transcript_text(mixed_segments).lower()
        assert "client" in text and "tuesday" in text

    def test_hebrew_sentences_remain_hebrew(self, mixed_segments):
        text = _transcript_text(mixed_segments)
        assert "בוקר טוב" in text


class TestCallChannelFollowsSameRule:
    """מסלול השיחה משתמש בפרומפט נפרד - בעבר קל היה לתקן אחד ולשכוח את השני."""

    def test_mixed_call_keeps_both_scripts(self, mixed_call_segments):
        text = _transcript_text(mixed_call_segments)
        assert _has_hebrew(text) and _has_latin(text), text

    def test_english_terms_keep_latin_script(self, mixed_call_segments):
        text = _transcript_text(mixed_call_segments).lower()
        assert "invoice" in text or "purchase order" in text, text

    def test_english_sentence_is_not_translated(self, mixed_call_segments):
        assert "confirm" in _transcript_text(mixed_call_segments).lower()


@pytest.fixture(scope="module")
def summary_of_english(english_segments):
    return summarize_and_extract_todos(english_segments, "2026-08-11")


class TestSummaryIsAlwaysHebrew:
    """גם כשהתמלול אנגלי לגמרי - הסיכום, הכותרת והמשימות בעברית."""

    def test_summary_is_hebrew(self, summary_of_english):
        _, summary, _, _ = summary_of_english
        assert _has_hebrew(summary), summary

    def test_title_is_hebrew(self, summary_of_english):
        title, _, _, _ = summary_of_english
        assert _has_hebrew(title), title

    def test_todos_are_hebrew(self, summary_of_english):
        _, _, todos, _ = summary_of_english
        assert todos, "לא חולצו משימות מהפגישה האנגלית"
        assert all(_has_hebrew(t.description) for t in todos), [t.description for t in todos]

    def test_numbers_survive_the_translation(self, summary_of_english):
        """הסיכום מתרגם לעברית - אבל הסכומים חייבים לשרוד במדויק."""
        _, summary, _, _ = summary_of_english
        assert "47,200" in summary or "47200" in summary, summary
