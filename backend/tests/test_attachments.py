"""קובץ מצורף: המרת Office, תקרת פלט מפורשת, והעלאה-לפני-סיכום שמאפשרת
retry בלי לבקש מהמשתמש לצרף מחדש.

הבדיקות כאן נועלות שלוש התנהגויות שנוספו יחד:
  1. .docx/.xlsx/.pptx לא נשלחים ל-Gemini כמות שהם (הוא לא קורא אותם) -
     הם עוברים המרה ל-PDF דרך Drive קודם (ראה _prepare_for_gemini).
  2. הקריאה ל-Gemini תמיד עם max_output_tokens מפורש - בלי זה, מסמך שהתמצית
     המורחבת שלו ארוכה חוזרת כ-JSON קטוע, בדיוק כמו שקרה לתמלול ב-2026-08-13
     (ראה _model.py).
  3. הקובץ המקורי עולה ל-Drive **לפני** הקריאה ל-Gemini, ולא אחריה - כישלון
     בסיכום לא מאבד את הקובץ, ו-retry_attachment יכול להוריד אותו בחזרה
     בלי שהמשתמש יצטרך לצרף אותו שוב.
"""

import pytest

from app.pipeline import attachments


class _FakeFiles:
    def upload(self, **kwargs):
        return "uploaded-file-handle"


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    _models = None
    _files = None

    def __init__(self, **kwargs):
        self.files = _FakeClient._files or _FakeFiles()
        self.models = _FakeClient._models


@pytest.fixture
def fake_gemini(monkeypatch):
    """מחליף את genai.Client בכפיל שמחזיר תשובות מוכנות ומתעד קריאות."""
    models = _FakeModels([])
    _FakeClient._models = models
    _FakeClient._files = _FakeFiles()
    monkeypatch.setattr(attachments.genai, "Client", _FakeClient)
    monkeypatch.setattr(attachments, "call_with_retry", lambda fn, *a, **k: fn(*a, **k))
    return models


# ---------- MIME / המרת Office ----------


def test_mime_type_prefers_content_type_over_guessing():
    assert attachments.mime_type_for("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_mime_type_falls_back_to_filename_when_content_type_is_generic():
    assert attachments.mime_type_for("invoice.pdf", "application/octet-stream") == "application/pdf"


def test_pdf_and_images_are_not_converted(monkeypatch):
    monkeypatch.setattr(
        attachments.drive, "convert_to_pdf", lambda *a, **k: pytest.fail("PDF לא אמור לעבור המרה")
    )

    content, mime = attachments._prepare_for_gemini(b"raw-pdf-bytes", "x.pdf", "application/pdf")

    assert content == b"raw-pdf-bytes"
    assert mime == "application/pdf"


def test_word_file_is_converted_to_pdf_before_reaching_gemini(monkeypatch):
    converted = {}

    def fake_convert(content, filename, mime_type):
        converted["args"] = (content, filename, mime_type)
        return b"%PDF-converted"

    monkeypatch.setattr(attachments.drive, "convert_to_pdf", fake_convert)
    word_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    content, mime = attachments._prepare_for_gemini(b"docx-bytes", "protocol.docx", word_mime)

    assert content == b"%PDF-converted"
    assert mime == "application/pdf"
    assert converted["args"] == (b"docx-bytes", "protocol.docx", word_mime)


def test_oversized_office_file_is_rejected_before_calling_drive(monkeypatch):
    monkeypatch.setattr(
        attachments.drive, "convert_to_pdf", lambda *a, **k: pytest.fail("לא אמור להגיע לכאן")
    )
    huge = b"x" * (attachments.MAX_OFFICE_ATTACHMENT_BYTES + 1)
    excel_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    with pytest.raises(ValueError, match="גדול מדי"):
        attachments._prepare_for_gemini(huge, "budget.xlsx", excel_mime)


# ---------- summarize_file: תקרת פלט מפורשת ----------


def test_summarize_file_sends_an_explicit_output_ceiling(fake_gemini):
    fake_gemini._responses.append(
        _FakeResponse('{"summary": "תקציר", "full_text": "תוכן מלא"}')
    )

    summary, full_text = attachments.summarize_file(b"pdf-bytes", "invoice.pdf", "application/pdf")

    assert summary == "תקציר"
    assert full_text == "תוכן מלא"
    config = fake_gemini.calls[0]["config"]
    assert config.max_output_tokens == attachments._MAX_OUTPUT_TOKENS


# ---------- integrate_into_summary: שילוב לפי הקשר + ציון מקור ----------


def test_integrate_sends_existing_summary_and_file_content_with_output_ceiling(fake_gemini):
    fake_gemini._responses.append(_FakeResponse('{"summary": "1. נושא קיים - הורחב\\n2. נושא חדש"}'))

    merged = attachments.integrate_into_summary(
        "1. נושא קיים", "תקציב.xlsx", "תקציר קצר", "תוכן מלא ומפורט"
    )

    assert merged == "1. נושא קיים - הורחב\n2. נושא חדש"
    call = fake_gemini.calls[0]
    assert "1. נושא קיים" in call["contents"]
    assert "תקציב.xlsx" in call["contents"]
    assert "תוכן מלא ומפורט" in call["contents"]
    assert call["config"].max_output_tokens == attachments._MAX_OUTPUT_TOKENS


def test_integrate_prefers_full_text_over_short_summary_when_both_present(fake_gemini):
    fake_gemini._responses.append(_FakeResponse('{"summary": "merged"}'))

    attachments.integrate_into_summary("קיים", "f.pdf", "תקציר קצר בלבד", "תוכן מפורט הרבה יותר")

    assert "תוכן מפורט הרבה יותר" in fake_gemini.calls[0]["contents"]
    assert "תקציר קצר בלבד" not in fake_gemini.calls[0]["contents"]


def test_integrate_falls_back_to_existing_summary_on_empty_model_response(fake_gemini):
    fake_gemini._responses.append(_FakeResponse('{"summary": ""}'))

    merged = attachments.integrate_into_summary("1. הקיים", "f.pdf", "תקציר", "")

    assert merged == "1. הקיים"


# ---------- process_attachment / retry_attachment: עלייה ל-Drive לפני Gemini ----------


@pytest.fixture
def store(monkeypatch):
    """Firestore בזיכרון: הקלטה אחת עם attachments, ו-summary נוכחי."""
    recordings = {"rec-1": {"title": "ישיבת צוות", "summary": "1. קיים", "attachments": []}}
    updates = []

    def fake_get(recording_id):
        return recordings.get(recording_id)

    def fake_update_attachment(recording_id, attachment_id, **fields):
        updates.append({"attachment_id": attachment_id, **fields})

    def fake_update_field_with(recording_id, field, updater):
        current = recordings[recording_id].get(field)
        new_value = updater(current)
        recordings[recording_id][field] = new_value
        return new_value

    monkeypatch.setattr(attachments.firestore_store, "get_recording", fake_get)
    monkeypatch.setattr(attachments.firestore_store, "update_attachment", fake_update_attachment)
    monkeypatch.setattr(
        attachments.firestore_store, "update_recording_field_with", fake_update_field_with
    )
    return type("Store", (), {"recordings": recordings, "updates": updates})()


def test_process_attachment_uploads_before_calling_gemini(store, fake_gemini, monkeypatch, tmp_path):
    """הסדר קריטי: אם ה-upload היה קורה אחרי Gemini, כישלון ב-Gemini היה
    משאיר את הקובץ בלי גיבוי ב-Drive בכלל - retry לא היה יכול לשחזר אותו."""
    order = []

    def fake_upload(content, filename, mime_type, title):
        order.append("upload")
        return {"id": "FILE1", "url": "https://drive.google.com/file/d/FILE1/view"}

    original_generate = fake_gemini.generate_content

    def fake_generate(**kwargs):
        order.append("gemini")
        return original_generate(**kwargs)

    monkeypatch.setattr(attachments.drive, "upload_attachment", fake_upload)
    monkeypatch.setattr(attachments.drive, "update_summary_doc", lambda *a, **k: None)
    monkeypatch.setattr(fake_gemini, "generate_content", fake_generate)
    fake_gemini._responses.append(_FakeResponse('{"summary": "תקציר", "full_text": "מלא"}'))
    fake_gemini._responses.append(_FakeResponse('{"summary": "1. קיים\\n2. חדש (מהקובץ)"}'))

    file_path = tmp_path / "note.pdf"
    file_path.write_bytes(b"pdf-bytes")

    attachments.process_attachment("rec-1", "att-1", str(file_path), "note.pdf", "application/pdf")

    assert order == ["upload", "gemini", "gemini"]
    assert not file_path.exists(), "הקובץ הזמני חייב להימחק אחרי הקריאה"
    assert store.recordings["rec-1"]["summary"] == "1. קיים\n2. חדש (מהקובץ)"
    done_update = [u for u in store.updates if u.get("status") == "done"][0]
    assert done_update["summary"] == "תקציר"
    assert done_update["full_text"] == "מלא"


def test_process_attachment_records_error_and_keeps_the_drive_upload_on_gemini_failure(
    store, fake_gemini, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        attachments.drive,
        "upload_attachment",
        lambda *a, **k: {"id": "FILE1", "url": "https://drive.google.com/file/d/FILE1/view"},
    )
    fake_gemini._responses.append(_FakeResponse("not valid json"))

    file_path = tmp_path / "note.pdf"
    file_path.write_bytes(b"pdf-bytes")

    attachments.process_attachment("rec-1", "att-1", str(file_path), "note.pdf", "application/pdf")

    upload_recorded = [u for u in store.updates if "drive_file_id" in u][0]
    assert upload_recorded["drive_file_id"] == "FILE1"
    error_recorded = [u for u in store.updates if u.get("status") == "error"][0]
    assert error_recorded["attachment_id"] == "att-1"
    # הסיכום לא נגע בו - כישלון בקובץ אחד לא אמור לפגוע בסיכום הקיים.
    assert store.recordings["rec-1"]["summary"] == "1. קיים"


def test_retry_attachment_downloads_from_drive_instead_of_reuploading(
    store, fake_gemini, monkeypatch
):
    monkeypatch.setattr(attachments.drive, "download_file", lambda file_id: b"pdf-bytes-from-drive")
    monkeypatch.setattr(
        attachments.drive,
        "upload_attachment",
        lambda *a, **k: pytest.fail("retry לא אמור להעלות שוב - הקובץ כבר ב-Drive"),
    )
    monkeypatch.setattr(attachments.drive, "update_summary_doc", lambda *a, **k: None)
    fake_gemini._responses.append(_FakeResponse('{"summary": "תקציר", "full_text": "מלא"}'))
    fake_gemini._responses.append(_FakeResponse('{"summary": "1. קיים\\n2. חדש"}'))

    attachments.retry_attachment(
        "rec-1",
        "att-1",
        "FILE1",
        "https://drive.google.com/file/d/FILE1/view",
        "note.pdf",
        "application/pdf",
    )

    done_update = [u for u in store.updates if u.get("status") == "done"][0]
    assert done_update["summary"] == "תקציר"
