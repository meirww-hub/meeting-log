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
from app.pipeline import speaker_embedding
from app.pipeline._retry import call_with_retry

from app.pipeline._model import GEMINI_MAX_OUTPUT_TOKENS as _MAX_OUTPUT_TOKENS
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

# כלל השפה משותף לשני מסלולי התמלול (פגישה עם diarization / ערוץ שיחה מבודד),
# כי הוא אותה דרישה בדיוק ואסור שהם ייפרדו בטעות.
#
# **לא להחזיר הוראה שמכתיבה שפה קבועה** ("תמלל בשפה he-IL"). כך זה היה עד
# 2026-08-11, וזה עבד רק במקרה: בבדיקה חיה על אודיו באנגלית המודל התעלם
# מההוראה ותמלל באנגלית - אבל היינו מבקשים ממנו במפורש לתרגם, וגרסת מודל
# אחרת הייתה יכולה לציית ולהחזיר פגישה באנגלית מתורגמת לעברית. התמלול הוא
# מסמך המקור של השיחה; תרגום בשלב הזה מאבד מידע שאי אפשר לשחזר.
_LANGUAGE_RULE = """\
תמלל כל קטע בשפה שבה הוא נאמר בפועל, מילה במילה, ולעולם אל תתרגם. אם
בהקלטה מדברים ביותר משפה אחת - התמלול יהיה משולב באותו אופן: מה שנאמר
בעברית ייכתב בעברית ומה שנאמר באנגלית ייכתב באנגלית ובאותיות לטיניות.

זה נכון גם בתוך משפט אחד: משפט בעברית שמשובצות בו מילים באנגלית נכתב
בדיוק כך - המילים האנגליות באותיות לטיניות, גם כשהן נאמרות במבטא ישראלי
וגם כשהן מילה בודדת באמצע משפט עברי. אסור לתעתק מילה אנגלית לאותיות
עבריות ואסור לתרגם אותה. לדוגמה:
  נכון:  "שלחתי לך את ה-invoice, תעדכן את ה-roadmap עד מחר"
  שגוי:  "שלחתי לך את האינבויס, תעדכן את המפת דרכים עד מחר"
"""

_SCHEMA_HINT = """\
החזר מערך JSON של קטעי דיבור, לפי סדר כרונולוגי, במבנה הבא בדיוק:
[
  {{"speaker_tag": 1, "text": "...", "start_seconds": 0.0, "end_seconds": 3.2}}
]
speaker_tag הוא מספר עוקב לכל דובר (1, 2, 3...) - אותו דובר תמיד אותו מספר
לאורך כל ההקלטה.

{language_rule}"""


# כמה בקשות המשך מותר לשרשר כשהתמלול עדיין נקטע. שיחה של שעה מסתכמת בכ-30
# אלף טוקנים, כלומר סבב אחד מספיק לה בנוחות; המכסה כאן נועדה להקלטות חריגות
# ממש (כמה שעות), ובעיקר לחסום לולאה אינסופית אם המודל מפסיק להתקדם.
_MAX_CONTINUATIONS = 8

# חפיפה מותרת בין סבב לסבב. המודל לא חוזר לשנייה מדויקת, ובלי הסובלנות הזו
# משפט שנחתך בדיוק על הגבול היה נופל בין הכיסאות.
_RESUME_TOLERANCE_SECONDS = 1.0

_RESUME_RULE = """\

חשוב: כבר תומללו {done_seconds:.0f} השניות הראשונות של ההקלטה. תמלל **רק את
ההמשך**, החל משנייה {done_seconds:.0f} ועד סוף ההקלטה, ואל תחזור על מה שכבר
תומלל. start_seconds ו-end_seconds נמדדים כרגיל מתחילת ההקלטה כולה (כלומר
הקטע הראשון שתחזיר יתחיל בסביבות שנייה {done_seconds:.0f}).
"""

_RESUME_SPEAKER_RULE = """\
שמור על אותו מספור דוברים כמו קודם - אלה אותם אנשים. אלה הקטעים האחרונים
שתומללו, כדי שתדע איזה מספר שייך למי:
{tail}
"""


class IncompleteTranscriptError(RuntimeError):
    """התמלול נקטע ולא הצליח להשלים את ההקלטה עד סופה.

    נזרק במקום להחזיר תמלול חלקי: הקלטה שנשמרת כ"done" עם חצי מהתוכן היא
    אובדן שקט שאיש לא ישים לב אליו, בעוד כישלון גלוי מופיע בהיסטוריה ונכנס
    לניסיון חוזר אוטומטי.
    """


class HallucinatedTranscriptError(RuntimeError):
    """התמלול מכיל טקסט, אבל קטעי הדיבור הארוכים ביותר בו שקטים בפועל
    באודיו - סימן שהמודל "המציא" שיחה סבירה במקום לדווח שאין מה לתמלל
    (למשל מיקרופון שלא תפס כלום, או קובץ פגום).

    נמצא ב-2026-08-17: הקלטה אמיתית (לא בדיקה) נשמרה כ"done" עם תמלול
    ודוברים פיקטיביים - כולל פרופיל דובר שהצביע לרגע שקט לגמרי באודיו,
    ולכן לא השמיע כלום במסך "דוברים לא מזוהים". בלי הבדיקה הזו אין שום
    סימן שהתמלול לא באמת קרה. כישלון גלוי כאן מגיע להיסטוריה ולניסיון
    חוזר, בדיוק כמו IncompleteTranscriptError.
    """


# כמה מהקטעים הארוכים ביותר בודקים בפועל מול האודיו - תקרה, לא רצפה
# (כמו _MAX_SAMPLE_SEGMENTS ב-speaker_id.py): מספיק כדי לא להיתפס על קטע
# בודד שנחתך בטעות על רעש, בלי לפענח את כל ההקלטה בשביל הבדיקה.
_MAX_SILENCE_CHECK_SEGMENTS = 3

# קטע קצר מזה לא נבדק: RMS על פחות משנייה וחצי רועש מדי כדי להבחין בין
# שקט לדיבור קצר, ואין טעם לתפוס עליו הקלטה אמיתית.
_MIN_SILENCE_CHECK_SECONDS = 1.5


def _verify_segments_are_audible(
    segments: list[TranscriptSegment], audio_path: str
) -> None:
    """זורק HallucinatedTranscriptError אם כל הקטעים הארוכים שנבדקו שקטים
    בפועל באודיו. דורש שכולם יהיו שקטים (לא רוב) - מספיק קטע ארוך אחד עם
    אנרגיית שמע אמיתית כדי לבטוח בשאר התמלול; זה שומר את קצב הפספוסים
    השווא נמוך, גם על הקלטה אמיתית שקטה בחלקה."""
    ranked = sorted(
        (s for s in segments if s.end_seconds - s.start_seconds >= _MIN_SILENCE_CHECK_SECONDS),
        key=lambda s: s.end_seconds - s.start_seconds,
        reverse=True,
    )[:_MAX_SILENCE_CHECK_SEGMENTS]
    if not ranked:
        return
    if all(
        speaker_embedding.segment_is_silent(audio_path, s.start_seconds, s.end_seconds)
        for s in ranked
    ):
        raise HallucinatedTranscriptError(
            f"{len(ranked)} מהקטעים הארוכים ביותר בתמלול שקטים בפועל באודיו - "
            "כנראה תמלול מדומיין על הקלטה שקטה/פגומה"
        )


def _mime_type_for(audio_path: str) -> str:
    guessed, _ = mimetypes.guess_type(audio_path)
    return guessed or "audio/mp4"


def _hit_output_ceiling(response) -> bool:
    """true כשהמודל הפסיק לכתוב כי נגמר לו תקציב הפלט, ולא כי סיים."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return False
    return "MAX_TOKENS" in str(getattr(candidates[0], "finish_reason", ""))


def _decode_items(text: str) -> tuple[list[dict], bool]:
    """מפרק את תשובת המודל למערך פריטים, ומחזיר גם אם היא הגיעה קטועה.

    `json.loads` על תשובה שנקטעה באמצע זורק, ואיתו יורדים לטמיון גם מאות
    הקטעים **השלמים** שכן הספיקו להגיע - כך אבדה שיחה שלמה בגלל שהקטע
    האחרון בה נחתך באמצע מילה. לכן כישלון פירוק לא מסיים כאן את העבודה אלא
    נסוג אחורה אל הסוגר המסולסל האחרון שסוגר פריט שלם, ומחזיר את הרישא
    התקינה - הזנב החסר מושלם אחר כך בבקשת המשך.
    """
    if not text.strip():
        return [], True
    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        pass

    # נסיגה אחורה על פני סוגרי פריטים, מהאחרון לראשון: הסוגר האחרון הוא
    # בדרך כלל הנכון, אבל '}' יכול להופיע גם בתוך טקסט מדובר.
    for cut in range(len(text) - 1, -1, -1):
        if text[cut] != "}":
            continue
        try:
            items = json.loads(f"{text[: cut + 1]}]")
        except json.JSONDecodeError:
            continue
        if isinstance(items, list):
            return items, True
    return [], True


def _segment_end(item: dict) -> float:
    try:
        return float(item.get("end_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _segment_start(item: dict) -> float:
    try:
        return float(item.get("start_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _transcribe_in_full(
    *,
    audio_path: str,
    system_prompt: str,
    schema_hint: str,
    response_schema,
    speaker_tail: bool,
    client: genai.Client | None = None,
) -> list[dict]:
    """מתמלל קובץ שלם, גם כשהתשובה לא נכנסת לבקשה אחת.

    כל עוד המודל נקטע באמצע, נשלחת בקשת המשך שמתחילה מהשנייה שאליה הגיע
    התמלול עד כה - במקום להיכשל ולאבד את ההקלטה, או להחזיר אותה חתוכה בשקט.
    הקובץ מועלה פעם אחת בלבד ומשמש את כל הסבבים.

    [client] מאפשר לקרוא לפונקציה הזו כמה פעמים על אותו לקוח קיים במקום
    ליצור חדש בכל קריאה - ראה transcribe_single_channel: שיחת טלפון קוראת
    לה פעמיים ברצף (ערוץ שלי, ערוץ שני), ושני client-ים חיים בו-זמנית בתוך
    אותה בקשה (ל-genai.Client אין close() ציבורי, אז ה-httpx client שמתחתיו
    לא משתחרר עד שה-GC מגיע אליו) הוא בדיוק מה שחרג ממכסת הזיכרון של
    Cloud Run והפיל הקלטה שלמה ב-2026-08-16 (מכסה 512MiB, שימוש בפועל
    671MiB, 47 שניות אחרי תחילת התמלול).
    """
    client = client or genai.Client(api_key=settings.gemini_api_key)
    uploaded_file = client.files.upload(
        file=audio_path,
        config=types.UploadFileConfig(mime_type=_mime_type_for(audio_path)),
    )

    collected: list[dict] = []
    covered_seconds = 0.0
    complete = False

    for round_index in range(_MAX_CONTINUATIONS):
        prompt = schema_hint
        if round_index > 0:
            prompt += _RESUME_RULE.format(done_seconds=covered_seconds)
            if speaker_tail:
                tail = "\n".join(
                    f"דובר {item.get('speaker_tag')}: {item.get('text', '')}"
                    for item in collected[-5:]
                )
                prompt += _RESUME_SPEAKER_RULE.format(tail=tail)

        response = call_with_retry(
            client.models.generate_content,
            model=_MODEL,
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=response_schema,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
        )

        items, salvaged = _decode_items(response.text or "")
        truncated = salvaged or _hit_output_ceiling(response)

        # בסבב המשך המודל עלול לפתוח מעט לפני הנקודה שביקשנו; הקטעים
        # שכבר בידינו נשמטים כדי שהתמלול לא יכפיל משפטים.
        floor = covered_seconds - _RESUME_TOLERANCE_SECONDS if round_index else -1.0
        fresh = [item for item in items if _segment_start(item) > floor]

        if fresh:
            collected.extend(fresh)
            covered_seconds = max(covered_seconds, max(_segment_end(i) for i in fresh))
        else:
            # אף קטע חדש. או שהמודל התעלם מבקשת ההמשך והתחיל לתמלל מחדש
            # מההתחלה - ואז התשובה הזו היא ניסיון שלם יותר, ומחליפה את מה
            # שבידינו - או שהוא נתקע ומחזיר את אותו זנב, ואז אין טעם בעוד סבב.
            restart_coverage = max((_segment_end(i) for i in items), default=0.0)
            if restart_coverage <= covered_seconds:
                break
            collected = list(items)
            covered_seconds = restart_coverage

        if not truncated:
            complete = True
            break

    if not complete:
        # תמלול חלקי **אסור** שייראה כהצלחה: הקלטה שנשמרה כ"done" עם חצי
        # תוכן היא בדיוק סוג האובדן השקט שהמנגנון הזה נועד למנוע, ורק כאן
        # ידוע שזה מה שקרה. כישלון גלוי מגיע להיסטוריה ולניסיון חוזר
        # (ראה main.py ו-_run_recording_pipeline).
        raise IncompleteTranscriptError(
            f"התמלול נקטע ולא הושלם גם אחרי {_MAX_CONTINUATIONS} בקשות המשך - "
            f"הושלמו {covered_seconds / 60:.1f} דקות ב-{len(collected)} קטעים"
        )

    return collected


def transcribe_with_diarization(
    audio_path: str, max_speakers: int = 6, client: genai.Client | None = None
) -> list[TranscriptSegment]:
    items = _transcribe_in_full(
        audio_path=audio_path,
        system_prompt=_SYSTEM_PROMPT,
        schema_hint=_SCHEMA_HINT.format(language_rule=_LANGUAGE_RULE),
        response_schema=list[_DiarizedSegment],
        speaker_tail=True,
        client=client,
    )

    segments = [
        TranscriptSegment(
            speaker_label=f"דובר {item['speaker_tag']}",
            speaker_tag=item["speaker_tag"],
            text=item["text"],
            start_seconds=item["start_seconds"],
            end_seconds=item["end_seconds"],
        )
        for item in items
        if str(item.get("text", "")).strip()
    ]
    _verify_segments_are_audible(segments, audio_path)
    return segments


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
אל תכלול שדה דובר - כל הקטעים שייכים לאותו דובר יחיד.

{language_rule}"""


def transcribe_single_channel(
    audio_path: str,
    speaker_label: str,
    speaker_tag: int,
    client: genai.Client | None = None,
) -> list[TranscriptSegment]:
    """תמלול ערוץ מבודד של דובר יחיד (צד אחד בשיחת טלפון).

    כשההקלטה מגיעה משני ערוצים נפרדים, ההפרדה בין הדוברים כבר קיימת ברמת
    הקובץ - ולכן אין צורך ב-diarization, וזיהוי הדובר יוצא ודאי במקום
    ניחוש לפי מאפייני קול (ראה transcribe_with_diarization לעומת זאת).

    [client] - ראה _transcribe_in_full: שיחת טלפון קוראת לפונקציה הזו
    פעמיים ברצף (ערוץ שלי, ערוץ שני), ו-process_call_recording מעביר לשתי
    הקריאות את אותו לקוח כדי שרק חיבור HTTP אחד יהיה חי בו-זמנית.
    """
    items = _transcribe_in_full(
        audio_path=audio_path,
        system_prompt=_SINGLE_CHANNEL_SYSTEM_PROMPT,
        schema_hint=_SINGLE_CHANNEL_SCHEMA_HINT.format(language_rule=_LANGUAGE_RULE),
        response_schema=list[_SingleChannelSegment],
        speaker_tail=False,
        client=client,
    )

    segments = [
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
    _verify_segments_are_audible(segments, audio_path)
    return segments
