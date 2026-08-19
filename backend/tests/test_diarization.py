"""בדיקות לאימות האקוסטי של הפרדת הדוברים (ראה pipeline/diarization.py).

טביעות הקול כאן הן וקטורים דו-ממדיים מלאכותיים, לא אודיו אמיתי: המודל
(sherpa-onnx) ו-ffmpeg מוחלפים בפונקציה שמחזירה וקטור לפי זמן ההתחלה של
הקטע. כך נבדקת בדיוק הלוגיקה שאפשר לשבור בשוגג - מתי מאחדים, מתי מפצלים,
ומתי מוותרים ומסמנים "לא ודאי" - בלי רשת, בלי מודל, ובמהירות.

הזוויות נבחרו כך שהתוצאה לא תלויה בכיול העדין של הספים ב-config.py: X ו-Y
ניצבים (דמיון 0 - בבירור שני אנשים), ווריאציות של X יושבות במעלות בודדות
ממנו (בבירור אותו אדם).
"""

import math

import pytest

from app.models import TranscriptSegment
from app.pipeline import diarization, speaker_embedding

# X ו-Y: שני אנשים שונים לחלוטין. X_AGAIN: אותו אדם, משפט אחר.
VOICE_X = [1.0, 0.0]
VOICE_X_AGAIN = [0.9986, 0.0523]  # 3°
VOICE_Y = [0.0, 1.0]
# בדיוק באמצע בין X ל-Y (45°): דומה לשניהם באותה מידה - הקטע שאי אפשר
# לשייך, וזה שממנו נולד ייחוס שגוי בסיכום.
VOICE_MIDDLE = [math.cos(math.radians(45)), math.sin(math.radians(45))]


def _seg(label: str, start: float, voice_index: int, duration: float = 3.0):
    """קטע דיבור. [voice_index] נשמר בטקסט כדי שהאודיו המדומה יחזיר עבורו
    את הווקטור הנכון - ראה _fake_audio."""
    return TranscriptSegment(
        speaker_label=label,
        speaker_tag=1,
        text=f"קטע מספר {voice_index}",
        start_seconds=start,
        end_seconds=start + duration,
    )


def _fake_audio(monkeypatch, voices: dict[float, list[float]], silent: set[float] = frozenset()):
    """מחליף את המדידה האקוסטית: כל קטע מקבל וקטור לפי זמן ההתחלה שלו."""
    monkeypatch.setattr(
        speaker_embedding,
        "analyze_segment",
        lambda audio_path, start, end: (voices.get(start), start in silent),
    )


def _fake_samples(voice: list[float]):
    """מערך דגימות מדומה שסופו ה"קול" - _embedding_of המוחלף קורא ממנו.
    האורך רק צריך לעבור את סף המינימום של analyze_segment."""
    import numpy as np

    return np.concatenate([np.array(voice, dtype=np.float32), np.full(48000, 0.1, dtype=np.float32)])


def _build(pairs: list[tuple[str, list[float]]], duration: float = 3.0):
    """רצף קטעים לפי (תווית, קול), עם זמנים עוקבים. מחזיר (קטעים, מפת קולות)."""
    segments, voices = [], {}
    start = 0.0
    for index, (label, voice) in enumerate(pairs):
        segments.append(_seg(label, start, index, duration))
        voices[start] = voice
        start += duration
    return segments, voices


class TestMergingASplitSpeaker:
    """התקלה הנפוצה יותר: אדם אחד שקיבל שתי תוויות. קורה בעיקר סביב בקשות
    ההמשך בהקלטה ארוכה, ומרגע כזה כל יתרת התמלול מיוחסת לאדם הלא נכון."""

    def test_two_labels_of_the_same_voice_become_one(self, monkeypatch):
        segments, voices = _build(
            [("דובר 1", VOICE_X)] * 3 + [("דובר 4", VOICE_X_AGAIN)] * 3
        )
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert {s.speaker_label for s in result.segments} == {"דובר 1"}
        assert result.stats["labels_before"] == 2
        assert result.stats["labels_after"] == 1
        assert all(s.speaker_confident for s in result.segments)

    def test_two_genuinely_different_voices_are_left_alone(self, monkeypatch):
        segments, voices = _build([("דובר 1", VOICE_X)] * 3 + [("דובר 2", VOICE_Y)] * 3)
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert [s.speaker_label for s in result.segments] == ["דובר 1"] * 3 + ["דובר 2"] * 3
        assert result.stats["labels_after"] == 2


class TestSplittingAMixedSpeaker:
    """התקלה ההפוכה: שני אנשים שנדחסו לתווית אחת - ואז הסיכום מייחס לאחד
    מהם אמירות של השני."""

    def test_one_label_holding_two_voices_is_split(self, monkeypatch):
        segments, voices = _build([("דובר 1", VOICE_X)] * 4 + [("דובר 1", VOICE_Y)] * 4)
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert [s.speaker_label for s in result.segments] == ["דובר 1"] * 4 + ["דובר 2"] * 4

    def test_a_single_odd_segment_does_not_split_a_speaker(self, monkeypatch):
        """פיצול שמוציא קטע בודד הצידה הוא כמעט תמיד רעש או דיבור חופף,
        לא אדם שני - ראה _MIN_SPLIT_MEMBERS."""
        segments, voices = _build([("דובר 1", VOICE_X)] * 7 + [("דובר 1", VOICE_Y)])
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert {s.speaker_label for s in result.segments} == {"דובר 1"}


class TestUncertainty:
    """הבקשה המפורשת של המשתמש: כשלא ברור מי אמר - להשאיר את זה עמום
    במקום לנחש. הדגל הזה הוא מה שמייצר את "(?)" בתמלול ואת "אחד הדוברים"
    בסיכום (ראה speakers.display_label ו-summarize.UNCERTAIN_HINT)."""

    def test_a_segment_between_two_speakers_is_marked_uncertain(self, monkeypatch):
        segments, voices = _build(
            [("דובר 1", VOICE_X)] * 10
            + [("דובר 2", VOICE_Y)] * 10
            + [("דובר 1", VOICE_MIDDLE)]
        )
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert result.segments[-1].speaker_confident is False
        assert all(s.speaker_confident for s in result.segments[:-1])

    def test_a_short_segment_inherits_its_label_and_stays_certain(self, monkeypatch):
        """קטע קצר מכדי להימדד יורש את האשכול של התווית שלו. כשהתווית ההיא
        נמדדה פה אחד, אין שום ממצא שסותר את השיוך - ולכן אין סיבה להטיל בו
        ספק. סימון גורף של כל קטע קצר היה מרוקן את הסימון ממשמעות."""
        segments, voices = _build([("דובר 1", VOICE_X)] * 3 + [("דובר 2", VOICE_Y)] * 3)
        short = _seg("דובר 2", 100.0, 99, duration=0.4)
        segments.append(short)
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert short.speaker_label == "דובר 2"
        assert short.speaker_confident is True

    def test_a_short_segment_of_a_split_label_becomes_uncertain(self, monkeypatch):
        """אבל אם התווית שלו התפצלה לשני אנשים - אין שום דרך לדעת לאיזה צד
        הקטע הקצר שייך, וזה בדיוק מה שהסימון נועד לומר."""
        segments, voices = _build([("דובר 1", VOICE_X)] * 4 + [("דובר 1", VOICE_Y)] * 4)
        short = _seg("דובר 1", 100.0, 99, duration=0.4)
        segments.append(short)
        _fake_audio(monkeypatch, voices)

        diarization.refine_speaker_labels(segments, "audio.m4a")

        assert short.speaker_confident is False


class TestNoMeasurement:
    """כשאין מה למדוד, ההפרדה שהתמלול החזיר נשארת כמות שהיא - ובלי סימון
    ספק: אין ממצא שסותר אותה, וסימון בלי ממצא הוא רעש."""

    def test_no_segments(self):
        assert diarization.refine_speaker_labels([], "audio.m4a").segments == []

    def test_all_segments_too_short_to_measure(self, monkeypatch):
        segments, voices = _build([("דובר 1", VOICE_X), ("דובר 2", VOICE_Y)], duration=0.5)
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert [s.speaker_label for s in result.segments] == ["דובר 1", "דובר 2"]
        assert all(s.speaker_confident for s in result.segments)
        assert result.label_voices is None

    def test_silent_segments_are_never_measured(self, monkeypatch):
        """קטע ששקט בפועל לא נכנס לאשכול גם כשיש לו טקסט: טביעת קול משקט
        היא רעש טהור. ראה HallucinatedTranscriptError ב-transcription.py."""
        segments, voices = _build([("דובר 1", VOICE_X)] * 3 + [("דובר 2", VOICE_Y)] * 3)
        silent_starts = {s.start_seconds for s in segments[3:]}
        _fake_audio(monkeypatch, voices, silent=silent_starts)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        # רק שלושת הראשונים נמדדו. השקטים לא נכנסו לאשכול, ולכן אין עליהם
        # שום עדות - הם נשארים דובר נפרד ומסומנים לא-ודאיים.
        assert result.stats["measured_segments"] == 3
        assert [s.speaker_label for s in result.segments] == ["דובר 1"] * 3 + ["דובר 2"] * 3
        assert [s.speaker_confident for s in result.segments] == [True] * 3 + [False] * 3

    def test_an_unmeasured_label_never_reuses_a_number_given_to_someone_else(
        self, monkeypatch
    ):
        """המספור נקבע כאן מחדש, ולכן "דובר 2" של התמלול עלול להיות בדיוק
        המספר שניתן זה עתה לאדם אחר. תווית בלי עדות אקוסטית חייבת לקבל
        מספר פנוי משלה - אחרת שני אנשים שונים מופיעים תחת אותה תווית."""
        segments, voices = _build([("דובר 5", VOICE_X)] * 3 + [("דובר 6", VOICE_Y)] * 3)
        # שני קטעים של תווית שלישית, שניהם קצרים מכדי להימדד.
        quiet = [_seg("דובר 7", 100.0, 98, duration=0.4), _seg("דובר 7", 101.0, 99, duration=0.4)]
        segments.extend(quiet)
        _fake_audio(monkeypatch, voices)

        diarization.refine_speaker_labels(segments, "audio.m4a")

        assert [s.speaker_label for s in quiet] == ["דובר 3", "דובר 3"]
        assert len({s.speaker_label for s in segments}) == 3
        assert all(not s.speaker_confident for s in quiet)


class TestLabelOrder:
    def test_labels_follow_first_appearance_not_cluster_size(self, monkeypatch):
        """חוזה מסך "עריכת דוברים": המשתמש ממלא שם לפי מי שדיבר ראשון.
        ראה speakers.speakers_in_order."""
        segments, voices = _build(
            [("דובר 9", VOICE_Y)] + [("דובר 3", VOICE_X)] * 5 + [("דובר 9", VOICE_Y)] * 2
        )
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert result.segments[0].speaker_label == "דובר 1"  # דיבר ראשון
        assert result.segments[1].speaker_label == "דובר 2"  # למרות שדיבר יותר


class TestLabelVoices:
    """טביעת הקול לכל תווית סופית נבנית כאן ממילא, ועוברת ל-speaker_id כדי
    שאותם קטעים לא יפוענחו ולא יעברו במודל פעם שנייה."""

    def test_each_final_label_gets_a_voice_and_an_audible_sample(self, monkeypatch):
        segments, voices = _build([("דובר 1", VOICE_X)] * 3 + [("דובר 2", VOICE_Y)] * 3)
        segments[1].end_seconds = segments[1].start_seconds + 9.0  # הארוך ביותר
        _fake_audio(monkeypatch, voices)

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert set(result.label_voices) == {"דובר 1", "דובר 2"}
        assert result.label_voices["דובר 1"].sample is segments[1]
        assert result.label_voices["דובר 1"].embedding == pytest.approx(VOICE_X, abs=1e-9)


class TestMeasurementIsResilient:
    """המדידה רצה מאות פעמים על הקלטה אחת, על גבולות זמן שמודל התמלול
    שיערך - וקטע פגום בודד לא יכול להיות הסיבה שפגישה שלמה נופלת."""

    def test_a_failing_decode_skips_the_segment_instead_of_raising(self, monkeypatch):
        import subprocess

        segments, voices = _build([("דובר 1", VOICE_X)] * 3 + [("דובר 2", VOICE_Y)] * 3)
        broken = segments[0].start_seconds

        def read(audio_path, start, end):
            if start == broken:
                raise subprocess.CalledProcessError(1, "ffmpeg")
            return _fake_samples(voices[start])

        monkeypatch.setattr(speaker_embedding, "_read_samples", read)
        monkeypatch.setattr(speaker_embedding, "_embedding_of", lambda samples: list(samples[:2]))

        result = diarization.refine_speaker_labels(segments, "audio.m4a")

        assert result.stats["measured_segments"] == 5  # אחד דולג, השאר נמדדו
        assert len({s.speaker_label for s in result.segments}) == 2
