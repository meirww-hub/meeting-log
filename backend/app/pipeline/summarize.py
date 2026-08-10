"""סיכום איכותי + חילוץ TO DO מתוך התמלול, באמצעות Gemini API (Google AI Studio)."""

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import settings
from app.models import TodoItem, TranscriptSegment
from app.pipeline._retry import call_with_retry

from app.pipeline._model import GEMINI_MODEL as _MODEL


class _SpeakerName(BaseModel):
    label: str
    name: str


class _MeetingSummary(BaseModel):
    title: str
    summary: str
    todos: list[TodoItem] = []
    speaker_names: list[_SpeakerName] = []


_SYSTEM_PROMPT = """\
אתה עוזר שמסכם פגישות ומחלץ מהן משימות לביצוע. תענה תמיד בעברית תקנית,
תמציתית וברורה. פלט המשימות חייב להיות JSON תקני בלבד, ללא טקסט נוסף.
"""

_TASK_SCHEMA_HINT = """\
החזר אובייקט JSON יחיד במבנה הבא בדיוק:
{{
  "title": "כותרת קצרה (2-5 מילים) שמתארת את נושא הפגישה",
  "summary": "סיכום הפגישה מחולק לנושאים ממוספרים, למשל:\\n1. <כותרת נושא קצרה>: <תיאור קצר>\\n2. <כותרת נושא קצרה>: <תיאור קצר>\\nוכן הלאה - כל נושא בשורה נפרדת",
  "todos": [
    {{"description": "...", "owner": "שם או null", "due_date": "YYYY-MM-DD או null"}}
  ],
  "speaker_names": [
    {{"label": "התווית כפי שמופיעה בתמלול (למשל 'דובר 1')", "name": "השם האמיתי שלו"}}
  ]
}}
אם לא ניתן לזהות תאריך יעד או אחראי למשימה מסוימת - החזר null בשדה המתאים.
היום הוא {today}.

לגבי summary: חובה לחלק את הסיכום לנושאים ממוספרים (1, 2, 3 וכו'), כשכל
נושא הוא שורה או שתיים שמתחילות במספר הנושא ואחריו נקודה (למשל "1. ...").
אל תכתבו סיכום כפסקאות רציפות ללא מספור.

לגבי speaker_names: כללו דובר ברשימה רק אם שמו האמיתי הוזכר בבירור בתוך
השיחה עצמה (הוא הציג את עצמו, או שדובר אחר פנה אליו בשמו באופן חד-משמעי) -
אל תנחשו ואל תכלילו דובר שאתם לא בטוחים לגביו. לעולם אל תכלילו ברשימה דובר
שכבר מתויג "אני". אם אף דובר לא זוהה בוודאות - החזירו רשימה ריקה [].
"""


def _format_transcript(segments: list[TranscriptSegment]) -> str:
    return "\n".join(f"{s.speaker_label}: {s.text}" for s in segments)


def summarize_and_extract_todos(
    segments: list[TranscriptSegment], today_iso: str
) -> tuple[str, str, list[TodoItem], dict[str, str]]:
    """מחזיר (כותרת מוצעת, סיכום, TO DO, מיפוי תוויות-דובר->שם אמיתי). הכותרת
    המוצעת משמשת רק כשהמשתמש לא הזין כותרת משלו (ראה pipeline.py). מיפוי
    השמות משמש להחלפת תוויות גנריות ("דובר 1"/"הצד השני") בשם האמיתי שהוזכר
    בתוך השיחה עצמה - ראה pipeline._apply_speaker_names."""
    client = genai.Client(api_key=settings.gemini_api_key)

    transcript_text = _format_transcript(segments)

    response = call_with_retry(
        client.models.generate_content,
        model=_MODEL,
        contents=(
            f"{_TASK_SCHEMA_HINT.format(today=today_iso)}\n\n"
            f"תמלול הפגישה:\n{transcript_text}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=_MeetingSummary,
        ),
    )

    # result.parsed הוא כבר מופע _MeetingSummary מאומת (Pydantic), לא dict/JSON
    # גולמי - כך שאי-התאמה בין הסכמה (list) לקוד שקורא אותה (שהניח dict,
    # וקרס עם "'list' object has no attribute 'items'" על כל הקלטה עם שם
    # דובר אמיתי) הופכת לבלתי-אפשרית structurally במקום להתגלות רק בזמן ריצה.
    parsed: _MeetingSummary = response.parsed or _MeetingSummary.model_validate_json(
        response.text
    )

    speaker_names = {
        sn.label: sn.name.strip()
        for sn in parsed.speaker_names
        if sn.label != "אני" and sn.name.strip()
    }

    return parsed.title, parsed.summary, parsed.todos, speaker_names
