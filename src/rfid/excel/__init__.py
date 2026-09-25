"""Работа с Excel: списки групп, которые даёт пользователь.

Источник истины по составу групп — сами файлы, а не наша база. Программа
их не создаёт: она находит, разбирает и дописывает в них колонки с датами.

    folders.py     предметы = папки в tables/, поиск файлов
    roster.py      разбор списка: шапка, группа, студенты
    cells.py       значения ячеек: время, прочерк, дата в заголовке
    attendance.py  отметки в файле: запись и чтение
    save.py        атомарная запись — сорвавшаяся не губит файл

Снаружи пользуются этим фасадом, а не модулями внутри.
"""

from .attendance import LATE_FILL, FileMarks, read_attendance, write_attendance
from .cells import ABSENT_MARK, DATE_HEADER_FORMAT, cell_time, header_date
from .folders import SubjectFolder, as_folder, discover_subjects, find_subject, misplaced_rosters
from .roster import Roster, RosterError, RosterStudent, read_roster, unmatched_names

__all__ = [
    "ABSENT_MARK", "DATE_HEADER_FORMAT", "LATE_FILL",
    "FileMarks", "Roster", "RosterError", "RosterStudent", "SubjectFolder",
    "as_folder", "cell_time", "discover_subjects", "find_subject", "header_date",
    "misplaced_rosters", "read_attendance", "read_roster", "unmatched_names",
    "write_attendance",
]
