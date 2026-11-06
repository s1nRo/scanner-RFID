"""Карта уходит от человека: ошибочная привязка должна реально исправляться.

Все сценарии — из ревью 07.11.2026: файл возвращал снятую отметку, ручная
отметка уходила с картой, чужие отметки присваивались молча.
"""

from datetime import datetime, timedelta

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from rfid import excel, journal
from rfid.db import MANUAL_RAW, Storage
from rfid.excel import as_folder
from tests.helpers import ROSTER_NAMES, active, card, make_roster_file, must, open_sheet

GROUP = "1000000/10001"
WRONG, RIGHT = ROSTER_NAMES[0], ROSTER_NAMES[1]
CARD = card("Em-Marine[A100] 007,42")
OTHER = card("Em-Marine[B200] 008,43")
TODAY = datetime.now().replace(hour=9, minute=0, second=30, microsecond=0)
# Прошлый день: заголовок «дд.мм» без года читается как не будущая дата.
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "tables" / "Предмет"
    folder.mkdir(parents=True)
    make_roster_file(folder / "group.xlsx", group=GROUP)
    return tmp_path


@pytest.fixture
def folder(home):
    return as_folder(home / "tables" / "Предмет")


@pytest.fixture
def storage(home):
    with Storage(home / "attendance.db") as st:
        yield st


@pytest.fixture
def subject(storage):
    return storage.get_or_create_subject("Предмет")


def present(storage, subject):
    return {name for day in storage.marks_by_day(subject).values() for name in day}


def cell_of(folder, name, at=TODAY):
    roster = excel.read_roster(folder.rosters[0])
    sheet = open_sheet(roster.path)
    row = next(s.row for s in roster.students if s.full_name == name)
    title = at.strftime(excel.DATE_HEADER_FORMAT)
    for col in range(roster.first_date_col, sheet.max_column + 1):
        if sheet.cell(row=roster.header_row, column=col).value == title:
            return sheet.cell(row=row, column=col).value
    return None


def bound_and_exported(storage, subject, folder):
    wrong, _ = storage.bind_card(CARD, WRONG, GROUP)
    storage.mark(CARD, subject, at=TODAY)
    journal.fill_subject(storage, subject, folder)
    assert cell_of(folder, WRONG) == "09:00"
    return wrong


class TestUnbindAndFile:
    def test_unbound_mark_does_not_come_back_from_the_file(
        self, home, storage, subject, folder
    ):
        wrong = bound_and_exported(storage, subject, folder)
        assert journal.unbind_card(storage, wrong, home / "tables") == 1
        journal.fill_subject(storage, subject, folder)
        assert present(storage, subject) == set()
        assert cell_of(folder, WRONG) == excel.ABSENT_MARK
        assert [u.card_code for u in storage.unknown_cards()] == [CARD.canonical]

    def test_giving_the_card_to_the_right_person(self, home, storage, subject, folder):
        wrong = bound_and_exported(storage, subject, folder)
        journal.unbind_card(storage, wrong, home / "tables")
        journal.fill_subject(storage, subject, folder)
        storage.bind_card(CARD, RIGHT, GROUP)
        journal.fill_subject(storage, subject, folder)
        assert present(storage, subject) == {RIGHT}
        assert cell_of(folder, RIGHT) == "09:00"
        assert cell_of(folder, WRONG) == excel.ABSENT_MARK

    def test_take_moves_the_cell_too(self, home, storage, subject, folder):
        wrong = bound_and_exported(storage, subject, folder)
        right = storage.get_or_create_student(RIGHT, GROUP)
        journal.retract_card_marks(storage, wrong, home / "tables")
        storage.replace_card(right.id, CARD, take=True)
        journal.fill_subject(storage, subject, folder)
        assert present(storage, subject) == {RIGHT}

    def test_time_edited_by_hand_stays(self, home, storage, subject, folder):
        """В ячейке другое время — его вписал человек, это не наша ячейка."""
        wrong = bound_and_exported(storage, subject, folder)
        roster = excel.read_roster(folder.rosters[0])
        workbook = load_workbook(roster.path)
        sheet = active(workbook)
        row = next(s.row for s in roster.students if s.full_name == WRONG)
        title = TODAY.strftime(excel.DATE_HEADER_FORMAT)
        col = next(c for c in range(roster.first_date_col, sheet.max_column + 1)
                   if sheet.cell(row=roster.header_row, column=c).value == title)
        sheet[f"{get_column_letter(col)}{row}"] = "09:40"
        workbook.save(roster.path)
        workbook.close()
        assert journal.unbind_card(storage, wrong, home / "tables") == 0
        assert cell_of(folder, WRONG) == "09:40"

    def test_only_this_students_cell_is_cleared(self, home, storage, subject, folder):
        wrong = bound_and_exported(storage, subject, folder)
        storage.bind_card(OTHER, RIGHT, GROUP)
        storage.mark(OTHER, subject, at=TODAY)
        journal.fill_subject(storage, subject, folder)
        journal.unbind_card(storage, wrong, home / "tables")
        assert cell_of(folder, RIGHT) == "09:00"

    def test_file_open_in_excel_changes_nothing(
        self, home, storage, subject, folder, monkeypatch
    ):
        wrong = bound_and_exported(storage, subject, folder)

        def locked(*_args, **_kwargs):
            raise PermissionError("файл занят")

        monkeypatch.setattr("rfid.excel.attendance.save_atomically", locked)
        with pytest.raises(journal.CardFilesBusy):
            journal.unbind_card(storage, wrong, home / "tables")
        assert must(storage.find_student(CARD)).full_name == WRONG
        assert present(storage, subject) == {WRONG}

    def test_broken_file_does_not_block(self, home, storage, subject, folder):
        wrong = bound_and_exported(storage, subject, folder)
        (folder.path / "broken.xlsx").write_bytes(b"")
        journal.unbind_card(storage, wrong, home / "tables")
        assert storage.find_student(CARD) is None

    def test_swapped_cards_are_fixed_by_unbinding_both(self, home, storage, subject, folder):
        """Подсказка окна: перепутаны — снять обе и привязать заново."""
        wrong, _ = storage.bind_card(CARD, WRONG, GROUP)
        right, _ = storage.bind_card(OTHER, RIGHT, GROUP)
        storage.mark(CARD, subject, at=TODAY)
        storage.mark(OTHER, subject, at=YESTERDAY)
        journal.fill_subject(storage, subject, folder)
        journal.unbind_card(storage, wrong, home / "tables")
        journal.unbind_card(storage, right, home / "tables")
        storage.bind_card(CARD, RIGHT, GROUP)
        storage.bind_card(OTHER, WRONG, GROUP)
        journal.fill_subject(storage, subject, folder)
        by_day = storage.marks_by_day(subject)
        assert set(by_day[TODAY.date()]) == {RIGHT}
        assert set(by_day[YESTERDAY.date()]) == {WRONG}


class TestManualMark:
    @pytest.fixture
    def manual(self, storage, subject):
        owner, _ = storage.bind_card(OTHER, RIGHT, GROUP)
        storage.import_mark(owner, subject, TODAY, raw=MANUAL_RAW)
        return owner

    def test_stays_when_the_card_is_taken(self, storage, subject, manual):
        taker, _ = storage.bind_card(CARD, WRONG, GROUP)
        storage.replace_card(taker.id, OTHER, take=True)
        assert present(storage, subject) == {RIGHT}

    def test_stays_when_the_card_is_removed(self, storage, subject, manual):
        storage.remove_student(OTHER)
        assert present(storage, subject) == {RIGHT}
        assert storage.unknown_cards() == []

    def test_not_counted_as_card_mark(self, storage, manual):
        assert storage.card_marks_of(manual.id) == []


class TestPreview:
    def test_counts_and_changes_nothing(self, storage, subject):
        ivan, _ = storage.bind_card(CARD, WRONG, GROUP)
        storage.mark(CARD, subject, at=TODAY)
        storage.mark(OTHER, subject, at=TODAY + timedelta(minutes=5))  # чужая, то же занятие
        storage.mark(OTHER, subject, at=YESTERDAY)
        preview = storage.preview_replace(ivan.id, OTHER)
        assert (preview.moved, preview.dropped) == (1, 1)
        assert storage.count_marks() == 3
        assert must(storage.student_by_id(ivan.id)).card_code == CARD.canonical
        assert {u.card_code for u in storage.unknown_cards()} == {OTHER.canonical}
        # И предсказание сходится с делом.
        result = storage.replace_card(ivan.id, OTHER)
        assert result.moved == 1
        assert storage.count_marks() == 2

    def test_new_card_without_marks_has_nothing_to_warn_about(self, storage, subject):
        ivan, _ = storage.bind_card(CARD, WRONG, GROUP)
        storage.mark(CARD, subject, at=TODAY)
        preview = storage.preview_replace(ivan.id, OTHER)
        assert (preview.moved, preview.dropped) == (0, 0)
