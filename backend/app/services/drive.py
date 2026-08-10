"""יצירת תיקיית הפגישה וקבצי התוצר ב-Google Drive.

תמלול וסיכום נשמרים כ-Google Docs (מומרים אוטומטית מטקסט בהעלאה). קובץ
האודיו המקורי מועלה כפי שהוא. רשימת המשימות נבנית כ-Google Sheet דרך
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
import io

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import settings
from app.models import MeetingResult, TodoItem, TranscriptSegment

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"

_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"

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


def _drive_client():
    credentials = Credentials(
        token=None,
        refresh_token=settings.drive_oauth_refresh_token,
        token_uri=_TOKEN_URI,
        client_id=settings.drive_oauth_client_id,
        client_secret=settings.drive_oauth_client_secret,
        scopes=_SCOPES,
    )
    return build("drive", "v3", credentials=credentials)


def _sheets_client():
    credentials = Credentials(
        token=None,
        refresh_token=settings.drive_oauth_refresh_token,
        token_uri=_TOKEN_URI,
        client_id=settings.drive_oauth_client_id,
        client_secret=settings.drive_oauth_client_secret,
        scopes=_SCOPES,
    )
    return build("sheets", "v4", credentials=credentials)


def _create_folder(drive, name: str, parent_id: str) -> str:
    metadata = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]}
    folder = drive.files().create(body=metadata, fields="id, webViewLink").execute()
    return folder["id"]


def _upload_text_as_doc(drive, name: str, text: str, folder_id: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/plain")
    metadata = {"name": name, "mimeType": _DOC_MIME, "parents": [folder_id]}
    file = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    return file["webViewLink"]


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
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 0, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 320},
                "fields": "pixelSize",
            }
        },
    ]
    for i in range(len(todos)):
        color_hex = _TASK_COLORS_HEX[i % len(_TASK_COLORS_HEX)]
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": i + 1, "endRowIndex": i + 2, "startColumnIndex": 0, "endColumnIndex": 3},
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
        {
            "range": "משימות!A1",
            "values": [["משימה", "אחראי", "תאריך יעד"]]
            + [[todo.description, todo.owner or "", todo.due_date or ""] for todo in todos],
        }
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


def _upload_audio_file(
    drive, audio_path: str, folder_id: str, name: str = "הקלטה מקורית.m4a"
) -> str:
    with open(audio_path, "rb") as f:
        media = MediaIoBaseUpload(io.BytesIO(f.read()), mimetype="audio/mpeg", resumable=True)
    metadata = {"name": name, "parents": [folder_id]}
    file = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    return file["webViewLink"]


def save_meeting_to_drive(
    result: MeetingResult,
    audio_path: str,
    extra_audio: list[tuple[str, str]] | None = None,
) -> dict:
    """יוצר תיקייה אחת עם כל תוצרי הפגישה, ומחזיר את הקישורים והמזהים לכל
    קובץ בנפרד (לא רק לתיקייה) - כדי שאפשר יהיה לקשר אליהם ישירות מהיסטוריית
    ההקלטות באפליקציה, ולעדכן את קובץ התמלול/הסיכום מאוחר יותר (ראה
    update_text_doc, לעריכה מהאפליקציה ולצירוף קבצים)."""
    drive = _drive_client()
    sheets = _sheets_client()

    folder_name = f"{result.date} - {result.title}"
    folder_id = _create_folder(drive, folder_name, settings.drive_root_folder_id)

    transcript_file = _upload_text_as_doc_full(
        drive, "תמלול", _transcript_to_text(result.transcript), folder_id
    )
    summary_file = _upload_text_as_doc_full(
        drive, "סיכום", result.summary, folder_id
    )
    todo_file = _build_todo_spreadsheet(
        sheets, drive, "TO DO", result.todos, datetime.date.fromisoformat(result.date), folder_id
    )
    todo_url = todo_file["webViewLink"]
    audio_url = _upload_audio_file(drive, audio_path, folder_id)
    for extra_path, extra_name in extra_audio or []:
        _upload_audio_file(drive, extra_path, folder_id, extra_name)

    folder = drive.files().get(fileId=folder_id, fields="webViewLink").execute()

    return {
        "folder_id": folder_id,
        "folder_url": folder["webViewLink"],
        "transcript_url": transcript_file["webViewLink"],
        "transcript_doc_id": transcript_file["id"],
        "summary_url": summary_file["webViewLink"],
        "summary_doc_id": summary_file["id"],
        "todo_url": todo_url,
        "audio_url": audio_url,
    }


def _upload_text_as_doc_full(drive, name: str, text: str, folder_id: str) -> dict:
    media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/plain")
    metadata = {"name": name, "mimeType": _DOC_MIME, "parents": [folder_id]}
    return drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()


def create_text_doc(name: str, text: str, folder_id: str) -> dict:
    """יוצר Google Doc חדש בתיקיית ההקלטה (למשל קובץ הערות חדש). מחזיר
    {"id", "webViewLink"}."""
    return _upload_text_as_doc_full(_drive_client(), name, text, folder_id)


def update_text_doc(doc_id: str, new_full_text: str) -> None:
    """מחליף את תוכן קובץ טקסט קיים (סיכום/תמלול/הערות) בטקסט המעודכן.
    Google ממיר אוטומטית טקסט רגיל ל-Google Doc גם בעדכון, בדיוק כמו
    ביצירה הראשונית."""
    drive = _drive_client()
    media = MediaIoBaseUpload(
        io.BytesIO(new_full_text.encode("utf-8")), mimetype="text/plain"
    )
    drive.files().update(fileId=doc_id, media_body=media).execute()


# נשמר בשם הקודם לשם תאימות לקוד קיים (attachments.py) - זהה ל-update_text_doc.
update_summary_doc = update_text_doc


def rename_folder(folder_id: str, new_name: str) -> None:
    """שינוי שם תיקיית ההקלטה ב-Drive, בעקבות עריכת כותרת מהאפליקציה."""
    drive = _drive_client()
    drive.files().update(fileId=folder_id, body={"name": new_name}).execute()


def trash_folder(folder_id: str) -> None:
    """מעביר את תיקיית ההקלטה (וכל תוכנה) לאשפה ב-Drive, בעקבות מחיקה
    מהאפליקציה. לא מוחק לצמיתות - ניתן לשחזר מהאשפה ב-Drive עד שהיא מתרוקנת."""
    drive = _drive_client()
    drive.files().update(fileId=folder_id, body={"trashed": True}).execute()


def upload_attachment(file_path: str, filename: str, mime_type: str, folder_id: str) -> str:
    """מעלה קובץ מצורף כמו שהוא (לא ממיר ל-Google Doc) לאותה תיקיית הקלטה."""
    drive = _drive_client()
    with open(file_path, "rb") as f:
        media = MediaIoBaseUpload(io.BytesIO(f.read()), mimetype=mime_type, resumable=True)
    metadata = {"name": filename, "parents": [folder_id]}
    file = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    return file["webViewLink"]
