"""חילוץ טביעת-קול (speaker embedding) מקטע אודיו.

דרך sherpa-onnx (ONNX Runtime בלבד) עם מודל wespeaker קוד-פתוח - לא torch,
לא pyannote.audio (ראה ההערה שנדחתה ב-requirements.txt: הם כבדים מדי
לקונטיינר). המודל עצמו יורד ב-Dockerfile אל speaker_embedding_model_path.

sherpa-onnx מקבל מערך דגימות PCM, לא קובץ - ffmpeg עושה את הפענוח/החיתוך
(תומך ב-m4a שממנו מגיע האודיו, מה ש-libsndfile לא יודע לקרוא).
"""

import subprocess

import numpy as np
import sherpa_onnx

from app.config import settings

_SAMPLE_RATE = 16000

# מתחת לזה טביעת הקול לא אמינה - המודל (גרסת LM) מכוונן במיוחד לקטעים
# ארוכים מ-3 שניות; שנייה אחת בודדת עלולה להתאים "בטעות" לכל אחד.
_MIN_SECONDS = 1.0

# RMS (על דגימות float32 מנורמלות ל-[-1,1]) מתחת לזה נחשב "שקט" ולא דיבור -
# לכל היותר רעש רקע/הזזת מיקרופון. סף שמרני בכוונה (דיבור אמיתי, גם
# לחישה, יושב משמעותית מעליו) כדי לא לתפוס הקלטה שקטה לגיטימית כתמלול
# מדומיין (ראה transcription.py: ההגנה מפני "המצאת" שיחה על אודיו שקט).
_SILENCE_RMS_THRESHOLD = 0.004

_extractor: "sherpa_onnx.SpeakerEmbeddingExtractor | None" = None


def _get_extractor() -> "sherpa_onnx.SpeakerEmbeddingExtractor":
    """טוען את המודל פעם אחת בלבד לכל מופע קונטיינר - הטעינה איטית יחסית,
    ואין סיבה לחזור עליה בכל הקלטה."""
    global _extractor
    if _extractor is None:
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=settings.speaker_embedding_model_path,
            num_threads=1,
            provider="cpu",
        )
        if not config.validate():
            raise RuntimeError(f"תצורת מודל טביעת-קול לא תקינה: {config}")
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return _extractor


def _read_samples(
    audio_path: str, start_seconds: float, end_seconds: float
) -> np.ndarray:
    """מריץ ffmpeg להמרת הקטע המבוקש ל-PCM float32 גולמי, 16kHz מונו."""
    duration = max(end_seconds - start_seconds, 0.0)
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-ss", str(max(start_seconds, 0.0)),
        "-i", audio_path,
        "-t", str(duration),
        "-ar", str(_SAMPLE_RATE), "-ac", "1", "-f", "f32le", "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype=np.float32)


def extract_embedding(
    audio_path: str, start_seconds: float, end_seconds: float
) -> list[float] | None:
    """טביעת קול לקטע [start_seconds, end_seconds) בקובץ. None אם קצר מדי."""
    if end_seconds - start_seconds < _MIN_SECONDS:
        return None
    samples = _read_samples(audio_path, start_seconds, end_seconds)
    if samples.size < _SAMPLE_RATE * _MIN_SECONDS:
        return None

    extractor = _get_extractor()
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=_SAMPLE_RATE, waveform=samples)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    return np.asarray(extractor.compute(stream), dtype=np.float64).tolist()


def segment_is_silent(audio_path: str, start_seconds: float, end_seconds: float) -> bool:
    """True אם לקטע הזה אין בפועל אנרגיית שמע - "שקט", לא דיבור - גם אם
    Gemini תימלל בו טקסט (ראה transcription.py)."""
    samples = _read_samples(audio_path, start_seconds, end_seconds)
    if samples.size == 0:
        return True
    rms = float(np.sqrt(np.mean(np.square(samples))))
    return rms < _SILENCE_RMS_THRESHOLD


def average_embedding(embeddings: list[list[float]]) -> list[float]:
    """ממוצע כמה טביעות-קול (ראה בחירת קטעים ב-speaker_id.py) לוקטור אחד -
    בדיוק כמו שהדוגמה הרשמית של sherpa-onnx ממצעת כמה קבצים של אותו דובר."""
    return np.mean(np.array(embeddings), axis=0).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float64), np.array(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)
