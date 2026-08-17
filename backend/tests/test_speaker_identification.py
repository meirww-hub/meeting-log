"""בדיקות לזיהוי דוברים חוצה-הקלטות לפי טביעת קול (ראה pipeline/speaker_id.py).

מדמות את חנות הפרופילים (Firestore) ואת חילוץ ה-embedding (ffmpeg+ONNX) -
הבדיקות כאן בודקות רק את לוגיקת ההתאמה/המיזוג/הבחירה, בלי רשת ובלי מודל
אמיתי - מריצות מהר על כל שינוי (ראה pytest.ini). השוואת הדמיון עצמה
(cosine_similarity) כן רצה אמיתית, על וקטורים מלאכותיים פשוטים, כדי
לבדוק את הסף בפועל ולא רק את ה"צנרת" סביבו.

חילוץ embedding אמיתי (ffmpeg + מודל wespeaker) נבדק בנפרד למטה, ומדולג
אוטומטית אם המודל לא הורד מקומית - הוא רק בקונטיינר של Cloud Run
(ראה Dockerfile), לא בסביבת הפיתוח.
"""

import pathlib
import shutil
import wave

import numpy as np
import pytest

from app.models import TranscriptSegment
from app.pipeline import pipeline, speaker_embedding, speaker_id
from tests.transcripts import _segs


class FakeProfileStore:
    """מדמה את פונקציות ה-Firestore הרלוונטיות ב-firestore_store.py, ללא
    שום I/O אמיתי - כדי לבדוק את לוגיקת ההתאמה/המיזוג בבידוד."""

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self._next_id = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return f"profile{self._next_id}"

    def create_speaker_profile(
        self, user_id, embedding, recording_id, channel, start_seconds, name=None
    ):
        profile_id = self._new_id()
        self.profiles[profile_id] = {
            "profile_id": profile_id,
            "user_id": user_id,
            "name": name,
            "embedding": embedding,
            "sample_count": 1,
            "sample_recording_id": recording_id,
            "sample_channel": channel,
            "sample_start_seconds": start_seconds,
        }
        return profile_id

    def update_speaker_profile(self, profile_id, **fields):
        self.profiles[profile_id].update(fields)

    def list_speaker_profiles(self, user_id):
        return [dict(p) for p in self.profiles.values() if p["user_id"] == user_id]

    def find_speaker_profile_by_name(self, user_id, name):
        for p in self.profiles.values():
            if p["user_id"] == user_id and p.get("name") == name:
                return dict(p)
        return None

    def get_speaker_profile(self, profile_id):
        return self.profiles.get(profile_id)


@pytest.fixture
def store(monkeypatch):
    fake = FakeProfileStore()
    monkeypatch.setattr(speaker_id, "firestore_store", fake)
    return fake


# וקטורים מלאכותיים: A/A' הם שתי דגימות של אותו קול (כמעט אותו כיוון), B
# הוא כיוון ניצב לגמרי (דובר אחר, דמיון קוסינוס 0) - כך שהתוצאה לא תלויה
# בכיול העדין של settings.speaker_match_threshold (ברירת מחדל 0.6).
VOICE_A = [1.0, 0.0]
VOICE_A_AGAIN = [0.95, 0.05]
VOICE_B = [0.0, 1.0]


class TestEnrollKnown:
    def test_creates_a_new_named_profile(self, store):
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec1", 1, 0.0)
        profiles = store.list_speaker_profiles("u1")
        assert len(profiles) == 1
        assert profiles[0]["name"] == "דנה"
        assert profiles[0]["embedding"] == VOICE_A

    def test_merges_into_existing_profile_with_the_same_name(self, store):
        """שיחה שנייה עם אותו איש קשר לא יוצרת פרופיל כפול - היא משפרת
        את הפרופיל הקיים."""
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec1", 1, 0.0)
        speaker_id.enroll_known("u1", "דנה", VOICE_A_AGAIN, "rec2", 1, 5.0)
        profiles = store.list_speaker_profiles("u1")
        assert len(profiles) == 1
        assert profiles[0]["sample_count"] == 2
        assert profiles[0]["sample_recording_id"] == "rec2"


class TestResolveOrEnroll:
    def test_no_existing_profiles_creates_unnamed_one_and_returns_none(self, store):
        name = speaker_id.resolve_or_enroll("u1", VOICE_A, "rec1", 0, 3.0)
        assert name is None
        profiles = store.list_speaker_profiles("u1")
        assert len(profiles) == 1
        assert profiles[0]["name"] is None

    def test_matches_an_existing_named_profile_above_threshold(self, store):
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec1", 1, 0.0)
        name = speaker_id.resolve_or_enroll("u1", VOICE_A_AGAIN, "rec2", 0, 10.0)
        assert name == "דנה"
        assert len(store.list_speaker_profiles("u1")) == 1  # מוזג, לא שוכפל

    def test_a_different_voice_does_not_match_and_opens_its_own_profile(self, store):
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec1", 1, 0.0)
        name = speaker_id.resolve_or_enroll("u1", VOICE_B, "rec2", 0, 10.0)
        assert name is None
        assert len(store.list_speaker_profiles("u1")) == 2

    def test_moderate_similarity_to_a_named_profile_does_not_auto_assign_the_name(self, store):
        """אירוע 2026-08-17: קול של אישה זוהה בטעות כ"אמא". דמיון בינוני
        (מעל סף הצבירה הכללי אבל מתחת לסף המחמיר לשם) לא מספיק כדי להצמיד
        שם בפועל - עדיף להישאר "לא מזוהה" ולתת למשתמש להחליט. ה-embedding
        גם לא נצבר לתוך הפרופיל הקיים - כדי לא לזהם את טביעת הקול של "דנה"
        בדגימה שכנראה שייכת למישהי אחרת."""
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec1", 1, 0.0)
        moderate = [0.7, 0.7141428428542851]  # דמיון-קוסינוס ~0.7 מול VOICE_A
        name = speaker_id.resolve_or_enroll("u1", moderate, "rec2", 0, 10.0)
        assert name is None
        profiles = store.list_speaker_profiles("u1")
        assert len(profiles) == 2
        dana = next(p for p in profiles if p["name"] == "דנה")
        assert dana["sample_count"] == 1  # לא עודכן

    def test_the_same_unidentified_voice_accumulates_under_one_profile(self, store):
        """החלטת "מיקבוץ אחד": אותו קול לא-מזוהה שחוזר בכמה הקלטות לא
        ייצר שורה נפרדת בכל פעם במסך "דוברים לא מזוהים"."""
        speaker_id.resolve_or_enroll("u1", VOICE_A, "rec1", 0, 1.0)
        speaker_id.resolve_or_enroll("u1", VOICE_A_AGAIN, "rec2", 0, 2.0)
        profiles = store.list_speaker_profiles("u1")
        assert len(profiles) == 1
        assert profiles[0]["sample_count"] == 2

    def test_users_do_not_see_each_others_profiles(self, store):
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec1", 1, 0.0)
        name = speaker_id.resolve_or_enroll("u2", VOICE_A_AGAIN, "rec2", 0, 0.0)
        assert name is None


class TestRepresentativeEmbedding:
    def test_picks_the_longest_segments_and_caps_at_three(self, monkeypatch):
        segments = _segs([
            ("דובר 1", "קצר"),
            ("דובר 1", "משפט ארוך בהרבה יותר מהראשון בכמות המילים שלו"),
            ("דובר 1", "משפט באורך בינוני עם כמה מילים"),
        ])
        calls = []
        monkeypatch.setattr(
            speaker_embedding, "extract_embedding",
            lambda audio_path, start, end: calls.append((start, end)) or [1.0, 0.0],
        )

        _, sample_segment = speaker_id.representative_embedding("audio.m4a", segments)

        assert sample_segment is segments[1]  # הקטע הארוך ביותר (8 מילים)
        assert len(calls) == 3  # כל הקטעים כאן מתחת לתקרת ה-3

    def test_no_segments_yields_nothing(self):
        assert speaker_id.representative_embedding("audio.m4a", []) == (None, None)

    def test_all_segments_too_short_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(speaker_embedding, "extract_embedding", lambda *a: None)
        segments = _segs([("דובר 1", "משהו")])
        assert speaker_id.representative_embedding("audio.m4a", segments) == (None, None)


class TestIdentifySpeakers:
    def test_matched_speaker_is_renamed_everywhere_in_the_recording(self, store, monkeypatch):
        speaker_id.enroll_known("u1", "דנה", VOICE_A, "rec0", 1, 0.0)
        segments = _segs([
            ("דובר 1", "שלום לכולם, בואו נתחיל את הישיבה של היום"),
            ("דובר 1", "יש לנו כמה נושאים על הפרק"),
        ])
        monkeypatch.setattr(speaker_embedding, "extract_embedding", lambda *a: VOICE_A_AGAIN)

        result = speaker_id.identify_speakers(segments, "u1", "rec1", "audio.m4a")

        assert [s.speaker_label for s in result] == ["דנה", "דנה"]

    def test_me_is_never_sent_through_voice_matching(self, store, monkeypatch):
        segments = _segs([("אני", "שלום, זו אני מדברת")])
        monkeypatch.setattr(
            speaker_embedding, "extract_embedding",
            lambda *a: pytest.fail("אסור לחשב embedding בשביל 'אני'"),
        )
        result = speaker_id.identify_speakers(segments, "u1", "rec1", "audio.m4a")
        assert result[0].speaker_label == "אני"
        assert store.list_speaker_profiles("u1") == []

    def test_unmatched_speaker_keeps_the_generic_label_but_opens_a_profile(self, store, monkeypatch):
        segments = _segs([("דובר 1", "שלום לכולם, בואו נתחיל את הישיבה של היום")])
        monkeypatch.setattr(speaker_embedding, "extract_embedding", lambda *a: VOICE_A)

        result = speaker_id.identify_speakers(segments, "u1", "rec1", "audio.m4a")

        assert result[0].speaker_label == "דובר 1"
        profiles = store.list_speaker_profiles("u1")
        assert len(profiles) == 1
        assert profiles[0]["name"] is None


class TestEmbeddingMath:
    def test_average_embedding(self):
        assert speaker_embedding.average_embedding([[1.0, 1.0], [3.0, 3.0]]) == [2.0, 2.0]

    def test_cosine_similarity_identical_vectors_is_one(self):
        assert speaker_embedding.cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal_vectors_is_zero(self):
        assert speaker_embedding.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_cosine_similarity_zero_vector_is_zero_not_nan(self):
        assert speaker_embedding.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestCallRecordingSpeakerLocking:
    """process_call_recording: מי שנחשב "ודאי" ולכן צריך להינעל מפני ניחוש
    Gemini מתוך תוכן השיחה (ראה locked_labels / _resolve_speaker_names).

    כיסוי לבאג אמיתי שנמצא בסקירת קוד ותוקן באותו מקום: contact_name חייב
    להישאר נעול גם כשאין קטע שמע ארוך מספיק לטביעת קול (embedding None) -
    בגרסה הראשונה locked תלה את עצמו בטעות בהצלחת ההרשמה במקום בוודאות
    המקור (אנשי הקשר), כך ששיחה קצרה עם contact_name הייתה נשארת פתוחה
    לשכתוב ע"י ניחוש Gemini."""

    def _stub_transcription(self, monkeypatch):
        def fake_transcribe(audio_path, label, tag, client=None):
            return [TranscriptSegment(
                speaker_label=label, speaker_tag=tag, text="שלום שלום שלום",
                start_seconds=0.0, end_seconds=3.0,
            )]

        monkeypatch.setattr(pipeline, "transcribe_single_channel", fake_transcribe)
        monkeypatch.setattr(pipeline.firestore_store, "set_recording_status", lambda *a, **k: None)

    def _capture_locked_labels(self, monkeypatch) -> dict:
        captured = {}

        def fake_summarize_and_save(recording_id, user_id, segments, title, audio_path, **kwargs):
            captured["locked_labels"] = kwargs.get("locked_labels")
            captured["segments"] = segments

        monkeypatch.setattr(pipeline, "_summarize_and_save", fake_summarize_and_save)
        return captured

    def test_contact_name_stays_locked_even_when_no_embedding_available(self, monkeypatch):
        self._stub_transcription(monkeypatch)
        captured = self._capture_locked_labels(monkeypatch)
        monkeypatch.setattr(pipeline.speaker_id, "representative_embedding", lambda *a: (None, None))

        pipeline.process_call_recording(
            "rec1", "u1", "up.m4a", "down.m4a", "כותרת", contact_name="גדעון",
        )

        assert captured["locked_labels"] == {"גדעון"}

    def test_contact_name_enrolls_and_stays_locked_when_embedding_available(self, monkeypatch):
        self._stub_transcription(monkeypatch)
        captured = self._capture_locked_labels(monkeypatch)
        monkeypatch.setattr(
            pipeline.speaker_id, "representative_embedding",
            lambda audio_path, segments: (VOICE_A, segments[0]),
        )
        enroll_calls = []
        monkeypatch.setattr(
            pipeline.speaker_id, "enroll_known", lambda *a: enroll_calls.append(a)
        )

        pipeline.process_call_recording(
            "rec1", "u1", "up.m4a", "down.m4a", "כותרת", contact_name="גדעון",
        )

        assert captured["locked_labels"] == {"גדעון"}
        assert len(enroll_calls) == 1

    def test_no_contact_name_matched_voice_renames_and_locks(self, monkeypatch):
        self._stub_transcription(monkeypatch)
        captured = self._capture_locked_labels(monkeypatch)
        monkeypatch.setattr(
            pipeline.speaker_id, "representative_embedding",
            lambda audio_path, segments: (VOICE_A, segments[0]),
        )
        monkeypatch.setattr(pipeline.speaker_id, "resolve_or_enroll", lambda *a: "דנה")

        pipeline.process_call_recording(
            "rec1", "u1", "up.m4a", "down.m4a", "כותרת", contact_name="",
        )

        assert captured["locked_labels"] == {"דנה"}
        assert all(
            s.speaker_label != "הצד השני" for s in captured["segments"] if s.speaker_tag == 2
        )

    def test_no_contact_name_no_match_stays_generic_and_unlocked(self, monkeypatch):
        self._stub_transcription(monkeypatch)
        captured = self._capture_locked_labels(monkeypatch)
        monkeypatch.setattr(
            pipeline.speaker_id, "representative_embedding",
            lambda audio_path, segments: (VOICE_A, segments[0]),
        )
        monkeypatch.setattr(pipeline.speaker_id, "resolve_or_enroll", lambda *a: None)

        pipeline.process_call_recording(
            "rec1", "u1", "up.m4a", "down.m4a", "כותרת", contact_name="",
        )

        assert captured["locked_labels"] == set()


# --- חילוץ embedding אמיתי (ffmpeg + מודל) --------------------------------
#
# מדולג אוטומטית כשהמודל לא קיים מקומית (הוא יורד רק ב-Dockerfile - ראה
# הערה ראש הקובץ). לא בודק זהות דובר (טון סינוסי אינו קול אמיתי) - רק
# שהצנרת (ffmpeg subprocess + קריאות sherpa-onnx) לא נופלת ומחזירה וקטור.

_MODEL_PATH = pathlib.Path(speaker_embedding.__file__).resolve().parents[2] / "models" / "speaker_embedding.onnx"
_ffmpeg_missing = shutil.which("ffmpeg") is None


def _write_tone_wav(path: str, freq_hz: float, seconds: float = 3.0, sample_rate: int = 16000) -> None:
    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * freq_hz * t) * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(samples.tobytes())


@pytest.mark.skipif(not _MODEL_PATH.is_file(), reason="מודל טביעת הקול לא הורד מקומית")
@pytest.mark.skipif(_ffmpeg_missing, reason="ffmpeg לא מותקן מקומית")
def test_extract_embedding_end_to_end(tmp_path):
    wav_path = str(tmp_path / "tone.wav")
    _write_tone_wav(wav_path, freq_hz=220.0)
    embedding = speaker_embedding.extract_embedding(wav_path, 0.0, 3.0)
    assert embedding is not None
    assert len(embedding) > 0


def _write_silence_wav(path: str, seconds: float = 3.0, sample_rate: int = 16000) -> None:
    samples = np.zeros(int(sample_rate * seconds), dtype=np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(samples.tobytes())


@pytest.mark.skipif(_ffmpeg_missing, reason="ffmpeg לא מותקן מקומית")
class TestSegmentIsSilent:
    """ראה test_transcription_silence.py לבדיקת ההחלטה-מתי-לזרוק (בלי
    ffmpeg אמיתי) - כאן רק ה-RMS עצמו, על אודיו אמיתי."""

    def test_real_tone_is_not_silent(self, tmp_path):
        wav_path = str(tmp_path / "tone.wav")
        _write_tone_wav(wav_path, freq_hz=220.0)
        assert speaker_embedding.segment_is_silent(wav_path, 0.0, 3.0) is False

    def test_true_silence_is_silent(self, tmp_path):
        wav_path = str(tmp_path / "silence.wav")
        _write_silence_wav(wav_path)
        assert speaker_embedding.segment_is_silent(wav_path, 0.0, 3.0) is True
