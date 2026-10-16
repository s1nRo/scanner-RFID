"""Данные для графического интерфейса без зависимости от Qt.

Этот модуль отделён от виджетов, чтобы правила отчёта и случайной выборки
можно было проверять обычными тестами даже на машине без PyQt5.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from random import Random

from .. import excel
from ..db import DayRow, GroupStat, Storage, Student, Subject
from ..excel import SubjectFolder
from ..lessons import late_arrivals
from ..names import normalize_name


@dataclass(frozen=True, slots=True)
class LateStudent:
    student: Student
    arrived_at: datetime
    lesson_started_at: datetime

    @property
    def delay_minutes(self) -> int:
        return int((self.arrived_at - self.lesson_started_at).total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class AttendanceSnapshot:
    subject: Subject | None
    day: date
    rows: tuple[DayRow, ...]
    stats: tuple[GroupStat, ...]
    late: tuple[LateStudent, ...]
    unknown_count: int = 0

    @property
    def present(self) -> int:
        return sum(row.present for row in self.rows)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def absent(self) -> int:
        return self.total - self.present

    @property
    def percent(self) -> float:
        return self.present / self.total * 100 if self.total else 0.0


def load_snapshot(
    db_path: str | Path,
    subject_name: str,
    day: date,
    folder: SubjectFolder | None = None,
) -> AttendanceSnapshot:
    """Снимок журнала для одного предмета и дня."""
    with Storage(db_path) as storage:
        subject = storage.find_subject(subject_name)
        if subject is None:
            return AttendanceSnapshot(None, day, (), (), ())

        rows = tuple(storage.day_rows(day, subject))
        if folder is not None:
            people = _roster_people(folder)
            rows = tuple(
                row for row in rows
                if (normalize_name(row.student.full_name), row.student.group_name) in people
            )
            group_names = sorted({row.student.group_name for row in rows})
            stats = tuple(
                GroupStat(
                    group_name,
                    sum(row.student.group_name == group_name for row in rows),
                    sum(
                        row.student.group_name == group_name and row.present
                        for row in rows
                    ),
                )
                for group_name in group_names
            )
        else:
            stats = tuple(storage.group_stats(day, subject))
        arrivals = {row.student: row.at for row in rows if row.at is not None}
        late = tuple(
            LateStudent(student, arrived_at, lesson_started_at)
            for student, arrived_at, lesson_started_at in late_arrivals(arrivals)
        )
        unknown_count = len(storage.unknown_marks_on(day, subject))
        return AttendanceSnapshot(subject, day, rows, stats, late, unknown_count)


def _roster_people(folder: SubjectFolder) -> set[tuple[str, str]]:
    people: set[tuple[str, str]] = set()
    for path in folder.rosters:
        try:
            roster = excel.read_roster(path)
        except excel.RosterError:
            continue
        people.update(
            (normalize_name(student.full_name), roster.group_name)
            for student in roster.students
        )
    return people


def choose_random_late(
    students: tuple[LateStudent, ...] | list[LateStudent],
    limit: int = 10,
    *,
    rng: Random | None = None,
) -> list[LateStudent]:
    """Выбрать до ``limit`` опоздавших без повторов.

    Когда людей меньше лимита, возвращаются все. Выбранный набор сортируется
    по ФИО, чтобы случайность не мешала быстро читать готовый список.
    """
    if limit < 1:
        return []
    source = list(students)
    generator = rng or random.SystemRandom()
    chosen = generator.sample(source, min(limit, len(source)))
    return sorted(chosen, key=lambda item: item.student.full_name.casefold())
