"""Отметка идёт в файл своей группы. Сценарии — из поиска ошибок 07.11.2026."""

from argparse import Namespace
from datetime import datetime, timedelta

import pytest
from openpyxl import load_workbook

from rfid import excel, journal
from rfid.cli import tables as cli_tables
from rfid.cli.reports import cmd_late, cmd_report
from rfid.cli.subjects import describe_folder
from rfid.db import CardConflict, Storage
from rfid.excel import as_folder
from tests.helpers import ROSTER_NAMES, active, card, make_roster_file, must

GROUP_A, GROUP_B = "111/1", "222/2"
OTHERS = ["Другой Дмитрий Дмитриевич", "Иная Инна Ивановна"]
NAME = ROSTER_NAMES[0]
CARD = card("Em-Marine[A100] 007,42")
SECOND = card("Em-Marine[B200] 008,43")
THIRD = card("Em-Marine[C300] 009,44")
NINE = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)


@pytest.fixture
def tables(tmp_path):
    return tmp_path / "tables"


@pytest.fixture
def folder_dir(tables):
    path = tables / "Предмет"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def storage(tmp_path):
    with Storage(tmp_path / "attendance.db") as st:
        yield st


@pytest.fixture
def subject(storage):
    return storage.get_or_create_subject("Предмет")


def row_cells(path, name):
    """Заголовок и ячейка студента по всем датам: {заголовок: значение}."""
    roster = excel.read_roster(path)
    workbook = load_workbook(path)
    try:
        sheet = active(workbook)
        row = next(s.row for s in roster.students if s.full_name == name)
        found = {}
        for col in range(roster.first_date_col, sheet.max_column + 1):
            head = sheet.cell(row=roster.header_row, column=col).value
            if head is not None:
                found[head] = sheet.cell(row=row, column=col).value
        return found
    finally:
        workbook.close()


def is_yellow(path, name, at):
    roster = excel.read_roster(path)
    workbook = load_workbook(path)
    try:
        sheet = active(workbook)
        row = next(s.row for s in roster.students if s.full_name == name)
        title = at.strftime(excel.DATE_HEADER_FORMAT)
        for col in range(roster.first_date_col, sheet.max_column + 1):
            if sheet.cell(row=roster.header_row, column=col).value == title:
                return sheet.cell(row=row, column=col).fill == excel.LATE_FILL
    finally:
        workbook.close()
    raise AssertionError("нет колонки даты")


TITLE = NINE.strftime(excel.DATE_HEADER_FORMAT)


class TestNamesake:
    def test_other_group_does_not_get_the_mark(self, storage, subject, folder_dir):
        a = make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        b = make_roster_file(folder_dir / "b.xlsx", group=GROUP_B)
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.mark(CARD, subject, at=NINE)
        folder = as_folder(folder_dir)
        journal.fill_subject(storage, subject, folder)
        journal.fill_subject(storage, subject, folder)  # второй проход не должен втянуть чужое
        assert row_cells(a, NAME)[TITLE] == "09:00"
        assert TITLE not in row_cells(b, NAME)
        twin = must(storage.student_by_name(NAME, GROUP_B))
        assert not storage.has_marks(twin.id)

    def test_both_namesakes_keep_their_own_time(self, storage, subject, folder_dir):
        a = make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        b = make_roster_file(folder_dir / "b.xlsx", group=GROUP_B)
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.bind_card(SECOND, NAME, GROUP_B)
        storage.mark(CARD, subject, at=NINE)
        storage.mark(SECOND, subject, at=NINE + timedelta(minutes=5))
        journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert row_cells(a, NAME)[TITLE] == "09:00"
        assert row_cells(b, NAME)[TITLE] == "09:05"


class TestGroupWithoutLesson:
    def test_no_column_of_dashes(self, storage, subject, folder_dir):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        b = make_roster_file(folder_dir / "b.xlsx", names=OTHERS, group=GROUP_B)
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.mark(CARD, subject, at=NINE)
        results = journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert TITLE not in row_cells(b, OTHERS[0])
        by_file = {r.path.name: r.dates for r in results}
        assert by_file["a.xlsx"] == (NINE.date(),)
        assert by_file["b.xlsx"] == ()

    def test_absent_classmates_still_get_a_dash(self, storage, subject, folder_dir):
        a = make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.mark(CARD, subject, at=NINE)
        journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert row_cells(a, ROSTER_NAMES[1])[TITLE] == excel.ABSENT_MARK


class TestLateMatchesWindow:
    def test_outsider_does_not_start_the_lesson(self, storage, subject, folder_dir):
        a = make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        storage.bind_card(THIRD, "Чужой Человек", "999/9")
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.mark(THIRD, subject, at=NINE - timedelta(minutes=30))
        storage.mark(CARD, subject, at=NINE)
        journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert not is_yellow(a, NAME, NINE)

    def test_shared_lecture_counts_from_the_first_group(self, storage, subject, folder_dir):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        b = make_roster_file(folder_dir / "b.xlsx", names=OTHERS, group=GROUP_B)
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.bind_card(SECOND, OTHERS[0], GROUP_B)
        storage.mark(CARD, subject, at=NINE)
        late = NINE + timedelta(minutes=30)
        storage.mark(SECOND, subject, at=late)
        journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert is_yellow(b, OTHERS[0], late)


class TestBrokenLink:
    def test_missing_name_is_reported(self, storage, subject, folder_dir):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        storage.bind_card(CARD, "Старая Фамилия Иванович", GROUP_A)
        storage.mark(CARD, subject, at=NINE)
        results = journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert results[0].missing == ("Старая Фамилия Иванович",)
        problems = journal.fill_problems(results)
        assert len(problems) == 1
        assert "Старая Фамилия Иванович" in problems[0]

    def test_clean_write_has_no_problems(self, storage, subject, folder_dir):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        storage.bind_card(CARD, NAME, GROUP_A)
        storage.mark(CARD, subject, at=NINE)
        results = journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert journal.fill_problems(results) == []

    def test_group_gone_still_writes_by_name(self, storage, subject, folder_dir):
        a = make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        storage.bind_card(CARD, NAME, "Старая группа")
        storage.mark(CARD, subject, at=NINE)
        journal.fill_subject(storage, subject, as_folder(folder_dir))
        assert row_cells(a, NAME)[TITLE] == "09:00"


class TestRenamedGroup:
    @pytest.fixture
    def renamed(self, storage, subject, folder_dir):
        """Карта привязана в старой группе, а файл уже называет её иначе."""
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        student, _ = storage.bind_card(CARD, NAME, "Старая группа")
        storage.mark(CARD, subject, at=NINE)
        return student

    def test_found(self, storage, tables, renamed):
        moves = journal.renamed_groups(storage, tables)
        assert [(m.student.id, m.new_group) for m in moves] == [(renamed.id, GROUP_A)]

    def test_person_without_card_or_marks_is_not_asked(self, storage, tables, folder_dir):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        storage.get_or_create_student(NAME, "Старая группа")
        assert journal.renamed_groups(storage, tables) == []

    def test_live_group_is_not_touched(self, storage, tables, folder_dir):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)
        make_roster_file(folder_dir / "b.xlsx", group=GROUP_B)
        storage.bind_card(CARD, NAME, GROUP_A)
        assert journal.renamed_groups(storage, tables) == []

    def test_name_in_two_groups_is_not_guessed(self, storage, tables, folder_dir, renamed):
        make_roster_file(folder_dir / "b.xlsx", group=GROUP_B)
        assert journal.renamed_groups(storage, tables) == []

    def test_unreadable_file_means_no_question(self, storage, tables, folder_dir, renamed):
        (folder_dir / "broken.xlsx").write_bytes(b"")
        assert journal.renamed_groups(storage, tables) == []

    def test_move_merges_the_import_twin(self, storage, subject, folder_dir, renamed):
        folder = as_folder(folder_dir)
        journal.fill_subject(storage, subject, folder)  # импорт завёл второго человека
        twin = must(storage.student_by_name(NAME, GROUP_A))
        assert twin.id != renamed.id
        moved = storage.move_to_group(renamed.id, GROUP_A)
        assert moved.card_code == CARD.canonical
        assert moved.group_name == GROUP_A
        assert storage.student_by_id(twin.id) is None
        assert must(storage.find_student(CARD)).group_name == GROUP_A
        assert storage.count_marks() == 1  # живой приход остался, копия из таблицы нет
        journal.fill_subject(storage, subject, folder)
        assert storage.count_marks() == 1

    def test_move_keeps_twins_other_days(self, storage, subject, renamed):
        twin = storage.get_or_create_student(NAME, GROUP_A)
        other_day = NINE - timedelta(days=2)
        storage.import_mark(twin, subject, other_day, raw="таблица: a.xlsx")
        storage.move_to_group(renamed.id, GROUP_A)
        assert storage.count_marks() == 2
        days = storage.marks_by_day(subject)
        assert set(days) == {NINE.date(), other_day.date()}

    def test_two_cards_are_not_merged(self, storage, renamed):
        storage.bind_card(SECOND, NAME, GROUP_A)
        with pytest.raises(CardConflict):
            storage.move_to_group(renamed.id, GROUP_A)
        assert must(storage.find_student(CARD)).group_name == "Старая группа"
        assert must(storage.find_student(SECOND)).group_name == GROUP_A

    def test_console_asks_and_moves(self, storage, tables, renamed, monkeypatch):
        monkeypatch.setattr(cli_tables.common, "confirm", lambda _text: True)
        cli_tables._offer_group_moves(storage, tables)
        assert must(storage.find_student(CARD)).group_name == GROUP_A

    def test_console_refusal_changes_nothing(self, storage, tables, renamed, monkeypatch):
        monkeypatch.setattr(cli_tables.common, "confirm", lambda _text: False)
        cli_tables._offer_group_moves(storage, tables)
        assert must(storage.find_student(CARD)).group_name == "Старая группа"


class TestConsoleReports:
    @pytest.fixture
    def two_subjects(self, tables, tmp_path):
        """Два предмета с разными группами; карту приложили на первом."""
        for name, group, names in (("Первый", GROUP_A, ROSTER_NAMES), ("Второй", GROUP_B, OTHERS)):
            (tables / name).mkdir(parents=True)
            make_roster_file(tables / name / "g.xlsx", names=names, group=group)
        db = tmp_path / "attendance.db"
        with Storage(db) as st:
            st.bind_card(CARD, NAME, GROUP_A)
            st.bind_card(SECOND, OTHERS[0], GROUP_B)
            st.mark(CARD, st.get_or_create_subject("Первый"), at=NINE)
        return db

    def args(self, db, tables, **extra):
        return Namespace(db=db, tables=tables, subject="Первый",
                         date=NINE.strftime("%d.%m.%Y"), group=None, **extra)

    def test_report_shows_only_this_subject(self, two_subjects, tables, capsys):
        cmd_report(self.args(two_subjects, tables))
        out = capsys.readouterr().out
        assert NAME in out
        assert OTHERS[0] not in out
        assert GROUP_B not in out

    def test_group_flag_limits_the_totals_too(self, two_subjects, tables, capsys):
        args = self.args(two_subjects, tables)
        args.group = "нет такой"
        cmd_report(args)
        assert "1/" not in capsys.readouterr().out

    def test_late_ignores_outsiders(self, tables, tmp_path, capsys):
        (tables / "Первый").mkdir(parents=True)
        make_roster_file(tables / "Первый" / "g.xlsx", group=GROUP_A)
        db = tmp_path / "a.db"
        with Storage(db) as st:
            subject = st.get_or_create_subject("Первый")
            st.bind_card(THIRD, "Чужой Человек", "999/9")
            st.bind_card(CARD, NAME, GROUP_A)
            st.mark(THIRD, subject, at=NINE - timedelta(minutes=40))
            st.mark(CARD, subject, at=NINE)
        cmd_late(self.args(db, tables))
        assert "Опоздавших нет" in capsys.readouterr().out

    def test_busy_file_does_not_break_the_menu(self, folder_dir, monkeypatch):
        make_roster_file(folder_dir / "a.xlsx", group=GROUP_A)

        def busy(_path):
            raise PermissionError("занят")

        monkeypatch.setattr(excel, "read_roster", busy)
        assert "занят" in describe_folder(as_folder(folder_dir))
