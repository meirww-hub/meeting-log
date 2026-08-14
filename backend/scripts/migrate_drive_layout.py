"""העברת הקלטות קיימות ב-Drive מהמבנה הישן (תיקייה לכל פגישה) לחדש
(תיקייה לכל סוג תוצר). הרצה חד-פעמית, מהמחשב, מול backend/.env.

    cd backend
    python scripts/migrate_drive_layout.py            # הדמיה בלבד - מדפיס מה יקרה
    python scripts/migrate_drive_layout.py --apply    # מבצע בפועל

מה קורה לכל הקלטה שיש לה drive_folder_id (כלומר נשמרה במבנה הישן):
כל קובץ בתיקייה שלה עובר לתיקיית הסוג המתאימה בשורש ומקבל שם שכולל את
כותרת הפגישה, התיקייה הריקה שנשארה עוברת לאשפה, ורשומת ה-Firestore
מתעדכנת - drive_folder_id נמחק (הוא מה שמסמן "מבנה ישן" לשאר הקוד, ראה
pipeline/edit.py), ונשמרים מזהי הקבצים שנדרשים לשינוי שם ולמחיקה במבנה
החדש (drive_todo_file_id, drive_audio_file_ids).

הקישורים עצמם (drive_transcript_url וכו') לא משתנים: קישור ב-Drive נגזר
ממזהה הקובץ, והעברה או שינוי שם לא משנים אותו.

הסקריפט חוזר על עצמו בבטחה: הקלטה שכבר עברה אין לה drive_folder_id והיא
מדולגת.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import drive as drive_service  # noqa: E402
from app.services import firestore_store  # noqa: E402

# שמות הקבצים כפי שנוצרו במבנה הישן -> (תיקיית היעד, הסוג בשם החדש). כל
# שם אחר בתיקיית פגישה הוא קובץ שהמשתמש צירף בעצמו.
_KNOWN_FILES = {
    "תמלול": (drive_service.TRANSCRIPT_FOLDER, drive_service.TRANSCRIPT_FOLDER),
    "סיכום": (drive_service.SUMMARY_FOLDER, drive_service.SUMMARY_FOLDER),
    "TO DO": (drive_service.TODO_FOLDER, drive_service.TODO_FOLDER),
    "הערות": (drive_service.NOTES_FOLDER, drive_service.NOTES_FOLDER),
    "הקלטה מקורית.m4a": (drive_service.AUDIO_FOLDER, drive_service.AUDIO_FILE_KIND),
    "הקלטה - הצד השני.m4a": (drive_service.AUDIO_FOLDER, drive_service.AUDIO_FILE_KIND),
}
_SECOND_CHANNEL = "הקלטה - הצד השני.m4a"


def _target(old_name: str, title: str) -> tuple[str, str]:
    """(שם תיקיית היעד, שם הקובץ החדש) עבור קובץ מתיקיית פגישה ישנה."""
    known = _KNOWN_FILES.get(old_name)
    if known is None:
        # קובץ מצורף: שם הקובץ המקורי כבר מרמז על תוכנו, הכותרת מקדימה אותו.
        return drive_service.ATTACHMENTS_FOLDER, f"{title} - {old_name}".strip(" -")

    folder, kind = known
    name = f"{kind} - {title}".strip(" -")
    if old_name.endswith(".m4a"):
        suffix = " - הצד השני" if old_name == _SECOND_CHANNEL else ""
        name = f"{name}{suffix}.m4a"
    return folder, name


def _folder_resolver(drive):
    """מזהה תיקיית סוג, עם זיכרון - אחרת כל אחד מ-217 הקבצים היה מייצר
    שאילתת חיפוש משלו ל-Drive, וההעברה נמשכת רבע שעה במקום דקות."""
    cache: dict[str, str] = {}

    def resolve(name: str) -> str:
        if name not in cache:
            cache[name] = drive_service._type_folder_id(drive, name)
        return cache[name]

    return resolve


def _folder_children(drive, folder_id: str) -> list[dict]:
    files, page_token = [], None
    while True:
        response = (
            drive.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                pageSize=100,
            )
            .execute()
        )
        files += response.get("files", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return files


def _recordings_in_old_layout() -> list[tuple[str, dict]]:
    docs = firestore_store._client().collection("recordings").stream()
    return [
        (doc.id, data)
        for doc, data in ((doc, doc.to_dict() or {}) for doc in docs)
        if data.get("drive_folder_id")
    ]


def migrate(apply: bool) -> None:
    drive = drive_service._drive_client()
    root = (
        drive.files()
        .get(fileId=settings.drive_root_folder_id, fields="webViewLink")
        .execute()
    )
    target_folder_id = _folder_resolver(drive)
    recordings = _recordings_in_old_layout()
    print(f"{len(recordings)} הקלטות במבנה הישן\n", flush=True)

    moved_total = 0
    for recording_id, recording in recordings:
        title = (recording.get("title") or recording.get("date") or "").strip()
        folder_id = recording["drive_folder_id"]
        try:
            children = _folder_children(drive, folder_id)
        except HttpError as error:
            # תיקייה שנמחקה ידנית מ-Drive: אין מה להעביר, אבל עדיין צריך
            # לנקות את השדה כדי שההקלטה תיחשב במבנה החדש.
            print(f"  ! {title}: התיקייה לא נגישה ({error.resp.status}) - רק ניקוי השדה")
            children = []

        print(f"• {title} ({len(children)} קבצים)", flush=True)
        updates: dict = {"drive_folder_id": None, "drive_folder_url": root["webViewLink"]}
        main_audio: list[str] = []
        second_audio: list[str] = []
        for child in children:
            target_folder, new_name = _target(child["name"], title)
            print(f"    {child['name']}  ->  {target_folder}/{new_name}")
            if apply:
                drive.files().update(
                    fileId=child["id"],
                    addParents=target_folder_id(target_folder),
                    removeParents=folder_id,
                    body={"name": new_name},
                ).execute()
            if child["name"] == drive_service.TODO_FOLDER:
                updates["drive_todo_file_id"] = child["id"]
            elif child["name"] == _SECOND_CHANNEL:
                second_audio.append(child["id"])
            elif child["name"].endswith(".m4a"):
                main_audio.append(child["id"])
            moved_total += 1

        # הערוץ הראשי ראשון ברשימה, כדי שתישאר עקבית עם drive_audio_url
        # (שמצביע עליו) - ראה pipeline.py.
        if main_audio or second_audio:
            updates["drive_audio_file_ids"] = main_audio + second_audio
        if apply:
            firestore_store.update_recording_fields(recording_id, **updates)
            try:
                # דרך אותו client, ולא drive_service.trash_folder - כל קריאה
                # שם בונה חיבור חדש ומחדשת טוקן, פעם בכל הקלטה.
                drive.files().update(fileId=folder_id, body={"trashed": True}).execute()
            except HttpError as error:
                print(f"  ! התיקייה הריקה נשארה ב-Drive ({error.resp.status})")

    print(f"\nסה\"כ {moved_total} קבצים ב-{len(recordings)} הקלטות")
    if not apply:
        print("הדמיה בלבד - הרץ שוב עם --apply כדי לבצע")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="בצע בפועל (ברירת מחדל: הדמיה)")
    migrate(parser.parse_args().apply)
