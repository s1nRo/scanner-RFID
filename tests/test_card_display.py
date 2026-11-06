"""Человек видит код карты десятичным (007,42), база хранит полный."""

from argparse import Namespace

import pytest

from rfid.cli.common import Interrupted
from rfid.cli.reports import cmd_students, cmd_unknown
from rfid.db import Storage
from rfid.scanner import parse_manual, pretty_code, short_form
from tests.helpers import card, must

GROUP = "1000000/10001"
CARD = card("Em-Marine[A100] 007,42")
TWIN = card("Em-Marine[C300] 007,42")  # другая карта, короткий вид тот же
OTHER = card("Em-Marine[B200] 008,43")


def test_pretty_code():
    assert pretty_code(CARD.canonical) == "007,42"


def test_pretty_code_of_garbage_shows_it_as_is():
    assert pretty_code("не код") == "не код"


@pytest.mark.parametrize(("text", "expected"), [
    ("007,42", "007,42"), ("7,42", "007,42"), (" 007,00042 ", "007,42"),
    ("A10007002A", None), ("", None),
])
def test_short_form(text, expected):
    assert short_form(text) == expected


def test_manual_input_still_refuses_short_form():
    with pytest.raises(ValueError):
        parse_manual("007,42")


@pytest.fixture
def db(tmp_path):
    return tmp_path / "attendance.db"


def remove(db, code):
    cmd_students(Namespace(db=db, tables=db.parent / "tables", students_action="remove",
                           code=code))


def bound(db, *pairs):
    with Storage(db) as storage:
        for code, name in pairs:
            storage.bind_card(code, name, GROUP)


def owner(db, code):
    with Storage(db) as storage:
        return storage.find_student(code)


def test_students_list_is_decimal(db, capsys):
    bound(db, (CARD, "Иванов Иван"))
    cmd_students(Namespace(db=db, students_action="list"))
    out = capsys.readouterr().out
    assert "007,42" in out
    assert CARD.canonical not in out


def test_unknown_list_is_decimal(db, capsys):
    with Storage(db) as storage:
        storage.mark(CARD, storage.get_or_create_subject("Предмет"))
    cmd_unknown(Namespace(db=db))
    out = capsys.readouterr().out
    assert "007,42" in out
    assert CARD.canonical not in out


def test_remove_by_code_from_the_screen(db, capsys):
    bound(db, (CARD, "Иванов Иван"), (OTHER, "Петров Пётр"))
    remove(db, "007,42")
    assert "снята" in capsys.readouterr().out
    assert owner(db, CARD) is None
    assert must(owner(db, OTHER)).full_name == "Петров Пётр"


def test_remove_unknown_short_code_says_so(db, capsys):
    bound(db, (CARD, "Иванов Иван"))
    remove(db, "009,44")
    assert "нет" in capsys.readouterr().out
    assert owner(db, CARD) is not None


def test_remove_lookalike_asks_for_full_code(db):
    """Короткий вид совпал — угадывать нельзя: снимем не ту карту."""
    bound(db, (CARD, "Иванов Иван"), (TWIN, "Петров Пётр"))
    with pytest.raises(Interrupted, match=CARD.canonical):
        remove(db, "007,42")
    assert owner(db, CARD) is not None
    assert owner(db, TWIN) is not None
    remove(db, TWIN.canonical)
    assert owner(db, TWIN) is None
    assert owner(db, CARD) is not None
