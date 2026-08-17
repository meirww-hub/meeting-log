"""הגנה מפני תמלול מדומיין: כשהאודיו שקט בפועל, Gemini עלול "להמציא" שיחה
סבירה במקום לדווח שאין מה לתמלל - ראה HallucinatedTranscriptError.

נמצא ב-2026-08-17: הקלטה אמיתית נשמרה כ"done" עם תמלול ודוברים פיקטיביים,
כולל פרופיל דובר שהצביע לרגע שקט לגמרי (לא נשמע כלום במסך "דוברים לא
מזוהים" בלחיצה על פליי). הבדיקות כאן מדמות את בדיקת האנרגיה (בלי ffmpeg/
מודל אמיתי) ובודקות רק את ההחלטה מתי לזרוק - ראה TestSegmentIsSilent
ב-test_speaker_identification.py לבדיקת ה-RMS האמיתי על אודיו אמיתי.
"""

import json

import pytest

from app.pipeline import speaker_embedding, transcription


def _segments_json(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, indent=2)


class _FakeCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text, finish_reason="STOP"):
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)]


@pytest.fixture
def fake_gemini(monkeypatch):
    calls: list[dict] = []
    responses: list[_FakeResponse] = []

    class _FakeFiles:
        def upload(self, **kwargs):
            return "uploaded-file-handle"

    class _FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return responses[len(calls) - 1]

    class _FakeClient:
        def __init__(self, **kwargs):
            self.files = _FakeFiles()
            self.models = _FakeModels()

    monkeypatch.setattr(transcription.genai, "Client", _FakeClient)
    return type("Fake", (), {"calls": calls, "responses": responses})()


def _all_silent(monkeypatch):
    monkeypatch.setattr(speaker_embedding, "segment_is_silent", lambda *a: True)


def _none_silent(monkeypatch):
    monkeypatch.setattr(speaker_embedding, "segment_is_silent", lambda *a: False)


class TestDiarizedTranscriptSilenceCheck:
    def test_all_long_segments_silent_raises(self, fake_gemini, monkeypatch, tmp_path):
        _all_silent(monkeypatch)
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"audio")
        items = [
            {"speaker_tag": 1, "text": "שלום, מה שלומך היום?", "start_seconds": 0.0, "end_seconds": 2.1},
            {"speaker_tag": 2, "text": "טוב מאוד, תודה ששאלת", "start_seconds": 2.1, "end_seconds": 5.0},
        ]
        fake_gemini.responses.append(_FakeResponse(_segments_json(items)))

        with pytest.raises(transcription.HallucinatedTranscriptError):
            transcription.transcribe_with_diarization(str(audio))

    def test_one_long_segment_audible_does_not_raise(self, fake_gemini, monkeypatch, tmp_path):
        """מספיק קטע ארוך אחד עם אנרגיית שמע אמיתית כדי לבטוח בשאר התמלול -
        הקלטה אמיתית עלולה להיות שקטה בחלקה בלי שזה סימן להמצאה."""
        calls = []
        monkeypatch.setattr(
            speaker_embedding, "segment_is_silent",
            lambda audio_path, start, end: calls.append(start) or start != 0.0,
        )
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"audio")
        items = [
            {"speaker_tag": 1, "text": "שלום, מה שלומך היום?", "start_seconds": 0.0, "end_seconds": 2.1},
            {"speaker_tag": 2, "text": "טוב מאוד, תודה ששאלת", "start_seconds": 2.1, "end_seconds": 5.0},
        ]
        fake_gemini.responses.append(_FakeResponse(_segments_json(items)))

        segments = transcription.transcribe_with_diarization(str(audio))

        assert len(segments) == 2

    def test_only_short_segments_are_not_checked(self, fake_gemini, monkeypatch, tmp_path):
        """קטעים קצרים מ-1.5 שניות לא נבדקים כלל - RMS על קטע כזה לא אמין,
        ואסור שהם יתפסו הקלטה אמיתית עם משפטים קצרים בלבד."""
        _all_silent(monkeypatch)
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"audio")
        items = [
            {"speaker_tag": 1, "text": "כן", "start_seconds": 0.0, "end_seconds": 0.5},
            {"speaker_tag": 2, "text": "לא", "start_seconds": 0.5, "end_seconds": 1.0},
        ]
        fake_gemini.responses.append(_FakeResponse(_segments_json(items)))

        segments = transcription.transcribe_with_diarization(str(audio))

        assert len(segments) == 2


def test_no_segments_to_check_does_not_raise(monkeypatch):
    """אין קטעים ארוכים מספיק לבדיקה (או תמלול ריק לגמרי) - שום דבר לתפוס
    כהמצאה, ואין קריאה ל-ffmpeg בכלל."""
    monkeypatch.setattr(
        speaker_embedding, "segment_is_silent",
        lambda *a: pytest.fail("אסור לבדוק אנרגיה כשאין קטעים ארוכים מספיק"),
    )
    transcription._verify_segments_are_audible([], "audio.m4a")


class TestSingleChannelSilenceCheck:
    """אותה הגנה בדיוק במסלול השני - שיחת טלפון (ראה test_speaker_labels.py
    להערה על שכפול פרומפט/כלל בין שני המסלולים)."""

    def test_all_long_segments_silent_raises(self, fake_gemini, monkeypatch, tmp_path):
        _all_silent(monkeypatch)
        audio = tmp_path / "call.m4a"
        audio.write_bytes(b"audio")
        items = [{"text": "היי, מה קורה?", "start_seconds": 0.0, "end_seconds": 3.0}]
        fake_gemini.responses.append(_FakeResponse(_segments_json(items)))

        with pytest.raises(transcription.HallucinatedTranscriptError):
            transcription.transcribe_single_channel(str(audio), "אני", 1)

    def test_audible_segment_does_not_raise(self, fake_gemini, monkeypatch, tmp_path):
        _none_silent(monkeypatch)
        audio = tmp_path / "call.m4a"
        audio.write_bytes(b"audio")
        items = [{"text": "היי, מה קורה?", "start_seconds": 0.0, "end_seconds": 3.0}]
        fake_gemini.responses.append(_FakeResponse(_segments_json(items)))

        segments = transcription.transcribe_single_channel(str(audio), "אני", 1)

        assert len(segments) == 1
