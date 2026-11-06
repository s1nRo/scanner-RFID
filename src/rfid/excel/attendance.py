"""Отметки в файле группы: колонки с датами справа от ФИО.

Запись сохраняет оформление файла и не затирает того, что вписано руками.
Чтение возвращает найденное как есть, а непонятное не угадывает.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ..lessons import is_late, session_starts
from ..names import normalize_name
from .cells import ABSENT_MARK, DATE_HEADER_FORMAT, TIME_FORMAT, cell_time, header_date
from .roster import Roster, RosterStudent
from .save import save_atomically

LATE_FILL = PatternFill(fill_type="solid", fgColor="FFFFFF00")

_DATE_COLUMN_WIDTH = 7
_FULL_DATE_FORMAT = "%d.%m.%Y"
_NO_YEAR = date(2400, 12, 31)


# -------------------------------------------------------------------- запись


def write_attendance(
    roster: Roster,
    marks: dict[date, dict[str, datetime]],
    *,
    absent_mark: str = ABSENT_MARK,
    lesson_times: Mapping[date, Iterable[datetime]] | None = None,
) -> Path:
    """Проставить отметки в файле группы, сохранив его оформление.

    marks: дата -> {ФИО: время прихода}. ФИО сверяются без учёта регистра и «ё».
    Имена, которых нет в списке, молча не теряются — их возвращает сверка
    через unmatched_names().

    lesson_times: приходы всего занятия, по всем его группам. Опоздание
    считается от начала занятия, а не от первого пришедшего из этой группы.
    Не задано — занятие определяется по самим marks.
    """
    workbook = load_workbook(roster.path, read_only=False)
    try:
        sheet = workbook[roster.sheet_title]

        _unmerge_header(sheet, roster.header_row, roster.first_date_col)
        columns = _date_columns(sheet, roster)

        for day in sorted(marks):
            column = _find_date_column(columns, day)
            if column is None:
                column = _add_date_column(sheet, roster, columns, day)

            # Приходы сначала режутся на занятия — см. lessons.session_starts().
            times = lesson_times.get(day) if lesson_times else None
            starts = session_starts(marks[day].values() if times is None else times)
            present = {normalize_name(name): at for name, at in marks[day].items()}
            for student in roster.students:
                at = present.get(normalize_name(student.full_name))
                _free_cell(sheet, student.row, column)
                cell = sheet.cell(row=student.row, column=column)
                assert isinstance(cell, Cell), "объединение с ячейки снято выше"
                if at is None and cell_time(cell.value) is not None:
                    # В файле что-то стоит, а в базе отметки нет: время, вписанное
                    # руками, или непонятное «+». Файл главнее — не затираем.
                    continue
                cell.value = at.strftime(TIME_FORMAT) if at else absent_mark
                cell.alignment = Alignment(horizontal="center")
                if at is not None and is_late(at, starts):
                    cell.fill = LATE_FILL
                elif cell.fill == LATE_FILL:
                    # Повторный экспорт должен снимать устаревшую подсветку.
                    cell.fill = PatternFill()

        save_atomically(workbook, roster.path)
    finally:
        workbook.close()
    return roster.path


def clear_marks(
    roster: Roster,
    full_name: str,
    times: Iterable[datetime],
    *,
    today: date | None = None,
    absent_mark: str = ABSENT_MARK,
) -> int:
    """Убрать у студента приходы, поставленные этими отметками. Вернуть, сколько.

    Только ячейка, где стоит ровно это время: другое время или непонятное
    вписал человек, это не трогаем. Нечего убирать — файл не перезаписываем.
    """
    key = normalize_name(full_name)
    student = next((s for s in roster.students if normalize_name(s.full_name) == key), None)
    wanted = {at.date(): at for at in times}
    if student is None or not wanted:
        return 0
    today = today or date.today()
    workbook = load_workbook(roster.path)
    try:
        sheet = workbook[roster.sheet_title]
        cleared = 0
        for col in range(roster.first_date_col, sheet.max_column + 1):
            day = header_date(sheet.cell(row=roster.header_row, column=col).value, today)
            at = wanted.get(day) if day else None
            cell = sheet.cell(row=student.row, column=col)
            if at is None or not isinstance(cell, Cell):
                continue
            if cell_time(cell.value) == at.time().replace(second=0, microsecond=0):
                cell.value = absent_mark
                if cell.fill == LATE_FILL:
                    cell.fill = PatternFill()
                cleared += 1
        if cleared:
            save_atomically(workbook, roster.path)
        return cleared
    finally:
        workbook.close()


def _unmerge_header(sheet: Worksheet, row: int, from_col: int) -> None:
    """Снять объединения, мешающие писать в строку заголовка дат.

    В настоящем списке C2:Y2 объединено — без этого дату туда не положить.
    """
    for merged in list(sheet.merged_cells.ranges):
        if merged.min_row <= row <= merged.max_row and merged.max_col >= from_col:
            sheet.unmerge_cells(str(merged))


def _free_cell(sheet: Worksheet, row: int, col: int) -> None:
    """Снять объединение, накрывающее ячейку.

    Например, ФИО в B17:C17 занимает будущую ячейку отметки C17.
    Освобождается только она; значение в B17 сохраняется.
    """
    for merged in list(sheet.merged_cells.ranges):
        if (merged.min_row <= row <= merged.max_row
                and merged.min_col <= col <= merged.max_col):
            sheet.unmerge_cells(str(merged))


def _date_columns(sheet: Worksheet, roster: Roster) -> dict[str, int]:
    """Какие даты уже есть в шапке и в каких колонках.

    Заголовок понимаем так же, как чтение (header_date): «7.11» и «07.11.2026» —
    тот же день, что наш «07.11». Иначе рядом появлялась вторая колонка, а
    синхронизация принимала пустую первую за прочерк и объявляла отметку удалённой.

    Ключ — «дд.мм» или, если год указан явно, «дд.мм.гггг»: чужой год не совпадёт.
    """
    found: dict[str, int] = {}
    for col in range(roster.first_date_col, sheet.max_column + 1):
        value = sheet.cell(row=roster.header_row, column=col).value
        if isinstance(value, date):  # datetime тоже
            found.setdefault(value.strftime(_FULL_DATE_FORMAT), col)
            found.setdefault(value.strftime(DATE_HEADER_FORMAT), col)
        elif isinstance(value, str) and value.strip():
            # Год-заглушка високосный: иначе «29.02» без года не разобрать.
            day = header_date(value, _NO_YEAR)
            if day is None:
                found.setdefault(value.strip(), col)
            elif day.year == _NO_YEAR.year:
                found.setdefault(day.strftime(DATE_HEADER_FORMAT), col)
            else:
                found.setdefault(day.strftime(_FULL_DATE_FORMAT), col)
    return found


def _find_date_column(columns: dict[str, int], day: date) -> int | None:
    full = columns.get(day.strftime(_FULL_DATE_FORMAT))
    return full if full is not None else columns.get(day.strftime(DATE_HEADER_FORMAT))


def _first_free_column(sheet: Worksheet, roster: Roster) -> int:
    """Колонка правее всего, что вписано в шапку или в строки студентов.

    Занятая колонка может быть с заголовком-числом (номер недели) или вовсе
    без заголовка: там чужие данные, например оценки, вернуть их будет неоткуда.
    Ширину листа по max_column брать нельзя: шапка бывает объединена до Y.
    """
    rows = [roster.header_row, *(student.row for student in roster.students)]
    last = roster.first_date_col - 1
    for col in range(roster.first_date_col, sheet.max_column + 1):
        for row in rows:
            value = sheet.cell(row=row, column=col).value
            if value is not None and str(value).strip():
                last = col
                break
    return last + 1


def _add_date_column(
    sheet: Worksheet, roster: Roster, columns: dict[str, int], day: date
) -> int:
    """Завести колонку для нового дня правее всех занятых."""
    title = day.strftime(DATE_HEADER_FORMAT)
    column = _first_free_column(sheet, roster)
    columns[title] = column
    header = sheet.cell(row=roster.header_row, column=column, value=title)
    header.alignment = Alignment(horizontal="center")
    header.font = Font(bold=True)
    sheet.column_dimensions[get_column_letter(column)].width = _DATE_COLUMN_WIDTH
    return column


# -------------------------------------------------------------------- чтение


@dataclass(frozen=True, slots=True)
class FileMarks:
    """Отметки, найденные в файле группы."""

    # дата -> [(студент, время прихода)]
    marks: dict[date, list[tuple[RosterStudent, datetime]]]
    # Что не удалось понять: (заголовок колонки или ФИО, значение).
    # Такое не угадываем, а показываем человеку.
    skipped: list[tuple[str, str]]
    # дата -> кто в этот день явно не был (пусто или прочерк)
    absent: dict[date, list[RosterStudent]] = field(default_factory=dict)


def read_attendance(roster: Roster, today: date | None = None) -> FileMarks:
    """Прочитать уже проставленные в файле отметки."""
    today = today or date.today()
    # Обычный режим, не read_only: тот держит файл до close() и здесь
    # медленнее в 18 раз — см. read_roster.
    workbook = load_workbook(roster.path)
    try:
        sheet = workbook[roster.sheet_title]

        marks: dict[date, list[tuple[RosterStudent, datetime]]] = {}
        absent: dict[date, list[RosterStudent]] = {}
        skipped: list[tuple[str, str]] = []
        for col in range(roster.first_date_col, sheet.max_column + 1):
            head = sheet.cell(row=roster.header_row, column=col).value
            day = header_date(head, today)
            if day is None:
                continue  # не дата — чужая колонка, например «Примечание»
            for student in roster.students:
                value = cell_time(sheet.cell(row=student.row, column=col).value)
                if value is None:
                    absent.setdefault(day, []).append(student)
                elif isinstance(value, str):
                    skipped.append((f"{head}, {student.full_name}", value))
                else:
                    marks.setdefault(day, []).append(
                        (student, datetime.combine(day, value))
                    )
        return FileMarks(marks, skipped, absent)
    finally:
        workbook.close()
