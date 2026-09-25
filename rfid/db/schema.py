"""Схема базы и переезды между её версиями.

Каждое открытие базы прогоняет migrate(): она доводит любую прежнюю версию
до текущей и ничего не делает, если доводить нечего. Отдельно от Storage,
потому что это история, а не рабочий API: читать её нужно, только когда
меняешь схему.

    1 — отметки без предмета
    2 — у отметки есть предмет: attendance.subject_id NOT NULL
    3 — человек отдельно от карты: students.card_code и attendance.card_code
        могут быть NULL, личность — (name_key, group_name)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..names import normalize_name

SCHEMA_VERSION = 3

NO_SUBJECT = "(без предмета)"

# Имя таблицы подставляется: при переезде данные копируются во временную
# таблицу, которая потом занимает место старой. SQLite не умеет снимать
# NOT NULL через ALTER, поэтому иначе никак.
_STUDENTS_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    id         INTEGER PRIMARY KEY,
    card_code  TEXT,
    full_name  TEXT    NOT NULL,
    name_key   TEXT    NOT NULL,
    group_name TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL
)
"""

_ATTENDANCE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    id         INTEGER PRIMARY KEY,
    day        TEXT    NOT NULL,
    at         TEXT    NOT NULL,
    card_code  TEXT,
    student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    raw        TEXT,
    UNIQUE(day, subject_id, card_code)
)
"""

_BASE_SCHEMA = """
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

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_attendance_day ON attendance(day);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_subject ON attendance(subject_id);
CREATE INDEX IF NOT EXISTS idx_students_group ON students(group_name);

-- Карта необязательна: студент может существовать без неё (например,
-- втянутый из таблицы). Но если карта есть, она принадлежит одному человеку.
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_card
    ON students(card_code) WHERE card_code IS NOT NULL;

-- Один человек — одна строка. Ключ нормализован (регистр, «ё», пробелы),
-- иначе «Иванов» и «иванов» завелись бы дважды.
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_person
    ON students(name_key, group_name);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Довести базу до SCHEMA_VERSION. Идемпотентна."""
    conn.executescript(_BASE_SCHEMA)
    conn.execute(_STUDENTS_DDL.format(table="students"))
    _students_to_v3(conn)

    if _table_exists(conn, "attendance") and not _column_exists(conn, "attendance", "subject_id"):
        _attendance_to_v2(conn)

    conn.execute(_ATTENDANCE_DDL.format(table="attendance"))
    _attendance_to_v3(conn)
    conn.executescript(_INDEXES)
    _repair_orphan_marks(conn)

    # Пишем, только если версия и правда изменилась: иначе каждое открытие
    # базы брало бы блокировку записи и мешало другому окну программы.
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or row[0] != str(SCHEMA_VERSION):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


def _rebuild(conn: sqlite3.Connection, table: str, ddl: str, copy_sql: str,
             params: tuple | list = ()) -> None:
    """Пересобрать таблицу по новому DDL, скопировав данные.

    Внешние ключи на время выключаются: иначе нельзя подменить таблицу,
    на которую ссылаются другие.
    """
    temp = f"_{table}_new"
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {temp}")
        conn.execute(ddl.format(table=temp))
        if isinstance(params, list):
            conn.executemany(copy_sql.format(table=temp), params)
        else:
            conn.execute(copy_sql.format(table=temp), params)
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _attendance_to_v2(conn: sqlite3.Connection) -> None:
    """Схема 1 → 2: отметки без предмета переносятся в служебный предмет,
    чтобы при обновлении ничего не потерялось."""
    row = conn.execute(
        "SELECT id FROM subjects WHERE name = ? AND course = ''", (NO_SUBJECT,)
    ).fetchone()
    if row is None:
        legacy_id = conn.execute(
            "INSERT INTO subjects(name, course, created_at) VALUES(?, '', ?)",
            (NO_SUBJECT, datetime.now().isoformat(timespec="seconds")),
        ).lastrowid
    else:
        legacy_id = row[0]
    _rebuild(
        conn, "attendance", _ATTENDANCE_DDL,
        "INSERT INTO {table}(id, day, at, card_code, student_id, subject_id, raw) "
        "SELECT id, day, at, card_code, student_id, ?, raw FROM attendance",
        (legacy_id,),
    )


def _students_to_v3(conn: sqlite3.Connection) -> None:
    """Схема 3: студент существует без карты.

    Раньше `students` была таблицей привязок — человек попадал в базу
    только вместе с картой. Чтобы втягивать отметки из таблиц, человек
    должен существовать сам по себе, а карта стать необязательной.

    Ключ ФИО считается в Python: нормализация с «ё» и регистром средствами
    SQL не выражается.
    """
    if _column_exists(conn, "students", "name_key"):
        return
    rows = conn.execute(
        "SELECT id, card_code, full_name, group_name, created_at FROM students"
    ).fetchall()
    _rebuild(
        conn, "students", _STUDENTS_DDL,
        "INSERT INTO {table}(id, card_code, full_name, name_key, group_name, "
        "created_at) VALUES(?, ?, ?, ?, ?, ?)",
        [(r[0], r[1], r[2], normalize_name(r[2]), r[3], r[4]) for r in rows],
    )


def _attendance_to_v3(conn: sqlite3.Connection) -> None:
    """Схема 3: у отметки может не быть карты.

    Отметки, втянутые из таблицы, приходят от человека, а не от карты.
    Раньше card_code был обязателен, и записать такую отметку было некуда.
    """
    if not _column_not_null(conn, "attendance", "card_code"):
        return
    _rebuild(
        conn, "attendance", _ATTENDANCE_DDL,
        "INSERT INTO {table}(id, day, at, card_code, student_id, subject_id, raw) "
        "SELECT id, day, at, card_code, student_id, subject_id, raw FROM attendance",
    )


def _repair_orphan_marks(conn: sqlite3.Connection) -> int:
    """Отдать безымянные отметки студентам, чьи карты уже известны.

    Это страховка, а не замена привязке в Storage.bind_card. Карта — это
    и есть идентификатор, поэтому отметка карты, которая теперь закреплена
    за человеком, принадлежит ему, когда бы его ни завели. Чинит записи,
    испорченные прежними версиями программы.
    """
    # Сначала смотрим, есть ли что чинить: иначе каждое открытие базы
    # брало бы блокировку записи и мешало другому окну программы.
    orphan = conn.execute(
        "SELECT 1 FROM attendance a WHERE a.student_id IS NULL "
        "  AND EXISTS (SELECT 1 FROM students s WHERE s.card_code = a.card_code) "
        "LIMIT 1"
    ).fetchone()
    if orphan is None:
        return 0
    cur = conn.execute(
        "UPDATE attendance SET student_id = ("
        "    SELECT s.id FROM students s WHERE s.card_code = attendance.card_code"
        ") "
        "WHERE student_id IS NULL "
        "  AND EXISTS (SELECT 1 FROM students s WHERE s.card_code = attendance.card_code)"
    )
    return cur.rowcount


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, bool]:
    """Колонки таблицы: имя -> NOT NULL."""
    return {row[1]: bool(row[3]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in _columns(conn, table)


def _column_not_null(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return _columns(conn, table).get(column, False)
