"""Данные GUI: отчёт и случайный список без зависимости от PyQt5."""

from datetime import datetime
from random import Random

from rfid.db import Storage, Student
from rfid.gui.data import LateStudent, choose_random_late, load_snapshot
from tests.helpers import card


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 16, hour, minute)


def test_snapshot_uses_common_late_rule(tmp_path):
    db = tmp_path / "attendance.db"
    first = card("Em-Marine[A100] 007,42")
    late = card("Em-Marine[B200] 008,43")
    with Storage(db) as storage:
        subject = storage.add_subject("Математика")
        storage.add_student(first, "Первый Студент", "101")
        storage.add_student(late, "Опоздавший Студент", "101")
        storage.mark(first, subject, at=at(9, 0))
        storage.mark(late, subject, at=at(9, 15))

    snapshot = load_snapshot(db, "Математика", at(9, 0).date())

    assert snapshot.present == 2
    assert snapshot.total == 2
    assert [item.student.full_name for item in snapshot.late] == ["Опоздавший Студент"]
    assert snapshot.late[0].delay_minutes == 15


def test_random_late_has_no_duplicates_and_is_limited():
    students = [
        LateStudent(Student(i, None, f"Студент {i:02}", "101"), at(9, 20), at(9, 0))
        for i in range(20)
    ]

    chosen = choose_random_late(students, rng=Random(7))

    assert len(chosen) == 10
    assert len({item.student.id for item in chosen}) == 10
    assert [item.student.full_name for item in chosen] == sorted(
        item.student.full_name for item in chosen
    )


def test_random_late_returns_everyone_when_fewer_than_ten():
    students = [
        LateStudent(Student(i, None, f"Студент {i}", "101"), at(9, 20), at(9, 0))
        for i in range(3)
    ]
    assert {item.student.id for item in choose_random_late(students, rng=Random(1))} == {0, 1, 2}
