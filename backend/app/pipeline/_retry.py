"""ריטריי גנרי לקריאות Gemini - העומס משתנה (503 UNAVAILABLE) הוא תופעה
נפוצה וזמנית ב-API, ולא סימן לבעיה אמיתית. בלי זה, כל 503 מפיל את כל
עיבוד ההקלטה בלי סיכוי שני.

אותו טיפול (וגם אותה המתנה ארוכה) ניתן גם לחריגת מכסה (429
RESOURCE_EXHAUSTED): המכסה החינמית נמדדת בבקשות לדקה, ושיחת טלפון נשלחת
כשני ערוצים - כלומר שתי קריאות תמלול צמודות - ולכן קל להיתקל בה בשיא זמני
שחולף מעצמו.

עד 2026-09 גם 503 קיבל רק את ה-backoff הקצר (35 שניות סה"כ על פני 4
ניסיונות) - מספיק לתנודה רגעית, אבל לא ל"עומס גבוה" אמיתי בצד גוגל, שנצפה
נמשך כמה דקות. הקלטה אמיתית נפלה ל-error על "503 - the model is currently
experiencing high demand" בזמן שהאפליקציה בכלל לא הייתה בשימוש לפני כן,
כלומר לא הייתה שום סיבה שקשורה למכסה של המשתמש - זו הייתה תקלת זמינות
זמנית בצד גוגל, שפשוט נמשכה יותר מ-35 שניות.
"""

import time

from google.genai import errors

_MAX_ATTEMPTS = 4
_BASE_DELAY_SECONDS = 5
# חריגת מכסה נמדדת בחלון של דקה, ו"עומס גבוה" (503) נצפה נמשך כמה דקות -
# בשני המקרים אין טעם לנסות שוב אחרי שניות בודדות.
_LONG_BACKOFF_BASE_DELAY_SECONDS = 30


def _needs_long_backoff(error: Exception) -> bool:
    if isinstance(error, errors.ServerError):
        return True
    return isinstance(error, errors.ClientError) and getattr(error, "code", None) == 429


def call_with_retry(fn, *args, **kwargs):
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except (errors.ServerError, errors.ClientError) as e:
            if isinstance(e, errors.ClientError) and not _needs_long_backoff(e):
                # שגיאת לקוח אמיתית (מפתח שגוי, קלט לא תקין) - ניסיון חוזר
                # רק יחזור על אותה תקלה.
                raise
            last_error = e
            if attempt < _MAX_ATTEMPTS - 1:
                base = (
                    _LONG_BACKOFF_BASE_DELAY_SECONDS
                    if _needs_long_backoff(e)
                    else _BASE_DELAY_SECONDS
                )
                time.sleep(base * (2**attempt))
    raise last_error
