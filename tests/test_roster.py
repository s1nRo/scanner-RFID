"""Тесты чтения и заполнения списков групп.

Образец воспроизводит формат списка с вымышленными данными: объединённые ячейки,
номера-формулы в колонке A и примечание «Староста» под списком. Именно
на этих особенностях наивный разбор и ломается.
"""

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from rfid import roster as R

NAMES = [
    "Тестов Тест Тестович",
    "Примеров Пример Примерович",
    "Образцова Проба Тестовна",
    "Макетов Пётр Тестович",
]

DAY_ONE = date(2026, 9, 20)
DAY_TWO = date(2026, 9, 22)


def make_roster_file(path: Path, names=NAMES, group="1000000/10001") -> Path:
    """Копия формата пользователя со всеми его неудобствами."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Лист1"

    sheet["A1"] = f"Группа {group}"
    sheet.merge_cells("A1:Y1")

    sheet["A2"] = "№"
    sheet["B2"] = "ФИО студента"
    sheet.merge_cells("C2:Y2")  # пустая объединённая шапка под даты

    for index, name in enumerate(names):
        row = 3 + index
        # Номера именно формулами, как в настоящем файле.
        sheet.cell(row=row, column=1, value=1 if index == 0 else f"=A{row - 1}+1")
        sheet.cell(row=row, column=2, value=name)

    note_row = 3 + len(names)
    sheet.cell(row=note_row, column=3, value="Староста: Тестовый Студент; контакт скрыт")
    sheet.merge_cells(start_row=note_row, start_column=3, end_row=note_row, end_column=25)

    workbook.save(path)
    return path


@pytest.fixture
def roster_file(tmp_path):
    return make_roster_file(tmp_path / "группа.xlsx")


class TestReading:
    def test_group_from_title_row(self, roster_file):
        assert R.read_roster(roster_file).group_name == "1000000/10001"

    def test_finds_all_students(self, roster_file):
        students = R.read_roster(roster_file).students
        assert [s.full_name for s in students] == NAMES

    def test_numbering_is_positional_not_from_formulas(self, roster_file):
        """В колонке A формулы — брать номер оттуда нельзя."""
        students = R.read_roster(roster_file).students
        assert [s.number for s in students] == [1, 2, 3, 4]

    def test_note_below_list_is_not_a_student(self, roster_file):
        """«Староста: …» лежит ниже списка и в студенты попасть не должен."""
        names = [s.full_name for s in R.read_roster(roster_file).students]
        assert not any("Староста" in n for n in names)

    def test_rows_point_at_real_cells(self, roster_file):
        sheet = load_workbook(roster_file).active
        for student in R.read_roster(roster_file).students:
            assert sheet.cell(row=student.row, column=2).value == student.full_name

    def test_dates_start_right_after_names(self, roster_file):
        parsed = R.read_roster(roster_file)
        assert parsed.name_col == 2
        assert parsed.first_date_col == 3

    def test_group_code_found_without_the_word(self, tmp_path):
        """Пример «ИКНК 1000000/10002» без слова «Группа»."""
        path = make_roster_file(tmp_path / "Список_10002.xlsx")
        workbook = load_workbook(path)
        workbook.active["A1"] = "ИКНК 1000000/10002"
        workbook.save(path)
        assert R.read_roster(path).group_name == "1000000/10002"

    def test_plain_text_above_header_is_used(self, tmp_path):
        path = make_roster_file(tmp_path / "файл.xlsx")
        workbook = load_workbook(path)
        workbook.active["A1"] = "Первый курс, поток А"
        workbook.save(path)
        assert R.read_roster(path).group_name == "Первый курс, поток А"

    def test_group_falls_back_to_filename(self, tmp_path):
        path = tmp_path / "БИ-11.xlsx"
        make_roster_file(path, group="")
        workbook = load_workbook(path)
        workbook.active["A1"] = None
        workbook.save(path)
        assert R.read_roster(path).group_name == "БИ-11"

    def test_file_without_header_is_rejected(self, tmp_path):
        path = tmp_path / "мусор.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = "что-то не то"
        workbook.save(path)
        with pytest.raises(R.RosterError):
            R.read_roster(path)


class TestNameMatching:
    def test_case_and_yo_are_ignored(self, roster_file):
        parsed = R.read_roster(roster_file)
        assert parsed.find("макетов пётр тестович") is not None
        assert parsed.find("МАКЕТОВ ПЕТР ТЕСТОВИЧ") is not None

    def test_extra_spaces_ignored(self, roster_file):
        parsed = R.read_roster(roster_file)
        assert parsed.find("  Образцова   Проба  Тестовна ") is not None

    def test_unknown_name_reported(self, roster_file):
        parsed = R.read_roster(roster_file)
        assert R.unmatched_names(parsed, ["Образцова Проба Тестовна", "Кто-то Левый"]) == [
            "Кто-то Левый"
        ]


class TestWriting:
    def test_merged_name_does_not_block_attendance(self, tmp_path):
        names = [f"Студент {i}" for i in range(15)]
        path = make_roster_file(tmp_path / "merged.xlsx", names=names)
        workbook = load_workbook(path)
        sheet = workbook.active
        sheet.merge_cells("B17:C17")
        sheet.merge_cells("D17:E17")
        workbook.save(path)

        parsed = R.read_roster(path)
        marks = {DAY_ONE: {
            names[0]: datetime(2026, 9, 20, 9, 0),
            names[-1]: datetime(2026, 9, 20, 9, 10),
        }}
        R.write_attendance(parsed, marks)
        R.write_attendance(R.read_roster(path), marks)

        sheet = load_workbook(path).active
        assert sheet["B17"].value == names[-1]
        assert sheet["A17"].value == "=A16+1"
        assert sheet["C17"].value == "09:10"
        assert sheet["C17"].fill.fgColor.rgb == "FFFFFF00"
        assert "B17:C17" not in sheet.merged_cells
        assert "D17:E17" in sheet.merged_cells
        assert "A1:Y1" in sheet.merged_cells
        assert "C18:Y18" in sheet.merged_cells
        assert [s.full_name for s in R.read_roster(path).students] == names

    def test_late_threshold_includes_exactly_ten_minutes(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {
            NAMES[0]: datetime(2026, 9, 20, 9, 0, 30),
            NAMES[1]: datetime(2026, 9, 20, 9, 10, 29),
            NAMES[2]: datetime(2026, 9, 20, 9, 10, 30),
            NAMES[3]: datetime(2026, 9, 20, 9, 15),
        }})
        sheet = load_workbook(roster_file).active
        assert sheet["C3"].fill.patternType is None
        assert sheet["C4"].fill.patternType is None
        for address in ("C5", "C6"):
            assert sheet[address].fill.patternType == "solid"
            assert sheet[address].fill.fgColor.rgb == "FFFFFF00"

    def test_late_threshold_is_independent_for_each_day(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {
            DAY_ONE: {
                NAMES[0]: datetime(2026, 9, 20, 9, 0),
                NAMES[1]: datetime(2026, 9, 20, 9, 10),
            },
            DAY_TWO: {NAMES[1]: datetime(2026, 9, 22, 12, 0)},
        })
        sheet = load_workbook(roster_file).active
        assert sheet["C4"].fill.patternType == "solid"
        assert sheet["D4"].fill.patternType is None
        assert sheet["C5"].fill.patternType is None  # отсутствующий

    @pytest.mark.parametrize("still_present", [True, False])
    def test_reexport_clears_outdated_late_fill(self, roster_file, still_present):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {
            NAMES[0]: datetime(2026, 9, 20, 9, 0),
            NAMES[1]: datetime(2026, 9, 20, 9, 10),
        }})
        updated = {NAMES[1]: datetime(2026, 9, 20, 9, 10)} if still_present else {}
        R.write_attendance(parsed, {DAY_ONE: updated})
        sheet = load_workbook(roster_file).active
        assert sheet["C4"].fill.patternType is None
        assert sheet["C4"].value == ("09:10" if still_present else R.ABSENT_MARK)

    def test_date_column_added(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {NAMES[0]: datetime(2026, 9, 20, 9, 2)}})

        sheet = load_workbook(roster_file).active
        assert sheet.cell(row=2, column=3).value == "20.09"
        assert sheet.cell(row=3, column=3).value == "09:02"

    def test_absent_get_a_dash(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {NAMES[0]: datetime(2026, 9, 20, 9, 2)}})
        sheet = load_workbook(roster_file).active
        assert sheet.cell(row=4, column=3).value == R.ABSENT_MARK

    def test_second_date_goes_to_next_column(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {
            DAY_ONE: {NAMES[0]: datetime(2026, 9, 20, 9, 2)},
            DAY_TWO: {NAMES[1]: datetime(2026, 9, 22, 9, 5)},
        })
        sheet = load_workbook(roster_file).active
        assert sheet.cell(row=2, column=3).value == "20.09"
        assert sheet.cell(row=2, column=4).value == "22.09"

    def test_rewriting_same_date_reuses_column(self, roster_file):
        """Повторный экспорт не должен плодить колонки за один и тот же день."""
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {NAMES[0]: datetime(2026, 9, 20, 9, 2)}})
        R.write_attendance(parsed, {DAY_ONE: {NAMES[1]: datetime(2026, 9, 20, 9, 7)}})

        sheet = load_workbook(roster_file).active
        assert sheet.cell(row=2, column=3).value == "20.09"
        assert sheet.cell(row=2, column=4).value is None
        assert sheet.cell(row=3, column=3).value == R.ABSENT_MARK
        assert sheet.cell(row=4, column=3).value == "09:07"

    def test_names_and_numbers_are_not_touched(self, roster_file):
        before = load_workbook(roster_file).active
        names_before = [before.cell(row=r, column=2).value for r in range(3, 7)]

        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {NAMES[0]: datetime(2026, 9, 20, 9, 2)}})

        after = load_workbook(roster_file).active
        assert [after.cell(row=r, column=2).value for r in range(3, 7)] == names_before

    def test_note_under_the_list_survives(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {}})
        sheet = load_workbook(roster_file).active
        note = sheet.cell(row=3 + len(NAMES), column=3).value
        assert note and "Староста" in note

    def test_merged_header_is_released(self, roster_file):
        """C2:Y2 объединено — без снятия дату туда не записать."""
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {}})
        sheet = load_workbook(roster_file).active
        assert "C2:Y2" not in [str(r) for r in sheet.merged_cells.ranges]

    def test_reparse_after_write_is_stable(self, roster_file):
        parsed = R.read_roster(roster_file)
        R.write_attendance(parsed, {DAY_ONE: {NAMES[0]: datetime(2026, 9, 20, 9, 2)}})
        again = R.read_roster(roster_file)
        assert [s.full_name for s in again.students] == NAMES
        assert again.group_name == "1000000/10001"


class TestSubjectFolders:
    def test_folders_with_lists_are_subjects(self, tmp_path):
        for name in ("Бургеростроение 1 курс", "Матанализ 2 курс"):
            (tmp_path / name).mkdir()
            make_roster_file(tmp_path / name / "г.xlsx")
        assert [s.name for s in R.discover_subjects(tmp_path)] == [
            "Бургеростроение 1 курс",
            "Матанализ 2 курс",
        ]

    def test_empty_folders_are_skipped(self, tmp_path):
        """Выбирать в папке без .xlsx нечего — не показываем её."""
        (tmp_path / "Пустая").mkdir()
        assert R.discover_subjects(tmp_path) == []

    def test_root_is_not_a_subject(self, tmp_path):
        """tables — корень, где лежат папки предметов, а не предмет сам."""
        make_roster_file(tmp_path / "россыпью.xlsx")
        assert R.discover_subjects(tmp_path) == []

    def test_only_subfolders_are_returned(self, tmp_path):
        make_roster_file(tmp_path / "россыпью.xlsx")
        (tmp_path / "Физика").mkdir()
        make_roster_file(tmp_path / "Физика" / "г.xlsx")
        assert [f.name for f in R.discover_subjects(tmp_path)] == ["Физика"]

    def test_misplaced_files_are_listed_separately(self, tmp_path):
        make_roster_file(tmp_path / "забытый.xlsx")
        (tmp_path / "Физика").mkdir()
        make_roster_file(tmp_path / "Физика" / "г.xlsx")
        assert [p.name for p in R.misplaced_rosters(tmp_path)] == ["забытый.xlsx"]

    def test_rosters_inside_are_listed(self, tmp_path):
        folder = tmp_path / "Физика 1 курс"
        folder.mkdir()
        make_roster_file(folder / "группа1.xlsx")
        make_roster_file(folder / "группа2.xlsx")
        subject = R.discover_subjects(tmp_path)[0]
        assert subject.group_count == 2

    def test_excel_lock_files_ignored(self, tmp_path):
        folder = tmp_path / "Физика"
        folder.mkdir()
        make_roster_file(folder / "группа.xlsx")
        (folder / "~$группа.xlsx").write_bytes(b"lock")
        assert R.discover_subjects(tmp_path)[0].group_count == 1

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert R.discover_subjects(tmp_path / "нет") == []

    def test_as_folder_takes_any_directory(self, tmp_path):
        make_roster_file(tmp_path / "г.xlsx")
        folder = R.as_folder(tmp_path)
        assert folder.name == tmp_path.name
        assert folder.group_count == 1


@pytest.fixture
def full_roster_file(tmp_path):
    names = [f"Тестовый Студент {i:02d}" for i in range(1, 26)]
    return make_roster_file(tmp_path / "полный_тестовый_список.xlsx", names=names)


class TestFullRoster:
    """Полный список генерируется без зависимости от личных таблиц."""

    def test_parses(self, full_roster_file):
        parsed = R.read_roster(full_roster_file)
        assert parsed.group_name == "1000000/10001"
        assert len(parsed.students) == 25
        assert parsed.students[0].full_name == "Тестовый Студент 01"
        assert parsed.students[-1].full_name == "Тестовый Студент 25"

    def test_note_not_counted(self, full_roster_file):
        names = [s.full_name for s in R.read_roster(full_roster_file).students]
        assert not any("Староста" in n for n in names)
