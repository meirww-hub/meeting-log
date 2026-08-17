"""תמלול ששב קטוע לא מאבד את ההקלטה.

זה מה שקרה בפועל ב-2026-08-13: תשובת המודל לשיחה בת 28 דקות נחתכה באמצע
מחרוזת, `json.loads` נפל על "Unterminated string starting at: line 576",
והשיחה כולה ירדה לטמיון. הבדיקות כאן נועלות את שתי ההתנהגויות שמונעות את
זה - שחזור הרישא התקינה, והמשך התמלול מהנקודה שנקטעה.
"""

import json

import pytest

from app.pipeline import transcription


def _segments_json(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, indent=2)


def _items(count: int, *, start: int = 0) -> list[dict]:
    return [
        {
            "text": f"משפט מספר {i}",
            "start_seconds": float(i * 10),
            "end_seconds": float(i * 10 + 9),
        }
        for i in range(start, start + count)
    ]


class _FakeCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text, finish_reason="STOP"):
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)]


# ---------- שחזור רישא תקינה מתשובה קטועה ----------


def test_complete_response_parses_as_is():
    items, truncated = transcription._decode_items(_segments_json(_items(3)))

    assert truncated is False
    assert len(items) == 3


def test_truncated_mid_string_keeps_the_complete_prefix():
    """בדיוק התקלה שנצפתה: התשובה נחתכה בתוך ערך "text"."""
    cut = _segments_json(_items(50))[:1200] + 'עוד קצת טקסט שנקטע כא'

    items, truncated = transcription._decode_items(cut)

    assert truncated is True
    # לא מאבדים את הכול בגלל הקטע האחרון.
    assert len(items) > 0
    assert items[0]["text"] == "משפט מספר 0"
    # וכל מה שנשמר הוא קטע שלם ותקין.
    assert all(set(i) == {"text", "start_seconds", "end_seconds"} for i in items)


def test_empty_response_is_reported_as_truncated():
    assert transcription._decode_items("") == ([], True)


def test_closing_brace_inside_spoken_text_does_not_break_salvage():
    spoken = [{"text": "אמרתי } בסוגריים", "start_seconds": 0.0, "end_seconds": 1.0}]
    items, truncated = transcription._decode_items(_segments_json(spoken))

    assert truncated is False
    assert items[0]["text"] == "אמרתי } בסוגריים"


# ---------- המשך התמלול אחרי קטיעה ----------


@pytest.fixture
def fake_gemini(monkeypatch):
    """מחליף את הקריאה למודל בתשובות מוכנות מראש, ומתעד את הפרומפטים."""
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
    # הבדיקות כאן על היגיון הקטיעה/ההמשך בלבד - לא על בדיקת השקט (ראה
    # test_transcription_silence.py), ואין להן קובץ אודיו אמיתי ל-ffmpeg.
    monkeypatch.setattr(transcription.speaker_embedding, "segment_is_silent", lambda *a: False)
    return type("Fake", (), {"calls": calls, "responses": responses})()


def test_truncated_transcription_continues_from_where_it_stopped(fake_gemini, tmp_path):
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"audio")

    # הקטע החמישי נחתך באמצע, ולכן הרישא שנשמרת מסתיימת בשנייה 39.
    fake_gemini.responses.append(
        _FakeResponse(_segments_json(_items(5))[:-3], finish_reason="MAX_TOKENS")
    )
    # ההמשך מתחיל מהקטע שנקטע - כולל אותו, לא אחריו.
    fake_gemini.responses.append(
        _FakeResponse(_segments_json(_items(5, start=4)), finish_reason="STOP")
    )

    segments = transcription.transcribe_single_channel(str(audio), "אני", 1)

    assert len(fake_gemini.calls) == 2, "התמלול הקטוע היה צריך בקשת המשך"
    # הבקשה השנייה אומרת למודל מהיכן להמשיך.
    assert "39" in fake_gemini.calls[1]["contents"][1]
    # ההקלטה שלמה: גם הרישא שנשמרה וגם ההמשך.
    assert [s.text for s in segments][0] == "משפט מספר 0"
    assert [s.text for s in segments][-1] == "משפט מספר 8"
    assert len(segments) == 9


def test_continuation_drops_segments_already_transcribed(fake_gemini, tmp_path):
    """המודל פותח מוקדם מהמבוקש - אסור שהתמלול יכפיל משפטים."""
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"audio")

    fake_gemini.responses.append(
        _FakeResponse(_segments_json(_items(5))[:-3], finish_reason="MAX_TOKENS")
    )
    # חוזר אחורה על שני קטעים שכבר יש לנו.
    fake_gemini.responses.append(
        _FakeResponse(_segments_json(_items(4, start=3)), finish_reason="STOP")
    )

    segments = transcription.transcribe_single_channel(str(audio), "אני", 1)

    texts = [s.text for s in segments]
    assert texts == sorted(set(texts), key=texts.index), "יש כפילויות בתמלול"
    assert texts[-1] == "משפט מספר 6"


def test_stuck_model_fails_loudly_instead_of_returning_half(fake_gemini, tmp_path):
    """מודל שנתקע ומחזיר שוב את אותו זנב לא מייצר לולאה אינסופית - **וגם לא
    תמלול חלקי שנראה כמו הצלחה**. הקלטה שנשמרה כ-done עם חצי תוכן היא בדיוק
    האובדן השקט שהמנגנון הזה נועד למנוע."""
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"audio")

    for _ in range(transcription._MAX_CONTINUATIONS + 2):
        fake_gemini.responses.append(
            _FakeResponse(_segments_json(_items(3)), finish_reason="MAX_TOKENS")
        )

    with pytest.raises(transcription.IncompleteTranscriptError):
        transcription.transcribe_single_channel(str(audio), "אני", 1)

    assert len(fake_gemini.calls) == 2, "סבב שלא הוסיף כלום היה צריך לעצור"


def test_exhausting_all_continuations_fails_loudly(fake_gemini, tmp_path):
    """גם כשכל סבב מוסיף תוכן, תמלול שלא הגיע לסוף ההקלטה אינו הצלחה."""
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"audio")

    for round_index in range(transcription._MAX_CONTINUATIONS):
        fake_gemini.responses.append(
            _FakeResponse(
                _segments_json(_items(3, start=round_index * 3)),
                finish_reason="MAX_TOKENS",
            )
        )

    with pytest.raises(transcription.IncompleteTranscriptError):
        transcription.transcribe_single_channel(str(audio), "אני", 1)

    assert len(fake_gemini.calls) == transcription._MAX_CONTINUATIONS


def test_model_restarting_from_zero_supersedes_the_partial_attempt(
    fake_gemini, tmp_path
):
    """המודל מתעלם מבקשת ההמשך ומתמלל מחדש מההתחלה.

    בלי הטיפול הזה כל הקטעים שהוא החזיר היו נופלים בסינון לפי חותמת הזמן,
    הלולאה הייתה נעצרת - וההקלטה נשמרת חתוכה כאילו הכל תקין.
    """
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"audio")

    fake_gemini.responses.append(
        _FakeResponse(_segments_json(_items(3))[:-3], finish_reason="MAX_TOKENS")
    )
    # ניסיון שלם מחדש מההתחלה, ארוך יותר ממה שכבר בידינו.
    fake_gemini.responses.append(_FakeResponse(_segments_json(_items(9))))

    segments = transcription.transcribe_single_channel(str(audio), "אני", 1)

    texts = [s.text for s in segments]
    assert texts == [f"משפט מספר {i}" for i in range(9)], "הניסיון השלם לא אומץ"


def test_every_request_sends_an_explicit_output_ceiling(fake_gemini, tmp_path):
    """הפרמטר שחסר היה הוא שגרם לקטיעה מלכתחילה."""
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"audio")
    fake_gemini.responses.append(_FakeResponse(_segments_json(_items(2))))

    transcription.transcribe_single_channel(str(audio), "אני", 1)

    config = fake_gemini.calls[0]["config"]
    assert config.max_output_tokens == transcription._MAX_OUTPUT_TOKENS


def test_diarized_continuation_carries_speaker_numbering(fake_gemini, tmp_path):
    """בהמשך של פגישה, המודל חייב לדעת איזה מספר שייך למי."""
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"audio")

    first = [
        {"speaker_tag": 1, "text": "שלום", "start_seconds": 0.0, "end_seconds": 5.0},
        {"speaker_tag": 2, "text": "מה נשמע", "start_seconds": 5.0, "end_seconds": 9.0},
    ]
    second = [
        {"speaker_tag": 2, "text": "בסדר גמור", "start_seconds": 10.0, "end_seconds": 14.0}
    ]
    fake_gemini.responses.append(
        _FakeResponse(_segments_json(first)[:-3], finish_reason="MAX_TOKENS")
    )
    fake_gemini.responses.append(_FakeResponse(_segments_json(second)))

    segments = transcription.transcribe_with_diarization(str(audio))

    resume_prompt = fake_gemini.calls[1]["contents"][1]
    assert "שלום" in resume_prompt, "הזנב שתומלל לא נשלח כהקשר"
    assert [s.speaker_label for s in segments][-1] == "דובר 2"
