"""Общие правила привязки для консоли и окна."""

import pytest

from rfid import enrollment
from rfid.db import CardConflict, MarkStatus, Storage
from rfid.excel import discover_subjects
from tests.helpers import ROSTER_NAMES, card, make_roster_file

CARD_A = card("Em-Marine[A100] 007,42")
CARD_B = card("Em-Marine[B200] 008,43")


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Предмет"
    path.mkdir()
    make_roster_file(path / "g1.xlsx", group="1/1")
    make_roster_file(path / "g2.xlsx", group="2/2")
    return discover_subjects(tmp_path)[0]


@pytest.fixture
def storage(tmp_path):
    with Storage(tmp_path / "a.db") as s:
        yield s


def test_numbers_run_through_all_groups(folder):
    items = enrollment.read_candidates(folder).items
    assert [c.number for c in items] == list(range(1, 2 * len(ROSTER_NAMES) + 1))
    assert {c.group_name for c in items} == {"1/1", "2/2"}


def test_namesake_with_a_card_does_not_hide_the_person(folder, storage):
    # Тот же человек по ФИО в другой группе с картой — не повод прятать этого.
    # Раньше консоль сверяла одно ФИО и прятала, окно — нет.
    storage.bind_card(CARD_A, ROSTER_NAMES[0], "1/1")
    items = enrollment.read_candidates(folder).items
    left = enrollment.without_card(items, storage)
    assert (ROSTER_NAMES[0], "2/2") in {(c.full_name, c.group_name) for c in left}
    assert (ROSTER_NAMES[0], "1/1") not in {(c.full_name, c.group_name) for c in left}
    assert enrollment.bound_count(items, storage) == 1


def test_name_spelling_does_not_matter(folder, storage):
    storage.bind_card(CARD_A, ROSTER_NAMES[0].upper(), "1/1")
    items = enrollment.read_candidates(folder).items
    assert enrollment.bound_count(items, storage) == 1


def test_broken_file_is_reported_not_fatal(folder, tmp_path):
    (folder.path / "битый.xlsx").write_bytes(b"")
    found = enrollment.read_candidates(discover_subjects(tmp_path)[0])
    assert found.items
    assert [path.name for path, _ in found.problems] == ["битый.xlsx"]


def test_past_marks_do_not_count_the_current_one(storage):
    subject = storage.get_or_create_subject("Предмет")
    assert storage.mark(CARD_A, subject).status is MarkStatus.UNKNOWN
    bound = enrollment.bind(storage, CARD_A, ROSTER_NAMES[0], "1/1", includes_current=True)
    assert bound.past == 0
    assert bound.student.full_name == ROSTER_NAMES[0]


def test_past_marks_when_binding_later(storage):
    subject = storage.get_or_create_subject("Предмет")
    storage.mark(CARD_A, subject)
    bound = enrollment.bind(storage, CARD_A, ROSTER_NAMES[0], "1/1")
    assert bound.past == 1


def test_second_card_is_a_conflict(storage):
    enrollment.bind(storage, CARD_A, ROSTER_NAMES[0], "1/1")
    with pytest.raises(CardConflict):
        enrollment.bind(storage, CARD_B, ROSTER_NAMES[0], "1/1")
