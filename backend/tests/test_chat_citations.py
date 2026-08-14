"""ציטוט של הצ'אט חייב להיות בר-ניגון: מזהה הקלטה אמיתי + זמן בשניות.

בלי שני אלה התשובה "זה נאמר בדקה 2:21" נשארת טקסט בלבד, והמשתמש צריך לחפש
את הרגע ביד בתוך הקלטה של שעה. הבדיקות כאן לא פונות למודל - הן בודקות את
העיבוד של מה שהוא החזיר.
"""

from app.pipeline.chat import _normalize_citations, parse_timestamp_seconds


def test_parses_minutes_and_seconds():
    assert parse_timestamp_seconds("3:42") == 222


def test_parses_hours():
    assert parse_timestamp_seconds("1:02:03") == 3723


def test_takes_the_start_of_a_range():
    """תשובה טיפוסית מנסחת טווח - הניגון מתחיל מתחילתו."""
    assert parse_timestamp_seconds("בין 2:21 ל-5:04") == 141


def test_no_time_at_all():
    """ציטוט שמצביע על קובץ מצורף - אין לו מקום בציר הזמן."""
    assert parse_timestamp_seconds("תקציב 2026.pdf") is None
    assert parse_timestamp_seconds("") is None


def _recording(recording_id: str, title: str) -> dict:
    return {"recording_id": recording_id, "title": title}


def test_keeps_a_valid_recording_id():
    recordings = [_recording("rec-1", "ישיבת צוות"), _recording("rec-2", "שיחה עם דני")]
    citations = _normalize_citations(
        [{"recording_id": "rec-2", "recording_title": "שיחה עם דני", "timestamp": "0:30"}],
        recordings,
    )
    assert citations[0]["recording_id"] == "rec-2"
    assert citations[0]["start_seconds"] == 30


def test_falls_back_to_the_title_when_the_id_is_invented():
    """המודל נוטה לקצר/להמציא מזהים; כותרת ייחודית עדיין מזהה את ההקלטה."""
    recordings = [_recording("rec-1", "ישיבת צוות"), _recording("rec-2", "שיחה עם דני")]
    citations = _normalize_citations(
        [{"recording_id": "rec_2", "recording_title": "שיחה עם דני", "timestamp": "1:00"}],
        recordings,
    )
    assert citations[0]["recording_id"] == "rec-2"


def test_falls_back_to_the_only_recording_asked_about():
    recordings = [_recording("rec-1", "ישיבת צוות")]
    citations = _normalize_citations(
        [{"recording_title": "משהו אחר לגמרי", "timestamp": "2:21"}], recordings
    )
    assert citations[0]["recording_id"] == "rec-1"


def test_unresolvable_citation_gets_no_id():
    """עדיף ציטוט שאי אפשר להקיש עליו מאשר נגן שקופץ להקלטה הלא נכונה."""
    recordings = [_recording("rec-1", "ישיבת צוות"), _recording("rec-2", "שיחה עם דני")]
    citations = _normalize_citations(
        [{"recording_title": "פגישה שלא נבחרה", "timestamp": "2:21"}], recordings
    )
    assert citations[0]["recording_id"] is None
    assert citations[0]["start_seconds"] == 141


def test_ambiguous_title_between_two_recordings_gets_no_id():
    recordings = [_recording("rec-1", "ישיבת צוות"), _recording("rec-2", "ישיבת צוות")]
    citations = _normalize_citations(
        [{"recording_title": "ישיבת צוות", "timestamp": "0:10"}], recordings
    )
    assert citations[0]["recording_id"] is None


def test_junk_citations_are_dropped():
    citations = _normalize_citations(["לא אובייקט", None], [_recording("rec-1", "א")])
    assert citations == []
