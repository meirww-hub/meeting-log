import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from app.config import settings
from app.models import ChatRequest, RecordingUpdateRequest
from app.pipeline import edit as recording_edit
from app.pipeline.attachments import process_attachment
from app.pipeline.chat import answer_question
from app.pipeline.pipeline import process_call_recording, process_recording
from app.services import firestore_store, usage_tracker

app = FastAPI(title="Meeting Log Backend")


def _run_recording_pipeline(pipeline_fn, recording_id: str, user_id: str, *args) -> None:
    """עוטף את עיבוד ההקלטה ברקע - בלי זה, חריגה (למשל Gemini עמוס) הייתה
    משאירה את ההקלטה תקועה בסטטוס האחרון שלה לנצח, בלי שום סימן שגיאה."""
    try:
        pipeline_fn(recording_id, user_id, *args)
    except Exception as e:
        firestore_store.set_recording_status(recording_id, user_id, "error", error=str(e))


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
) -> dict:
    """העלאת הקלטה לעיבוד.

    שיחת טלפון שנקלטה אוטומטית מ-cally מגיעה כשני ערוצים מבודדים: `file`
    הוא הצד שלי (uplink) ו-`file_downlink` הצד השני. במקרה כזה מתבצע תמלול
    נפרד לכל ערוץ, כך שזיהוי הדוברים ודאי. הקלטה רגילה (מיקרופון/שיתוף)
    מגיעה עם `file` בלבד ועוברת diarization כרגיל. contact_name (רלוונטי רק
    לשיחות טלפון) הוא שם איש הקשר שהאפליקציה שלפה מהיסטוריית השיחות של
    הטלפון, לתיוג ודאי של הצד השני (ראה CallImportWorker.kt).
    """
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
        )
    else:
        background_tasks.add_task(
            _run_recording_pipeline, process_recording, recording_id, user_id, str(audio_path), title
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


@app.post("/recordings/cleanup", dependencies=[Depends(require_api_key)])
def cleanup_recordings(user_id: str) -> dict:
    """מוחק אוטומטית הקלטות קצרות מ-2 דקות שלא נערכו, 48 שעות ומעלה אחרי
    יצירתן. נקרא מהאפליקציה בכל טעינה של מסך ההיסטוריה (ראה
    HistoryActivity.kt) - אין מנגנון תזמון בצד השרת."""
    deleted_ids = recording_edit.cleanup_expired_recordings(user_id)
    return {"deleted": deleted_ids}


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
    recordings = [firestore_store.get_recording(rid) for rid in payload.recording_ids]
    recordings = [r for r in recordings if r is not None]
    if not recordings:
        raise HTTPException(status_code=404, detail="no matching recordings found")
    return answer_question(recordings, payload.question)


@app.post("/recordings/{recording_id}/attachments", dependencies=[Depends(require_api_key)])
async def upload_attachments(
    recording_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user_id: str = Form(...),
) -> dict:
    """מצרף קובץ אחד או יותר לתיקיית ההקלטה ב-Drive, מסכם כל קובץ ומוסיף
    את התקציר לקובץ הסיכום הקיים. עיבוד מלא רץ ברקע (העלאת קבצים גדולים +
    קריאה ל-Gemini לוקחת זמן)."""
    if firestore_store.get_recording(recording_id) is None:
        raise HTTPException(status_code=404, detail="recording not found")

    tmp_dir = Path(tempfile.gettempdir()) / "meetingscribe_attachments"
    tmp_dir.mkdir(exist_ok=True)

    saved_filenames = []
    for file in files:
        file_path = tmp_dir / f"{uuid.uuid4()}_{file.filename}"
        with file_path.open("wb") as out_file:
            shutil.copyfileobj(file.file, out_file)
        background_tasks.add_task(
            process_attachment, recording_id, user_id, str(file_path), file.filename
        )
        saved_filenames.append(file.filename)

    return {"recording_id": recording_id, "status": "processing", "files": saved_filenames}
