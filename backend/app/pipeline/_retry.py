"""ריטריי גנרי לקריאות Gemini - העומס משתנה (503 UNAVAILABLE) הוא תופעה
נפוצה וזמנית ב-API, ולא סימן לבעיה אמיתית. בלי זה, כל 503 מפיל את כל
עיבוד ההקלטה בלי סיכוי שני.

אותו טיפול ניתן גם לחריגת מכסה (429 RESOURCE_EXHAUSTED), עם המתנה ארוכה
יותר: המכסה החינמית נמדדת בבקשות לדקה, ושיחת טלפון נשלחת כשני ערוצים -
כלומר שתי קריאות תמלול צמודות - ולכן קל להיתקל בה בשיא זמני שחולף מעצמו.
"""

import time

from google.genai import errors

# עומס (503) יכול להימשך דקות: ב-2026-11-24 שתי הקלטות נפלו כי 4 ניסיונות
# בהמתנה של 5-20 שניות (כחצי דקה בסך הכל) נגמרו לפני שהעומס חלף. 6 ניסיונות
# עם 10, 20, 40, 80, 160 שניות מכסים כ-5 דקות.
_MAX_ATTEMPTS = 6
_RATE_LIMIT_MAX_ATTEMPTS = 4
_BASE_DELAY_SECONDS = 10
# חריגת מכסה נמדדת בחלון של דקה, ולכן אין טעם לנסות שוב אחרי שניות בודדות.
_RATE_LIMIT_BASE_DELAY_SECONDS = 30


def _is_rate_limit(error: Exception) -> bool:
    return isinstance(error, errors.ClientError) and getattr(error, "code", None) == 429


def call_with_retry(fn, *args, **kwargs):
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except (errors.ServerError, errors.ClientError) as e:
            if isinstance(e, errors.ClientError) and not _is_rate_limit(e):
                # שגיאת לקוח אמיתית (מפתח שגוי, קלט לא תקין) - ניסיון חוזר
                # רק יחזור על אותה תקלה.
                raise
            last_error = e
            limit = _RATE_LIMIT_MAX_ATTEMPTS if _is_rate_limit(e) else _MAX_ATTEMPTS
            if attempt >= limit - 1:
                break
            if attempt < _MAX_ATTEMPTS - 1:
                base = (
                    _RATE_LIMIT_BASE_DELAY_SECONDS
                    if _is_rate_limit(e)
                    else _BASE_DELAY_SECONDS
                )
                time.sleep(base * (2**attempt))
    raise last_error
