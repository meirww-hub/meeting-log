"""שאלות ותשובות חופשיות על תמלולי הקלטות עבר, עם ציטוט מדויק של דקה:שנייה
ושם ההקלטה שממנה הגיע המידע.

כל ציטוט חוזר גם עם המזהה של ההקלטה ועם הזמן בשניות, ולא רק כמחרוזת
לתצוגה: כך אפשר להקיש עליו באפליקציה ולשמוע את הרגע עצמו, במקום לחפש אותו
ביד בתוך הקלטה של שעה (ראה ChatActivity.kt ו-GET /recordings/{id}/audio).
"""

import json
import re

from google import genai
from google.genai import types

from app.config import settings
from app.pipeline.speakers import display_label

from app.pipeline._model import GEMINI_MAX_OUTPUT_TOKENS as _MAX_OUTPUT_TOKENS
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
    {{"recording_id": "מזהה ההקלטה שממנה נלקח המידע, בדיוק כפי שמופיע בכותרת שלה למטה", "recording_title": "...", "timestamp": "דקה:שנייה (למשל 3:42) לתמלול, או שם הקובץ המצורף אם המידע ממנו", "quote": "המשפט המדויק שמבוסס עליו התשובה"}}
  ]
}}
אם לא נמצאה תשובה - השאירו "citations" מערך ריק.

כשאתה מזכיר זמן בתוך "answer", כתוב אותו תמיד בצורה דקה:שנייה (למשל 2:21),
כדי שיהיה אפשר להקיש עליו ולהאזין לרגע עצמו.

תווית דובר שמסתיימת ב-"(?)" פירושה שלא ידוע בוודאות מי הדובר באותה שורה.
אל תייחס אמירה כזו לאדם בשם - כתוב "אחד הדוברים", ואל תעתיק את הסימון עצמו.

המקורות (תמלול מתויג בזמן [דקה:שנייה]; קבצים מצורפים מתויגים בשמם):
{transcripts}

השאלה: {question}
"""

# "3:42" או "1:02:03", גם בתוך משפט ("בין 2:21 ל-5:04" -> 2:21).
_TIME_PATTERN = re.compile(r"(\d{1,3}):([0-5]\d)(?::([0-5]\d))?")


def _format_seconds(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def parse_timestamp_seconds(timestamp: str) -> float | None:
    """הזמן הראשון שמופיע במחרוזת, בשניות - או None אם אין בה זמן כלל.

    None הוא מצב לגיטימי: ציטוט יכול להצביע על קובץ מצורף ("תקציב.pdf"),
    שאין לו מקום בציר הזמן של ההקלטה.
    """
    match = _TIME_PATTERN.search(timestamp or "")
    if match is None:
        return None
    first, second, third = match.groups()
    if third is None:
        return int(first) * 60 + int(second)
    return int(first) * 3600 + int(second) * 60 + int(third)


def _resolve_recording_id(citation: dict, recordings: list[dict]) -> str | None:
    """מזהה ההקלטה שהציטוט מצביע עליה, אחרי אימות מול ההקלטות שנשאלו.

    המודל מתבקש להחזיר את המזהה כלשונו, אבל מזהה שהוא ממציא (או מקצר) היה
    שולח את הנגן להקלטה שלא קיימת; לכן מקבלים רק מזהה מתוך הרשימה שנשלחה,
    ונופלים לזיהוי לפי כותרת ולבסוף - כשנשאלה הקלטה אחת בלבד - עליה.
    """
    known_ids = {r.get("recording_id") for r in recordings}
    candidate = str(citation.get("recording_id") or "").strip()
    if candidate in known_ids:
        return candidate

    title = str(citation.get("recording_title") or "").strip()
    by_title = [
        r["recording_id"] for r in recordings if str(r.get("title") or "").strip() == title
    ]
    if len(by_title) == 1:
        return by_title[0]

    if len(recordings) == 1:
        return recordings[0].get("recording_id")
    return None


def _normalize_citations(raw_citations, recordings: list[dict]) -> list[dict]:
    citations = []
    for citation in raw_citations or []:
        if not isinstance(citation, dict):
            continue
        timestamp = str(citation.get("timestamp") or "")
        citations.append(
            {
                "recording_id": _resolve_recording_id(citation, recordings),
                "recording_title": str(citation.get("recording_title") or ""),
                "timestamp": timestamp,
                "start_seconds": parse_timestamp_seconds(timestamp),
                "quote": str(citation.get("quote") or ""),
            }
        )
    return citations


def _format_recording(recording: dict) -> str:
    title = recording.get("title") or "ללא כותרת"
    date = recording.get("date") or ""
    recording_id = recording.get("recording_id") or ""
    lines = [f"=== הקלטה: {title} ({date}) | מזהה: {recording_id} ==="]
    for seg in recording.get("transcript") or []:
        ts = _format_seconds(seg.get("start_seconds", 0))
        # אותו סימון "(?)" שבתמלול עצמו: קטע שהאימות האקוסטי לא הצליח לשייך
        # (ראה pipeline/diarization.py). בלעדיו הצ'אט עונה "דנה אמרה ש..."
        # באותו ביטחון גם על שורה שהיא מלכתחילה ניחוש.
        label = display_label(
            seg.get("speaker_label", ""), seg.get("speaker_confident", True)
        )
        lines.append(f"[{ts}] {label}: {seg.get('text', '')}")

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
            # מפורש, מאותה סיבה שבסיכום ובתמלול (ראה _model.py): בלי זה
            # תשובה ארוכה עם הרבה ציטוטים חוזרת כ-JSON קטוע ונופלת על
            # json.loads, במקום להחזיר תשובה שלמה.
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
    )

    result = json.loads(response.text)
    return {
        "answer": str(result.get("answer") or ""),
        "citations": _normalize_citations(result.get("citations"), recordings),
    }
