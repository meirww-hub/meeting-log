"""תמלול + הפרדת דוברים גנרית באמצעות Gemini API (הבנת אודיו מולטימודלית).

בשלב 1 אנו נשענים על יכולת ה-diarization המובנית של Gemini (מחזיר speaker_tag
מספרי לכל קטע). בשלב 2 נשלים זאת עם pyannote.audio כדי לקבל גם
speaker embeddings להתאמה מול פרופילים שמורים (ראו pipeline/speaker_id.py).
"""

import json
import mimetypes

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import settings
from app.models import TranscriptSegment
from app.pipeline._retry import call_with_retry

from app.pipeline._model import GEMINI_MODEL as _MODEL


class _DiarizedSegment(BaseModel):
    speaker_tag: int
    text: str
    start_seconds: float
    end_seconds: float


class _SingleChannelSegment(BaseModel):
    text: str
    start_seconds: float
    end_seconds: float

_SYSTEM_PROMPT = """\
אתה מתמלל פגישות מדויק. תמלל את קובץ האודיו המצורף במדויק, תוך זיהוי מעברים
בין דוברים שונים לפי הקול (diarization). פלט חייב להיות JSON תקני בלבד,
ללא טקסט נוסף.
"""

_SCHEMA_HINT = """\
החזר מערך JSON של קטעי דיבור, לפי סדר כרונולוגי, במבנה הבא בדיוק:
[
  {{"speaker_tag": 1, "text": "...", "start_seconds": 0.0, "end_seconds": 3.2}}
]
speaker_tag הוא מספר עוקב לכל דובר (1, 2, 3...) - אותו דובר תמיד אותו מספר
לאורך כל ההקלטה. תמלל בשפה {language}.
"""


def _mime_type_for(audio_path: str) -> str:
    guessed, _ = mimetypes.guess_type(audio_path)
    return guessed or "audio/mp4"


def transcribe_with_diarization(
    audio_path: str, max_speakers: int = 6
) -> list[TranscriptSegment]:
    client = genai.Client(api_key=settings.gemini_api_key)

    uploaded_file = client.files.upload(
        file=audio_path,
        config=types.UploadFileConfig(mime_type=_mime_type_for(audio_path)),
    )

    response = call_with_retry(
        client.models.generate_content,
        model=_MODEL,
        contents=[
            uploaded_file,
            _SCHEMA_HINT.format(language=settings.transcription_language),
        ],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=list[_DiarizedSegment],
        ),
    )

    items = json.loads(response.text)

    return [
        TranscriptSegment(
            speaker_label=f"דובר {item['speaker_tag']}",
            speaker_tag=item["speaker_tag"],
            text=item["text"],
            start_seconds=item["start_seconds"],
            end_seconds=item["end_seconds"],
        )
        for item in items
    ]


_SINGLE_CHANNEL_SYSTEM_PROMPT = """\
אתה מתמלל שיחות טלפון מדויק. בקובץ שלפניך נשמע דובר אחד בלבד - זהו ערוץ
מבודד של צד אחד בשיחה. תמלל רק את הדיבור שנשמע בו, והתעלם מרעשי רקע או
מדליפה חלשה של הצד השני. פלט חייב להיות JSON תקני בלבד, ללא טקסט נוסף.
"""

_SINGLE_CHANNEL_SCHEMA_HINT = """\
החזר מערך JSON של קטעי דיבור, לפי סדר כרונולוגי, במבנה הבא בדיוק:
[
  {{"text": "...", "start_seconds": 0.0, "end_seconds": 3.2}}
]
אל תכלול שדה דובר - כל הקטעים שייכים לאותו דובר יחיד. תמלל בשפה {language}.
"""


def transcribe_single_channel(
    audio_path: str, speaker_label: str, speaker_tag: int
) -> list[TranscriptSegment]:
    """תמלול ערוץ מבודד של דובר יחיד (צד אחד בשיחת טלפון).

    כשההקלטה מגיעה משני ערוצים נפרדים, ההפרדה בין הדוברים כבר קיימת ברמת
    הקובץ - ולכן אין צורך ב-diarization, וזיהוי הדובר יוצא ודאי במקום
    ניחוש לפי מאפייני קול (ראה transcribe_with_diarization לעומת זאת).
    """
    client = genai.Client(api_key=settings.gemini_api_key)

    uploaded_file = client.files.upload(
        file=audio_path,
        config=types.UploadFileConfig(mime_type=_mime_type_for(audio_path)),
    )

    response = call_with_retry(
        client.models.generate_content,
        model=_MODEL,
        contents=[
            uploaded_file,
            _SINGLE_CHANNEL_SCHEMA_HINT.format(language=settings.transcription_language),
        ],
        config=types.GenerateContentConfig(
            system_instruction=_SINGLE_CHANNEL_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=list[_SingleChannelSegment],
        ),
    )

    items = json.loads(response.text)

    return [
        TranscriptSegment(
            speaker_label=speaker_label,
            speaker_tag=speaker_tag,
            text=item["text"],
            start_seconds=item["start_seconds"],
            end_seconds=item["end_seconds"],
        )
        for item in items
        if str(item.get("text", "")).strip()
    ]
