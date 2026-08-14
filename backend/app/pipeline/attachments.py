"""קריאה וסיכום של קבצים מצורפים (PDF/תמונה/Word/Excel/PowerPoint/טקסט)
באמצעות Gemini, ושילוב תוכנם בתוך סיכום הפגישה הקיים.

מפיק שני דברים: תקציר קצר (שמשולב בתוך קובץ הסיכום של הפגישה, בהקשר
המתאים ותוך ציון שם הקובץ שממנו הגיע - ראה integrate_into_summary) ותוכן
מלא/מורחב (לשימוש פנימי בלבד - כדי שמסך הצ'אט יוכל לענות על שאלות מדויקות
על הקובץ, בלי להציג את הטקסט המלא בשום מקום אחר).

הקובץ המקורי עולה ל-Drive **תמיד, לפני כל קריאה ל-Gemini**: כך שגם אם
הסיכום נכשל (עומס, קובץ לא נתמך, תקלה זמנית) הקובץ עצמו כבר בטוח ב-Drive,
ואפשר לנסות לסכם אותו שוב (retry_attachment) בלי שהמשתמש יצטרך לצרף אותו
מחדש - ראה POST /recordings/{id}/attachments/{attachment_id}/retry ב-main.py.
"""

import io
import json
import mimetypes
import os

from google import genai
from google.genai import types

from app.config import settings
from app.pipeline._retry import call_with_retry
from app.services import drive, firestore_store

from app.pipeline._model import GEMINI_MAX_OUTPUT_TOKENS as _MAX_OUTPUT_TOKENS
from app.pipeline._model import GEMINI_MODEL as _MODEL

# מגבלת Drive files.export (ההמרה ל-PDF של קבצי Office, ראה
# drive.convert_to_pdf) היא 10MB לפלט. נבדק כאן מראש כדי להחזיר שגיאה
# ברורה בעברית במקום HttpError גולמי מ-Drive. קובץ שאינו Office (PDF/
# תמונה) לא עובר המרה כלל ולכן לא כפוף למגבלה הזו.
MAX_OFFICE_ATTACHMENT_BYTES = 10 * 1024 * 1024

_SYSTEM_PROMPT = """\
אתה מסכם מסמכים. תקרא את הקובץ המצורף ותחזיר JSON תקני בלבד, ללא טקסט נוסף.
"""

_SCHEMA_HINT = """\
החזר אובייקט JSON יחיד במבנה הבא בדיוק:
{
  "summary": "תקציר קצר וברור של הקובץ, כמה משפטים בלבד, בעברית תקנית",
  "full_text": "תמצית מורחבת/תוכן עיקרי של הקובץ בעברית - לא בהכרח כל מילה, אבל מספיק מפורט כדי לענות על שאלות עתידיות עליו"
}
"""

# הפרומפט הזה הוא הליבה של "שילוב לפי הקשר": בלי אותו הוא הסיכום הישן פשוט
# הדביק "--- קובץ מצורף: X ---" בתור פסקה נפרדת בסוף. עכשיו המודל מקבל את
# הסיכום הקיים ואת תוכן הקובץ יחד, ומתבקש למזג - להרחיב נושא קיים כשהקובץ
# רלוונטי אליו, ולהוסיף נושא חדש רק כשאין לו זיקה לאף נושא קיים.
_INTEGRATE_SYSTEM_PROMPT = """\
אתה עוזר שמשלב תוכן של קובץ מצורף (Word/Excel/PowerPoint/PDF/תמונה) לתוך
סיכום פגישה קיים, שכתוב כרשימת נושאים ממוספרים. תענה תמיד בעברית תקנית.
הפלט חייב להיות JSON תקני בלבד, ללא טקסט נוסף.
"""

_INTEGRATE_TASK_HINT = """\
הסיכום הקיים של הפגישה:
{existing_summary}

תוכן הקובץ המצורף "{filename}":
{file_content}

שלבו את תוכן הקובץ בתוך הסיכום, לפי הכללים הבאים:
  • אם תוכן הקובץ קשור לנושא שכבר מופיע בסיכום - הרחיבו את אותו נושא
    בפרטים מהקובץ, במקום להוסיף נושא נפרד.
  • אם תוכן הקובץ לא קשור לאף נושא קיים - הוסיפו נושא ממוספר חדש (בהמשך
    המספור הקיים) שמסכם אותו.
  • **חובה לציין בכל מקום שבו נעשה שימוש בתוכן הקובץ שהמידע לקוח ממנו**,
    בתוך המשפט עצמו, בניסוח כמו "לפי הקובץ '{filename}'" או "(מתוך
    {filename})". אל תשלבו עובדה מהקובץ בלי לציין את המקור - זה מה שמבדיל
    מידע שנאמר בפגישה בפועל ממידע שהגיע מקובץ מצורף.
  • אל תמציאו ואל תשערו - רק מה שכתוב בפועל בקובץ.
  • אל תמחקו ואל תשנו תוכן קיים בסיכום שלא קשור לקובץ הזה - רק הוסיפו/
    הרחיבו.
  • שמרו על מבנה הנושאים הממוספרים הקיים (1. ... 2. ... וכן הלאה).

החזירו אובייקט JSON יחיד במבנה הבא בדיוק:
{{"summary": "הסיכום המלא המעודכן, עם כל הנושאים הממוספרים"}}
"""


def mime_type_for(filename: str, content_type: str = "") -> str:
    """MIME של קובץ מצורף: קודם ה-Content-Type שהלקוח שלח (אמין יותר -
    כולל את סוג ה-Office המדויק), ורק אם הוא חסר/גנרי נופלים לניחוש לפי
    סיומת הקובץ."""
    if content_type and content_type != "application/octet-stream":
        return content_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _prepare_for_gemini(content: bytes, filename: str, mime_type: str) -> tuple[bytes, str]:
    """ממיר קובץ Office ל-PDF (Gemini לא קורא .docx/.xlsx/.pptx ישירות),
    ומחזיר את הבייטים+MIME שנשלחים בפועל. קובץ שאינו Office (PDF/תמונה/
    טקסט) חוזר כמות שהוא."""
    if mime_type not in drive.OFFICE_TO_GOOGLE_MIME:
        return content, mime_type
    if len(content) > MAX_OFFICE_ATTACHMENT_BYTES:
        raise ValueError(
            f"קובץ {filename} גדול מדי להמרה "
            f"({len(content) // (1024 * 1024)}MB, מגבלה "
            f"{MAX_OFFICE_ATTACHMENT_BYTES // (1024 * 1024)}MB)"
        )
    return drive.convert_to_pdf(content, filename, mime_type), "application/pdf"


def summarize_file(content: bytes, filename: str, mime_type: str) -> tuple[str, str]:
    """מחזיר (summary, full_text)."""
    client = genai.Client(api_key=settings.gemini_api_key)
    gemini_content, gemini_mime = _prepare_for_gemini(content, filename, mime_type)

    uploaded_file = client.files.upload(
        file=io.BytesIO(gemini_content),
        config=types.UploadFileConfig(mime_type=gemini_mime),
    )

    response = call_with_retry(
        client.models.generate_content,
        model=_MODEL,
        contents=[uploaded_file, _SCHEMA_HINT],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            # מפורש, אותה סיבה כמו בכל שלב אחר (ראה _model.py) - בלי זה
            # תמצית מורחבת (full_text) של מסמך ארוך חוזרת כ-JSON קטוע.
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
    )

    data = json.loads(response.text)
    return data["summary"], data["full_text"]


def integrate_into_summary(
    existing_summary: str, filename: str, file_summary: str, file_full_text: str
) -> str:
    """משלב את תוכן הקובץ המצורף לתוך הסיכום הקיים, לפי הקשר ועם ציון
    המקור - ולא כפסקה נפרדת שנדבקת לסוף (ראה docstring למעלה)."""
    client = genai.Client(api_key=settings.gemini_api_key)

    # תמצית מורחבת (full_text) עדיפה על התקציר הקצר - היא זו שמאפשרת למודל
    # לשלב פרטים קונקרטיים (סכומים, תאריכים) לתוך הנושא הרלוונטי ולא רק
    # משפט כללי.
    file_content = file_full_text.strip() or file_summary.strip()

    response = call_with_retry(
        client.models.generate_content,
        model=_MODEL,
        contents=_INTEGRATE_TASK_HINT.format(
            existing_summary=existing_summary.strip() or "(אין עדיין סיכום)",
            filename=filename,
            file_content=file_content,
        ),
        config=types.GenerateContentConfig(
            system_instruction=_INTEGRATE_SYSTEM_PROMPT,
            response_mime_type="application/json",
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
    )

    data = json.loads(response.text)
    merged = str(data.get("summary") or "").strip()
    return merged or existing_summary


def _summary_doc_id(recording: dict) -> str | None:
    return recording.get("drive_summary_doc_id") or drive.file_id_from_url(
        recording.get("drive_summary_url")
    )


def _process_content(
    recording_id: str,
    attachment_id: str,
    content: bytes,
    filename: str,
    mime_type: str,
    drive_file: dict | None,
) -> None:
    """הליבה המשותפת לצירוף חדש ולניסיון חוזר: מעלה ל-Drive אם עוד לא
    הועלה, מסכם, ומשלב את התקציר לתוך סיכום הפגישה."""
    recording = firestore_store.get_recording(recording_id)
    if recording is None:
        return

    if drive_file is None:
        drive_file = drive.upload_attachment(content, filename, mime_type, recording.get("title", ""))
        firestore_store.update_attachment(
            recording_id,
            attachment_id,
            drive_file_id=drive_file["id"],
            drive_url=drive_file["url"],
        )

    try:
        summary, full_text = summarize_file(content, filename, mime_type)
    except Exception as e:
        firestore_store.update_attachment(recording_id, attachment_id, status="error", error=str(e))
        return

    merged_summary = firestore_store.update_recording_field_with(
        recording_id,
        "summary",
        lambda current: integrate_into_summary(current or "", filename, summary, full_text),
    )

    summary_doc_id = _summary_doc_id(recording)
    if summary_doc_id:
        drive.update_summary_doc(summary_doc_id, merged_summary)

    firestore_store.update_attachment(
        recording_id,
        attachment_id,
        status="done",
        summary=summary,
        full_text=full_text,
    )


def process_attachment(
    recording_id: str, attachment_id: str, file_path: str, filename: str, mime_type: str
) -> None:
    """זורם רקע מלא לצירוף חדש: קורא את הקובץ הזמני שהועלה, מעבד אותו,
    ומנקה את הקובץ הזמני מיד אחרי הקריאה - התוכן כבר עומד לעלות ל-Drive,
    שהוא המקום שממנו retry_attachment מוריד אותו בחזרה אם צריך, כך שאין
    צורך להשאיר עותק מקומי (ובוודאי לא ב-Cloud Run, שבו הדיסק המקומי חולף
    בין מופעים)."""
    try:
        with open(file_path, "rb") as f:
            content = f.read()
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

    _process_content(recording_id, attachment_id, content, filename, mime_type, drive_file=None)


def retry_attachment(
    recording_id: str, attachment_id: str, file_id: str, drive_url: str, filename: str, mime_type: str
) -> None:
    """מנסה שוב לסכם מצורף שנכשל, בלי לבקש מהמשתמש לצרף אותו מחדש - מוריד
    את הבייטים המקוריים בחזרה מ-Drive, ששם הם כבר שמורים מאז ההעלאה
    הראשונה (ראה docstring למעלה)."""
    content = drive.download_file(file_id)
    _process_content(
        recording_id,
        attachment_id,
        content,
        filename,
        mime_type,
        drive_file={"id": file_id, "url": drive_url},
    )
