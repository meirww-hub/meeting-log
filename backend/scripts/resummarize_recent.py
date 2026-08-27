"""מריץ מחדש את הסיכום (summarize_and_extract_todos) על ההקלטות האחרונות,
עם הפרומפט המעודכן שאוסר ציון שם דובר (ראה pipeline/summarize.py). הרצה
חד-פעמית/בדיקתית, מהמחשב, מול backend/.env.

    cd backend
    python scripts/resummarize_recent.py              # 2 ההקלטות האחרונות, הדמיה בלבד
    python scripts/resummarize_recent.py --count 5     # מספר הקלטות אחר
    python scripts/resummarize_recent.py --apply       # מבצע בפועל (Firestore + Drive)

בהדמיה מדפיס את הסיכום הישן מול החדש לכל הקלטה, בלי לגעת בכלום. עם --apply
מעדכן את שדה summary ב-Firestore ואת קובץ הסיכום ב-Drive (update_summary_doc)
- לא נוגע ב-todos ובקובץ ה-todo (ראה edit.py לגבי מה עוד תלוי ב-summary).
"""

import argparse
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.models import TranscriptSegment  # noqa: E402
from app.pipeline.summarize import summarize_and_extract_todos  # noqa: E402
from app.services import drive as drive_service  # noqa: E402
from app.services import firestore_store  # noqa: E402

_USER_ID = "primary_user"


def _doc_id(recording: dict) -> str | None:
    return recording.get("drive_summary_doc_id") or drive_service.file_id_from_url(
        recording.get("drive_summary_url")
    )


def _recent_done_recordings(count: int) -> list[dict]:
    recordings = [
        r for r in firestore_store.list_recordings(_USER_ID) if r.get("status") == "done"
    ]
    recordings.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return recordings[:count]


def resummarize(count: int, apply: bool) -> None:
    recordings = _recent_done_recordings(count)
    print(f"{len(recordings)} הקלטות אחרונות\n", flush=True)

    for recording in recordings:
        recording_id = recording.get("recording_id")
        title = recording.get("title") or recording.get("date") or recording_id
        raw_segments = recording.get("transcript") or []
        doc_id = _doc_id(recording)

        print(f"=== {title} ({recording_id}) ===")
        if not raw_segments:
            print("  אין תמלול שמור - מדלג\n")
            continue

        segments = [TranscriptSegment(**s) for s in raw_segments]
        today_iso = recording.get("date") or datetime.date.today().isoformat()
        _, new_summary, _ = summarize_and_extract_todos(segments, today_iso)

        old_summary = recording.get("summary") or ""
        print("--- ישן ---")
        print(old_summary.strip() or "(ריק)")
        print("--- חדש ---")
        print(new_summary.strip())
        print()

        if not apply:
            continue
        firestore_store.update_recording_fields(recording_id, summary=new_summary)
        if doc_id:
            drive_service.update_summary_doc(doc_id, new_summary)
        else:
            print("  ! אין doc_id בדרייב - עודכן רק ב-Firestore")

    if apply:
        print("בוצע.")
    else:
        print("הדמיה בלבד - הרץ שוב עם --apply כדי לעדכן בפועל")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=2, help="כמה הקלטות אחרונות (ברירת מחדל 2)")
    parser.add_argument("--apply", action="store_true", help="בצע בפועל (ברירת מחדל: הדמיה)")
    args = parser.parse_args()
    resummarize(args.count, args.apply)
