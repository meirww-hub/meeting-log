"""אורך ההקלטה נלקח מהאודיו, לא מסוף הדיבור האחרון.

הרקע: שיחה בת 3:57 ל-*5525 נרשמה כ-125 שניות, כי אחרי 125 שניות הייתה רק
המתנה למוקד. התג באפליקציה הראה אורך שגוי, והניקוי האוטומטי - שמוחק
הקלטות קצרות שלא נערכו - ראה אותה כקצרה ועמד למחוק אותה.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.models import TranscriptSegment
from app.pipeline.pipeline import _duration_of


def segments(*ends: float) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            text="דיבור",
            start_seconds=max(0.0, end - 1),
            end_seconds=end,
            speaker_tag=1,
            speaker_label="אני",
        )
        for end in ends
    ]


def test_measured_audio_length_wins_over_last_spoken_word():
    # המקרה האמיתי: 237 שניות של שיחה, דיבור אחרון בשנייה 125.
    assert _duration_of(236.7, segments(2.2, 45.9, 125.13)) == 236.7


def test_falls_back_to_the_transcript_when_nothing_was_measured():
    """הקלטה מגרסת אפליקציה ישנה, או קובץ שלא ניתן היה למדוד."""
    assert _duration_of(0.0, segments(2.2, 45.9, 125.13)) == 125.13


def test_recording_without_speech_and_without_measurement_is_zero():
    assert _duration_of(0.0, []) == 0.0


def test_measurement_survives_a_recording_with_no_speech_at_all():
    """שיחה שכולה המתנה: התמלול ריק, אבל האורך האמיתי ידוע ולא אפס.

    בלי זה היא נרשמת כאפס שניות - כלומר נמחקת אוטומטית בסבב הבא.
    """
    assert _duration_of(212.0, []) == 212.0


@pytest.fixture
def upload_calls(monkeypatch):
    """קולט את הארגומנטים שהועברו לפייפליין, במקום להריץ אותו."""
    settings.backend_api_key = "test-key"
    calls: list[tuple] = []

    monkeypatch.setattr(main.firestore_store, "set_recording_status", lambda *a, **k: None)
    monkeypatch.setattr(main.firestore_store, "get_recording", lambda *a, **k: None)
    monkeypatch.setattr(main, "process_recording", lambda *a: calls.append(("single",) + a))
    monkeypatch.setattr(main, "process_call_recording", lambda *a: calls.append(("call",) + a))
    return TestClient(main.app), calls


def upload(client, files, **form):
    return client.post(
        "/recordings",
        headers={"X-API-Key": "test-key"},
        data={"user_id": "primary_user", **form},
        files=files,
    )


def audio(name="call.m4a"):
    return (name, io.BytesIO(b"audio-bytes"), "audio/mp4")


def test_meeting_upload_forwards_the_measured_length(upload_calls):
    client, calls = upload_calls

    upload(client, {"file": audio()}, duration_seconds="236.7")

    assert calls[0][0] == "single"
    assert calls[0][-1] == 236.7


def test_call_upload_forwards_the_measured_length(upload_calls):
    """שיחה דו-ערוצית - הנתיב שבו הפרמטר בא אחרי contact_name."""
    client, calls = upload_calls

    upload(
        client,
        {"file": audio("uplink.m4a"), "file_downlink": audio("downlink.m4a")},
        contact_name="גדעון",
        duration_seconds="236.7",
    )

    assert calls[0][0] == "call"
    # השם והאורך לא התחלפו במסלול הפוזיציוני אל הפייפליין.
    assert calls[0][-2:] == ("גדעון", 236.7)


def test_upload_without_a_measurement_passes_zero(upload_calls):
    """גרסת אפליקציה ישנה: אין שדה, והשרת נופל חזרה לחישוב מהתמלול."""
    client, calls = upload_calls

    upload(client, {"file": audio()})

    assert calls[0][-1] == 0.0
