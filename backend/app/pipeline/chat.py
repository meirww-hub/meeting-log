"""שאלות ותשובות חופשיות על תמלולי הקלטות עבר, עם ציטוט מדויק של דקה:שנייה
ושם ההקלטה שממנה הגיע המידע."""

import json

from google import genai
from google.genai import types

from app.config import settings

from app.pipeline._model import GEMINI_MODEL as _MODEL

_SYSTEM_PROMPT = """\
אתה עוזר שעונה על שאלות בהתבסס אך ורק על תמלולי הקלטות וקבצים מצורפים
שסופקו לך. אל תמציא מידע שלא מופיע בהם - אם התשובה לא נמצאת בהם, אמור זאת
בפירוש. תענה תמיד בעברית תקנית. הפלט חייב להיות JSON תקני בלבד, ללא טקסט
נוסף.
"""

_SCHEMA_HINT = """\
החזר אובייקט JSON יחיד במבנה הבא בדיוק:
{{
  "answer": "התשובה לשאלה, או הסבר שלא נמצא מידע רלוונטי במקורות שסופקו",
  "citations": [
    {{"recording_title": "...", "timestamp": "דקה:שנייה (למשל 3:42) לתמלול, או שם הקובץ המצורף אם המידע ממנו", "quote": "המשפט המדויק שמבוסס עליו התשובה"}}
  ]
}}
אם לא נמצאה תשובה - השאירו "citations" מערך ריק.

המקורות (תמלול מתויג בזמן [דקה:שנייה]; קבצים מצורפים מתויגים בשמם):
{transcripts}

השאלה: {question}
"""


def _format_seconds(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _format_recording(recording: dict) -> str:
    title = recording.get("title") or "ללא כותרת"
    date = recording.get("date") or ""
    lines = [f"=== הקלטה: {title} ({date}) ==="]
    for seg in recording.get("transcript") or []:
        ts = _format_seconds(seg.get("start_seconds", 0))
        lines.append(f"[{ts}] {seg.get('speaker_label', '')}: {seg.get('text', '')}")

    for attachment in recording.get("attachments") or []:
        lines.append(f"--- קובץ מצורף: {attachment.get('filename', '')} ---")
        lines.append(attachment.get("full_text", ""))

    return "\n".join(lines)


def answer_question(recordings: list[dict], question: str) -> dict:
    client = genai.Client(api_key=settings.gemini_api_key)

    transcripts_text = "\n\n".join(_format_recording(r) for r in recordings)

    response = client.models.generate_content(
        model=_MODEL,
        contents=_SCHEMA_HINT.format(transcripts=transcripts_text, question=question),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )

    return json.loads(response.text)
