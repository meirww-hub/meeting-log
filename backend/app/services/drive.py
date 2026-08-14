"""יצירת קבצי התוצר של הפגישה ב-Google Drive.

הקבצים מסודרים לפי **סוג** ולא לפי פגישה: בתיקיית השורש (Meeting Log) יש
תיקייה קבועה אחת לכל סוג תוצר - "סיכום", "תמלול", "TO DO", "הקלטות",
"הערות", "קבצים מצורפים" - וכל הפגישות מוזרמות לתוכן. כדי שאפשר יהיה
להבחין בין פגישה לפגישה בתוך תיקייה משותפת, שם הקובץ נושא גם את סוגו וגם
את כותרת הפגישה: "סיכום - ישיבת צוות". ראה _file_name ו-_type_folder_id.

מכיוון שאין יותר תיקייה אחת לכל פגישה, פעולות ברמת הפגישה עובדות על
רשימת הקבצים שלה: שינוי כותרת מחליף את הכותרת בשם כל אחד מהם
(retitle_files) ומחיקה מעבירה את כולם לאשפה (trash_files). הקלטות ישנות
שנשמרו במבנה הקודם (תיקייה לפגישה) עדיין נתמכות דרך rename_folder /
trash_folder - ראה pipeline/edit.py. את הקיימות אפשר להעביר למבנה החדש עם
scripts/migrate_drive_layout.py.

תמלול וסיכום נשמרים כ-Google Docs (מומרים אוטומטית בהעלאה, מ-HTML עם
כיווניות עברית - ראה _text_to_rtl_html). הסיכום מקבל עיצוב עשיר יותר -
כותרות נושא, ריווח והדגשת נתונים - ראה _summary_to_rtl_html. קובץ האודיו
המקורי מועלה כפי שהוא. רשימת המשימות נבנית כ-Google Sheet דרך
Sheets API עצמו (לא CSV) - כדי לאפשר טאב "גאנט" חזותי לצד טאב "משימות",
עם צבע ייחודי לכל משימה שמופיע גם בטבלה וגם כפס בגאנט (ראה
_build_todo_spreadsheet למטה). בגלל זה, בנוסף ל-Drive API, גם ה-Google
Sheets API חייב להיות מופעל בפרויקט ה-GCP (Google Cloud Console > APIs
& Services > Enabled APIs > Enable APIs and Services > חפשו "Google
Sheets API").

האימות מול Drive/Sheets מבוצע כ-OAuth בשם החשבון האישי של המשתמש (לא
Service Account): ל-Service Account בחשבון Gmail אישי (שאינו Google
Workspace) אין מכסת אחסון שמישה ב-Drive, כך שקבצים שהוא יוצר נכשלים על
"storage quota exceeded". ה-refresh_token מופק פעם אחת דרך
scripts/get_drive_oauth_token.py ונשמר ב-.env. (Firestore ממשיך להשתמש
ב-Service Account הרגיל - שם אין בעיית מכסה.) scope ה-OAuth הקיים
("https://www.googleapis.com/auth/drive") מספיק גם ל-Sheets API - אין
צורך בהפקת טוקן חדש.
"""

import datetime
import html
import io
import re
from collections.abc import Iterator

import requests
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import settings
from app.models import MeetingResult, TodoItem, TranscriptSegment

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# הורדת התוכן הבינארי של קובץ (להבדיל מהמטא-דאטה שלו).
_MEDIA_URL = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

# "https://drive.google.com/file/d/<ID>/view",
# "https://docs.google.com/document/d/<ID>/edit?usp=drivesdk"
_FILE_ID_IN_URL = re.compile(r"/d/([a-zA-Z0-9_-]+)")

_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_SLIDES_MIME = "application/vnd.google-apps.presentation"

# קבצי Office שאפשר להמיר ל-PDF דרך Drive, וסוג ה-Google Doc/Sheet/Slides
# שהם עוברים דרכו. ראה convert_to_pdf.
OFFICE_TO_GOOGLE_MIME = {
    # Word ומשפחתו
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _DOC_MIME,
    "application/msword": _DOC_MIME,
    "application/vnd.oasis.opendocument.text": _DOC_MIME,
    "application/rtf": _DOC_MIME,
    "text/rtf": _DOC_MIME,
    # Excel
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _SHEET_MIME,
    "application/vnd.ms-excel": _SHEET_MIME,
    "application/vnd.oasis.opendocument.spreadsheet": _SHEET_MIME,
    # PowerPoint
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": _SLIDES_MIME,
    "application/vnd.ms-powerpoint": _SLIDES_MIME,
    "application/vnd.oasis.opendocument.presentation": _SLIDES_MIME,
}

# שם תיקיית הסוג בשורש = הסוג שמופיע בתחילת שם כל קובץ שבתוכה. היוצא מן
# הכלל היחיד הוא האודיו: התיקייה בלשון רבים ("הקלטות") והקובץ בלשון יחיד
# ("הקלטה - ..."), כי בפגישה אחת יכולים להיות שני קבצי אודיו (שיחת טלפון
# מוקלטת בשני ערוצים נפרדים).
SUMMARY_FOLDER = "סיכום"
TRANSCRIPT_FOLDER = "תמלול"
TODO_FOLDER = "TO DO"
AUDIO_FOLDER = "הקלטות"
AUDIO_FILE_KIND = "הקלטה"
NOTES_FOLDER = "הערות"
ATTACHMENTS_FOLDER = "קבצים מצורפים"

# צבע ייחודי לכל משימה (לפי אינדקס שלה ב-result.todos), משותף לטאב
# "משימות" ולטאב "גאנט" - כדי שאפשר יהיה לזהות ויזואלית איזה פס בגאנט
# שייך לאיזו שורה בטבלה.
_TASK_COLORS_HEX = [
    "#4285F4",  # כחול
    "#EA4335",  # אדום
    "#34A853",  # ירוק
    "#FBBC04",  # צהוב
    "#A142F4",  # סגול
    "#FF6D01",  # כתום
    "#00ACC1",  # תכלת
    "#E91E63",  # ורוד
    "#8BC34A",  # ירוק בהיר
    "#795548",  # חום
    "#607D8B",  # אפור-כחול
    "#F4B400",  # זהב
]


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=settings.drive_oauth_refresh_token,
        token_uri=_TOKEN_URI,
        client_id=settings.drive_oauth_client_id,
        client_secret=settings.drive_oauth_client_secret,
        scopes=_SCOPES,
    )


def _drive_client():
    return build("drive", "v3", credentials=_credentials())


def _sheets_client():
    return build("sheets", "v4", credentials=_credentials())


def file_id_from_url(url: str | None) -> str | None:
    """מזהה הקובץ מתוך קישור Drive שנשמר ב-Firestore.

    נדרש להקלטות ישנות, שנשמרו לפני שהמזהים עצמם נכתבו לרשומה - ראה
    pipeline/edit.py."""
    match = _FILE_ID_IN_URL.search(url or "")
    return match.group(1) if match else None


def file_size(file_id: str) -> int | None:
    """גודל הקובץ בבתים, או None אם Drive לא מדווח עליו (מסמכי Google)."""
    meta = _drive_client().files().get(fileId=file_id, fields="size").execute()
    size = meta.get("size")
    return int(size) if size is not None else None


def stream_file(file_id: str, start: int = 0, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
    """מזרים את תוכן הקובץ מ-Drive, החל מהבית [start].

    ההזרמה היא בייט-בייט דרך הזיכרון ולא הורדה מלאה לדיסק: קובץ אודיו של
    פגישה ארוכה שוקל עשרות מגה-בייט, והשרת (Cloud Run) מוגבל בזיכרון.
    ההרשאה נשלחת כטוקן גישה טרי, כי כאן פונים ל-HTTP הגולמי של Drive
    ולא דרך googleapiclient - רק כך אפשר להעביר Range ולהמשיך הורדה
    שנקטעה.
    """
    credentials = _credentials()
    credentials.refresh(AuthRequest())

    headers = {"Authorization": f"Bearer {credentials.token}"}
    if start > 0:
        headers["Range"] = f"bytes={start}-"

    with requests.get(
        _MEDIA_URL.format(file_id=file_id), headers=headers, stream=True, timeout=120
    ) as response:
        response.raise_for_status()
        yield from response.iter_content(chunk_size)


def download_file(file_id: str) -> bytes:
    """מוריד את תוכן הקובץ במלואו לזיכרון.

    להבדיל מ-stream_file, שנועד לאודיו גדול שזורם ללקוח - כאן מדובר בקובץ
    מצורף שצריך להימסר שלם ל-Gemini, וממילא הוא מוגבל בגודלו (ראה
    MAX_ATTACHMENT_BYTES ב-pipeline/attachments.py). זה מה שמאפשר לנסות
    לסכם קובץ מצורף שוב אחרי כישלון: הקובץ המקורי כבר יושב ב-Drive, ואין
    צורך לבקש מהמשתמש לצרף אותו מחדש."""
    return b"".join(stream_file(file_id))


def convert_to_pdf(content: bytes, filename: str, mime_type: str) -> bytes:
    """ממיר קובץ Office (Word/Excel/PowerPoint) ל-PDF, דרך Drive.

    Gemini לא יודע לקרוא .docx/.xlsx/.pptx - הוא מקבל PDF, תמונות וטקסט
    בלבד. במקום להוסיף ספריות חילוץ טקסט (python-docx/openpyxl/python-pptx),
    שמאבדות טבלאות, פריסה ותמונות, ההמרה נעשית ע"י Google עצמו: הקובץ
    מועלה ל-Drive עם המרה ל-Google Doc/Sheet/Slides ומיוצא משם כ-PDF.
    זה גם מה שהמשתמש היה רואה אילו פתח את הקובץ בעצמו ב-Drive.

    ההעתק המומר הוא זמני בלבד ונזרק לאשפה מיד - הקובץ שנשמר לתיקיית
    "קבצים מצורפים" הוא תמיד המקור, כפי שהמשתמש צירף אותו.

    מגבלת files.export של Drive היא 10MB לפלט; מסמך שחורג ממנה יזרוק
    שגיאה שנתפסת ומדווחת למשתמש כמצורף שנכשל (ראה attachments.py).
    """
    google_mime = OFFICE_TO_GOOGLE_MIME[mime_type]
    drive = _drive_client()
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
    temp_file = (
        drive.files()
        .create(
            body={"name": f"[המרה זמנית] {filename}", "mimeType": google_mime},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    try:
        return drive.files().export(fileId=temp_file["id"], mimeType="application/pdf").execute()
    finally:
        drive.files().update(fileId=temp_file["id"], body={"trashed": True}).execute()


def _create_folder(drive, name: str, parent_id: str) -> str:
    metadata = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]}
    folder = drive.files().create(body=metadata, fields="id, webViewLink").execute()
    return folder["id"]


def _file_name(kind: str, title: str) -> str:
    """שם הקובץ בתוך תיקיית הסוג: "<סוג> - <כותרת הפגישה>".

    הסוג לבדו לא מספיק כשכל הסיכומים יושבים באותה תיקייה, והכותרת לבדה לא
    מסבירה מה הקובץ מכיל - שניהם יחד נותנים שם שאפשר לזהות לפיו גם ברשימה
    ארוכה וגם בתוצאות חיפוש ב-Drive."""
    title = (title or "").strip()
    return f"{kind} - {title}" if title else kind


def _find_by_name(drive, name: str, parent_id: str, mime_type: str = "") -> str | None:
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    mime_clause = f"and mimeType = '{mime_type}' " if mime_type else ""
    response = (
        drive.files()
        .list(
            q=(
                f"name = '{escaped}' {mime_clause}"
                f"and '{parent_id}' in parents and trashed = false"
            ),
            orderBy="createdTime",
            fields="files(id)",
            pageSize=1,
        )
        .execute()
    )
    files = response.get("files", [])
    return files[0]["id"] if files else None


def _find_folder(drive, name: str) -> str | None:
    return _find_by_name(drive, name, settings.drive_root_folder_id, _FOLDER_MIME)


def _type_folder_id(drive, name: str) -> str:
    """מזהה תיקיית הסוג בשורש, ויוצר אותה בפעם הראשונה שנדרשת.

    Drive מתיר שתי תיקיות באותו שם באותו הורה, כך ששתי הקלטות שנשמרות
    בו-זמנית (וזה קורה - ראה סריקות מקבילות ב-CallImportScan) עלולות ליצור
    שתי תיקיות "סיכום" מקבילות ולפצל את הספרייה לשתיים. לכן מי שיצר את
    התיקייה בודק מיד מי הראשונה שנוצרה: אם זו לא שלו, הוא זורק את שלו
    (עדיין ריקה) ונצמד לראשונה. השאילתה ממוינת לפי createdTime כדי ששני
    הצדדים יגיעו לאותה הכרעה."""
    existing = _find_folder(drive, name)
    if existing:
        return existing
    created = _create_folder(drive, name, settings.drive_root_folder_id)
    winner = _find_folder(drive, name)
    if winner and winner != created:
        drive.files().update(fileId=created, body={"trashed": True}).execute()
        return winner
    return created


def _unique_title(drive, title: str) -> str:
    """כותרת שאין כמותה בספרייה: לכותרת תפוסה נוסף " 2", אחר כך " 3" וכן
    הלאה.

    בלי זה שתי פגישות באותו שם (וזה קורה - "שיחת תיאום קצרה" פעמיים
    בשבוע) מייצרות שני קבצים באותו שם בדיוק בכל תיקיית סוג, ומשם אי אפשר
    לדעת איזה שייך לאיזו פגישה. המספר נקבע פעם אחת לכל הקלטה ומשמש בכל
    קבציה וגם ככותרת שלה באפליקציה, כדי שהזיהוי יהיה זהה בכל מקום.

    תיקיית הסיכומים היא המדד: לכל פגישה יש בה בדיוק קובץ אחד, ולכן היא
    רשימת הכותרות הקיימות."""
    title = (title or "").strip()
    if not title:
        return title
    summary_folder_id = _type_folder_id(drive, SUMMARY_FOLDER)
    candidate, index = title, 1
    while _find_by_name(drive, _file_name(SUMMARY_FOLDER, candidate), summary_folder_id):
        index += 1
        candidate = f"{title} {index}"
    return candidate


def unique_title(title: str) -> str:
    """גרסה חיצונית של _unique_title - לעריכת כותרת מהאפליקציה
    (pipeline/edit.py)."""
    return _unique_title(_drive_client(), title)


def _rtl_document(blocks: str) -> str:
    return (
        '<html><head><meta charset="utf-8"></head>'
        '<body dir="rtl" style="direction:rtl;text-align:right;">'
        f"{blocks}</body></html>"
    )


def _text_to_rtl_html(text: str) -> str:
    """עוטף טקסט בעברית ב-HTML שממנו Drive ייצר Google Doc מיושר לימין.

    Drive ממיר ל-Google Doc גם text/plain וגם text/html, אבל בהמרה מטקסט
    רגיל כל פסקה נוצרת עם כיווניות LTR ויישור לשמאל - מה שהופך מסמך עברי
    לקשה לקריאה: השורות נצמדות לשמאל והפיסוק בסוף המשפט קופץ לצד הלא נכון.
    כיווניות ב-Google Docs היא תכונה של כל פסקה בנפרד (אין הגדרה ברמת
    המסמך), ולכן כל שורה נעטפת ב-<p> משלה עם dir="rtl" ועם היישור בסגנון
    inline - שתי הדרכים יחד, כי המרת ה-HTML של Google מסתמכת על שתיהן.

    שורות ריקות נשמרות כפסקאות ריקות (<br>), כדי שהמרווח בין קטע לקטע
    בתמלול יישאר כפי שהוא בטקסט המקורי.

    זהו העיצוב של התמלול וההערות - מסמכים שנקראים ברצף. הסיכום, שנקרא
    בסריקה, מקבל עיצוב עשיר יותר: ראה _summary_to_rtl_html.
    """
    paragraphs = "".join(
        f'<p dir="rtl" style="direction:rtl;text-align:right;margin:0;">'
        f'{html.escape(line) if line.strip() else "<br>"}</p>'
        for line in text.split("\n")
    )
    return _rtl_document(paragraphs)


# —————————————— עיצוב מסמך הסיכום ——————————————

# הסיכום הוא המסמך שנפתח הכי הרבה, ובניגוד לתמלול לא קוראים אותו מהתחלה
# לסוף אלא סורקים אותו בעיניים - ולכן הוא מעוצב ולא נשפך כטקסט אחיד:
# ריווח שורות מוגדל (line-height), שורה ריקה בין פסקה לפסקה (margin ולא
# פסקה ריקה - כך המרווח נשמר גם כשעורכים את המסמך בדפדפן), כותרת נושא
# בולטת מעל גוף הסעיף וכל נתון קונקרטי מודגש.
_RTL = "direction:rtl;text-align:right;"
_SUMMARY_BODY_STYLE = f"{_RTL}font-size:11pt;line-height:1.6;margin:0 0 12pt 0;"
_SUMMARY_TOPIC_STYLE = f"{_RTL}font-size:13pt;line-height:1.6;margin:18pt 0 6pt 0;"
_SUMMARY_ITEM_STYLE = f"{_RTL}font-size:11pt;line-height:1.6;margin:0 0 6pt 0;"

# "1. תקציב הפרויקט: מאיר אמר..." - הפורמט שהמודל מתבקש לכתוב בו את
# הסיכום (ראה pipeline/summarize.py).
_TOPIC_RE = re.compile(r"^(\d+)[.)]\s*(.+)$")
# "--- קובץ מצורף: חשבונית.pdf ---" - המפריד שנוסף לסיכום כשמצרפים קובץ
# (ראה pipeline/attachments.py).
_SECTION_RE = re.compile(r"^-{3,}\s*(.+?)\s*-{3,}$")
_BULLET_RE = re.compile(r"^[-•*]\s+(.+)$")
# המודל לפעמים מדגיש בעצמו בסימון Markdown; בטקסט גולמי זה נראה כמו
# כוכביות תועות, וכאן זו בדיוק ההדגשה שרצינו.
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# כותרת נושא נלקחת עד הנקודתיים הראשונות, אבל רק אם הן קרובות לתחילת
# השורה - אחרת נקודתיים באמצע משפט ("הוא אמר: אין בעיה") היו הופכות חצי
# סעיף לכותרת.
_MAX_TOPIC_HEADING = 70

_MONTHS = (
    "ינואר|פברואר|מרץ|מרס|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר"
)
_UNITS = (
    r'₪|ש"ח|שקלים|שקל|\$|דולרים|דולר|€|אירו|יורו|%|אחוזים|אחוז|'
    r"ימים|יום|שבועות|שבוע|חודשים|חודש|שנים|שנה|שעות|שעה|דקות|דקה|"
    r'אלפים|אלף|מיליארד|מיליון|ק"מ|ק"ג|מטרים|מטר|טון'
)
# 12,450 / 15/09 / 2026-08-20 / 14:00 / 3.5 - וכל מה שנצמד להם: סימן
# מטבע לפני, ואחרי - יחידה או שם חודש. שתי יחידות ולא אחת, כי בעברית
# הסכום נאמר בשתיים: "40 אלף ₪", "12 מיליון דולר".
_IMPORTANT_RE = re.compile(
    rf"(?:[₪$€]\s?)?\d+(?:[.,:/-]\d+)*"
    rf"(?:\s*(?:{_UNITS}|ב?(?:{_MONTHS}))(?![א-ת])){{0,2}}"
)


def _emphasize(text: str) -> str:
    """מסמן בהדגשה את הפרטים שאי אפשר לפספס בסריקה מהירה של הסיכום: כל
    נתון קונקרטי - סכום, תאריך, שעה, אחוז, כמות ומשך.

    זה בדיוק המידע שהפרומפט מחייב את המודל לשמר במדויק (ראה
    pipeline/summarize.py), והדבר הראשון שמחפשים כשחוזרים לסיכום פגישה -
    כמה, מתי, עד מתי. שאר ההדגשות אינן ניתנות לזיהוי אוטומטי בלי לנחש מה
    חשוב, ולכן מודגש כאן רק מה שוודאי.
    """
    escaped = html.escape(text, quote=False)
    escaped = _MARKDOWN_BOLD_RE.sub(r"<b>\1</b>", escaped)
    return _IMPORTANT_RE.sub(lambda match: f"<b>{match.group(0)}</b>", escaped)


def _split_topic(text: str) -> tuple[str, str]:
    """מפצל "כותרת הנושא: גוף הסעיף" לשניים. סעיף בלי נקודתיים קרובות
    נשאר גוש אחד - כותרת אם הוא קצר, גוף סעיף אם הוא ארוך."""
    head, sep, body = text.partition(":")
    if sep and len(head) <= _MAX_TOPIC_HEADING:
        return head.strip(), body.strip()
    if len(text) <= _MAX_TOPIC_HEADING:
        return text, ""
    return "", text


def _summary_to_rtl_html(text: str) -> str:
    """בונה את ה-HTML של מסמך הסיכום מהטקסט של הסיכום.

    כל נושא ממוספר הופך לכותרת (<h2>, שגם נכנסת למפתח המסמך ב-Google Docs
    ומאפשרת לקפוץ בין נושאים) ולפסקה מתחתיה, שורות תבליט הופכות לרשימה,
    והמפריד של קובץ מצורף הופך לכותרת משלו. טקסט שאינו מזוהה כאף אחד מאלה
    יורד כפסקה רגילה - הסיכום נכתב בידי מודל, ועיצוב שמניח מבנה קשיח היה
    שובר את המסמך בדיוק בפעם שבה המודל יסטה ממנו.
    """
    blocks: list[str] = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        if bullets:
            items = "".join(
                f'<li dir="rtl" style="{_SUMMARY_ITEM_STYLE}">{item}</li>'
                for item in bullets
            )
            blocks.append(f'<ul dir="rtl" style="{_RTL}">{items}</ul>')
            bullets.clear()

    def heading(content: str) -> None:
        blocks.append(f'<h2 dir="rtl" style="{_SUMMARY_TOPIC_STYLE}">{content}</h2>')

    def paragraph(content: str) -> None:
        blocks.append(f'<p dir="rtl" style="{_SUMMARY_BODY_STYLE}">{content}</p>')

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_bullets()
            continue

        section = _SECTION_RE.match(line)
        if section:
            flush_bullets()
            heading(_emphasize(section.group(1)))
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            bullets.append(_emphasize(bullet.group(1)))
            continue

        flush_bullets()
        topic = _TOPIC_RE.match(line)
        if topic:
            number, rest = topic.groups()
            title, body = _split_topic(rest)
            if title:
                heading(f"{number}. {_emphasize(title)}")
                if body:
                    paragraph(_emphasize(body))
            else:
                paragraph(f"<b>{number}.</b> {_emphasize(body)}")
            continue

        paragraph(_emphasize(line))

    flush_bullets()
    return _rtl_document("".join(blocks))


def _html_media(text: str, to_html=_text_to_rtl_html) -> MediaIoBaseUpload:
    return MediaIoBaseUpload(io.BytesIO(to_html(text).encode("utf-8")), mimetype="text/html")


def _transcript_to_text(segments: list[TranscriptSegment]) -> str:
    return "\n\n".join(f"{s.speaker_label}:\n{s.text}" for s in segments)


def _hex_to_rgb(hex_code: str) -> tuple[float, float, float]:
    hex_code = hex_code.lstrip("#")
    return (
        int(hex_code[0:2], 16) / 255,
        int(hex_code[2:4], 16) / 255,
        int(hex_code[4:6], 16) / 255,
    )


def _tint(hex_code: str, alpha: float) -> dict:
    """מערבב את הצבע עם לבן לפי alpha (1.0 = הצבע המלא, 0.0 = לבן) - כדי
    לקבל גרסה בהירה של אותו צבע לרקע שורה בטבלה, וגרסה בינונית לגוף הפס
    בגאנט, בלי לפגוע בקריאות הטקסט."""
    r, g, b = _hex_to_rgb(hex_code)
    return {
        "red": 1 - alpha + alpha * r,
        "green": 1 - alpha + alpha * g,
        "blue": 1 - alpha + alpha * b,
    }


def _parse_due_date(due_date: str | None) -> datetime.date | None:
    if not due_date:
        return None
    try:
        return datetime.date.fromisoformat(due_date)
    except ValueError:
        return None


_TODO_HEADERS = ["משימה", "אחראי", "תאריך יעד"]

# רוחב לכל עמודה בטאב "משימות", בפיקסלים. "אחראי" רחב מברירת המחדל (100)
# כדי ששם מלא לא ייחתך - העמודה הזו היא הראשונה שקוראים בה אחרי תיאור
# המשימה, ושם חתוך למחצה מחטיא את מטרתה.
_TODO_COLUMN_WIDTHS = [320, 150, 110]


def _todo_sheet_values(todos: list[TodoItem]) -> list[list[str]]:
    """שורת כותרת + שורה לכל משימה, בסדר העמודות של _TODO_HEADERS. משימה
    בלי אחראי או בלי תאריך יעד משאירה תא ריק ולא "None"."""
    return [list(_TODO_HEADERS)] + [
        [todo.description, todo.owner or "", todo.due_date or ""] for todo in todos
    ]


def _todo_sheet_requests(todos: list[TodoItem]) -> list[dict]:
    """שורה אחת לכל משימה, עם רקע בצבע הייעודי שלה (ראה _TASK_COLORS_HEX)."""
    requests: list[dict] = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.945, "green": 0.945, "blue": 0.945},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
    ]
    requests += [
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 0, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1},
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        }
        for col, width in enumerate(_TODO_COLUMN_WIDTHS)
    ]
    for i in range(len(todos)):
        color_hex = _TASK_COLORS_HEX[i % len(_TASK_COLORS_HEX)]
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": i + 1, "endRowIndex": i + 2, "startColumnIndex": 0, "endColumnIndex": len(_TODO_HEADERS)},
                    "cell": {"userEnteredFormat": {"backgroundColor": _tint(color_hex, 0.18)}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        )
    return requests


def _gantt_row_label(todo: TodoItem) -> str:
    return f"{todo.description} ({todo.owner})" if todo.owner else todo.description


def _gantt_sheet_requests(
    todos: list[TodoItem], dated_indices: list[int], meeting_date: datetime.date, dates: list[datetime.date]
) -> list[dict]:
    """שורה אחת לכל משימה עם תאריך יעד: שם המשימה בעמודה הקבועה מימין
    (עמודה A, ב-sheet מסוג RTL זו העמודה שמוצגת בפועל מימין), ופס ממוזג
    בצבע הייעודי שלה מתאריך הפגישה ועד תאריך היעד - עם טווח התאריכים כתוב
    בתוך הפס עצמו, כדי שאפשר יהיה לראות מתי כל משימה מתחילה ומתי נגמרת בלי
    להזדקק לספור עמודות מול שורת התאריכים למעלה. שורת התאריכים למעלה
    מסובבת אנכית כדי שהיא תישאר קריאה למרות עמודות היום הצרות."""
    meeting_col = 1 + (meeting_date - dates[0]).days
    header_gray = {"red": 0.945, "green": 0.945, "blue": 0.945}
    requests: list[dict] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": 1,
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": header_gray,
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment,verticalAlignment)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 1 + len(dates)},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": header_gray,
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "textRotation": {"angle": 90},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment,verticalAlignment,textRotation)",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 1, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 60},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 1, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 260},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 1, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 1 + len(dates)},
                "properties": {"pixelSize": 34},
                "fields": "pixelSize",
            }
        },
    ]
    for row, todo_index in enumerate(dated_indices, start=1):
        todo = todos[todo_index]
        due_date = _parse_due_date(todo.due_date)
        color_hex = _TASK_COLORS_HEX[todo_index % len(_TASK_COLORS_HEX)]
        due_col = 1 + (due_date - dates[0]).days
        bar_start, bar_end = sorted((meeting_col, due_col))
        bar_range = {
            "sheetId": 1,
            "startRowIndex": row,
            "endRowIndex": row + 1,
            "startColumnIndex": bar_start,
            "endColumnIndex": bar_end + 1,
        }
        if bar_end > bar_start:
            requests.append({"mergeCells": {"range": bar_range, "mergeType": "MERGE_ALL"}})
        bar_start_date = dates[bar_start - 1]
        bar_end_date = dates[bar_end - 1]
        bar_label = (
            bar_start_date.strftime("%d/%m")
            if bar_start_date == bar_end_date
            else f"{bar_start_date.strftime('%d/%m')} - {bar_end_date.strftime('%d/%m')}"
        )
        requests.append(
            {
                "updateCells": {
                    "range": {**bar_range, "endColumnIndex": bar_start + 1},
                    "rows": [
                        {
                            "values": [
                                {
                                    "userEnteredValue": {"stringValue": bar_label},
                                    "userEnteredFormat": {
                                        "backgroundColor": _tint(color_hex, 0.85),
                                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                        "horizontalAlignment": "CENTER",
                                        "verticalAlignment": "MIDDLE",
                                    },
                                }
                            ]
                        }
                    ],
                    "fields": "userEnteredValue,userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
                }
            }
        )
    return requests


def _build_todo_spreadsheet(sheets, drive, name: str, todos: list[TodoItem], meeting_date: datetime.date, folder_id: str) -> dict:
    """בונה Google Sheet חדש עם טאב "משימות" (טבלה) ועם טאב "גאנט" (ציר זמן
    חזותי, רק אם יש לפחות משימה אחת עם תאריך יעד) - כל משימה מקבלת צבע
    ייחודי שמופיע בשני הטאבים, כדי לקשר ויזואלית בין השורה בטבלה לפס בגאנט.
    """
    dated_indices = [i for i, t in enumerate(todos) if _parse_due_date(t.due_date)]

    sheet_props = [{"sheetId": 0, "title": "משימות", "rightToLeft": True}]
    if dated_indices:
        sheet_props.append({"sheetId": 1, "title": "גאנט", "rightToLeft": True})

    spreadsheet = (
        sheets.spreadsheets()
        .create(
            body={"properties": {"title": name}, "sheets": [{"properties": p} for p in sheet_props]},
            fields="spreadsheetId",
        )
        .execute()
    )
    spreadsheet_id = spreadsheet["spreadsheetId"]

    value_ranges = [
        {"range": "משימות!A1", "values": _todo_sheet_values(todos)}
    ]
    requests = _todo_sheet_requests(todos)

    if dated_indices:
        due_dates = [_parse_due_date(todos[i].due_date) for i in dated_indices]
        start = min([meeting_date, *due_dates])
        end = max([meeting_date, *due_dates])
        dates = [start + datetime.timedelta(days=n) for n in range((end - start).days + 1)]
        value_ranges.append(
            {
                "range": "גאנט!A1",
                "values": [["משימה"] + [d.strftime("%d/%m") for d in dates]]
                + [[_gantt_row_label(todos[i])] + [""] * len(dates) for i in dated_indices],
            }
        )
        requests += _gantt_sheet_requests(todos, dated_indices, meeting_date, dates)

    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": value_ranges},
    ).execute()
    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()

    file = drive.files().get(fileId=spreadsheet_id, fields="parents").execute()
    return (
        drive.files()
        .update(
            fileId=spreadsheet_id,
            addParents=folder_id,
            removeParents=",".join(file.get("parents", [])),
            fields="id, webViewLink",
        )
        .execute()
    )


def _upload_audio_file(drive, audio_path: str, folder_id: str, name: str) -> dict:
    with open(audio_path, "rb") as f:
        media = MediaIoBaseUpload(io.BytesIO(f.read()), mimetype="audio/mpeg", resumable=True)
    metadata = {"name": name, "parents": [folder_id]}
    return (
        drive.files()
        .create(body=metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )


def save_meeting_to_drive(
    result: MeetingResult,
    audio_path: str,
    extra_audio: list[tuple[str, str]] | None = None,
) -> dict:
    """שומר כל תוצר של הפגישה בתיקיית הסוג שלו, ומחזיר את הקישורים והמזהים
    לכל קובץ בנפרד - כדי שאפשר יהיה לקשר אליהם ישירות מהיסטוריית ההקלטות
    באפליקציה, לעדכן את קובץ התמלול/הסיכום מאוחר יותר (ראה update_text_doc)
    ולשנות/למחוק את כולם יחד כשהפגישה נערכת או נמחקת (retitle_files,
    trash_files) - שכן אין יותר תיקייה אחת שמאגדת אותם.

    extra_audio הוא [(נתיב, תווית)] לערוצי אודיו נוספים (שיחת טלפון מוקלטת
    בשני ערוצים): התווית נוספת בסוף שם הקובץ, אחרי הכותרת.

    אם כבר קיימת פגישה באותה כותרת, הכותרת מקבלת מספר (" 2", " 3") לפני
    שנוצר ולו קובץ אחד - ומוחזרת ב-"title" כדי שגם הרשומה באפליקציה תישא
    אותה. ראה _unique_title."""
    drive = _drive_client()
    sheets = _sheets_client()

    title = _unique_title(drive, result.title)

    transcript_file = _upload_text_as_doc_full(
        drive,
        _file_name(TRANSCRIPT_FOLDER, title),
        _transcript_to_text(result.transcript),
        _type_folder_id(drive, TRANSCRIPT_FOLDER),
    )
    summary_file = _upload_text_as_doc_full(
        drive,
        _file_name(SUMMARY_FOLDER, title),
        result.summary,
        _type_folder_id(drive, SUMMARY_FOLDER),
        _summary_to_rtl_html,
    )
    todo_file = _build_todo_spreadsheet(
        sheets,
        drive,
        _file_name(TODO_FOLDER, title),
        result.todos,
        datetime.date.fromisoformat(result.date),
        _type_folder_id(drive, TODO_FOLDER),
    )

    audio_folder_id = _type_folder_id(drive, AUDIO_FOLDER)
    audio_name = _file_name(AUDIO_FILE_KIND, title)
    audio_file = _upload_audio_file(
        drive, audio_path, audio_folder_id, f"{audio_name}.m4a"
    )
    audio_file_ids = [audio_file["id"]]
    for extra_path, extra_label in extra_audio or []:
        extra_file = _upload_audio_file(
            drive, extra_path, audio_folder_id, f"{audio_name} - {extra_label}.m4a"
        )
        audio_file_ids.append(extra_file["id"])

    root = (
        drive.files()
        .get(fileId=settings.drive_root_folder_id, fields="webViewLink")
        .execute()
    )

    return {
        # הכותרת שבה נשמרו הקבצים בפועל - זהה למבוקשת, אלא אם היא הייתה
        # תפוסה ונוסף לה מספר.
        "title": title,
        # אין יותר תיקייה משלה לפגישה - הקישור מוביל לספרייה כולה, וכל
        # תוצר בנפרד נפתח מהקישור הישיר שלו.
        "folder_url": root["webViewLink"],
        "transcript_url": transcript_file["webViewLink"],
        "transcript_doc_id": transcript_file["id"],
        "summary_url": summary_file["webViewLink"],
        "summary_doc_id": summary_file["id"],
        "todo_url": todo_file["webViewLink"],
        "todo_file_id": todo_file["id"],
        "audio_url": audio_file["webViewLink"],
        "audio_file_ids": audio_file_ids,
    }


def _upload_text_as_doc_full(
    drive, name: str, text: str, folder_id: str, to_html=_text_to_rtl_html
) -> dict:
    metadata = {"name": name, "mimeType": _DOC_MIME, "parents": [folder_id]}
    return (
        drive.files()
        .create(
            body=metadata, media_body=_html_media(text, to_html), fields="id, webViewLink"
        )
        .execute()
    )


def create_note_doc(title: str, text: str) -> dict:
    """יוצר את קובץ ההערות של הפגישה בתיקיית "הערות". מחזיר
    {"id", "webViewLink"}."""
    drive = _drive_client()
    return _upload_text_as_doc_full(
        drive,
        _file_name(NOTES_FOLDER, title),
        text,
        _type_folder_id(drive, NOTES_FOLDER),
    )


def update_text_doc(doc_id: str, new_full_text: str) -> None:
    """מחליף את תוכן קובץ התמלול או ההערות בטקסט המעודכן.
    Google ממיר אוטומטית ל-Google Doc גם בעדכון, בדיוק כמו ביצירה הראשונית,
    ולכן גם כאן מעלים HTML מיושר לימין (ראה _text_to_rtl_html) - אחרת כל
    עריכה מהאפליקציה הייתה מאפסת את הכיווניות של המסמך בחזרה לשמאל."""
    drive = _drive_client()
    drive.files().update(fileId=doc_id, media_body=_html_media(new_full_text)).execute()


def update_summary_doc(doc_id: str, new_full_text: str) -> None:
    """מחליף את תוכן קובץ הסיכום. אותו דבר בדיוק כמו update_text_doc, אלא
    שהעיצוב נבנה מחדש (_summary_to_rtl_html) - אחרת עריכה מהאפליקציה
    (שינוי שמות דוברים) או צירוף קובץ היו מחזירים את הסיכום לטקסט שטוח."""
    drive = _drive_client()
    drive.files().update(
        fileId=doc_id, media_body=_html_media(new_full_text, _summary_to_rtl_html)
    ).execute()


def retitle_files(file_ids: list[str], old_title: str, new_title: str) -> None:
    """מחליף את כותרת הפגישה בשם כל אחד מקבצי הפגישה, אחרי עריכת הכותרת.

    ההחלפה נעשית בתוך השם הקיים ולא בבנייה מחדש שלו, כי הסוג והתוספות
    שאחרי הכותרת שונים מקובץ לקובץ ("הקלטה - X - הצד השני.m4a", "X -
    חשבונית.pdf"), וכולם צריכים להתעדכן מאותה פעולה. קובץ שהכותרת הישנה
    לא מופיעה בשמו נשאר כפי שהוא."""
    old_title = (old_title or "").strip()
    new_title = (new_title or "").strip()
    if not old_title or not new_title or old_title == new_title:
        return
    drive = _drive_client()
    for file_id in file_ids:
        name = drive.files().get(fileId=file_id, fields="name").execute()["name"]
        if old_title in name:
            drive.files().update(
                fileId=file_id, body={"name": name.replace(old_title, new_title)}
            ).execute()


def trash_files(file_ids: list[str]) -> None:
    """מעביר את כל קבצי הפגישה לאשפה ב-Drive, בעקבות מחיקה מהאפליקציה. לא
    מוחק לצמיתות - ניתן לשחזר מהאשפה ב-Drive עד שהיא מתרוקנת."""
    drive = _drive_client()
    for file_id in file_ids:
        drive.files().update(fileId=file_id, body={"trashed": True}).execute()


def rename_folder(folder_id: str, new_name: str) -> None:
    """שינוי שם תיקיית ההקלטה ב-Drive - רק להקלטות ישנות, מלפני המעבר
    לתיקיות לפי סוג (ראה docstring למעלה)."""
    drive = _drive_client()
    drive.files().update(fileId=folder_id, body={"name": new_name}).execute()


def trash_folder(folder_id: str) -> None:
    """מעביר תיקיית הקלטה ישנה (וכל תוכנה) לאשפה - רק להקלטות מלפני המעבר
    לתיקיות לפי סוג."""
    drive = _drive_client()
    drive.files().update(fileId=folder_id, body={"trashed": True}).execute()


def upload_attachment(content: bytes, filename: str, mime_type: str, title: str) -> dict:
    """מעלה קובץ מצורף כמו שהוא (לא ממיר ל-Google Doc) לתיקיית "קבצים
    מצורפים", בשם "<כותרת הפגישה> - <שם הקובץ המקורי>": שם הקובץ המקורי
    כבר מרמז על תוכנו, והכותרת מקדימה אותו כדי שכל המצורפים של אותה פגישה
    יישבו יחד ברשימה.

    מחזיר {"id", "url"} ולא קישור בלבד: המזהה נדרש כדי למחוק מצורף בודד
    ולהוריד אותו בחזרה לניסיון סיכום חוזר. עד היום נשמר הקישור בלבד, והמזהה
    חולץ ממנו בחיפוש טקסטואלי - עקיף ושביר."""
    drive = _drive_client()
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
    metadata = {
        "name": f"{(title or '').strip()} - {filename}".strip(" -"),
        "parents": [_type_folder_id(drive, ATTACHMENTS_FOLDER)],
    }
    file = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    return {"id": file["id"], "url": file["webViewLink"]}
