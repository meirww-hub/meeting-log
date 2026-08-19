"""פתיחת קישור-לכל-מי-שיש-לו-אותו לקבצי הקלטות קיימות ב-Drive, שנשמרו לפני
שהשיתוף האוטומטי נוסף ליצירת קובץ (ראה services/drive.py:_share_with_link).
הרצה חד-פעמית, מהמחשב, מול backend/.env.

    cd backend
    python scripts/share_existing_recordings.py            # הדמיה בלבד - מדפיס מה יקרה
    python scripts/share_existing_recordings.py --apply     # מבצע בפועל

לא נוגע בתוכן הקבצים או ב-Firestore - רק מוסיף הרשאת קריאה ("anyone",
"reader") לכל קובץ של כל הקלטה, כולל הקלטות ישנות שנשמרו בתיקייה משלהן
(drive_folder_id) - שם משתפים את התיקייה עצמה, וההרשאה חלה על כל מה
שבתוכה.

הסקריפט חוזר על עצמו בבטחה: הוספת אותה הרשאה בשנית לא זורקת שגיאה.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError  # noqa: E402

from app.pipeline.edit import _recording_file_ids  # noqa: E402
from app.services import drive as drive_service  # noqa: E402
from app.services import firestore_store  # noqa: E402

_USER_ID = "primary_user"


def _targets(recording: dict) -> list[str]:
    folder_id = recording.get("drive_folder_id")
    return [folder_id] if folder_id else _recording_file_ids(recording)


def share_all(apply: bool) -> None:
    recordings = firestore_store.list_recordings(_USER_ID)
    print(f"{len(recordings)} הקלטות\n", flush=True)

    done, failed = 0, 0
    for recording in recordings:
        title = recording.get("title") or recording.get("date") or recording.get("recording_id")
        targets = _targets(recording)
        print(f"• {title} ({len(targets)} קבצים)", flush=True)
        if not apply:
            continue
        for file_id in targets:
            try:
                drive_service.share_existing_file(file_id)
                done += 1
            except HttpError as error:
                failed += 1
                print(f"  ! נכשל ({error.resp.status}): {file_id}")

    if apply:
        print(f"\nשותפו {done} קבצים" + (f", {failed} נכשלו" if failed else ""))
    else:
        print("\nהדמיה בלבד - הרץ שוב עם --apply כדי לבצע")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="בצע בפועל (ברירת מחדל: הדמיה)")
    share_all(parser.parse_args().apply)
