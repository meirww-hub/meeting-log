"""מריץ מחדש תמלול+סיכום להקלטה קיימת שכבר נשמרה כ-"done", ומעדכן במקום את
Firestore ואת מסמכי התמלול/הסיכום ב-Drive - בלי ליצור רשומה או קבצים
חדשים. הרצה חד-פעמית, מהמחשב, מול backend/.env.

הרקע: עד 2026-08-23 בדיקת שלמות התמלול הסתמכה רק על finish_reason של
המודל (ראה transcription.py) - ולכן פגישה שהמודל "חשב" שסיים לתמלל
(finish_reason=STOP) בזמן שבפועל כיסה רק חלק מהאודיו נשמרה כ"done" עם
תמלול/סיכום חתוכים, בלי שום סימן חיצוני לכך. התיקון ב-transcription.py
מונע את זה מכאן ואילך; הסקריפט הזה מתקן הקלטה שכבר נפגעה מכך: מוריד את
קובץ האודיו המקורי מ-Drive (הוא לא נגע בו - האודיו עצמו תמיד היה שלם),
מריץ עליו את אותו פייפליין תמלול/דיארוזציה/סיכום שהעלאה רגילה מריצה, ומחליף
בו את התמלול/הסיכום הקיימים ב-Firestore ובקבצי ה-Drive (במקום, לא עותק
חדש). לא נוגע בכותרת, ב-TO DO, בקובץ האודיו עצמו או במצורפים.

    cd backend
    python scripts/reprocess_recording.py <recording_id>            # הדמיה - מדפיס ולא כותב
    python scripts/reprocess_recording.py <recording_id> --apply    # מבצע בפועל
    python scripts/reprocess_recording.py --title "התנעת פרויקט" --apply

recording_id לא תמיד ידוע מראש - חיפוש לפי כותרת (חלקית, --title) מוצא
אותו במקום זה; הסקריפט מדפיס את ההתאמות אם יש יותר מאחת.

הערה: הרצה חוזרת ממזגת שוב את טביעות הקול של הדוברים בהקלטה הזו לתוך
הפרופילים שלהם (ראה speaker_id.identify_speakers) - בדיוק כמו שהעיבוד
הראשוני כבר עשה. זו השפעת-צד קלה ולא בעייתית בפועל (עיגון חוזר לאותה
דגימה), לא סיבה להימנע מהרצה חוזרת כשצריך.
"""

import argparse
import datetime
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.pipeline import diarization, speaker_id  # noqa: E402
from app.pipeline.edit import _doc_id, _transcript_to_text, audio_file_ids  # noqa: E402
from app.pipeline.pipeline import (  # noqa: E402
    _apply_speaker_names,
    _duration_of,
    _resolve_speaker_names,
)
from app.pipeline.speakers import speakers_in_order  # noqa: E402
from app.pipeline.summarize import summarize_and_extract_todos  # noqa: E402
from app.pipeline.transcription import (  # noqa: E402
    _probe_duration_seconds,
    transcribe_with_diarization,
)
from app.services import drive as drive_service  # noqa: E402
from app.services import firestore_store  # noqa: E402

_USER_ID = "primary_user"


def _find_recording(recording_id: str | None, title: str | None) -> dict:
    if recording_id:
        recording = firestore_store.get_recording(recording_id)
        if not recording:
            sys.exit(f"לא נמצאה הקלטה עם recording_id={recording_id}")
        recording["recording_id"] = recording_id
        return recording

    needle = (title or "").strip()
    matches = [
        r for r in firestore_store.list_recordings(_USER_ID)
        if needle in (r.get("title") or "")
    ]
    if len(matches) != 1:
        print(f"{len(matches)} התאמות לכותרת '{needle}':")
        for r in matches:
            print(f"  {r.get('recording_id')}  {r.get('title')}  {r.get('date')}")
        sys.exit(0 if matches else "לא נמצאה הקלטה בשם הזה")
    return matches[0]


def _download_audio(file_id: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".m4a")
    with open(fd, "wb") as f:
        for chunk in drive_service.stream_file(file_id):
            f.write(chunk)
    return path


def reprocess(recording: dict, apply: bool) -> None:
    recording_id = recording["recording_id"]
    title = recording.get("title") or ""
    print(f"מעבד מחדש: {title} ({recording_id})", flush=True)

    file_ids = audio_file_ids(recording)
    if not file_ids:
        sys.exit("אין קובץ אודיו שמור להקלטה הזו")

    print(f"מוריד אודיו מ-Drive ({file_ids[0]})...", flush=True)
    audio_path = _download_audio(file_ids[0])

    try:
        measured_seconds = _probe_duration_seconds(audio_path)
        print(
            f"אורך שנמדד בפועל בקובץ: {measured_seconds / 60:.1f} דקות"
            if measured_seconds
            else "לא הצלחתי למדוד את אורך הקובץ (ffprobe נכשל)",
            flush=True,
        )

        print("מתמלל מחדש (יכול לקחת כמה דקות על הקלטה ארוכה)...", flush=True)
        segments = transcribe_with_diarization(audio_path)
        refined = diarization.refine_speaker_labels(segments, audio_path)
        segments = refined.segments
        profile_ids = speaker_id.identify_speakers(
            segments, _USER_ID, recording_id, audio_path, label_voices=refined.label_voices
        )

        today_iso = recording.get("date") or datetime.date.today().isoformat()
        _suggested_title, summary, todos, speaker_names = summarize_and_extract_todos(
            segments, today_iso
        )
        renames = _resolve_speaker_names(segments, speaker_names, set())
        summary = _apply_speaker_names(segments, todos, summary, renames)
        profile_ids = {renames.get(label, label): pid for label, pid in profile_ids.items()}
        speaker_id.learn_names_from_content(profile_ids, renames)

        speakers = speakers_in_order(s.speaker_label for s in segments)
        duration_seconds = _duration_of(measured_seconds or 0.0, segments)
        covered = max((s.end_seconds for s in segments), default=0.0)

        print(f"\nתמלול חדש: {len(segments)} קטעים, מכסה עד דקה {covered / 60:.1f}")
        print(f"דוברים: {', '.join(speakers) or '-'}")
        print(f"\n--- סיכום חדש ---\n{summary}\n")

        if not apply:
            print("הדמיה בלבד - הרץ שוב עם --apply כדי לעדכן את Firestore ואת Drive")
            return

        transcript_doc_id = _doc_id(recording, "drive_transcript_doc_id", "drive_transcript_url")
        summary_doc_id = _doc_id(recording, "drive_summary_doc_id", "drive_summary_url")
        segment_dicts = [s.model_dump() for s in segments]

        if transcript_doc_id:
            drive_service.update_text_doc(transcript_doc_id, _transcript_to_text(segment_dicts))
        else:
            print("! לא נמצא מסמך תמלול קיים - הטקסט לא עודכן ב-Drive")
        if summary_doc_id:
            drive_service.update_summary_doc(summary_doc_id, summary)
        else:
            print("! לא נמצא מסמך סיכום קיים - הטקסט לא עודכן ב-Drive")

        firestore_store.update_recording_fields(
            recording_id,
            transcript=segment_dicts,
            summary=summary,
            speakers=speakers,
            duration_seconds=duration_seconds,
            speaker_profile_ids=profile_ids,
        )
        print("עודכן ב-Firestore וב-Drive.")
    finally:
        pathlib.Path(audio_path).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("recording_id", nargs="?", help="מזהה ההקלטה ב-Firestore")
    parser.add_argument("--title", help="חיפוש לפי כותרת (חלקית) במקום recording_id")
    parser.add_argument("--apply", action="store_true", help="בצע בפועל (ברירת מחדל: הדמיה)")
    args = parser.parse_args()
    if not args.recording_id and not args.title:
        parser.error("צריך recording_id או --title")
    reprocess(_find_recording(args.recording_id, args.title), args.apply)
