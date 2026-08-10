"""קריאה וסיכום של קבצים מצורפים (PDF/תמונה/מסמך/טקסט) באמצעות Gemini.

מפיק שני דברים: תקציר קצר (שמתווסף לקובץ הסיכום של הפגישה) ותוכן מלא/מורחב
(לשימוש פנימי בלבד - כדי שמסך הצ'אט יוכל לענות על שאלות מדויקות על הקובץ,
בלי להציג את הטקסט המלא בשום מקום אחר)."""

import json
import mimetypes

from google import genai
from google.genai import types

from app.config import settings
from app.services import firestore_store
from app.services.drive import update_summary_doc, upload_attachment

from app.pipeline._model import GEMINI_MODEL as _MODEL

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


def _mime_type_for(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def summarize_file(file_path: str, filename: str) -> tuple[str, str]:
    """מחזיר (summary, full_text)."""
    client = genai.Client(api_key=settings.gemini_api_key)

    uploaded_file = client.files.upload(
        file=file_path,
        config=types.UploadFileConfig(mime_type=_mime_type_for(filename)),
    )

    response = client.models.generate_content(
        model=_MODEL,
        contents=[uploaded_file, _SCHEMA_HINT],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )

    data = json.loads(response.text)
    return data["summary"], data["full_text"]


def process_attachment(
    recording_id: str, user_id: str, file_path: str, filename: str
) -> None:
    """זורם רקע מלא: מסכם את הקובץ, מעלה אותו כמו שהוא לתיקיית ההקלטה
    ב-Drive, מוסיף את התקציר לקובץ הסיכום הקיים, ושומר הכול ב-Firestore
    (כולל full_text הפנימי, לשימוש מסך הצ'אט)."""
    recording = firestore_store.get_recording(recording_id)
    if recording is None:
        return

    summary, full_text = summarize_file(file_path, filename)

    folder_id = recording.get("drive_folder_id")
    drive_url = ""
    if folder_id:
        drive_url = upload_attachment(
            file_path, filename, _mime_type_for(filename), folder_id
        )

    combined_summary = (
        f"{recording.get('summary', '')}\n\n"
        f"--- קובץ מצורף: {filename} ---\n{summary}"
    ).strip()

    summary_doc_id = recording.get("drive_summary_doc_id")
    if summary_doc_id:
        update_summary_doc(summary_doc_id, combined_summary)

    attachments = recording.get("attachments") or []
    attachments.append(
        {
            "filename": filename,
            "summary": summary,
            "full_text": full_text,
            "drive_url": drive_url,
        }
    )

    firestore_store.set_recording_status(
        recording_id,
        user_id,
        "done",
        summary=combined_summary,
        attachments=attachments,
    )
