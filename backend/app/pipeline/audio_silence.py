"""בדיקת שקט אקוסטי בקטע אודיו.

משמש רק כהגנה מפני תמלול מדומיין (ראה transcription.py:
HallucinatedTranscriptError) - לא קשור לזיהוי דוברים. ffmpeg עושה את
הפענוח/החיתוך (תומך ב-m4a שממנו מגיע האודיו, מה ש-libsndfile לא יודע
לקרוא).
"""

import subprocess

import numpy as np

_SAMPLE_RATE = 16000

# RMS (על דגימות float32 מנורמלות ל-[-1,1]) מתחת לזה נחשב "שקט" ולא דיבור -
# לכל היותר רעש רקע/הזזת מיקרופון. סף שמרני בכוונה (דיבור אמיתי, גם
# לחישה, יושב משמעותית מעליו) כדי לא לתפוס הקלטה שקטה לגיטימית כתמלול
# מדומיין (ראה transcription.py: ההגנה מפני "המצאת" שיחה על אודיו שקט).
_SILENCE_RMS_THRESHOLD = 0.004


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


def _is_silent(samples: np.ndarray) -> bool:
    if samples.size == 0:
        return True
    return float(np.sqrt(np.mean(np.square(samples)))) < _SILENCE_RMS_THRESHOLD


def segment_is_silent(audio_path: str, start_seconds: float, end_seconds: float) -> bool:
    """True אם לקטע הזה אין בפועל אנרגיית שמע - "שקט", לא דיבור - גם אם
    Gemini תימלל בו טקסט (ראה transcription.py)."""
    return _is_silent(_read_samples(audio_path, start_seconds, end_seconds))
