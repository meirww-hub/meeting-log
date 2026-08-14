"""עמודת "אחראי" בטאב "משימות" של גיליון ה-TO DO.

העמודה קיימת בגיליון מאז ומתמיד, אבל היא שווה משהו רק אם שדה owner באמת
מתמלא - ולכן שתי הדרישות נבדקות בנפרד: כאן הצד הדטרמיניסטי (העמודה קיימת,
במקום הנכון, ומכילה את מה שהמודל החזיר), וב-test_summarize_live הצד שתלוי
במודל (שהוא באמת מזהה את האחראי מתוך השיחה).
"""

from app.models import TodoItem
from app.services.drive import (
    _TASK_COLORS_HEX,
    _TODO_COLUMN_WIDTHS,
    _TODO_HEADERS,
    _gantt_row_label,
    _todo_sheet_requests,
    _todo_sheet_values,
)

_TODOS = [
    TodoItem(description="לשלוח את החוזה", owner="דנה", due_date="2026-08-20"),
    TodoItem(description="לבדוק מול הספק", owner="יוסי"),
    TodoItem(description="להזמין ציוד"),  # בלי אחראי ובלי תאריך
]


class TestOwnerColumn:
    def test_header_row_has_an_owner_column(self):
        assert _todo_sheet_values(_TODOS)[0] == ["משימה", "אחראי", "תאריך יעד"]

    def test_owner_lands_in_the_owner_column(self):
        rows = _todo_sheet_values(_TODOS)[1:]
        assert [row[1] for row in rows] == ["דנה", "יוסי", ""]

    def test_missing_owner_is_an_empty_cell_not_none(self):
        """None בערך של תא גורם ל-Sheets להציג את המחרוזת "None"."""
        for row in _todo_sheet_values(_TODOS):
            assert all(isinstance(cell, str) for cell in row)

    def test_owner_does_not_displace_the_description_or_due_date(self):
        assert _todo_sheet_values(_TODOS)[1] == ["לשלוח את החוזה", "דנה", "2026-08-20"]

    def test_every_row_has_a_cell_for_every_column(self):
        assert all(len(row) == len(_TODO_HEADERS) for row in _todo_sheet_values(_TODOS))


class TestFormattingCoversTheOwnerColumn:
    """הפורמט נכתב לפי מספר העמודות; עמודה שנוספה בלי לעדכן אותו נשארת לבנה
    וצרה בזמן ששאר השורה צבועה."""

    def test_row_color_spans_all_columns(self):
        ranges = [
            r["repeatCell"]["range"]
            for r in _todo_sheet_requests(_TODOS)
            if "repeatCell" in r and r["repeatCell"]["range"].get("startRowIndex", 0) > 0
        ]
        assert ranges, "אין בקשת צביעה לשורות המשימות"
        assert all(r["endColumnIndex"] == len(_TODO_HEADERS) for r in ranges)

    def test_every_column_gets_an_explicit_width(self):
        assert len(_TODO_COLUMN_WIDTHS) == len(_TODO_HEADERS)
        widths = [
            r["updateDimensionProperties"]
            for r in _todo_sheet_requests(_TODOS)
            if "updateDimensionProperties" in r
        ]
        columns = {w["range"]["startIndex"] for w in widths}
        assert columns == set(range(len(_TODO_HEADERS)))

    def test_owner_column_is_wider_than_the_sheets_default(self):
        """ברירת המחדל (100px) חותכת שם מלא באמצע."""
        assert _TODO_COLUMN_WIDTHS[_TODO_HEADERS.index("אחראי")] >= 140

    def test_colors_cycle_and_never_run_out(self):
        many = [TodoItem(description=f"משימה {i}") for i in range(len(_TASK_COLORS_HEX) + 3)]
        assert _todo_sheet_requests(many)  # לא IndexError


class TestGanttLabel:
    def test_owner_is_appended_to_the_task_name(self):
        """בטאב הגאנט אין עמודות - האחראי נכנס לתווית השורה עצמה."""
        assert _gantt_row_label(_TODOS[0]) == "לשלוח את החוזה (דנה)"

    def test_task_without_owner_has_no_empty_parentheses(self):
        assert _gantt_row_label(_TODOS[2]) == "להזמין ציוד"
