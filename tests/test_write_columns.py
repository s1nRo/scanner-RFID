"""Новая колонка даты не имеет права занять колонку с чужими данными."""

import zipfile
from datetime import date, datetime

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image
from openpyxl.utils import get_column_letter

from rfid import excel
from tests.helpers import ROSTER_NAMES, active, make_roster_file

DAY = date(2026, 11, 2)
AT = datetime(2026, 11, 2, 9, 0)
NEXT_DAY = date(2026, 11, 3)
NEXT_AT = datetime(2026, 11, 3, 9, 0)


def put(path, cells):
    """cells: [(строка, колонка, значение)]"""
    workbook = load_workbook(path)
    sheet = active(workbook)
    for row, col, value in cells:
        sheet[f"{get_column_letter(col)}{row}"] = value
    workbook.save(path)
    workbook.close()


def value(path, row, col):
    workbook = load_workbook(path)
    try:
        return active(workbook).cell(row=row, column=col).value
    finally:
        workbook.close()


def write(path, day=DAY, at=AT):
    roster = excel.read_roster(path)
    excel.write_attendance(roster, {day: {ROSTER_NAMES[0]: at}})
    return excel.read_roster(path)


def student_row(roster):
    return next(s.row for s in roster.students if s.full_name == ROSTER_NAMES[0])


def test_numeric_header_and_grades_survive(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col, head, row = roster.first_date_col, roster.header_row, student_row(roster)
    put(path, [(head, col, 1), (row, col, 5)])
    write(path)
    assert value(path, head, col) == 1
    assert value(path, row, col) == 5
    assert value(path, head, col + 1) == DAY.strftime(excel.DATE_HEADER_FORMAT)
    assert value(path, row, col + 1) == "09:00"


def test_column_without_header_but_with_data_survives(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col, row = roster.first_date_col, student_row(roster)
    put(path, [(row, col, "сдал")])
    write(path)
    assert value(path, row, col) == "сдал"
    assert value(path, row, col + 1) == "09:00"


def test_text_column_in_the_middle_is_kept_and_days_go_right(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col, head = roster.first_date_col, roster.header_row
    write(path)
    put(path, [(head, col + 1, "Примечание")])
    write(path, NEXT_DAY, NEXT_AT)
    assert value(path, head, col) == DAY.strftime(excel.DATE_HEADER_FORMAT)
    assert value(path, head, col + 1) == "Примечание"
    assert value(path, head, col + 2) == NEXT_DAY.strftime(excel.DATE_HEADER_FORMAT)


def test_plain_file_still_starts_right_after_the_names(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    write(path)
    assert value(path, roster.header_row, roster.first_date_col) == DAY.strftime(
        excel.DATE_HEADER_FORMAT
    )


def test_existing_day_is_reused(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col, head = roster.first_date_col, roster.header_row
    write(path)
    write(path)
    assert value(path, head, col + 1) is None


@pytest.mark.parametrize("head", ["2.11", "02.11", "02.11.2026", "2.11.26",
                                  datetime(2026, 11, 2)])
def test_same_day_written_differently_is_reused(tmp_path, head):
    """Вторая колонка рядом дала бы пустую первую, а её синхронизация считает прочерком."""
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col = roster.first_date_col
    put(path, [(roster.header_row, col, head)])
    write(path)
    assert value(path, student_row(roster), col) == "09:00"
    assert value(path, roster.header_row, col + 1) is None


def test_other_year_is_a_different_day(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col = roster.first_date_col
    put(path, [(roster.header_row, col, "02.11.2025"), (student_row(roster), col, "08:00")])
    write(path)
    assert value(path, student_row(roster), col) == "08:00"
    assert value(path, student_row(roster), col + 1) == "09:00"


def test_writing_twice_does_not_grow_columns(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col = roster.first_date_col
    put(path, [(roster.header_row, col, "2.11")])
    write(path)
    write(path)
    assert value(path, roster.header_row, col + 1) is None


def add_sheet(path, *, active_index):
    workbook = load_workbook(path)
    workbook.create_sheet("Заметки")["A1"] = "что-то своё"
    workbook.active = active_index
    workbook.save(path)
    workbook.close()


def test_roster_found_when_saved_on_another_sheet(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    title = excel.read_roster(path).sheet_title
    add_sheet(path, active_index=1)
    roster = excel.read_roster(path)
    assert roster.sheet_title == title
    assert [s.full_name for s in roster.students] == ROSTER_NAMES


def test_writing_goes_to_the_roster_sheet_not_the_active_one(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    add_sheet(path, active_index=1)
    roster = write(path)
    workbook = load_workbook(path)
    try:
        sheet = workbook[roster.sheet_title]
        assert sheet.cell(row=student_row(roster), column=roster.first_date_col).value == "09:00"
        assert workbook["Заметки"]["A1"].value == "что-то своё"
    finally:
        workbook.close()


def test_book_without_a_list_is_still_refused(tmp_path):
    path = tmp_path / "g.xlsx"
    workbook = Workbook()
    active(workbook)["A1"] = "просто текст"
    workbook.create_sheet("Второй")["A1"] = "и тут текст"
    workbook.save(path)
    workbook.close()
    with pytest.raises(excel.RosterError, match="ФИО"):
        excel.read_roster(path)


def test_picture_survives_writing(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image as PilImage  # pyright: ignore[reportMissingImports]

    picture = tmp_path / "logo.png"
    PilImage.new("RGB", (4, 4), "red").save(picture)
    path = make_roster_file(tmp_path / "g.xlsx")
    workbook = load_workbook(path)
    active(workbook).add_image(Image(str(picture)), "H1")
    workbook.save(path)
    workbook.close()
    write(path)
    with zipfile.ZipFile(path) as saved:
        assert any(name.startswith("xl/media/") for name in saved.namelist())


def test_note_below_the_list_does_not_push_the_column(tmp_path):
    path = make_roster_file(tmp_path / "g.xlsx")
    roster = excel.read_roster(path)
    col = roster.first_date_col
    last = max(s.row for s in roster.students)
    put(path, [(last + 3, col + 4, "Староста: кто-то")])
    write(path)
    assert value(path, roster.header_row, col) == DAY.strftime(excel.DATE_HEADER_FORMAT)
