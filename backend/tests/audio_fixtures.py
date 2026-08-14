"""ייצור אודיו סינתטי לבדיקות התמלול, בלי להקליט כלום בטלפון.

משתמש ב-Google Cloud TTS (`texttospeech.googleapis.com`, מופעל על הפרויקט
meeting-log-504809) עם ה-Service Account שכבר קיים לבדיקות ה-Firestore. נבחר
על פני קולות Windows כי שם אין קול עברי בכלל - רק en-US - ובלי עברית אי אפשר
לבדוק את המקרה המעניין: שיחה שמשלבת עברית ואנגלית.

הקטעים מוחזרים כ-LINEAR16 ומחוברים לקובץ WAV אחד עם חצי שנייה שקט ביניהם,
כדי שה-diarization יקבל מעבר ברור בין הדוברים.
"""

import base64
import io
import pathlib
import wave

_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_SAMPLE_RATE = 24000
_SERVICE_ACCOUNT = pathlib.Path(__file__).resolve().parent.parent / "service-account.json"


def credentials_available() -> bool:
    return _SERVICE_ACCOUNT.is_file()


def _token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        str(_SERVICE_ACCOUNT), scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())
    return creds.token


def synthesize(lines: list[tuple[str, str]], out_path: str) -> str:
    """lines = [(שם קול, טקסט)]. מחזיר את הנתיב לקובץ ה-WAV שנוצר."""
    import requests

    headers = {"Authorization": f"Bearer {_token()}"}
    frames: list[bytes] = []
    params = None

    for voice, text in lines:
        language_code = "-".join(voice.split("-")[:2])
        response = requests.post(
            _TTS_URL,
            headers=headers,
            json={
                "input": {"text": text},
                "voice": {"languageCode": language_code, "name": voice},
                "audioConfig": {
                    "audioEncoding": "LINEAR16",
                    "sampleRateHertz": _SAMPLE_RATE,
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        chunk = base64.b64decode(response.json()["audioContent"])
        with wave.open(io.BytesIO(chunk)) as source:
            params = source.getparams()
            frames.append(source.readframes(source.getnframes()))
        frames.append(b"\x00" * _SAMPLE_RATE)  # חצי שנייה שקט (16 ביט)

    with wave.open(out_path, "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        for frame in frames:
            out.writeframes(frame)

    return out_path


# --- תסריטי הבדיקה ------------------------------------------------------

# פגישה באנגלית בלבד: התמלול חייב להישאר אנגלי במלואו.
ENGLISH_MEETING = [
    ("en-US-Standard-D", "Good morning Sarah, let's start the budget review for the Northside project."),
    ("en-US-Standard-F", "Sure David. The current quote from the contractor is forty seven thousand two hundred dollars."),
    ("en-US-Standard-D", "That is nine percent over what we approved in March. Can we bring it down?"),
    ("en-US-Standard-F", "I can renegotiate the plumbing line item, it is eight thousand three hundred and fifty dollars."),
    ("en-US-Standard-D", "Good. Please send me the revised quote by Friday, August fourteenth."),
]

# עברית עם אנגלית משובצת בתוך המשפט - המקרה שהכי קל להיכשל בו: המילים
# האנגליות חייבות להישאר באותיות לטיניות, לא מתועתקות ולא מתורגמות.
MIXED_MEETING = [
    ("he-IL-Standard-B", "בוקר טוב, בוא נתחיל עם הסטטוס של הפרויקט."),
    ("he-IL-Standard-A", "ה-deployment ל-production הסתיים אתמול בלילה, בלי downtime."),
    ("he-IL-Standard-B", "מצוין. כמה זה עלה בסוף?"),
    ("he-IL-Standard-A", "בסך הכל שנים עשר אלף ושלוש מאות שקל, זה תשעה אחוז מתחת לתכנון."),
    ("en-US-Standard-D", "Let me add one thing in English. The client asked for a follow up meeting next Tuesday."),
    ("he-IL-Standard-B", "בסדר, אני אשלח להם invite. תעדכן את ה-roadmap עד יום חמישי."),
]

# ערוץ מבודד של דובר אחד - מסלול שיחת הטלפון, שלא עובר diarization.
MIXED_CALL_CHANNEL = [
    (
        "he-IL-Standard-B",
        "היי, אני מתקשר לגבי ה-invoice. שלחתי לך את ה-purchase order במייל, "
        "הסכום הוא שלושת אלפים מאה ועשרים שקל. Please confirm by Friday, okay?",
    ),
]
