"""Мост между базой и присланными списками групп.

Раньше программа сама рисовала таблицы. Теперь состав групп задаёт
пользователь своими файлами, а наше дело — проставить в них отметки,
не тронув ни оформления, ни чужих строк.

Основное направление: база → файл. Файл можно испортить, переоткрыть
или выбросить — отметки останутся в базе и восстановятся следующей записью.
При расхождении главнее база. Обратное направление (import_subject)
делается перед каждой записью, но только добавляет недостающее — чтобы
дописанный руками студент не затёрся прочерком. Полная синхронизация,
где главнее таблица, — отдельное явное действие (sync_subject).

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
from .storage import (
    IMPORT_ADDED, IMPORT_KEPT, IMPORT_RAW, IMPORT_SAME, IMPORT_UPDATED,
    Storage, Subject,
)


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
    """Заполнить все списки групп внутри папки предмета.

    Сначала забираем из файлов то, чего нет в базе: без этого шага запись
    затёрла бы прочерком дописанное руками. Главнее при этом база —
    другое время в файле она не меняет. Файл, из
    которого забрать не удалось, не перезаписываем вовсе — иначе потеряли
    бы именно то, что прочитать не смогли.
    """
    imported = {r.path: r for r in import_subject(storage, folder)}
    marks = storage.marks_by_day(subject)
    # Группа студента известна из базы — её записали, когда привязывали карту.
    group_of = {
        R.normalize_name(s.full_name): s.group_name for s in storage.list_students()
    }
    results = []
    for path in folder.rosters:
        before = imported.get(path)
        if before is not None and not before.ok:
            results.append(FillResult(
                path, before.group_name,
                error=f"{before.error}; файл не перезаписан, чтобы не потерять его отметки",
            ))
        else:
            results.append(_fill_one(path, marks, group_of))
    return results


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


# ------------------------------------------------------- обратное направление


@dataclass(frozen=True, slots=True)
class Removal:
    """Отметка, которую синхронизация удалит: в таблице против неё прочерк."""

    full_name: str
    day: date
    at: datetime


@dataclass(frozen=True, slots=True)
class ImportResult:
    path: Path
    group_name: str
    added: int = 0       # новых отметок в базе
    updated: int = 0     # другое время, взято из файла (только синхронизация)
    conflicts: int = 0   # другое время, оставлено из базы (обычный режим)
    same: int = 0        # совпадает
    removed: tuple[Removal, ...] = ()
    # Прочерк в файле, но отметка сделана позже, чем файл сохраняли:
    # файл её просто не видел, удалять нельзя.
    newer: int = 0
    dates: tuple[date, ...] = ()
    # Непонятные ячейки: (где, что там). Не угадываем — показываем.
    skipped: tuple[tuple[str, str], ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class _DryRun(Exception):
    """Откатить пробный прогон."""


def import_subject(
    storage: Storage,
    folder: R.SubjectFolder,
    today: date | None = None,
    *,
    sync: bool = False,
    dry_run: bool = False,
) -> list[ImportResult]:
    """Втянуть в базу отметки, проставленные в файлах групп.

    Обычный режим — главнее база: добавляется только то, чего в ней нет,
    а другое время в файле базу не меняет.

    sync=True — полная синхронизация, главнее таблица: время берётся
    из файла, а отметки, против которых в файле прочерк или пусто,
    удаляются. Кроме сделанных позже, чем файл последний раз сохраняли:
    их файл видеть не мог — прочерк там поставил прошлый экспорт.

    dry_run=True — всё посчитать и откатить: чтобы показать человеку,
    что будет удалено, до того как удалять.

    Людей из списка заводим в базе без карты — связь с таблицей по ФИО.
    """
    if not dry_run:
        subject = storage.get_or_create_subject(folder.name)
        return [_import_one(storage, subject, path, today, sync) for path in folder.rosters]

    results: list[ImportResult] = []
    try:
        with storage.transaction():
            subject = storage.get_or_create_subject(folder.name)
            results = [_import_one(storage, subject, path, today, sync)
                       for path in folder.rosters]
            raise _DryRun
    except _DryRun:
        pass
    return results


def _import_one(
    storage: Storage, subject: Subject, path: Path, today: date | None, sync: bool
) -> ImportResult:
    try:
        parsed = R.read_roster(path)
        found = R.read_attendance(parsed, today)
        saved_at = datetime.fromtimestamp(path.stat().st_mtime)
    except R.RosterError as exc:
        return ImportResult(path, path.stem, error=str(exc))
    except PermissionError:
        return ImportResult(path, path.stem,
                            error="файл открыт в Excel — закройте и повторите")
    except Exception as exc:
        return ImportResult(path, path.stem,
                            error=f"не удалось прочитать ({type(exc).__name__}: {exc})")

    counts = {IMPORT_ADDED: 0, IMPORT_UPDATED: 0, IMPORT_KEPT: 0, IMPORT_SAME: 0}
    removed: list[Removal] = []
    newer = 0
    raw = f"{IMPORT_RAW}{path.name}"
    try:
        # Файл целиком или ничего: наполовину втянутая группа хуже, чем никакая.
        with storage.transaction():
            # Заводим весь список, а не только отмеченных: иначе в отчётах
            # не было бы видно, кто на занятии отсутствовал.
            people = {
                student.row: storage.get_or_create_student(
                    student.full_name, parsed.group_name
                )
                for student in parsed.students
            }
            for day_marks in found.marks.values():
                for student, at in day_marks:
                    status = storage.import_mark(
                        people[student.row], subject, at, raw=raw, file_wins=sync
                    )
                    counts[status] += 1

            if sync:
                for day, students in found.absent.items():
                    for student in students:
                        drop = []
                        for mark_id, at in storage.marks_of(people[student.row], subject, day):
                            if at > saved_at:
                                newer += 1
                            else:
                                drop.append(mark_id)
                                removed.append(Removal(student.full_name, day, at))
                        storage.delete_marks(drop)
    except Exception as exc:
        return ImportResult(
            path, parsed.group_name,
            error=f"не удалось записать в базу ({type(exc).__name__}: {exc}); "
                  "база не изменилась",
        )

    return ImportResult(
        path=path,
        group_name=parsed.group_name,
        added=counts[IMPORT_ADDED],
        updated=counts[IMPORT_UPDATED],
        conflicts=counts[IMPORT_KEPT],
        same=counts[IMPORT_SAME],
        removed=tuple(removed),
        newer=newer,
        dates=tuple(sorted(found.marks)),
        skipped=tuple(found.skipped),
    )


def sync_subject(
    storage: Storage, folder: R.SubjectFolder, today: date | None = None
) -> tuple[list[ImportResult], list[FillResult]]:
    """Полная синхронизация: таблицы → база, затем база → таблицы.

    После неё база и файлы совпадают: в базе то, что было в таблицах,
    а в таблицах появляются дни, которые были только в базе.
    """
    imported = import_subject(storage, folder, today, sync=True)
    subject = storage.get_or_create_subject(folder.name)
    return imported, fill_subject(storage, subject, folder)


def fill_all(storage: Storage, tables_dir: str | Path) -> dict[str, list[FillResult]]:
    """Пройти по всем папкам-предметам и заполнить всё, где были занятия."""
    out: dict[str, list[FillResult]] = {}
    for folder in R.discover_subjects(tables_dir):
        subject = storage.find_subject(folder.name)
        if subject is None:
            continue  # по этому предмету отметок ещё не было
        out[folder.name] = fill_subject(storage, subject, folder)
    return out
