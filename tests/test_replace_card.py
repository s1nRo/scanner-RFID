"""Замена карты у студента: потерял, сменил, привязали по ошибке.

Главное — не потерять отметки: человек тот же, меняется только карта.
"""

from datetime import datetime

import pytest

from rfid.db import CardTaken, MarkStatus, Storage
from tests.helpers import card

OLD = card("Em-Marine[A100] 007,42")
NEW = card("Em-Marine[B200] 008,43")
OTHER = card("Em-Marine[C300] 009,44")

MONDAY = datetime(2026, 11, 2, 9, 3)
MONDAY_LATER = datetime(2026, 11, 2, 9, 40)
TUESDAY = datetime(2026, 11, 3, 9, 5)


@pytest.fixture
def db(tmp_path):
    with Storage(tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def math(db):
    return db.add_subject("Матанализ")


def rows(db):
    return [tuple(r) for r in db.conn.execute(
        "SELECT day, card_code, student_id FROM attendance ORDER BY at")]


def test_past_marks_stay_with_the_student(db, math):
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    db.mark(OLD, math, at=MONDAY)
    result = db.replace_card(ivan.id, NEW)
    assert result.student.card_code == NEW.canonical
    assert result.old_code == OLD.canonical
    # Отметка его, и карта в ней новая — чтобы дедуп работал дальше.
    assert rows(db) == [("2026-11-02", NEW.canonical, ivan.id)]
    assert db.find_student(OLD) is None
    assert db.find_student(NEW) == result.student


def test_same_lesson_again_with_new_card_is_a_duplicate(db, math):
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    db.mark(OLD, math, at=MONDAY)
    db.replace_card(ivan.id, NEW)
    assert db.mark(NEW, math, at=MONDAY_LATER).status is MarkStatus.DUPLICATE


def test_unknown_marks_of_the_new_card_become_his(db, math):
    # Студент пришёл с новой картой до того, как её привязали.
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    db.mark(NEW, math, at=TUESDAY)
    result = db.replace_card(ivan.id, NEW)
    assert result.moved == 1
    assert db.unknown_cards() == []


def test_two_marks_for_one_lesson_keep_the_earliest(db, math):
    # Утром пришёл со старой картой, позже приложил новую (она была ничьей).
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    db.mark(NEW, math, at=MONDAY_LATER)
    db.mark(OLD, math, at=MONDAY)
    db.replace_card(ivan.id, NEW)
    assert rows(db) == [("2026-11-02", NEW.canonical, ivan.id)]
    (kept,) = db.marks_of(db.find_student(NEW), math, MONDAY.date())
    assert kept[1] == MONDAY


def test_card_of_another_student_is_refused(db, math):
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    petr = db.add_student(NEW, "Петров Пётр", "Г-1")
    with pytest.raises(CardTaken) as exc:
        db.replace_card(ivan.id, NEW)
    assert exc.value.owner == petr
    assert db.find_student(NEW) == petr  # ничего не поменялось


def test_take_fixes_a_wrong_binding(db, math):
    # Карту Ивана по ошибке привязали Петру; её отметки — приходы Ивана.
    ivan = db.get_or_create_student("Иванов Иван", "Г-1")
    petr = db.add_student(NEW, "Петров Пётр", "Г-1")
    db.mark(NEW, math, at=MONDAY)
    result = db.replace_card(ivan.id, NEW, take=True)
    assert result.previous_owner == petr
    assert db.find_student(NEW).id == ivan.id
    assert rows(db) == [("2026-11-02", NEW.canonical, ivan.id)]
    # Пётр остался в базе, просто без карты.
    assert db.student_by_id(petr.id).card_code is None


def test_student_without_card_gets_one(db, math):
    ivan = db.get_or_create_student("Иванов Иван", "Г-1")
    result = db.replace_card(ivan.id, NEW)
    assert result.old_code is None
    assert db.find_student(NEW).id == ivan.id


def test_same_card_is_a_no_op(db, math):
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    db.mark(OLD, math, at=MONDAY)
    result = db.replace_card(ivan.id, OLD)
    assert result.old_code == OLD.canonical
    assert rows(db) == [("2026-11-02", OLD.canonical, ivan.id)]


def test_other_students_are_untouched(db, math):
    ivan = db.add_student(OLD, "Иванов Иван", "Г-1")
    anna = db.add_student(OTHER, "Анна Сидорова", "Г-1")
    db.mark(OTHER, math, at=MONDAY)
    db.replace_card(ivan.id, NEW)
    assert rows(db) == [("2026-11-02", OTHER.canonical, anna.id)]


def test_unknown_student(db):
    with pytest.raises(ValueError):
        db.replace_card(999, NEW)
