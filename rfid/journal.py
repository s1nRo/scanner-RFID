"""Мост между базой и присланными списками групп.

Раньше программа сама рисовала таблицы. Теперь состав групп задаёт
пользователь своими файлами, а наше дело — проставить в них отметки,
не тронув ни оформления, ни чужих строк.

Направление всегда одно: база → файл. Файл можно испортить, переоткрыть
или выбросить — отметки останутся в базе и восстановятся следующей записью.

Сопоставление идёт по ФИО: в присланных списках кодов карт нет, а связка
«карта → человек» живёт в базе. Отсюда слабое место — правка фамилии
в файле рвёт связь. Поэтому каждая запись возвращает список тех, кого
отметили, но в файле не нашли: молчать об этом нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import roster as R
from .storage import Storage, Subject


@dataclass(frozen=True, slots=True)
class FillResult:
    path: Path
    group_name: str
    students: int = 0
    dates: tuple[date, ...] = ()
    # Отмеченные из этой группы, чьих ФИО в файле не оказалось.
    missing: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def fill_subject(
    storage: Storage, subject: Subject, folder: R.SubjectFolder
) -> list[FillResult]:
    """Заполнить все списки групп внутри папки предмета."""
    marks = storage.marks_by_day(subject)
    # Группа студента известна из базы — её записали, когда привязывали карту.
    group_of = {
        R.normalize_name(s.full_name): s.group_name for s in storage.list_students()
    }
    return [_fill_one(path, marks, group_of) for path in folder.rosters]


def _fill_one(
    path: Path,
    marks: dict[date, dict[str, datetime]],
    group_of: dict[str, str],
) -> FillResult:
    # Ошибки ловятся пофайлово и широко: битый или чужой файл в папке не имеет
    # права лишить остальные группы записи. Один раз уже случалось — пустой
    # .xlsx рядом ронял выгрузку целиком, и отметки не доходили ни до кого.
    try:
        parsed = R.read_roster(path)
    except R.RosterError as exc:
        return FillResult(path, path.stem, error=str(exc))
    except Exception as exc:
        return FillResult(
            path, path.stem,
            error=f"не удалось прочитать ({type(exc).__name__}: {exc})",
        )

    try:
        R.write_attendance(parsed, marks)
    except PermissionError:
        return FillResult(
            path,
            parsed.group_name,
            len(parsed.students),
            error=("файл открыт в Excel — закройте и повторите. "
                   "Отметки в базе целы."),
        )
    except Exception as exc:
        return FillResult(
            path,
            parsed.group_name,
            len(parsed.students),
            error=(f"не удалось записать ({type(exc).__name__}: {exc}). "
                   "Отметки в базе целы, повторите rfid export."),
        )

    return FillResult(
        path=path,
        group_name=parsed.group_name,
        students=len(parsed.students),
        dates=tuple(sorted(marks)),
        missing=_missing_from_file(parsed, marks, group_of),
    )


def _missing_from_file(
    parsed: R.Roster,
    marks: dict[date, dict[str, datetime]],
    group_of: dict[str, str],
) -> tuple[str, ...]:
    """Кого отметили как студента этой группы, но в файле не нашли.

    Студенты других групп сюда не попадают: их в этом файле и не должно быть.
    А вот своя фамилия, пропавшая из списка, — это разорванная связь.
    """
    in_file = {R.normalize_name(s.full_name) for s in parsed.students}
    missing = {
        name
        for day in marks.values()
        for name in day
        if group_of.get(R.normalize_name(name)) == parsed.group_name
        and R.normalize_name(name) not in in_file
    }
    return tuple(sorted(missing))


def fill_all(storage: Storage, tables_dir: str | Path) -> dict[str, list[FillResult]]:
    """Пройти по всем папкам-предметам и заполнить всё, где были занятия."""
    out: dict[str, list[FillResult]] = {}
    for folder in R.discover_subjects(tables_dir):
        subject = storage.find_subject(folder.name)
        if subject is None:
            continue  # по этому предмету отметок ещё не было
        out[folder.name] = fill_subject(storage, subject, folder)
    return out
