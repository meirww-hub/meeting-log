"""ממספר הקלטות קיימות שחולקות כותרת: הוותיקה נשארת כפי שהיא, הבאות
מקבלות " 2", " 3" וכן הלאה - בשם הקבצים ב-Drive ובכותרת שבאפליקציה.

    cd backend
    python scripts/number_duplicate_titles.py            # הדמיה
    python scripts/number_duplicate_titles.py --apply    # מבצע

הקלטות חדשות ממוספרות כבר בשמירה (services/drive.py:_unique_title), אז
הסקריפט הזה נועד לכפילויות שנוצרו לפני כן - למשל שתי הקלטות של אותה שיחה
מתקופת באג הכפילויות. אפשר להריץ אותו שוב בכל עת; הוא לא נוגע בכותרת
שכבר ייחודית.
"""

import argparse
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.pipeline import edit  # noqa: E402
from app.services import drive as drive_service  # noqa: E402
from app.services import firestore_store  # noqa: E402


def _groups_sharing_a_title() -> dict[str, list[tuple]]:
    groups = collections.defaultdict(list)
    for doc in firestore_store._client().collection("recordings").stream():
        data = doc.to_dict() or {}
        title = (data.get("title") or "").strip()
        if data.get("status") == "done" and title:
            groups[title].append((data.get("created_at"), doc.id, data))
    return {title: group for title, group in groups.items() if len(group) > 1}


def number_duplicates(apply: bool) -> None:
    groups = _groups_sharing_a_title()
    print(f"{len(groups)} כותרות שחוזרות על עצמן\n")

    for title, group in groups.items():
        # הוותיקה שומרת על הכותרת הנקייה; המאוחרות ממוספרות לפי סדר יצירתן.
        group.sort(key=lambda item: (item[0] is None, item[0]))
        print(f"• {title!r} - {len(group)} הקלטות")
        for _, recording_id, data in group[1:]:
            new_title = drive_service.unique_title(title) if apply else f"{title} ?"
            file_ids = edit._recording_file_ids(data)
            print(f"    {recording_id} -> {new_title!r} ({len(file_ids)} קבצים)")
            if apply:
                drive_service.retitle_files(file_ids, title, new_title)
                firestore_store.update_recording_fields(recording_id, title=new_title)

    if not apply:
        print("\nהדמיה בלבד - הרץ שוב עם --apply כדי לבצע")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="בצע בפועל (ברירת מחדל: הדמיה)")
    number_duplicates(parser.parse_args().apply)
