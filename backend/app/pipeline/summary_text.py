"""ניקוי מבנה הסיכום שחוזר מהמודל, לפני שמירה ולפני רינדור ל-Drive."""

import re


def normalize_summary_topics(text: str) -> str:
    """מבטיח שכל נושא ממוספר ("1. ...") יושב בשורה משלו.

    הפרומפט מבקש נושא בשורה, אבל המודל לפעמים מחזיר את כל הסיכום בשורה אחת
    ("1. ... 2. ... 3. ...") או עם "\n" כטקסט גולמי. בלי הניקוי, המסמך ב-Drive
    יוצא גוש אחד: בלי מספור בתחילת שורה ובלי ריווח בין הנושאים.

    הפיצול נעשה רק לפני מספר שהוא הבא בתור (2 אחרי 1, 3 אחרי 2...) ואחריו
    רווח, כך שמספרים רגילים בתוך משפט ("ב-30 ימים", "3.5 מיליון") לא נחתכים.
    """
    # "\\n" כאן הוא הרצף הגולמי של שני תווים (לוכסן + n), לא מעבר שורה.
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")

    expected = 2
    search_from = 0
    while True:
        marker = re.compile(rf"(?<=\S)[ \t]+(?={expected}[.)]\s)")
        match = marker.search(text, search_from)
        if match is None:
            break
        text = text[: match.start()] + "\n" + text[match.end() :]
        search_from = match.start() + 1
        expected += 1
    return text
