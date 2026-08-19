"""בדיקות מהירות (ללא רשת) לחוזה תוויות הדוברים.

המשתמש מזין שמות במסך "עריכת דוברים" **לפי סדר הופעתם בתמלול**: מי שדיבר
ראשון מקבל את השדה הראשון. שני דברים חייבים להחזיק כדי שזה יעבוד, ושניהם
נשברו בעבר:
  1. הסדר שהשרת מחזיר הוא סדר ההופעה, לא א"ב.
  2. השם שהוזן מחליף את התווית בכל מופעיה - בתמלול, בסיכום ובמשימות.
"""

import pytest

from app.models import RecordingUpdateRequest, TodoItem
from app.pipeline.edit import _doc_id, apply_update
from app.pipeline.pipeline import _apply_speaker_names, _resolve_speaker_names
from app.pipeline.speakers import replace_labels, speakers_in_order
from tests.transcripts import _segs


@pytest.fixture(autouse=True)
def _no_external_calls(monkeypatch):
    """apply_update כותב ל-Drive ול-Firestore - כאן בודקים רק את הלוגיקה."""
    import app.pipeline.edit as edit

    monkeypatch.setattr(edit.firestore_store, "update_recording_fields", lambda *a, **k: None)
    for name in ("rename_folder", "update_text_doc", "update_summary_doc"):
        monkeypatch.setattr(edit.drive, name, lambda *a, **k: None)


class TestAppearanceOrder:
    def test_order_follows_first_appearance_not_alphabet(self):
        """המקרה שנשבר: "אני" קודם ב-א"ב, אבל הצד השני פתח את השיחה."""
        labels = ["דנה", "אני", "דנה", "אני"]
        assert speakers_in_order(labels) == ["דנה", "אני"]

    def test_duplicates_collapse_to_first_position(self):
        labels = ["דובר 2", "דובר 1", "דובר 2", "דובר 3", "דובר 1"]
        assert speakers_in_order(labels) == ["דובר 2", "דובר 1", "דובר 3"]

    def test_empty_labels_are_dropped(self):
        assert speakers_in_order(["", "דובר 1", "", "דובר 2"]) == ["דובר 1", "דובר 2"]

    def test_pipeline_speakers_are_in_appearance_order(self):
        """הרשימה שנשמרת להקלטה - זו שהדיאלוג באפליקציה מציג לפי הסדר."""
        segments = _segs([("דובר 2", "שלום"), ("דובר 1", "היי"), ("דובר 2", "מה נשמע")])
        assert speakers_in_order(s.speaker_label for s in segments) == ["דובר 2", "דובר 1"]

    def test_rename_preserves_appearance_order(self):
        recording = {
            "transcript": [
                {"speaker_label": "דובר 2", "text": "שלום"},
                {"speaker_label": "דובר 1", "text": "היי"},
            ],
            # רשומה ישנה: הרשימה השמורה עדיין ממוינת א"ב
            "speakers": ["דובר 1", "דובר 2"],
        }
        payload = RecordingUpdateRequest(speaker_renames={"דובר 2": "מאיר"})
        result = apply_update("rec1", recording, payload)
        assert result["speakers"] == ["מאיר", "דובר 1"]


class TestRenameReplacesEveryOccurrence:
    def test_all_segments_of_that_speaker_are_renamed(self):
        recording = {
            "transcript": [
                {"speaker_label": "דובר 1", "text": "א"},
                {"speaker_label": "דובר 2", "text": "ב"},
                {"speaker_label": "דובר 1", "text": "ג"},
                {"speaker_label": "דובר 1", "text": "ד"},
            ]
        }
        payload = RecordingUpdateRequest(speaker_renames={"דובר 1": "מאיר"})
        result = apply_update("rec1", recording, payload)
        labels = [s["speaker_label"] for s in result["transcript"]]
        assert labels == ["מאיר", "דובר 2", "מאיר", "מאיר"]

    def test_summary_attribution_is_renamed_too(self):
        """הסיכום מייחס אמירות בשם הדובר, אז הוא חייב להתעדכן איתו."""
        recording = {
            "transcript": [{"speaker_label": "דובר 1", "text": "א"}],
            "summary": "1. תקציב: דובר 1 אמר שצריך 12,450 ₪. דובר 1 גם ביקש דחייה.",
        }
        payload = RecordingUpdateRequest(speaker_renames={"דובר 1": "מאיר"})
        result = apply_update("rec1", recording, payload)
        assert "דובר 1" not in result["summary"]
        assert result["summary"].count("מאיר") == 2

    def test_prefix_collision_does_not_corrupt_longer_label(self):
        """"דובר 1" הוא תחילית של "דובר 10" - החלפה נאיבית ייצרה "מאיר0"."""
        text = "דובר 10 ענה לדובר 1"
        out = replace_labels(text, {"דובר 1": "מאיר", "דובר 10": "דנה"})
        assert out == "דנה ענה למאיר"

    def test_rename_does_not_chain_through_another_rename(self):
        """החלפת שמות הדדית בין שני דוברים לא מקפלת את שניהם לאותו שם."""
        out = replace_labels("דובר 1 ו-דובר 2", {"דובר 1": "דובר 2", "דובר 2": "דובר 1"})
        assert out == "דובר 2 ו-דובר 1"

    def test_blank_and_noop_renames_are_ignored(self):
        recording = {"transcript": [{"speaker_label": "דובר 1", "text": "א"}]}
        payload = RecordingUpdateRequest(
            speaker_renames={"דובר 1": "  ", "דובר 2": "דובר 2"}
        )
        assert apply_update("rec1", recording, payload) == recording

    def test_whitespace_around_entered_name_is_trimmed(self):
        recording = {"transcript": [{"speaker_label": "דובר 1", "text": "א"}]}
        payload = RecordingUpdateRequest(speaker_renames={"דובר 1": "  מאיר  "})
        result = apply_update("rec1", recording, payload)
        assert result["transcript"][0]["speaker_label"] == "מאיר"


class TestDriveDocsFollowTheRename:
    """המסמכים ב-Drive הם מה שהמשתמש באמת פותח - עדכון ב-Firestore בלבד
    משאיר אותו עם "דובר 1" מול העיניים."""

    def test_transcript_doc_is_updated_even_when_only_the_url_was_stored(self, monkeypatch):
        """הבאג שנמצא: drive_transcript_doc_id לא נשמר ב-65 ההקלטות הראשונות,
        כך שעדכון מסמך התמלול פשוט לא רץ."""
        import app.pipeline.edit as edit

        written: dict = {}
        monkeypatch.setattr(edit.drive, "update_text_doc",
                            lambda doc_id, text: written.update({doc_id: text}))
        recording = {
            "transcript": [{"speaker_label": "דובר 1", "text": "שלום"}],
            "drive_transcript_url":
                "https://docs.google.com/document/d/1U41t8Tmj2SnP0cJX/edit?usp=drivesdk",
        }
        apply_update("rec1", recording, RecordingUpdateRequest(speaker_renames={"דובר 1": "מאיר"}))
        assert written == {"1U41t8Tmj2SnP0cJX": "מאיר:\nשלום"}

    def test_stored_doc_id_wins_over_the_url(self):
        recording = {
            "drive_summary_doc_id": "REAL_ID",
            "drive_summary_url": "https://docs.google.com/document/d/OTHER/edit",
        }
        assert _doc_id(recording, "drive_summary_doc_id", "drive_summary_url") == "REAL_ID"

    def test_missing_url_and_id_yields_none(self):
        assert _doc_id({}, "drive_summary_doc_id", "drive_summary_url") is None


class TestNamesIdentifiedFromTheConversation:
    def test_summary_and_todos_follow_the_transcript(self):
        """בלי זה התמלול אומר "יוסי" והסיכום ממשיך לומר "דובר 2" - אותו אדם
        בשני שמות באותה הקלטה."""
        segments = _segs([("דובר 1", "שלום"), ("דובר 2", "היי")])
        todos = [TodoItem(description="דובר 2 ישלח חוזה", owner="דובר 2")]
        summary = "1. פתיחה: דובר 2 אמר שהוא ישלח חוזה."
        renames = _resolve_speaker_names(segments, {"דובר 2": "יוסי"}, set())

        summary = _apply_speaker_names(segments, todos, summary, renames)

        assert [s.speaker_label for s in segments] == ["דובר 1", "יוסי"]
        assert summary == "1. פתיחה: יוסי אמר שהוא ישלח חוזה."
        assert todos[0].owner == "יוסי" and todos[0].description == "יוסי ישלח חוזה"

    def test_contact_list_name_wins_over_a_name_heard_in_the_audio(self):
        """השם מאנשי הקשר נשמר בידי המשתמש; מה שנשמע באודיו הוא ניחוש."""
        segments = _segs([("אני", "שלום"), ("מאיר וייס", "היי")])
        renames = _resolve_speaker_names(
            segments, {"מאיר וייס": "מאירי"}, locked_labels={"מאיר וייס"}
        )
        assert renames == {}

    def test_self_label_is_never_renamed(self):
        segments = _segs([("אני", "שלום")])
        assert _resolve_speaker_names(segments, {"אני": "מאיר"}, set()) == {}

    def test_label_absent_from_transcript_is_ignored(self):
        segments = _segs([("דובר 1", "שלום")])
        assert _resolve_speaker_names(segments, {"דובר 7": "רותי"}, set()) == {}


class TestUncertainAttributionIsLeftVague:
    """בקשה מפורשת של המשתמש: כשלא ברור מי אמר משהו - להשאיר עמום ולא
    לנחש. האימות האקוסטי (pipeline/diarization.py) מסמן קטע כזה, וכאן
    נבדק שהסימון באמת מגיע לשני המקומות שהמשתמש רואה: התמלול והסיכום."""

    def test_transcript_marks_an_uncertain_segment(self):
        from app.services.drive import _transcript_to_text

        segments = _segs([("דובר 1", "שלום"), ("דובר 2", "היי")])
        segments[1].speaker_confident = False

        assert _transcript_to_text(segments) == "דובר 1:\nשלום\n\nדובר 2 (?):\nהיי"

    def test_the_mark_is_not_part_of_the_label_itself(self):
        """אחרת הוא היה נספר כדובר נוסף במסך "עריכת דוברים", והחלפת השם
        בעריכה הייתה מפספסת אותו."""
        segments = _segs([("דובר 1", "שלום"), ("דובר 1", "היי")])
        segments[1].speaker_confident = False

        assert speakers_in_order(s.speaker_label for s in segments) == ["דובר 1"]

    def test_edit_and_drive_render_the_transcript_identically(self):
        """שתי הגרסאות (מודל מול dict מ-Firestore) חייבות לייצר טקסט זהה,
        אחרת המסמך ב-Drive משנה צורה בכל עריכת שם דובר."""
        from app.pipeline.edit import _transcript_to_text as edit_render
        from app.services.drive import _transcript_to_text as drive_render

        segments = _segs([("דובר 1", "שלום"), ("דובר 2", "היי")])
        segments[1].speaker_confident = False

        assert edit_render([s.model_dump() for s in segments]) == drive_render(segments)

    def test_legacy_segments_without_the_field_are_treated_as_certain(self):
        """הקלטות שנשמרו לפני האימות האקוסטי - אין להן speaker_confident,
        ואסור שיתחילו פתאום להיראות מסופקות."""
        from app.pipeline.edit import _transcript_to_text as edit_render

        assert edit_render([{"speaker_label": "דובר 1", "text": "שלום"}]) == "דובר 1:\nשלום"

    def test_the_summary_prompt_sees_which_lines_are_uncertain(self):
        from app.pipeline.summarize import UNCERTAIN_HINT, _format_transcript

        segments = _segs([("דובר 1", "שלום"), ("דובר 2", "היי")])
        segments[1].speaker_confident = False
        lines = _format_transcript(segments).splitlines()

        assert lines[0] == "דובר 1: שלום"
        assert lines[1] == f"דובר 2{UNCERTAIN_HINT}: היי"


class TestCorrectionTeachesTheVoiceProfile:
    """תיקון ידני של שם דובר הוא העדות האמינה ביותר שיש על זהות הקול, והיא
    נזרקה עד היום: ההקלטה התעדכנה, ופרופיל הקול נשאר בלי שם וחזר על אותה
    טעות בהקלטה הבאה."""

    def _recording(self):
        return {
            "transcript": [{"speaker_label": "דובר 2", "text": "היי"}],
            "speaker_profile_ids": {"דובר 2": "profile7"},
        }

    def test_renaming_a_speaker_names_the_profile(self, monkeypatch):
        import app.pipeline.edit as edit

        taught: list[tuple[str, str]] = []
        monkeypatch.setattr(
            edit.speaker_id, "learn_name_from_correction",
            lambda profile_id, name: taught.append((profile_id, name)),
        )

        updated = apply_update(
            "rec1", self._recording(),
            RecordingUpdateRequest(speaker_renames={"דובר 2": "רונית"}),
        )

        assert taught == [("profile7", "רונית")]
        # המיפוי נשמר תחת התווית החדשה, כדי שגם תיקון נוסף אחריו יעבוד.
        assert updated["speaker_profile_ids"] == {"רונית": "profile7"}

    def test_a_label_without_a_profile_is_simply_skipped(self, monkeypatch):
        import app.pipeline.edit as edit

        monkeypatch.setattr(
            edit.speaker_id, "learn_name_from_correction",
            lambda *a: pytest.fail("אין פרופיל לתווית הזו - אין מה ללמד"),
        )
        recording = {
            "transcript": [{"speaker_label": "דובר 9", "text": "היי"}],
            "speaker_profile_ids": {"דובר 2": "profile7"},
        }

        apply_update(
            "rec1", recording, RecordingUpdateRequest(speaker_renames={"דובר 9": "רונית"})
        )

    def test_old_recordings_without_the_map_still_rename_fine(self, monkeypatch):
        """הקלטות שנשמרו לפני שהמיפוי היה קיים - העריכה עצמה חייבת לעבוד
        כרגיל, פשוט בלי הלמידה."""
        recording = {"transcript": [{"speaker_label": "דובר 1", "text": "שלום"}]}

        updated = apply_update(
            "rec1", recording, RecordingUpdateRequest(speaker_renames={"דובר 1": "מאיר"})
        )

        assert updated["speakers"] == ["מאיר"]
        assert "speaker_profile_ids" not in updated
