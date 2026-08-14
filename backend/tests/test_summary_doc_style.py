"""העיצוב של מסמך הסיכום ב-Drive.

מה שנבדק כאן הוא ה-HTML שנשלח ל-Drive, לא ה-Doc שנוצר ממנו: את ההמרה
עצמה עושה Google. הבדיקות נצמדות לארבעת הדברים שהמשתמש רואה בפועל -
יישור לימין, ריווח שורות, שורה ריקה בין פסקה לפסקה, והדגשת נתונים - וגם
לכך שהעיצוב שורד עריכה של הסיכום מהאפליקציה ולא רק את היצירה הראשונה.
"""

import re

import pytest

from app.services import drive as drive_service

SUMMARY = """\
1. תקציב הפרויקט: מאיר אמר שהתקציב עומד על 120,000 ₪ לשנה הקרובה.
2. לוח זמנים: דנה ביקשה לדחות את המסירה ל-15 בספטמבר, ומאיר הסכים.
"""


def blocks(html: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, re.DOTALL)


def test_every_block_is_right_aligned():
    html = drive_service._summary_to_rtl_html(SUMMARY)

    for block in re.findall(r"<(?:h2|p|ul|li)\b[^>]*>", html):
        assert 'dir="rtl"' in block, block
        assert "text-align:right" in block, block


def test_paragraphs_are_spaced_and_separated_by_a_blank_line():
    """ריווח שורות מוגדל בתוך הפסקה, ומרווח של שורה בין פסקה לפסקה.

    המרווח הוא margin ולא פסקה ריקה: פסקה ריקה נמחקת ברגע שמישהו עורך את
    המסמך, והמרווח היה נעלם איתה."""
    html = drive_service._summary_to_rtl_html(SUMMARY)

    for block in re.findall(r"<(?:h2|p|li)\b[^>]*>", html):
        assert "line-height:1.6" in block, block
    for paragraph in re.findall(r"<p\b[^>]*>", html):
        assert "margin:0 0 12pt 0" in paragraph, paragraph
    assert "<br>" not in html


def test_topic_becomes_a_heading_above_its_own_paragraph():
    html = drive_service._summary_to_rtl_html(SUMMARY)

    headings = blocks(html, "h2")
    assert headings[0].startswith("1. תקציב הפרויקט")
    assert headings[1].startswith("2. לוח זמנים")
    assert "מאיר אמר" in blocks(html, "p")[0]


def test_headings_stand_out_from_the_body():
    html = drive_service._summary_to_rtl_html(SUMMARY)

    [heading] = re.findall(r"<h2\b[^>]*>", html)[:1]
    assert "font-size:13pt" in heading
    assert "font-size:11pt" in re.findall(r"<p\b[^>]*>", html)[0]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("שילמנו 12,450 ₪ על הרישוי", "12,450 ₪"),
        ("המחיר עלה ב-15%", "15%"),
        ("נפגשים ב-15 בספטמבר", "15 בספטמבר"),
        ("המסירה נקבעה ל-2026-09-15", "2026-09-15"),
        ("הפגישה תתחיל ב-14:00", "14:00"),
        ("הפרויקט יימשך 3 חודשים", "3 חודשים"),
        ("התקציב הוא 40 אלף ₪", "40 אלף ₪"),
        ("גויסו 12 מיליון דולר", "12 מיליון דולר"),
    ],
)
def test_concrete_figures_are_bold(line, expected):
    html = drive_service._summary_to_rtl_html(line)

    assert f"<b>{expected}" in html, html


def test_text_without_figures_is_left_alone():
    html = drive_service._summary_to_rtl_html("דנה הציגה את הסטטוס לצוות")

    assert "<b>" not in html


def test_a_hebrew_word_that_starts_like_a_unit_is_not_swallowed():
    """"3 שנהיה" - "שנה" בתחילת מילה אחרת אינה יחידת מידה."""
    html = drive_service._summary_to_rtl_html("היו שם 3 שנהיים")

    assert "<b>3</b>" in html


def test_model_markdown_emphasis_becomes_real_bold():
    """המודל מדגיש לפעמים בכוכביות; בטקסט גולמי זה נראה כמו זבל."""
    html = drive_service._summary_to_rtl_html("**חשוב**: לחתום לפני החג")

    assert "<b>חשוב</b>" in html
    assert "*" not in html


def test_bullets_become_a_list():
    html = drive_service._summary_to_rtl_html("1. סיכומים:\n- לשלוח חוזה\n- לאשר תקציב")

    assert blocks(html, "li") == ["לשלוח חוזה", "לאשר תקציב"]
    assert len(blocks(html, "ul")) == 1


def test_attachment_separator_becomes_a_heading():
    html = drive_service._summary_to_rtl_html(
        "1. נושא: תיאור\n\n--- קובץ מצורף: חשבונית.pdf ---\nתקציר הקובץ"
    )

    assert "קובץ מצורף: חשבונית.pdf" in blocks(html, "h2")[-1]
    assert "---" not in html


def test_a_long_line_without_a_colon_stays_a_paragraph():
    """סעיף בלי כותרת לא הופך לכותרת ענקית - רק המספר שלו מודגש."""
    long_line = "1. " + "מאיר סקר את מצב הפרויקט מול הלקוח והציג את ההתקדמות " * 3

    html = drive_service._summary_to_rtl_html(long_line)

    assert not blocks(html, "h2")
    assert blocks(html, "p")[0].startswith("<b>1.</b> מאיר סקר")


def test_free_text_is_not_dropped():
    """סיכום שלא נכתב במבנה הצפוי חייב להגיע למסמך במלואו."""
    html = drive_service._summary_to_rtl_html("שורה ראשונה\nשורה שנייה")

    assert blocks(html, "p") == ["שורה ראשונה", "שורה שנייה"]


def test_html_in_the_summary_is_escaped():
    html = drive_service._summary_to_rtl_html("דובר אמר <script>alert(1)</script>")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


class _CapturingDrive:
    """drive.files().update() ששומר את מה שהועלה, כדי לבדוק מה נשלח."""

    def __init__(self):
        self.uploaded = ""

    def files(self):
        return self

    def update(self, fileId, media_body=None, body=None):
        self.uploaded = media_body.getbytes(0, media_body.size()).decode("utf-8")
        return self

    def execute(self):
        return {}


def test_editing_the_summary_keeps_the_styling(monkeypatch):
    """שינוי שמות דוברים או צירוף קובץ מעדכנים את המסמך - והעיצוב חייב
    להיבנות מחדש, אחרת הסיכום היה חוזר לטקסט שטוח בעריכה הראשונה."""
    drive = _CapturingDrive()
    monkeypatch.setattr(drive_service, "_drive_client", lambda: drive)

    drive_service.update_summary_doc("doc-1", SUMMARY)

    assert "<h2" in drive.uploaded
    assert "line-height:1.6" in drive.uploaded
    assert "<b>120,000 ₪</b>" in drive.uploaded


def test_the_transcript_is_not_restyled(monkeypatch):
    """התמלול נקרא ברצף ולא בסריקה - הוא נשאר שורה-שורה, בלי כותרות
    והדגשות שהיו הופכות כל שעה וכל מספר בדיבור למודגשים."""
    drive = _CapturingDrive()
    monkeypatch.setattr(drive_service, "_drive_client", lambda: drive)

    drive_service.update_text_doc("doc-1", "דני:\nנתחיל ב-14:00")

    assert "<h2" not in drive.uploaded
    assert "<b>" not in drive.uploaded
