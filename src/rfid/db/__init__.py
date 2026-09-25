"""База данных: студенты, предметы и журнал посещаемости в SQLite.

База — источник истины для отметок. Списки групп даёт пользователь,
отметки в них проставляет rfid/journal.py, читая отсюда.

    models.py   что хранится и что возвращают запросы: Student, Subject, MarkResult…
    schema.py   таблицы и переезды между версиями схемы
    storage.py  Storage — соединение и все запросы

Снаружи пользуются этим фасадом, а не модулями внутри.
"""

from .models import (
    CardConflict, DayRow, GroupStat, ImportStatus, MarkResult, MarkStatus,
    Student, Subject, UnknownCard,
)
from .schema import NO_SUBJECT, SCHEMA_VERSION
from .storage import BUSY_TIMEOUT_MS, IMPORT_RAW, Storage

__all__ = [
    "BUSY_TIMEOUT_MS", "IMPORT_RAW", "NO_SUBJECT", "SCHEMA_VERSION",
    "CardConflict", "DayRow", "GroupStat", "ImportStatus", "MarkResult", "MarkStatus",
    "Storage", "Student", "Subject", "UnknownCard",
]
