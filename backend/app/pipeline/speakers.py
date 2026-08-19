"""כללי הטיפול בתוויות דוברים - סדר ההצגה והחלפת שם.

שני הכללים כאן משותפים למסלול העיבוד (pipeline.py) ולמסלול העריכה הידנית
(edit.py), ומוכרחים להיות זהים בשניהם: המשתמש מזין את השמות במסך "עריכת
דוברים" **לפי סדר הופעתם בתמלול**, ולכן הסדר שהשרת מחזיר הוא חוזה ולא
פרט עיצובי.
"""

import re
from collections.abc import Iterable


def speakers_in_order(labels: Iterable[str]) -> list[str]:
    """תוויות הדוברים לפי סדר הופעתן הראשונה, בלי כפילויות.

    **לא למיין את זה.** עד 2026-08-11 הרשימה נבנתה כ-sorted(set(...)), ואז
    מסך "עריכת דוברים" הציג את השדות בסדר א"ב: בשיחת טלפון "אני" קדם לשם
    איש הקשר גם כשהצד השני פתח את השיחה, ובפגישה עם דובר בעל שם אמיתי
    שזוהה מתוך השיחה הסדר התהפך לגמרי. המשתמש ממלא את השדות לפי מי שדיבר
    ראשון, אז מיון א"ב שם את השם על הדובר הלא נכון.
    """
    return list(dict.fromkeys(label for label in labels if label))


# מה שמסמן בתמלול קטע שהאימות האקוסטי לא הצליח לשייך בוודאות (ראה
# diarization.py). נספח לתווית בזמן **הצגה** בלבד ולא נכנס ל-speaker_label
# עצמו: אחרת הוא היה נספר כדובר נוסף ב-speakers_in_order, מופיע כשורה
# מיותרת במסך "עריכת דוברים", ומפספס את החלפת השם ב-replace_labels.
UNCERTAIN_MARK = " (?)"


def display_label(label: str, confident: bool) -> str:
    """התווית כפי שהיא נכתבת בתמלול שהמשתמש קורא.

    המשתמש ביקש במפורש שכשלא ברור מי דיבר - זה יישאר עמום ולא ינוחש. בסיכום
    זה מנוסח כ"אחד הדוברים" (ראה summarize.UNKNOWN_SPEAKER); בתמלול, שבו
    התווית עצמה היא המידע, הסימון נספח אליה.
    """
    return label if confident else f"{label}{UNCERTAIN_MARK}"


def replace_labels(text: str, renames: dict[str, str]) -> str:
    """מחליף תוויות דוברים בתוך טקסט חופשי (סיכום, תיאור משימה), בסריקה אחת.

    סריקה אחת ולא רצף של str.replace, בגלל שתי מלכודות:
      • תווית שהיא תחילית של אחרת: "דובר 1" מול "דובר 10". החלפה נאיבית
        לפי סדר המילון הייתה הופכת "דובר 10" ל-"מאיר0". המיון לפי אורך
        יורד מבטיח שהתווית הארוכה נתפסת ראשונה.
      • שרשור: החלפה שמייצרת טקסט שהחלפה מאוחרת יותר באותו לולאה תתפוס
        שוב (שינוי שם של דובר אחד לשם שהיה קודם של דובר אחר).
    """
    labels = [label for label in renames if label]
    if not text or not labels:
        return text
    pattern = re.compile(
        "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    )
    return pattern.sub(lambda m: renames[m.group(0)], text)
