"""Хранилище: студенты, предметы и журнал посещаемости в SQLite.

База — источник истины. Списки групп даёт пользователь, а отметки в них
проставляет rfid/journal.py, читая из базы. Открытый в Excel файл заблокирован
для записи, и отметка во время пары не должна от этого зависеть.

Отметка принадлежит занятию, а не просто дню, поэтому дубль — это
UNIQUE(day, subject_id, card_code). За один день студент может отметиться
на нескольких парах, но на одной и той же — только раз. Ограничение живёт
в базе, а не в памяти процесса, и потому переживает перезапуск программы.

Типы данных — models.py, схема и её переезды между версиями — schema.py.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from ..names import normalize_name
from ..scanner.codes import CardCode
from .models import (
    CardConflict, DayRow, GroupStat, ImportStatus, MarkResult, MarkStatus,
    Student, Subject, UnknownCard,
)
from .schema import migrate

# Пометка в attendance.raw у отметок, взятых из таблицы, а не с карты.
IMPORT_RAW = "таблица: "

# Сколько ждать, если база занята другим процессом, прежде чем сдаться.
# Пара идёт полтора часа, очередь у считывателя живая — пять секунд
# ожидания несопоставимы с потерянной отметкой.
BUSY_TIMEOUT_MS = 5000


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


def _new_id(cur: sqlite3.Cursor) -> int:
    """id строки, только что вставленной INSERT'ом. После вставки он есть всегда."""
    if cur.lastrowid is None:
        raise sqlite3.DatabaseError("INSERT не вернул id новой строки")
    return cur.lastrowid


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class _Transaction:
    """Всё-или-ничего. На точках сохранения, поэтому вкладывается:
    пробный прогон синхронизации оборачивает пофайловые транзакции в одну
    внешнюю и откатывает её целиком."""

    def __init__(self, storage: Storage):
        self.storage = storage

    def __enter__(self) -> None:
        self.storage._savepoints += 1
        self.name = f"sp{self.storage._savepoints}"
        self.storage.conn.execute(f"SAVEPOINT {self.name}")

    def __exit__(self, exc_type, *exc) -> None:
        self.storage._savepoints -= 1
        if exc_type:
            self.storage.conn.execute(f"ROLLBACK TO {self.name}")
        self.storage.conn.execute(f"RELEASE {self.name}")


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
        self._savepoints = 0
        migrate(self.conn)

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def transaction(self) -> _Transaction:
        """Одна транзакция на много записей: быстрее и всё-или-ничего."""
        return _Transaction(self)

    # -------------------------------------------------------------- предметы

    def add_subject(self, name: str, course: str = "") -> Subject:
        name = name.strip()
        if not name:
            raise ValueError("Название предмета не может быть пустым")
        cur = self.conn.execute(
            "INSERT INTO subjects(name, course, created_at) VALUES(?, ?, ?)",
            (name, course.strip(), _now()),
        )
        return Subject(_new_id(cur), name, course.strip())

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

    def has_mark(self, student: Student, subject: Subject | int, day: date) -> bool:
        """Есть ли у человека отметка на этом предмете в этот день."""
        return bool(self.marks_of(student, subject, day))

    def marks_of(
        self, student: Student, subject: Subject | int, day: date
    ) -> list[tuple[int, datetime]]:
        """Отметки человека на предмете за день: (id строки, время).

        Ищется и по человеку, и по карте: отметки из таблиц держатся
        за человека, а живые приходы — за карту.
        """
        rows = self.conn.execute(
            "SELECT id, at FROM attendance WHERE day = ? AND subject_id = ? "
            "AND (student_id = ? OR card_code = ?) ORDER BY at",
            (day.isoformat(), _subject_id(subject), student.id, student.card_code),
        )
        return [(r["id"], datetime.fromisoformat(r["at"])) for r in rows]

    def import_mark(
        self,
        student: Student,
        subject: Subject | int,
        at: datetime,
        *,
        raw: str = "",
        file_wins: bool = False,
    ) -> ImportStatus:
        """Отметка, взятая из таблицы.

        Обычно главнее база: другое время в файле её не меняет (KEPT).
        С file_wins — полная синхронизация — правится база.
        Сравнение до минуты: в файле секунд нет, а у прихода картой есть,
        и без этого каждая живая отметка считалась бы расхождением.
        """
        existing = self.marks_of(student, subject, at.date())
        if existing:
            mark_id, saved_at = existing[0]
            if saved_at.replace(second=0) == at.replace(second=0):
                return ImportStatus.SAME
            if not file_wins:
                return ImportStatus.KEPT
            self.conn.execute(
                "UPDATE attendance SET at = ? WHERE id = ?",
                (at.isoformat(timespec="seconds"), mark_id),
            )
            return ImportStatus.UPDATED
        cur = self.conn.execute(
            "INSERT INTO attendance(day, at, card_code, student_id, subject_id, raw) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(day, subject_id, card_code) DO NOTHING",
            (at.date().isoformat(), at.isoformat(timespec="seconds"), student.card_code,
             student.id, _subject_id(subject), raw),
        )
        return ImportStatus.ADDED if cur.rowcount else ImportStatus.SAME

    def delete_marks(self, ids: list[int]) -> int:
        cur = self.conn.executemany("DELETE FROM attendance WHERE id = ?", [(i,) for i in ids])
        return cur.rowcount

    def count_marks(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM attendance").fetchone()["n"]

    # --------------------------------------------------------------- справочник

    def find_student(self, code: CardCode | str) -> Student | None:
        canonical = code.canonical if isinstance(code, CardCode) else code
        row = self.conn.execute(
            "SELECT * FROM students WHERE card_code = ?", (canonical,)
        ).fetchone()
        return _as_student(row) if row else None

    def student_by_name(self, full_name: str, group_name: str = "") -> Student | None:
        """Найти человека по ФИО и группе, без учёта регистра и «ё»."""
        row = self.conn.execute(
            "SELECT * FROM students WHERE name_key = ? AND group_name = ?",
            (normalize_name(full_name), group_name.strip()),
        ).fetchone()
        return _as_student(row) if row else None

    def add_student(
        self, code: CardCode | str, full_name: str, group_name: str = ""
    ) -> Student:
        """Завести студента и забрать ему прошлые безымянные отметки этой карты.

        Привязка обязательна именно здесь: карту часто прикладывают до того,
        как владельца внесли в справочник. Если оставить те отметки ничьими,
        карта останется в «неизвестных», студент будет числиться отсутствующим,
        а повторное прикладывание скажет «уже отмечен» — отметка есть, но ничья.
        """
        return self.bind_card(code, full_name, group_name)[0]

    def bind_card(
        self, code: CardCode | str, full_name: str, group_name: str = ""
    ) -> tuple[Student, int]:
        """Закрепить карту за человеком.

        Возвращает его и число зачтённых прошлых отметок карты. Число
        показывают оператору: так видна странность вроде карты,
        перевыпущенной другому человеку.
        """
        student = self._insert_student(code, full_name, group_name)
        return student, self._attach_past_marks(student)

    def get_or_create_student(self, full_name: str, group_name: str = "") -> Student:
        """Человек из списка группы. Карты у него может не быть вовсе."""
        return self.student_by_name(full_name, group_name) or self._insert_student(
            None, full_name, group_name
        )

    def _insert_student(
        self, code: CardCode | str | None, full_name: str, group_name: str = ""
    ) -> Student:
        """Завести человека. Карта необязательна — её может ещё не быть."""
        canonical = code.canonical if isinstance(code, CardCode) else code
        full_name = full_name.strip()
        group_name = group_name.strip()
        if not full_name:
            raise ValueError("ФИО не может быть пустым")

        # Человек мог уже появиться в базе без карты — тогда не плодим вторую
        # строку, а привязываем карту к существующей.
        existing = self.student_by_name(full_name, group_name)
        if existing is not None:
            if canonical and existing.card_code not in (None, canonical):
                # Молча вернуть старую запись нельзя: оператор увидел бы
                # «привязано», а новая карта осталась бы ничьей.
                raise CardConflict(existing)
            if canonical and existing.card_code is None:
                self.conn.execute(
                    "UPDATE students SET card_code = ? WHERE id = ?",
                    (canonical, existing.id),
                )
                student = Student(existing.id, canonical, existing.full_name, group_name)
                self._stamp_card_on_marks(student)
                return student
            return existing

        cur = self.conn.execute(
            "INSERT INTO students(card_code, full_name, name_key, group_name, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (canonical, full_name, normalize_name(full_name), group_name, _now()),
        )
        return Student(_new_id(cur), canonical, full_name, group_name)

    def _attach_past_marks(self, student: Student) -> int:
        """Сделать именными все прошлые отметки карты этого студента."""
        if student.card_code is None:
            return 0
        cur = self.conn.execute(
            "UPDATE attendance SET student_id = ? WHERE card_code = ? AND student_id IS NULL",
            (student.id, student.card_code),
        )
        return cur.rowcount

    def _stamp_card_on_marks(self, student: Student) -> None:
        """Проставить карту в отметках, сделанных, пока карты не было.

        Отметки из таблицы пишутся без карты, и UNIQUE(day, subject_id,
        card_code) их не видит: NULL в SQLite ни с чем не совпадает. Если
        оставить их так, то после привязки то же занятие отметилось бы
        второй раз и прикладывание не сказало бы «уже отмечен».

        Где за то же занятие уже есть отметка картой, главнее она — это
        живой приход, а безкарточная строка просто лишняя.
        """
        self.conn.execute(
            "DELETE FROM attendance AS a "
            "WHERE a.student_id = ? AND a.card_code IS NULL AND EXISTS ("
            "  SELECT 1 FROM attendance b WHERE b.card_code = ? "
            "  AND b.day = a.day AND b.subject_id = a.subject_id)",
            (student.id, student.card_code),
        )
        self.conn.execute(
            "UPDATE attendance SET card_code = ? WHERE student_id = ? AND card_code IS NULL",
            (student.card_code, student.id),
        )

    def list_students(
        self, group_name: str | None = None, *, with_card: bool = False
    ) -> list[Student]:
        # None — без фильтра; "" — именно те, у кого группа не указана.
        where: list[str] = []
        params: list = []
        if group_name is not None:
            where.append("group_name = ?")
            params.append(group_name)
        if with_card:
            where.append("card_code IS NOT NULL")
        sql = "SELECT * FROM students"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY group_name, full_name"
        return [_as_student(r) for r in self.conn.execute(sql, params)]

    def remove_student(self, code: CardCode | str) -> bool:
        """Снять привязку карты. Человек и его отметки из таблиц остаются.

        Удалять саму запись нельзя: отметки, втянутые из таблиц, держатся
        только за человека, и после удаления стали бы ничьими и невидимыми.
        Отметки же, сделанные картой, возвращаются в «неизвестные» —
        привязка могла быть ошибочной, и их надо суметь отдать другому.
        """
        student = self.find_student(code)
        if student is None:
            return False
        self._unbind(student.id)
        return True

    def unbind_all_cards(self) -> None:
        """Снять все привязки карт, не трогая людей и отметки из таблиц."""
        self._unbind(None)

    def _unbind(self, student_id: int | None) -> None:
        """Снять карту с одного человека или, при None, со всех."""
        which = "card_code IS NOT NULL" if student_id is None else "id = :id"
        people = f"SELECT id FROM students WHERE {which}"
        params = {"id": student_id, "imported": f"{IMPORT_RAW}%"}
        with self.transaction():
            # Отметки из таблиц остаются за человеком: их связь — ФИО, не карта.
            self.conn.execute(
                f"UPDATE attendance SET card_code = NULL "
                f"WHERE student_id IN ({people}) AND raw LIKE :imported",
                params,
            )
            self.conn.execute(
                f"UPDATE attendance SET student_id = NULL "
                f"WHERE student_id IN ({people}) AND card_code IS NOT NULL",
                params,
            )
            self.conn.execute(
                f"UPDATE students SET card_code = NULL WHERE {which}", params
            )

    def count_cards(self) -> int:
        """Сколько карт привязано."""
        return self.conn.execute(
            "SELECT COUNT(*) AS n FROM students WHERE card_code IS NOT NULL"
        ).fetchone()["n"]

    def count_students(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"]

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

    # ------------------------------------------------------------------ очистка

    def clear_marks(self) -> None:
        """Удалить все отметки вместе с предметами.

        Предметы уходят с отметками: в базе они нужны только как якорь
        для журнала, а сам список предметов — это папки.
        """
        with self.transaction():
            self.conn.execute("DELETE FROM attendance")
            self.conn.execute("DELETE FROM subjects")

    def clear_all(self) -> None:
        """Удалить всё: отметки, предметы и людей."""
        with self.transaction():
            self.clear_marks()
            self.conn.execute("DELETE FROM students")
