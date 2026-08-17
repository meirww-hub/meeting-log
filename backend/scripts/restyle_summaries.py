"""עיצוב מחדש של קובצי הסיכום הקיימים ב-Drive עם _summary_to_rtl_html
(כותרות נושא, ריווח, הדגשת נתונים - ראה services/drive.py). הרצה חד-פעמית,
מהמחשב, מול backend/.env.

    cd backend
    python scripts/restyle_summaries.py            # הדמיה בלבד - מדפיס מה יקרה
    python scripts/restyle_summaries.py --apply    # מבצע בפועל

לא נוגע בתוכן הסיכום או ב-Firestore - קורא רק את הטקסט שכבר שמור שם ומעלה
אותו מחדש ל-Drive באותו doc_id, דרך אותו נתיב שעריכת שמות דוברים כבר
משתמשת בו (drive.update_summary_doc). מזהה המסמך נופל לחילוץ מה-URL
השמור בהקלטות ישנות (ראה pipeline/edit.py:_doc_id) - אותה סיבה בדיוק.

הסקריפט חוזר על עצמו בבטחה: מסמך שכבר מעוצב מקבל בדיוק אותו HTML בשנית.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError  # noqa: E402

from app.services import drive as drive_service  # noqa: E402
from app.services import firestore_store  # noqa: E402

_USER_ID = "primary_user"


def _doc_id(recording: dict) -> str | None:
    return recording.get("drive_summary_doc_id") or drive_service.file_id_from_url(
        recording.get("drive_summary_url")
    )


def restyle(apply: bool) -> None:
    recordings = firestore_store.list_recordings(_USER_ID)
    candidates = [
        (r.get("recording_id"), r.get("title") or r.get("date") or "", _doc_id(r), r["summary"])
        for r in recordings
        if (r.get("summary") or "").strip() and _doc_id(r)
    ]
    print(f"{len(candidates)} מתוך {len(recordings)} הקלטות עם סיכום ומסמך ב-Drive\n", flush=True)

    done, failed = 0, 0
    for recording_id, title, doc_id, summary in candidates:
        print(f"• {title} ({doc_id})", flush=True)
        if not apply:
            continue
        try:
            drive_service.update_summary_doc(doc_id, summary)
            done += 1
        except HttpError as error:
            failed += 1
            print(f"  ! נכשל ({error.resp.status}): {recording_id}")

    if apply:
        print(f"\nעוצבו מחדש {done} מסמכים" + (f", {failed} נכשלו" if failed else ""))
    else:
        print("\nהדמיה בלבד - הרץ שוב עם --apply כדי לבצע")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="בצע בפועל (ברירת מחדל: הדמיה)")
    restyle(parser.parse_args().apply)
