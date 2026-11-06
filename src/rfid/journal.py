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

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import excel
from .db import (
    IMPORT_RAW,
    Arrival,
    CardMark,
    DayRow,
    GroupStat,
    ImportStatus,
    Storage,
    Student,
    Subject,
)
from .excel import SubjectFolder, discover_subjects
from .names import normalize_name

RosterReader = Callable[[Path], excel.Roster]


def roster_people(
    folder: SubjectFolder, read: RosterReader = excel.read_roster
) -> set[tuple[str, str]]:
    """Кто числится в списках предмета: (нормализованное ФИО, группа).

    Одно правило для окна, консоли и записи в файлы: иначе отчёты показывали
    чужие группы, а начало занятия считалось по разным людям.
    """
    people: set[tuple[str, str]] = set()
    for path in folder.rosters:
        try:
            roster = read(path)
        except (excel.RosterError, OSError):
            continue
        people.update((normalize_name(s.full_name), roster.group_name) for s in roster.students)
    return people


def rows_of_folder(
    rows: Iterable[DayRow], people: set[tuple[str, str]]
) -> list[DayRow]:
    return [row for row in rows
            if (normalize_name(row.student.full_name), row.student.group_name) in people]


def stats_of(rows: Iterable[DayRow]) -> list[GroupStat]:
    """Итог по группам ровно по этим строкам."""
    rows = list(rows)
    groups = sorted({row.student.group_name for row in rows})
    return [
        GroupStat(
            group,
            sum(row.student.group_name == group for row in rows),
            sum(row.student.group_name == group and row.present for row in rows),
        )
        for group in groups
    ]


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
    storage: Storage, subject: Subject, folder: SubjectFolder
) -> list[FillResult]:
    """Заполнить все списки групп внутри папки предмета.

    Сначала забираем из файлов то, чего нет в базе: без этого шага запись
    затёрла бы прочерком дописанное руками. Главнее при этом база —
    другое время в файле она не меняет. Файл, из
    которого забрать не удалось, не перезаписываем вовсе — иначе потеряли
    бы именно то, что прочитать не смогли.

    В каждый файл идут только приходы его группы: тёзка из другой группы
    не должен получить чужое время, а группа, у которой в этот день
    никого не было, — колонку сплошных прочерков.
    """
    imported = {r.path: r for r in import_subject(storage, folder)}
    arrivals = storage.arrivals(subject)
    people = roster_people(folder)
    groups = {group for _name, group in people}
    names = {name for name, _group in people}

    def orphan(item: Arrival) -> bool:
        # Группы нет ни в одном файле предмета (переименовали, у старой записи
        # пусто): других оснований найти человека, кроме ФИО, не остаётся.
        return item.group_name not in groups and normalize_name(item.full_name) in names

    # Занятие — все, кто в списках предмета, тот же набор, что видит окно.
    lesson_times: dict[date, list[datetime]] = {}
    for item in arrivals:
        if orphan(item) or (normalize_name(item.full_name), item.group_name) in people:
            lesson_times.setdefault(item.at.date(), []).append(item.at)

    results = []
    for path in folder.rosters:
        before = imported.get(path)
        if before is not None and not before.ok:
            results.append(FillResult(
                path, before.group_name,
                error=f"{before.error}; файл не перезаписан, чтобы не потерять его отметки",
            ))
        else:
            results.append(_fill_one(path, arrivals, orphan, lesson_times))
    return results


def _fill_one(
    path: Path,
    arrivals: list[Arrival],
    orphan: Callable[[Arrival], bool],
    lesson_times: dict[date, list[datetime]],
) -> FillResult:
    # Ошибки ловятся пофайлово и широко: битый или чужой файл в папке не имеет
    # права лишить остальные группы записи. Один раз уже случалось — пустой
    # .xlsx рядом ронял выгрузку целиком, и отметки не доходили ни до кого.
    try:
        parsed = excel.read_roster(path)
    except excel.RosterError as exc:
        return FillResult(path, path.stem, error=str(exc))
    except Exception as exc:
        return FillResult(
            path, path.stem,
            error=f"не удалось прочитать ({type(exc).__name__}: {exc})",
        )

    in_file = {normalize_name(s.full_name) for s in parsed.students}
    marks: dict[date, dict[str, datetime]] = {}
    missing: set[str] = set()
    for item in arrivals:
        key = normalize_name(item.full_name)
        if item.group_name == parsed.group_name:
            if key not in in_file:
                # Своя фамилия, пропавшая из списка, — это разорванная связь.
                missing.add(item.full_name)
                continue
        elif not (orphan(item) and key in in_file):
            continue
        marks.setdefault(item.at.date(), {})[item.full_name] = item.at

    try:
        excel.write_attendance(parsed, marks, lesson_times=lesson_times)
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
        missing=tuple(sorted(missing)),
    )


def fill_problems(results: Iterable[FillResult]) -> list[str]:
    """О чём сказать человеку после записи: файл не записан, связь с ФИО разорвана."""
    problems = []
    for result in results:
        if not result.ok:
            problems.append(f"{result.path.name}: {result.error}")
        elif result.missing:
            problems.append(
                f"{result.path.name}: отмечены, но в списке их нет (исправили ФИО?), "
                f"в файл не попали: {', '.join(result.missing)}. Привяжите их карту заново."
            )
    return problems


# ------------------------------------------------- группу в файлах переименовали


@dataclass(frozen=True, slots=True)
class GroupMove:
    """Человек с картой или отметками, чья группа исчезла из всех файлов."""

    student: Student
    new_group: str


def renamed_groups(
    storage: Storage, tables_dir: str | Path, read: RosterReader = excel.read_roster
) -> list[GroupMove]:
    """Кого спросить о переносе: в файлах он значится под другой группой.

    Только когда старой группы нет нигде, а то же ФИО есть ровно в одной другой:
    тёзки в живых группах сюда не попадают. Файл не прочитался — молчим: его
    группа могла быть той самой, и перенос был бы ошибкой.
    """
    groups_of: dict[str, set[str]] = {}
    all_groups: set[str] = set()
    for folder in discover_subjects(tables_dir):
        for path in folder.rosters:
            try:
                roster = read(path)
            except (excel.RosterError, OSError):
                return []
            all_groups.add(roster.group_name)
            for student in roster.students:
                groups_of.setdefault(normalize_name(student.full_name), set()).add(
                    roster.group_name
                )
    moves = []
    for student in storage.list_students():
        found = groups_of.get(normalize_name(student.full_name), set())
        if student.group_name in all_groups or len(found) != 1:
            continue
        if student.card_code or storage.has_marks(student.id):
            moves.append(GroupMove(student, next(iter(found))))
    return moves


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
    folder: SubjectFolder,
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
    def run() -> list[ImportResult]:
        subject = storage.get_or_create_subject(folder.name)
        return [_import_one(storage, subject, path, today, sync) for path in folder.rosters]

    if not dry_run:
        return run()

    results: list[ImportResult] = []
    try:
        with storage.transaction():
            results = run()
            raise _DryRun
    except _DryRun:
        pass
    return results


def _import_one(
    storage: Storage, subject: Subject, path: Path, today: date | None, sync: bool
) -> ImportResult:
    try:
        parsed = excel.read_roster(path)
        found = excel.read_attendance(parsed, today)
        saved_at = datetime.fromtimestamp(path.stat().st_mtime)
    except excel.RosterError as exc:
        return ImportResult(path, path.stem, error=str(exc))
    except PermissionError:
        return ImportResult(path, path.stem,
                            error="файл открыт в Excel — закройте и повторите")
    except Exception as exc:
        return ImportResult(path, path.stem,
                            error=f"не удалось прочитать ({type(exc).__name__}: {exc})")

    counts: Counter[ImportStatus] = Counter()
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
        added=counts[ImportStatus.ADDED],
        updated=counts[ImportStatus.UPDATED],
        conflicts=counts[ImportStatus.KEPT],
        same=counts[ImportStatus.SAME],
        removed=tuple(removed),
        newer=newer,
        dates=tuple(sorted(found.marks)),
        skipped=tuple(found.skipped),
    )


def sync_subject(
    storage: Storage, folder: SubjectFolder, today: date | None = None
) -> tuple[list[ImportResult], list[FillResult]]:
    """Полная синхронизация: таблицы → база, затем база → таблицы.

    После неё база и файлы совпадают: в базе то, что было в таблицах,
    а в таблицах появляются дни, которые были только в базе.
    """
    imported = import_subject(storage, folder, today, sync=True)
    subject = storage.get_or_create_subject(folder.name)
    return imported, fill_subject(storage, subject, folder)


# ------------------------------------------------------- карта уходит от человека


class CardFilesBusy(Exception):
    """Файл группы нельзя поправить: карту снимать нельзя, иначе отметка вернётся."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"файл {path.name} открыт в Excel — закройте и повторите. "
                         "Карта осталась привязанной, отметки целы.")


def retract_card_marks(storage: Storage, student: Student, tables_dir: str | Path) -> int:
    """Убрать из файлов приходы, которые уйдут от студента вместе с картой.

    Зачем: перед записью файл втягивается в базу (fill_subject), и оставшаяся
    ячейка стала бы новой отметкой этого студента. Приходил один, а числились
    бы двое — ошибочная привязка не исправлялась бы, а размножалась.

    Вызывать ДО снятия карты. Сорвётся снятие — ячейку вернёт следующая запись
    из базы. Наоборот было бы хуже: отметка вернулась бы из файла навсегда.

    Битый файл пропускаем: в него и запись не пойдёт (fill_subject).
    """
    marks: dict[str, list[CardMark]] = {}
    for mark in storage.card_marks_of(student.id):
        marks.setdefault(mark.subject_name, []).append(mark)

    cleared = 0
    for folder in discover_subjects(tables_dir):
        times = [mark.at for mark in marks.get(folder.name, [])]
        if not times:
            continue
        for path in folder.rosters:
            try:
                parsed = excel.read_roster(path)
                if parsed.group_name == student.group_name:
                    cleared += excel.clear_marks(parsed, student.full_name, times)
            except excel.RosterError:
                continue
            except OSError as exc:
                raise CardFilesBusy(path) from exc
    return cleared


def unbind_card(storage: Storage, student: Student, tables_dir: str | Path) -> int:
    """Снять карту с человека: сперва файлы, затем база. Вернуть число убранных ячеек."""
    cleared = retract_card_marks(storage, student, tables_dir)
    if student.card_code:
        storage.remove_student(student.card_code)
    return cleared


def fill_all(storage: Storage, tables_dir: str | Path) -> dict[str, list[FillResult]]:
    """Пройти по всем папкам-предметам и заполнить всё, где были занятия."""
    out: dict[str, list[FillResult]] = {}
    for folder in discover_subjects(tables_dir):
        subject = storage.find_subject(folder.name)
        if subject is None:
            continue  # по этому предмету отметок ещё не было
        out[folder.name] = fill_subject(storage, subject, folder)
    return out
