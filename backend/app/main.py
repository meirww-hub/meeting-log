import shutil
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.config import settings
from app.models import ChatRequest, RecordingUpdateRequest, SpeakerProfileUpdateRequest
from app.pipeline import edit as recording_edit
from app.pipeline.attachments import mime_type_for, process_attachment, retry_attachment
from app.pipeline.chat import answer_question
from app.pipeline.pipeline import process_call_recording, process_recording
from app.services import drive, firestore_store, usage_tracker

app = FastAPI(title="Meeting Log Backend")


# כמה פעמים לנסות את העיבוד מחדש לפני שמכריזים על כישלון. הכישלונות שנצפו
# בפועל היו כולם חולפים (Gemini עמוס, חריגת מכסה, תמלול שנקטע), ועד היום כל
# אחד מהם הפיל את ההקלטה סופית בניסיון אחד - בלי שאיש יזם ניסיון שני, כי
# האפליקציה כבר מחקה את העותק המקומי ברגע שהשרת החזיר 200.
_PIPELINE_ATTEMPTS = 3
_PIPELINE_RETRY_DELAY_SECONDS = 20

# השלבים שאחרי כישלון בהם מותר להתחיל מחדש מאפס. משלב הכתיבה ל-Drive
# ואילך כבר נוצרו תיקייה ומסמכים, וריצה שנייה הייתה מייצרת עותק שני שלהם.
_RESUMABLE_STATUSES = frozenset(
    {"queued", "transcribing", "identifying_speakers", "summarizing"}
)


def _run_recording_pipeline(pipeline_fn, recording_id: str, user_id: str, *args) -> None:
    """מריץ את עיבוד ההקלטה ברקע, עם ניסיונות חוזרים.

    בלי העטיפה הזו חריגה הייתה משאירה את ההקלטה תקועה בסטטוס האחרון שלה
    לנצח, בלי שום סימן שגיאה. ובלי הניסיון החוזר, תקלה חולפת אחת הספיקה כדי
    לאבד שיחה שלמה: זה מה שקרה ב-2026-08-13, כשתמלול שנקטע הפיל שיחה בת 28
    דקות - ולא היה שום גורם שינסה שוב.
    """
    for attempt in range(_PIPELINE_ATTEMPTS):
        try:
            pipeline_fn(recording_id, user_id, *args)
            return
        except Exception as e:
            record = firestore_store.get_recording(recording_id) or {}
            reached = record.get("status", "queued")
            last_attempt = attempt == _PIPELINE_ATTEMPTS - 1
            # כישלון אחרי שהתחילה כתיבה ל-Drive לא נוסה שוב: הריצה הבאה
            # הייתה יוצרת תיקייה ומסמכים כפולים.
            if last_attempt or reached not in _RESUMABLE_STATUSES:
                firestore_store.set_recording_status(
                    recording_id, user_id, "error", error=str(e)
                )
                return
            time.sleep(_PIPELINE_RETRY_DELAY_SECONDS)


def recording_id_for_upload(user_id: str, client_upload_id: str) -> str:
    """מזהה ההקלטה שנגזר ממזהה המקור שהאפליקציה שלחה.

    אותו מקור (אותה שיחה, אותו קובץ פגישה) נותן תמיד את אותו recording_id,
    ולכן העלאה חוזרת שלו נופלת על מסמך קיים ולא יוצרת רשומה שנייה. זה מה
    שהופך את ההעלאה ל-idempotent: העלאה שהשרת קלט אבל האפליקציה לא הספיקה
    לראות את התשובה עליה (טיימאאוט/נפילת רשת) נשלחת שוב ע"י WorkManager,
    וללא הגזירה הזו כל ניסיון כזה היה מייצר הקלטה כפולה.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"meetinglog:{user_id}:{client_upload_id}"))


def require_api_key(x_api_key: str = Header(default="")) -> None:
    """השרת פרוס פומבית (Cloud Run) - מוודאים שהקריאה מגיעה מהאפליקציה
    ולא מכל האינטרנט, ע"י כותרת שיתופית פשוטה. /health פתוח ללא הגנה."""
    if not settings.backend_api_key or x_api_key != settings.backend_api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/usage", dependencies=[Depends(require_api_key)])
def get_usage() -> dict:
    """שימוש יומי ב-Firestore מול המכסה החינמית (reads/writes/deletes)."""
    return usage_tracker.get_today_usage()


@app.post("/recordings", dependencies=[Depends(require_api_key)])
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    file_downlink: UploadFile | None = File(None),
    title: str = Form(""),
    user_id: str = Form(...),
    contact_name: str = Form(""),
    client_upload_id: str = Form(""),
    duration_seconds: float = Form(0.0),
) -> dict:
    """העלאת הקלטה לעיבוד.

    שיחת טלפון שנקלטה אוטומטית מ-cally מגיעה כשני ערוצים מבודדים: `file`
    הוא הצד שלי (uplink) ו-`file_downlink` הצד השני. במקרה כזה מתבצע תמלול
    נפרד לכל ערוץ, כך שזיהוי הדוברים ודאי. הקלטה רגילה (מיקרופון/שיתוף)
    מגיעה עם `file` בלבד ועוברת diarization כרגיל. contact_name (רלוונטי רק
    לשיחות טלפון) הוא שם איש הקשר שהאפליקציה שלפה מהיסטוריית השיחות של
    הטלפון, לתיוג ודאי של הצד השני (ראה CallImportWorker.kt).

    client_upload_id הוא מזהה יציב של מקור ההקלטה (מפתח השיחה אצל cally, או
    תיקיית ה-session ושם הקובץ בהקלטת פגישה). העלאה שנייה של אותו מקור מזוהה
    כאן ומוחזרת כמות שהיא, בלי רשומה נוספת ובלי עיבוד חוזר - ראה
    recording_id_for_upload.

    duration_seconds הוא אורך האודיו כפי שנמדד בטלפון לפני ההעלאה (ראה
    AudioDuration.kt). בלעדיו האורך נגזר מסוף הדיבור האחרון בתמלול - קירוב
    שמעוות כל הקלטה עם שתיקה בסופה. ראה pipeline._duration_of.
    """
    if client_upload_id:
        recording_id = recording_id_for_upload(user_id, client_upload_id)
        existing = firestore_store.get_recording(recording_id)
        # הקלטה שהעיבוד שלה נכשל היא היחידה שמותר לשלוח שוב: המסמך שלה קיים,
        # ולכן עד היום כל העלאה חוזרת נענתה ב-"duplicate" - כלומר שיחה שנפלה
        # פעם אחת נתקעה ב-error לנצח, בלי שום דרך (לא מהאפליקציה ולא ביד)
        # להריץ אותה מחדש. היא גם לא מופיעה בהיסטוריה, שמציגה רק "done", אז
        # היא פשוט נעלמה. עכשיו העלאה חוזרת מפעילה את העיבוד שוב על אותו
        # מזהה, כך שאין גם כפילות.
        if existing is not None and existing.get("status") != "error":
            return {
                "recording_id": recording_id,
                "status": existing.get("status", "queued"),
                "duplicate": True,
            }
    else:
        recording_id = str(uuid.uuid4())

    tmp_dir = Path(tempfile.gettempdir()) / "meetingscribe"
    tmp_dir.mkdir(exist_ok=True)
    audio_path = tmp_dir / f"{recording_id}_{file.filename}"

    with audio_path.open("wb") as out_file:
        shutil.copyfileobj(file.file, out_file)

    firestore_store.set_recording_status(recording_id, user_id, "queued")

    if file_downlink is not None:
        downlink_path = tmp_dir / f"{recording_id}_downlink_{file_downlink.filename}"
        with downlink_path.open("wb") as out_file:
            shutil.copyfileobj(file_downlink.file, out_file)
        background_tasks.add_task(
            _run_recording_pipeline,
            process_call_recording,
            recording_id,
            user_id,
            str(audio_path),
            str(downlink_path),
            title,
            contact_name,
            duration_seconds,
        )
    else:
        background_tasks.add_task(
            _run_recording_pipeline,
            process_recording,
            recording_id,
            user_id,
            str(audio_path),
            title,
            duration_seconds,
        )

    return {"recording_id": recording_id, "status": "queued"}


@app.get("/recordings/{recording_id}", dependencies=[Depends(require_api_key)])
def get_recording_status(recording_id: str) -> dict:
    record = firestore_store.get_recording(recording_id)
    if record is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return record


@app.get("/recordings", dependencies=[Depends(require_api_key)])
def list_recordings(user_id: str) -> list[dict]:
    """כל ההקלטות שהושלמו, לצורך מסך ההיסטוריה באפליקציה. הסינון
    (כותרת/תאריך/דובר) מתבצע בצד האפליקציה על הרשימה המלאה - נפח הנתונים
    האישי קטן מכדי שיהיה צורך בסינון בצד השרת."""
    return firestore_store.list_recordings(user_id)


@app.patch("/recordings/{recording_id}", dependencies=[Depends(require_api_key)])
def update_recording(recording_id: str, payload: RecordingUpdateRequest) -> dict:
    """עריכת כותרת/דוברים/הערה מהאפליקציה. כל שדה בגוף הבקשה שנשלח (לא None)
    מתעדכן גם ב-Firestore וגם ב-Drive (שם התיקייה/קובץ התמלול/קובץ הערות)."""
    recording = firestore_store.get_recording(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return recording_edit.apply_update(recording_id, recording, payload)


@app.get("/speaker-profiles", dependencies=[Depends(require_api_key)])
def list_speaker_profiles(user_id: str) -> list[dict]:
    """כל פרופילי הדוברים שזוהו לפי טביעת קול - מתויגים ולא-מתויגים כאחד -
    למסך פרופילי הדוברים באפליקציה, גם לתיוג ראשוני וגם לתיקון שם קיים.
    כל פרופיל הוא קול אחד שנצבר על פני הקלטות (ראה pipeline/speaker_id.py),
    לא שורה לכל הקלטה - אותו דובר שחוזר בכמה הקלטות מופיע כאן פעם אחת
    בלבד. name הוא null כל עוד לא תויג. recording_id/channel/start_seconds
    מצביעים על דגימת שמע להשמעה - אותו מסלול הזרמה כמו /recordings/{id}/audio."""
    profiles = firestore_store.list_speaker_profiles(user_id)
    return [
        {
            "profile_id": p["profile_id"],
            "name": p.get("name"),
            "recording_id": p["sample_recording_id"],
            "channel": p["sample_channel"],
            "start_seconds": p["sample_start_seconds"],
        }
        for p in profiles
    ]


@app.patch("/speaker-profiles/{profile_id}", dependencies=[Depends(require_api_key)])
def name_speaker_profile(profile_id: str, payload: SpeakerProfileUpdateRequest) -> dict:
    """מתייג פרופיל דובר בשם, או מתקן שם שכבר קיים לו. חל רק מהיום והלאה -
    הקלטות שכבר נשמרו לא נסרקות ולא מתעדכנות (הוחלט במפורש; ראה speaker_id.py)."""
    if firestore_store.get_speaker_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="speaker profile not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    firestore_store.update_speaker_profile(profile_id, name=name)
    return {"profile_id": profile_id, "name": name}


@app.post("/recordings/cleanup", dependencies=[Depends(require_api_key)])
def cleanup_recordings(user_id: str) -> dict:
    """שתי תחזוקות שוטפות, שתיהן נקראות מהאפליקציה בכל טעינה של מסך
    ההיסטוריה (ראה HistoryActivity.kt) - אין מנגנון תזמון בצד השרת:

    1. מוחק הקלטות קצרות מ-2 דקות שלא נערכו, 48 שעות ומעלה אחרי יצירתן.
    2. מסמן כ"error" הקלטות שנתקעו בסטטוס ביניים יותר מ-30 דקות - תהליך
       שהומת מבחוץ (למשל Cloud Run שחרג ממכסת זיכרון) לא זורק חריגה
       שהניסיון החוזר הרגיל תופס, ובלי הבדיקה הזו הקלטה כזו נשארת קפואה
       לנצח בלי סימן. ראה recover_stale_recordings.
    """
    deleted_ids = recording_edit.cleanup_expired_recordings(user_id)
    recovered_ids = recording_edit.recover_stale_recordings(user_id)
    return {"deleted": deleted_ids, "recovered": recovered_ids}


@app.delete("/recordings/{recording_id}", dependencies=[Depends(require_api_key)])
def delete_recording(recording_id: str) -> dict:
    """מחיקת הקלטה: מעביר את תיקיית ה-Drive שלה לאשפה (ניתן לשחזור) ומוחק
    את הרשומה ב-Firestore."""
    recording = firestore_store.get_recording(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="recording not found")
    recording_edit.delete_recording(recording_id, recording)
    return {"recording_id": recording_id, "status": "deleted"}


@app.post("/chat", dependencies=[Depends(require_api_key)])
def chat(payload: ChatRequest) -> dict:
    """שאלה חופשית על תמלולי הקלטות עבר, עם ציטוט דקה:שנייה + שם ההקלטה."""
    recordings = []
    for recording_id in payload.recording_ids:
        record = firestore_store.get_recording(recording_id)
        if record is not None:
            # get_recording מחזיר את גוף המסמך בלבד; בלי המזהה כאן הצ'אט לא
            # יכול להחזיר ציטוט שאפשר לנגן ממנו (ראה pipeline/chat.py).
            recordings.append({**record, "recording_id": recording_id})
    if not recordings:
        raise HTTPException(status_code=404, detail="no matching recordings found")
    return answer_question(recordings, payload.question)


def _range_start(range_header: str) -> int:
    """הבית הראשון שהתבקש בכותרת Range ("bytes=1024-"), או 0 אם אין כזו.

    רק המשך הורדה מנקודה מסוימת נתמך (הצורה שהאפליקציה שולחת כשהורדה
    נקטעה באמצע); סוף טווח מפורש נענה עד סוף הקובץ, וזה מותר לפי
    התקן - הלקוח קורא בדיוק כמה שביקש.
    """
    prefix = "bytes="
    if not range_header.startswith(prefix):
        return 0
    start = range_header[len(prefix) :].split("-", 1)[0].strip()
    return int(start) if start.isdigit() else 0


@app.get("/recordings/{recording_id}/audio", dependencies=[Depends(require_api_key)])
def get_recording_audio(
    recording_id: str,
    channel: int = 0,
    range_header: str = Header(default="", alias="Range"),
) -> Response:
    """מזרים את קובץ האודיו של ההקלטה לנגן שבתוך האפליקציה.

    בלי המסלול הזה אפשר להגיע לאודיו רק דרך Drive בדפדפן - שם אין דרך
    לקפוץ לדקה מסוימת, וזו כל הנקודה: מסך הצ'אט מצטט "בדקה 2:21", והמשתמש
    רוצה לשמוע בדיוק את הרגע הזה (ראה ChatActivity.kt ו-AudioCache.kt).

    התשובה נשלחת תמיד ב-chunked (בלי Content-Length): Cloud Run חותך תשובה
    "רגילה" הגדולה מ-32MiB, וזה בדיוק הגודל של פגישה ארוכה - אותו קיר שכבר
    בלע כאן הקלטות שלמות בכיוון ההעלאה. הגודל המלא נשלח בכותרת X-Audio-Size
    כדי שהאפליקציה תוכל להציג התקדמות ולזהות הורדה שנקטעה.
    """
    recording = firestore_store.get_recording(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="recording not found")

    file_ids = recording_edit.audio_file_ids(recording)
    if channel < 0 or channel >= len(file_ids):
        raise HTTPException(status_code=404, detail="audio channel not found")
    file_id = file_ids[channel]

    size = drive.file_size(file_id)
    start = _range_start(range_header) if size is not None else 0

    headers = {"Accept-Ranges": "bytes"}
    if size is not None:
        headers["X-Audio-Size"] = str(size)

    status_code = 200
    if start > 0:
        if start >= size:
            return Response(
                status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
            )
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{size - 1}/{size}"

    return StreamingResponse(
        drive.stream_file(file_id, start),
        status_code=status_code,
        headers=headers,
        media_type="audio/mp4",
    )


# מגבלה משלנו על גודל מצורף בודד, נבדקת אחרי הכתיבה לדיסק הזמני. Cloud
# Run כבר חוסם בקשה שלמה מעל 32MiB לפני שהקוד שלנו בכלל רץ (413 גולמי,
# בלתי אפשרי ליירט), אבל זו לא ההגנה שרלוונטית כאן: כמה קבצים קטנים
# יכולים להצטרף לבקשה אחת מתחת ל-32MiB, ועדיין כל אחד מהם צריך להישאר
# בגבולות שה-API של Gemini/Drive מוכן לעבד. המגבלה כאן משאירה מרווח נוח
# מתחת לתקרת Cloud Run, כדי שהשגיאה שהמשתמש רואה תהיה שלנו בעברית - לא
# 413 גולמי מה-proxy.
_MAX_ATTACHMENT_UPLOAD_BYTES = 25 * 1024 * 1024


@app.post("/recordings/{recording_id}/attachments", dependencies=[Depends(require_api_key)])
async def upload_attachments(
    recording_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> dict:
    """מצרף קובץ אחד או יותר להקלטה: מעלה כל קובץ ל-Drive, מסכם אותו
    ומשלב את התקציר לתוך הסיכום הקיים לפי הקשר (ראה pipeline/attachments.py).

    כל קובץ נכתב מיד לרשימת attachments עם status="processing", לפני
    שהעיבוד ברקע בכלל התחיל - כך שהאפליקציה יכולה להציג אותו (ואת הכישלון,
    אם קרה) בלי לחכות שהעיבוד יסתיים ובלי שהקובץ ייעלם בשקט אם השרת קרס
    באמצע. עיבוד מלא רץ ברקע כי העלאת קבצים גדולים + קריאה ל-Gemini לוקחת
    זמן."""
    if firestore_store.get_recording(recording_id) is None:
        raise HTTPException(status_code=404, detail="recording not found")

    tmp_dir = Path(tempfile.gettempdir()) / "meetingscribe_attachments"
    tmp_dir.mkdir(exist_ok=True)

    entries: list[dict] = []
    saved_filenames = []
    for file in files:
        attachment_id = str(uuid.uuid4())
        mime_type = mime_type_for(file.filename or "", file.content_type or "")
        file_path = tmp_dir / f"{attachment_id}_{file.filename}"
        with file_path.open("wb") as out_file:
            shutil.copyfileobj(file.file, out_file)

        if file_path.stat().st_size > _MAX_ATTACHMENT_UPLOAD_BYTES:
            file_path.unlink(missing_ok=True)
            entries.append(
                {
                    "attachment_id": attachment_id,
                    "filename": file.filename,
                    "mime_type": mime_type,
                    "status": "error",
                    "error": (
                        f"הקובץ גדול מ-{_MAX_ATTACHMENT_UPLOAD_BYTES // (1024 * 1024)}MB"
                    ),
                }
            )
            continue

        entries.append(
            {
                "attachment_id": attachment_id,
                "filename": file.filename,
                "mime_type": mime_type,
                "status": "processing",
            }
        )
        background_tasks.add_task(
            process_attachment, recording_id, attachment_id, str(file_path), file.filename, mime_type
        )
        saved_filenames.append(file.filename)

    firestore_store.add_attachments(recording_id, entries)

    return {"recording_id": recording_id, "status": "processing", "files": saved_filenames}


@app.post(
    "/recordings/{recording_id}/attachments/{attachment_id}/retry",
    dependencies=[Depends(require_api_key)],
)
def retry_attachment_endpoint(
    recording_id: str, attachment_id: str, background_tasks: BackgroundTasks
) -> dict:
    """מנסה שוב לסכם מצורף שנכשל, בלי לבקש מהמשתמש לצרף אותו מחדש - הקובץ
    כבר שמור ב-Drive מאז ההעלאה הראשונה (ראה pipeline/attachments.py)."""
    recording = firestore_store.get_recording(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="recording not found")

    attachment = next(
        (a for a in recording.get("attachments") or [] if a.get("attachment_id") == attachment_id),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    if not attachment.get("drive_file_id"):
        # הקובץ המקורי מעולם לא הגיע ל-Drive (למשל השרת קרס באמצע ההעלאה
        # הראשונה עצמה) - אין ממה לנסות שוב, המשתמש צריך לצרף מחדש.
        raise HTTPException(
            status_code=409, detail="original file was never uploaded to Drive; re-attach it"
        )

    firestore_store.update_attachment(recording_id, attachment_id, status="processing", error=None)
    background_tasks.add_task(
        retry_attachment,
        recording_id,
        attachment_id,
        attachment["drive_file_id"],
        attachment.get("drive_url", ""),
        attachment.get("filename", ""),
        attachment.get("mime_type", "application/octet-stream"),
    )
    return {"recording_id": recording_id, "attachment_id": attachment_id, "status": "processing"}


@app.delete(
    "/recordings/{recording_id}/attachments/{attachment_id}",
    dependencies=[Depends(require_api_key)],
)
def delete_attachment(recording_id: str, attachment_id: str) -> dict:
    """מסיר מצורף בודד: מוחק אותו מרשימת attachments ומעביר את הקובץ
    ב-Drive לאשפה (אם כבר הועלה). לא נוגע בסיכום שכבר שולב - הסרת השילוב
    הטקסטואלי מהסיכום דורשת קריאה חוזרת ל-Gemini על כל המצורפים שנותרו,
    ומעבר לסקופ הזה; המשתמש שמוחק מצורף מקבל את הקובץ בחזרה מהרשימה, לא
    עריכה אוטומטית של הסיכום שכבר נכתב."""
    removed = firestore_store.remove_attachment(recording_id, attachment_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    if removed.get("drive_file_id"):
        drive.trash_files([removed["drive_file_id"]])
    return {"recording_id": recording_id, "attachment_id": attachment_id, "status": "deleted"}
