"""המבנה של הקבצים ב-Drive: תיקייה לכל סוג תוצר, ושם קובץ שנושא את הסוג
ואת כותרת הפגישה.

הבדיקות רצות מול כפיל של Drive/Sheets בזיכרון - בלי רשת, בלי חשבון ובלי
עלות. מה שנבדק כאן הוא בדיוק מה שהמשתמש רואה בדרייב: לאן כל קובץ הולך,
איך הוא נקרא, ומה קורה לכולם יחד כשהכותרת משתנה או שההקלטה נמחקת.
"""

import re

import pytest

from app.config import settings
from app.models import MeetingResult, RecordingUpdateRequest, TodoItem, TranscriptSegment
from app.pipeline import edit
from app.services import drive as drive_service

_FOLDER_MIME = "application/vnd.google-apps.folder"

_NAME_IN_QUERY = re.compile(r"name = '([^']*)'")
_PARENT_IN_QUERY = re.compile(r"'([^']*)' in parents")


class _Request:
    """מה ש-google-api-client מחזיר לפני execute()."""

    def __init__(self, result: dict):
        self._result = result

    def execute(self) -> dict:
        return self._result


class FakeDrive:
    """drive.files() מעל מילון בזיכרון, עם התמיכה המינימלית בשאילתות
    שהקוד שלנו באמת מנסח (שם + הורה + mimeType + trashed)."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self._counter = 0
        self.add("root", _FOLDER_MIME, [])

    def add(self, name: str, mime_type: str, parents: list[str]) -> str:
        self._counter += 1
        file_id = "root" if self._counter == 1 else f"id{self._counter}"
        self.items[file_id] = {
            "id": file_id,
            "name": name,
            "mimeType": mime_type,
            "parents": list(parents),
            "trashed": False,
            "order": self._counter,
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
        }
        return file_id

    def by_name(self, name: str) -> list[dict]:
        return [f for f in self.items.values() if f["name"] == name and not f["trashed"]]

    def folder_of(self, file_name: str) -> str:
        """שם התיקייה שבה יושב הקובץ - מה שמעניין בבדיקות."""
        [file] = self.by_name(file_name)
        return self.items[file["parents"][0]]["name"]

    def files(self):
        return self

    def permissions(self):
        return _FakePermissions(self)

    def create(self, body, media_body=None, fields=None):
        file_id = self.add(body["name"], body.get("mimeType", ""), body.get("parents", []))
        return _Request(self.items[file_id])

    def get(self, fileId, fields=None):
        return _Request(self.items[fileId])

    def update(self, fileId, body=None, media_body=None, addParents=None, removeParents=None, fields=None):
        item = self.items[fileId]
        for key in ("name", "trashed"):
            if body and key in body:
                item[key] = body[key]
        if removeParents:
            item["parents"] = [p for p in item["parents"] if p not in removeParents.split(",")]
        if addParents:
            item["parents"].append(addParents)
        return _Request(item)

    def list(self, q, fields=None, orderBy=None, pageSize=None, pageToken=None):
        name = _NAME_IN_QUERY.search(q)
        parent = _PARENT_IN_QUERY.search(q)
        matches = [
            f
            for f in self.items.values()
            if (not name or f["name"] == name.group(1))
            and (not parent or parent.group(1) in f["parents"])
            and (_FOLDER_MIME not in q or f["mimeType"] == _FOLDER_MIME)
            and not f["trashed"]
        ]
        matches.sort(key=lambda f: f["order"])
        return _Request({"files": matches})


class _FakePermissions:
    """drive.permissions() - רק רושם שהקובץ שותף, כדי שאפשר יהיה לבדוק את
    זה בלי לפנות לרשת."""

    def __init__(self, drive: "FakeDrive"):
        self._drive = drive

    def create(self, fileId, body=None, fields=None):
        self._drive.items[fileId].setdefault("permissions", []).append(body)
        return _Request({"id": "perm"})


class FakeSheets:
    """spreadsheets().create() רק רושם קובץ ב-Drive; העיצוב עצמו (צבעים,
    גאנט) לא מעניין את הבדיקות האלה."""

    def __init__(self, drive: FakeDrive):
        self._drive = drive

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def create(self, body, fields=None):
        file_id = self._drive.add(
            body["properties"]["title"], "application/vnd.google-apps.spreadsheet", []
        )
        return _Request({"spreadsheetId": file_id})

    def batchUpdate(self, spreadsheetId, body):
        return _Request({})


@pytest.fixture
def fake_drive(monkeypatch, tmp_path):
    drive = FakeDrive()
    settings.drive_root_folder_id = "root"
    monkeypatch.setattr(drive_service, "_drive_client", lambda: drive)
    monkeypatch.setattr(drive_service, "_sheets_client", lambda: FakeSheets(drive))
    return drive


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "recording.m4a"
    path.write_bytes(b"audio")
    return str(path)


def meeting(title: str) -> MeetingResult:
    return MeetingResult(
        title=title,
        date="2026-08-13",
        transcript=[
            TranscriptSegment(
                speaker_label="דני", speaker_tag=1, text="נתחיל", start_seconds=0.0, end_seconds=2.0
            )
        ],
        summary="סיכום הפגישה",
        todos=[TodoItem(description="לשלוח הצעת מחיר", owner="דני", due_date="2026-08-20")],
    )


def test_each_file_goes_to_its_type_folder_named_with_the_title(fake_drive, audio_file):
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)

    assert fake_drive.folder_of("סיכום - ישיבת צוות") == "סיכום"
    assert fake_drive.folder_of("תמלול - ישיבת צוות") == "תמלול"
    assert fake_drive.folder_of("TO DO - ישיבת צוות") == "TO DO"
    assert fake_drive.folder_of("הקלטה - ישיבת צוות.m4a") == "הקלטות"


def test_every_file_is_opened_to_anyone_with_the_link(fake_drive, audio_file):
    """קישור שנשלח בוואטסאפ צריך להיפתח ישירות, בלי שהמשתמש יאשר בקשת
    גישה לכל נמען בנפרד - ראה _share_with_link."""
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    drive_service.create_note_doc("ישיבת צוות", "לא לשכוח")
    drive_service.upload_attachment(b"pdf", "חשבונית.pdf", "application/pdf", "ישיבת צוות")

    for name in (
        "סיכום - ישיבת צוות",
        "תמלול - ישיבת צוות",
        "TO DO - ישיבת צוות",
        "הקלטה - ישיבת צוות.m4a",
        "הערות - ישיבת צוות",
        "ישיבת צוות - חשבונית.pdf",
    ):
        [file] = fake_drive.by_name(name)
        assert file.get("permissions") == [{"type": "anyone", "role": "reader"}], name

    # תיקיית השורש, שמאגדת את כל הפגישות יחד, לא משותפת - אחרת קישור
    # לפגישה אחת היה חושף את כל הארכיון.
    assert "permissions" not in fake_drive.items["root"]


def test_type_folders_sit_directly_under_the_library_root(fake_drive, audio_file):
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)

    for folder in ("סיכום", "תמלול", "TO DO", "הקלטות"):
        [item] = fake_drive.by_name(folder)
        assert item["parents"] == ["root"], f"{folder} לא יושבת בשורש"


def test_no_folder_is_created_per_meeting(fake_drive, audio_file):
    """המבנה הישן ("2026-08-13 - ישיבת צוות") לא נוצר יותר."""
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)

    folders = {f["name"] for f in fake_drive.items.values() if f["mimeType"] == _FOLDER_MIME}
    assert folders == {"root", "סיכום", "תמלול", "TO DO", "הקלטות"}


def test_second_meeting_reuses_the_same_type_folders(fake_drive, audio_file):
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    drive_service.save_meeting_to_drive(meeting("שיחה עם דני"), audio_file)

    assert len(fake_drive.by_name("סיכום")) == 1
    assert fake_drive.folder_of("סיכום - ישיבת צוות") == "סיכום"
    assert fake_drive.folder_of("סיכום - שיחה עם דני") == "סיכום"


def test_call_second_channel_keeps_the_title_and_its_own_label(fake_drive, audio_file):
    drive_service.save_meeting_to_drive(
        meeting("שיחה עם דני"), audio_file, extra_audio=[(audio_file, "הצד השני")]
    )

    assert fake_drive.folder_of("הקלטה - שיחה עם דני.m4a") == "הקלטות"
    assert fake_drive.folder_of("הקלטה - שיחה עם דני - הצד השני.m4a") == "הקלטות"


def test_both_audio_channels_are_returned_for_later_rename_and_delete(fake_drive, audio_file):
    links = drive_service.save_meeting_to_drive(
        meeting("שיחה עם דני"), audio_file, extra_audio=[(audio_file, "הצד השני")]
    )

    assert len(links["audio_file_ids"]) == 2
    # הראשון ברשימה הוא זה ש-drive_audio_url מצביע עליו.
    assert links["audio_file_ids"][0] in links["audio_url"]


def test_note_and_attachment_get_their_own_type_folders(fake_drive, audio_file, tmp_path):
    drive_service.create_note_doc("ישיבת צוות", "לא לשכוח לעדכן את הלקוח")
    drive_service.upload_attachment(b"pdf", "חשבונית.pdf", "application/pdf", "ישיבת צוות")

    assert fake_drive.folder_of("הערות - ישיבת צוות") == "הערות"
    assert fake_drive.folder_of("ישיבת צוות - חשבונית.pdf") == "קבצים מצורפים"


def test_upload_attachment_returns_id_and_url(fake_drive):
    result = drive_service.upload_attachment(b"pdf", "חשבונית.pdf", "application/pdf", "ישיבת צוות")

    [file] = fake_drive.by_name("ישיבת צוות - חשבונית.pdf")
    assert result["id"] == file["id"]
    assert result["url"] == file["webViewLink"]


def test_second_meeting_with_the_same_title_is_numbered(fake_drive, audio_file):
    drive_service.save_meeting_to_drive(meeting("שיחת תיאום קצרה"), audio_file)
    links = drive_service.save_meeting_to_drive(meeting("שיחת תיאום קצרה"), audio_file)

    assert links["title"] == "שיחת תיאום קצרה 2"
    assert fake_drive.by_name("סיכום - שיחת תיאום קצרה 2")
    assert fake_drive.by_name("תמלול - שיחת תיאום קצרה 2")
    assert fake_drive.by_name("TO DO - שיחת תיאום קצרה 2")
    assert fake_drive.by_name("הקלטה - שיחת תיאום קצרה 2.m4a")


def test_numbering_continues_past_two(fake_drive, audio_file):
    titles = [
        drive_service.save_meeting_to_drive(meeting("שיחת תיאום קצרה"), audio_file)["title"]
        for _ in range(3)
    ]

    assert titles == ["שיחת תיאום קצרה", "שיחת תיאום קצרה 2", "שיחת תיאום קצרה 3"]


def test_the_number_is_the_same_in_every_folder(fake_drive, audio_file):
    """המספר נקבע פעם אחת לפגישה - אחרת הסיכום היה "2" והתמלול "3"."""
    drive_service.save_meeting_to_drive(meeting("שיחה"), audio_file)
    # רק הסיכום קיים בשם הזה; שאר התיקיות "פנויות" בשם "שיחה 2".
    links = drive_service.save_meeting_to_drive(meeting("שיחה"), audio_file)

    suffix = links["title"]
    for name in (f"סיכום - {suffix}", f"תמלול - {suffix}", f"TO DO - {suffix}"):
        assert fake_drive.by_name(name), f"חסר {name}"


def test_a_different_title_is_left_untouched(fake_drive, audio_file):
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    links = drive_service.save_meeting_to_drive(meeting("שיחה עם דני"), audio_file)

    assert links["title"] == "שיחה עם דני"


def test_renaming_the_meeting_renames_every_one_of_its_files(fake_drive, audio_file):
    links = drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    file_ids = [links["summary_doc_id"], links["transcript_doc_id"], *links["audio_file_ids"]]

    drive_service.retitle_files(file_ids, "ישיבת צוות", "ישיבת הנהלה")

    assert fake_drive.by_name("סיכום - ישיבת הנהלה")
    assert fake_drive.by_name("תמלול - ישיבת הנהלה")
    assert fake_drive.by_name("הקלטה - ישיבת הנהלה.m4a")
    assert not fake_drive.by_name("סיכום - ישיבת צוות")


def test_renaming_leaves_other_meetings_alone(fake_drive, audio_file):
    first = drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    drive_service.save_meeting_to_drive(meeting("שיחה עם דני"), audio_file)

    drive_service.retitle_files([first["summary_doc_id"]], "ישיבת צוות", "ישיבת הנהלה")

    assert fake_drive.by_name("סיכום - שיחה עם דני")


def _recording_of(links: dict, title: str) -> dict:
    return {
        "title": title,
        "drive_summary_doc_id": links["summary_doc_id"],
        "drive_transcript_doc_id": links["transcript_doc_id"],
        "drive_todo_file_id": links["todo_file_id"],
        "drive_audio_file_ids": links["audio_file_ids"],
    }


def test_renaming_onto_a_taken_title_gets_a_number(fake_drive, audio_file, monkeypatch):
    drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    links = drive_service.save_meeting_to_drive(meeting("שיחה עם דני"), audio_file)
    monkeypatch.setattr(edit.firestore_store, "update_recording_fields", lambda *a, **k: None)

    updated = edit.apply_update(
        "rec-1", _recording_of(links, "שיחה עם דני"), RecordingUpdateRequest(title="ישיבת צוות")
    )

    assert updated["title"] == "ישיבת צוות 2"
    assert fake_drive.by_name("סיכום - ישיבת צוות 2")
    # ההקלטה הראשונה לא נגעו בה.
    assert fake_drive.by_name("סיכום - ישיבת צוות")


def test_resaving_the_same_title_does_not_number_the_recording(fake_drive, audio_file, monkeypatch):
    """שמירה חוזרת של אותה כותרת חייבת להיות no-op - אחרת כל עריכה קטנה
    הייתה מקדמת את המספר, כי ההקלטה הייתה מתנגשת בקבצים של עצמה."""
    links = drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    monkeypatch.setattr(edit.firestore_store, "update_recording_fields", lambda *a, **k: None)

    edit.apply_update(
        "rec-1", _recording_of(links, "ישיבת צוות"), RecordingUpdateRequest(title="ישיבת צוות")
    )

    assert fake_drive.by_name("סיכום - ישיבת צוות")
    assert not fake_drive.by_name("סיכום - ישיבת צוות 2")


def test_deleting_a_meeting_trashes_all_of_its_files(fake_drive, audio_file, monkeypatch):
    links = drive_service.save_meeting_to_drive(meeting("ישיבת צוות"), audio_file)
    recording = {
        "title": "ישיבת צוות",
        "drive_summary_doc_id": links["summary_doc_id"],
        "drive_transcript_doc_id": links["transcript_doc_id"],
        "drive_todo_file_id": links["todo_file_id"],
        "drive_audio_file_ids": links["audio_file_ids"],
    }
    monkeypatch.setattr(edit.firestore_store, "delete_recording", lambda recording_id: None)

    edit.delete_recording("rec-1", recording)

    assert not fake_drive.by_name("סיכום - ישיבת צוות")
    assert not fake_drive.by_name("תמלול - ישיבת צוות")
    assert not fake_drive.by_name("TO DO - ישיבת צוות")
    assert not fake_drive.by_name("הקלטה - ישיבת צוות.m4a")


def test_old_recording_is_still_deleted_by_its_own_folder(fake_drive, monkeypatch):
    """הקלטה מלפני המעבר (יש לה drive_folder_id) - כל התיקייה שלה לאשפה."""
    folder_id = fake_drive.add("2026-08-01 - פגישה ישנה", _FOLDER_MIME, ["root"])
    monkeypatch.setattr(edit.firestore_store, "delete_recording", lambda recording_id: None)

    edit.delete_recording("rec-old", {"drive_folder_id": folder_id})

    assert fake_drive.items[folder_id]["trashed"]


def test_old_recording_is_still_renamed_by_its_own_folder(fake_drive, monkeypatch):
    folder_id = fake_drive.add("2026-08-01 - פגישה ישנה", _FOLDER_MIME, ["root"])
    monkeypatch.setattr(edit.firestore_store, "update_recording_fields", lambda *a, **k: None)

    edit.apply_update(
        "rec-old",
        {"drive_folder_id": folder_id, "date": "2026-08-01", "title": "פגישה ישנה"},
        RecordingUpdateRequest(title="פגישה חדשה"),
    )

    assert fake_drive.items[folder_id]["name"] == "2026-08-01 - פגישה חדשה"


def test_file_ids_fall_back_to_the_saved_links(fake_drive):
    """הקלטות ישנות שמרו קישורים ולא מזהים - המחיקה חייבת לעבוד גם עליהן."""
    recording = {
        "drive_summary_url": "https://docs.google.com/document/d/DOC1/edit",
        "drive_todo_url": "https://docs.google.com/spreadsheets/d/SHEET1/edit",
        "drive_audio_url": "https://drive.google.com/file/d/AUDIO1/view",
        "attachments": [{"drive_url": "https://drive.google.com/file/d/ATT1/view"}],
    }

    assert edit._recording_file_ids(recording) == ["DOC1", "SHEET1", "AUDIO1", "ATT1"]


def test_file_ids_prefer_the_saved_attachment_id_over_the_link(fake_drive):
    """מצורף חדש נושא drive_file_id ישירות - אין צורך לחלץ אותו מהקישור."""
    recording = {
        "attachments": [
            {"drive_file_id": "ATT1", "drive_url": "https://drive.google.com/file/d/OTHER/view"}
        ],
    }

    assert edit._recording_file_ids(recording) == ["ATT1"]
