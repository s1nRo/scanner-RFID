"""Хранилище: студенты, предметы и журнал посещаемости в SQLite.

База — источник истины. Списки групп даёт пользователь, а отметки в них
проставляет rfid/journal.py, читая из базы. Открытый в Excel файл заблокирован
для записи, и отметка во время пары не должна от этого зависеть.

Отметка принадлежит занятию, а не просто дню, поэтому дубль — это
UNIQUE(day, subject_id, card_code). За один день студент может отметиться
на нескольких парах, но на одной и той же — только раз. Ограничение живёт
в базе, а не в памяти процесса, и потому переживает перезапуск программы.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from .codes import CardCode

SCHEMA_VERSION = 2

NO_SUBJECT = "(без предмета)"

# Сколько ждать, если база занята другим процессом, прежде чем сдаться.
# Пара идёт полтора часа, очередь у считывателя живая — пять секунд
# ожидания несопоставимы с потерянной отметкой.
BUSY_TIMEOUT_MS = 5000

_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id         INTEGER PRIMARY KEY,
    card_code  TEXT    NOT NULL UNIQUE,
    full_name  TEXT    NOT NULL,
    group_name TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS subjects (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    course     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    UNIQUE(name, course)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Имя таблицы подставляется: при переезде со схемы 1 данные копируются
# во временную таблицу, которая потом занимает место старой.
_ATTENDANCE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    id         INTEGER PRIMARY KEY,
    day        TEXT    NOT NULL,
    at         TEXT    NOT NULL,
    card_code  TEXT    NOT NULL,
    student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    raw        TEXT,
    UNIQUE(day, subject_id, card_code)
)
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_attendance_day ON attendance(day);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_subject ON attendance(subject_id);
CREATE INDEX IF NOT EXISTS idx_students_group ON students(group_name);
"""


class MarkStatus(Enum):
    MARKED = "marked"        # записано, студент найден в справочнике
    UNKNOWN = "unknown"      # записано, но карта неизвестна
    DUPLICATE = "duplicate"  # уже отмечен на этом занятии сегодня


@dataclass(frozen=True, slots=True)
class Student:
    id: int
    card_code: str
    full_name: str
    group_name: str


@dataclass(frozen=True, slots=True)
class Subject:
    id: int
    name: str
    course: str = ""

    @property
    def title(self) -> str:
        return f"{self.name} — {self.course}" if self.course else self.name


@dataclass(frozen=True, slots=True)
class MarkResult:
    status: MarkStatus
    code: CardCode
    at: datetime
    student: Student | None = None
    # Для повтора — когда эта карта была отмечена на этом занятии.
    first_at: datetime | None = None


    @property
    def recorded(self) -> bool:
        return self.status is not MarkStatus.DUPLICATE


@dataclass(frozen=True, slots=True)
class DayRow:
    """Строка отчёта за день: студент и была ли отметка."""

    student: Student
    at: datetime | None

    @property
    def present(self) -> bool:
        return self.at is not None


@dataclass(frozen=True, slots=True)
class GroupStat:
    group_name: str
    total: int
    present: int

    @property
    def percent(self) -> float:
        return self.present / self.total * 100 if self.total else 0.0


@dataclass(frozen=True, slots=True)
class UnknownCard:
    card_code: str
    times: int
    first_seen: datetime
    last_seen: datetime


def _as_student(row: sqlite3.Row) -> Student:
    return Student(
        id=row["id"],
        card_code=row["card_code"],
        full_name=row["full_name"],
        group_name=row["group_name"],
    )


def _as_subject(row: sqlite3.Row) -> Subject:
    return Subject(id=row["id"], name=row["name"], course=row["course"])


def _subject_id(subject: Subject | int) -> int:
    return subject.id if isinstance(subject, Subject) else subject


class Storage:
    """Соединение с базой. Используется как контекстный менеджер."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        # Без таймаута занятая база отвечает отказом мгновенно, и отметка
        # теряется. А занять её легко: второе окно программы, запущенный
        # параллельно export, открытый просмотрщик. Лучше подождать
        # несколько секунд, чем потерять студента.
        self.conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        self._migrate()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- миграции

    def _migrate(self) -> None:
        self.conn.executescript(_BASE_SCHEMA)

        # Схема 1 — отметки без предмета. Переносим их в служебный предмет,
        # чтобы при обновлении ничего не потерялось.
        if self._table_exists("attendance") and not self._column_exists("attendance", "subject_id"):
            legacy = self.get_or_create_subject(NO_SUBJECT)
            self.conn.execute("DROP TABLE IF EXISTS _attendance_v2")
            self.conn.execute(_ATTENDANCE_DDL.format(table="_attendance_v2"))
            self.conn.execute(
                "INSERT INTO _attendance_v2(id, day, at, card_code, student_id, subject_id, raw) "
                "SELECT id, day, at, card_code, student_id, ?, raw FROM attendance",
                (legacy.id,),
            )
            self.conn.execute("DROP TABLE attendance")
            self.conn.execute("ALTER TABLE _attendance_v2 RENAME TO attendance")

        self.conn.execute(_ATTENDANCE_DDL.format(table="attendance"))
        self.conn.executescript(_INDEXES)
        self._repair_orphan_marks()
        # Пишем, только если версия и правда изменилась: иначе каждое открытие
        # базы брало бы блокировку записи и мешало другому окну программы.
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row["value"] != str(SCHEMA_VERSION):
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def _repair_orphan_marks(self) -> int:
        """Отдать безымянные отметки студентам, чьи карты уже известны.

        Это страховка, а не замена привязке в add_student. Карта — это и есть
        идентификатор, поэтому отметка карты, которая теперь закреплена за
        человеком, принадлежит ему, когда бы его ни завели. Запускается при
        каждом открытии базы: дёшево, идемпотентно и чинит записи, испорченные
        прежними версиями программы.
        """
        # Сначала смотрим, есть ли что чинить: иначе каждое открытие базы
        # брало бы блокировку записи и мешало другому окну программы.
        orphan = self.conn.execute(
            "SELECT 1 FROM attendance a WHERE a.student_id IS NULL "
            "  AND EXISTS (SELECT 1 FROM students s WHERE s.card_code = a.card_code) "
            "LIMIT 1"
        ).fetchone()
        if orphan is None:
            return 0

        cur = self.conn.execute(
            "UPDATE attendance SET student_id = ("
            "    SELECT s.id FROM students s WHERE s.card_code = attendance.card_code"
            ") "
            "WHERE student_id IS NULL "
            "  AND EXISTS (SELECT 1 FROM students s WHERE s.card_code = attendance.card_code)"
        )
        return cur.rowcount

    def _table_exists(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _column_exists(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in rows)

    # -------------------------------------------------------------- предметы

    def add_subject(self, name: str, course: str = "") -> Subject:
        name = name.strip()
        if not name:
            raise ValueError("Название предмета не может быть пустым")
        cur = self.conn.execute(
            "INSERT INTO subjects(name, course, created_at) VALUES(?, ?, ?)",
            (name, course.strip(), datetime.now().isoformat(timespec="seconds")),
        )
        return Subject(cur.lastrowid, name, course.strip())

    def find_subject(self, name: str, course: str = "") -> Subject | None:
        row = self.conn.execute(
            "SELECT * FROM subjects WHERE name = ? AND course = ?",
            (name.strip(), course.strip()),
        ).fetchone()
        return _as_subject(row) if row else None

    def get_or_create_subject(self, name: str, course: str = "") -> Subject:
        return self.find_subject(name, course) or self.add_subject(name, course)

    def list_subjects(self) -> list[Subject]:
        rows = self.conn.execute("SELECT * FROM subjects ORDER BY name, course")
        return [_as_subject(r) for r in rows]

    def remove_subject(self, subject: Subject | int) -> bool:
        """Удалить предмет вместе со всеми его отметками."""
        cur = self.conn.execute("DELETE FROM subjects WHERE id = ?", (_subject_id(subject),))
        return cur.rowcount > 0

    # ------------------------------------------------------------------ отметки

    def mark(
        self,
        code: CardCode,
        subject: Subject | int,
        *,
        at: datetime | None = None,
        raw: str = "",
    ) -> MarkResult:
        """Отметить карту на занятии. Повтор за тот же день не дублируется."""
        at = at or datetime.now()
        day = at.date().isoformat()
        sid = _subject_id(subject)
        student = self.find_student(code)

        cur = self.conn.execute(
            "INSERT INTO attendance(day, at, card_code, student_id, subject_id, raw) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(day, subject_id, card_code) DO NOTHING",
            (day, at.isoformat(timespec="seconds"), code.canonical,
             student.id if student else None, sid, raw),
        )

        if cur.rowcount == 0:
            row = self.conn.execute(
                "SELECT at FROM attendance WHERE day = ? AND subject_id = ? AND card_code = ?",
                (day, sid, code.canonical),
            ).fetchone()
            first_at = datetime.fromisoformat(row["at"]) if row else None
            return MarkResult(MarkStatus.DUPLICATE, code, at, student, first_at)

        status = MarkStatus.MARKED if student else MarkStatus.UNKNOWN
        return MarkResult(status, code, at, student, first_at=at)

    # --------------------------------------------------------------- справочник

    def find_student(self, code: CardCode | str) -> Student | None:
        canonical = code.canonical if isinstance(code, CardCode) else code
        row = self.conn.execute(
            "SELECT * FROM students WHERE card_code = ?", (canonical,)
        ).fetchone()
        return _as_student(row) if row else None

    def add_student(
        self, code: CardCode | str, full_name: str, group_name: str = ""
    ) -> Student:
        """Завести студента и забрать ему прошлые безымянные отметки этой карты.

        Привязка обязательна именно здесь, а не только в resolve_unknown:
        карту часто прикладывают до того, как владельца внесли в справочник.
        Если оставить те отметки ничьими, карта останется в «неизвестных»,
        студент будет числиться отсутствующим, а повторное прикладывание
        скажет «уже отмечен» — отметка есть, но ничья.
        """
        student = self._insert_student(code, full_name, group_name)
        self._attach_past_marks(student)
        return student

    def _insert_student(
        self, code: CardCode | str, full_name: str, group_name: str = ""
    ) -> Student:
        canonical = code.canonical if isinstance(code, CardCode) else code
        full_name = full_name.strip()
        if not full_name:
            raise ValueError("ФИО не может быть пустым")
        cur = self.conn.execute(
            "INSERT INTO students(card_code, full_name, group_name, created_at) "
            "VALUES(?, ?, ?, ?)",
            (canonical, full_name, group_name.strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        return Student(cur.lastrowid, canonical, full_name, group_name.strip())

    def _attach_past_marks(self, student: Student) -> int:
        """Сделать именными все прошлые отметки карты этого студента."""
        cur = self.conn.execute(
            "UPDATE attendance SET student_id = ? WHERE card_code = ? AND student_id IS NULL",
            (student.id, student.card_code),
        )
        return cur.rowcount

    def list_students(self, group_name: str | None = None) -> list[Student]:
        # None — без фильтра; "" — именно те, у кого группа не указана.
        sql = "SELECT * FROM students"
        params: tuple = ()
        if group_name is not None:
            sql += " WHERE group_name = ?"
            params = (group_name,)
        sql += " ORDER BY group_name, full_name"
        return [_as_student(r) for r in self.conn.execute(sql, params)]

    def list_groups(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT group_name FROM students ORDER BY group_name"
        )
        return [r["group_name"] for r in rows]

    def remove_student(self, code: CardCode | str) -> bool:
        canonical = code.canonical if isinstance(code, CardCode) else code
        cur = self.conn.execute("DELETE FROM students WHERE card_code = ?", (canonical,))
        return cur.rowcount > 0

    # --------------------------------------------------------- неизвестные карты

    def unknown_cards(self) -> list[UnknownCard]:
        rows = self.conn.execute(
            "SELECT card_code, COUNT(*) AS times, MIN(at) AS first_seen, MAX(at) AS last_seen "
            "FROM attendance WHERE student_id IS NULL "
            "GROUP BY card_code ORDER BY last_seen DESC"
        )
        return [
            UnknownCard(
                card_code=r["card_code"],
                times=r["times"],
                first_seen=datetime.fromisoformat(r["first_seen"]),
                last_seen=datetime.fromisoformat(r["last_seen"]),
            )
            for r in rows
        ]

    def resolve_unknown(
        self, code: CardCode | str, full_name: str, group_name: str = ""
    ) -> tuple[Student, int]:
        """То же, что add_student, но ещё и сообщает, сколько отметок привязано."""
        student = self._insert_student(code, full_name, group_name)
        return student, self._attach_past_marks(student)

    def unknown_marks_on(self, day: date, subject: Subject | int) -> list[tuple[str, datetime]]:
        rows = self.conn.execute(
            "SELECT card_code, at FROM attendance "
            "WHERE day = ? AND subject_id = ? AND student_id IS NULL ORDER BY at",
            (day.isoformat(), _subject_id(subject)),
        )
        return [(r["card_code"], datetime.fromisoformat(r["at"])) for r in rows]

    # ------------------------------------------------------------------- отчёты

    def day_rows(
        self, day: date, subject: Subject | int, group_name: str | None = None
    ) -> list[DayRow]:
        """Все студенты на занятии за день — и пришедшие, и нет."""
        sql = (
            "SELECT s.*, a.at AS marked_at "
            "FROM students s "
            "LEFT JOIN attendance a "
            "  ON a.student_id = s.id AND a.day = ? AND a.subject_id = ? "
        )
        params: list = [day.isoformat(), _subject_id(subject)]
        if group_name is not None:
            sql += "WHERE s.group_name = ? "
            params.append(group_name)
        sql += "ORDER BY s.group_name, s.full_name"

        return [
            DayRow(
                student=_as_student(r),
                at=datetime.fromisoformat(r["marked_at"]) if r["marked_at"] else None,
            )
            for r in self.conn.execute(sql, params)
        ]

    def group_stats(self, day: date, subject: Subject | int) -> list[GroupStat]:
        rows = self.conn.execute(
            "SELECT s.group_name, COUNT(*) AS total, COUNT(a.id) AS present "
            "FROM students s "
            "LEFT JOIN attendance a "
            "  ON a.student_id = s.id AND a.day = ? AND a.subject_id = ? "
            "GROUP BY s.group_name ORDER BY s.group_name",
            (day.isoformat(), _subject_id(subject)),
        )
        return [GroupStat(r["group_name"], r["total"], r["present"]) for r in rows]

    def marks_by_day(self, subject: Subject | int) -> dict[date, dict[str, datetime]]:
        """Отметки предмета в виде дата -> {ФИО: время}.

        Ровно то, что нужно, чтобы заполнить присланные списки групп:
        сопоставление там идёт по ФИО, а не по нашим внутренним id.
        """
        result: dict[date, dict[str, datetime]] = {}
        rows = self.conn.execute(
            "SELECT a.day, a.at, s.full_name FROM attendance a "
            "JOIN students s ON s.id = a.student_id "
            "WHERE a.subject_id = ? ORDER BY a.day, a.at",
            (_subject_id(subject),),
        )
        for row in rows:
            day = date.fromisoformat(row["day"])
            result.setdefault(day, {})[row["full_name"]] = datetime.fromisoformat(row["at"])
        return result

    def subject_dates(self, subject: Subject | int) -> list[date]:
        rows = self.conn.execute(
            "SELECT DISTINCT day FROM attendance WHERE subject_id = ? ORDER BY day",
            (_subject_id(subject),),
        )
        return [date.fromisoformat(r["day"]) for r in rows]

    def days_with_data(self, subject: Subject | int | None = None) -> list[date]:
        if subject is None:
            rows = self.conn.execute("SELECT DISTINCT day FROM attendance ORDER BY day")
        else:
            return self.subject_dates(subject)
        return [date.fromisoformat(r["day"]) for r in rows]

    def count_students(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"]
