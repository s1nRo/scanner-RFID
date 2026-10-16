"""Выбор предмета и сквозной список его студентов.

Раскладку ведёт пользователь: программа папок не создаёт и файлов
не переносит, только показывает найденное и даёт выбрать. Корень,
в котором искать, меняется флагом --tables.
"""

from __future__ import annotations

from pathlib import Path

from .. import enrollment, excel
from ..db import Storage
from ..enrollment import Candidate
from ..excel import SubjectFolder, discover_subjects, find_subject, misplaced_rosters
from . import common
from .common import Interrupted

_PREVIEW_GROUPS = 4


def describe_folder(folder: SubjectFolder) -> str:
    """Что лежит в папке — чтобы выбирать осознанно, а не по имени."""
    groups = []
    for path in folder.rosters[:_PREVIEW_GROUPS]:
        try:
            parsed = excel.read_roster(path)
            groups.append(f"{parsed.group_name} ({len(parsed.students)})")
        except excel.RosterError:
            groups.append(f"{path.name} — не список")
    if len(folder.rosters) > _PREVIEW_GROUPS:
        groups.append(f"и ещё {len(folder.rosters) - _PREVIEW_GROUPS}")
    return ", ".join(groups)


def choose_subject(args, tables_dir: Path) -> SubjectFolder:
    """Предмет из --subject, а без него — выбором из подпапок tables/."""
    if getattr(args, "subject", None):
        return subject_by_name(args.subject, tables_dir)

    folders = discover_subjects(tables_dir)
    if not folders:
        raise Interrupted(nothing_found(tables_dir))

    items = [f"{folder.name}\n{describe_folder(folder)}" for folder in folders]
    return folders[common.pick(f"Предмет (папки в {tables_dir})", items)]


def subject_by_name(name: str, tables_dir: Path) -> SubjectFolder:
    folder = find_subject(tables_dir, name)
    if folder is not None:
        return folder
    known = ", ".join(f.name for f in discover_subjects(tables_dir)) or "ни одной"
    raise Interrupted(f"  Папки «{name}» в {tables_dir} нет. Известные: {known}")


def nothing_found(tables_dir: Path) -> str:
    lines = [f"  В {tables_dir} нет ни одной папки предмета со списками групп."]
    misplaced = misplaced_rosters(tables_dir)
    if misplaced:
        lines.append(
            f"  При этом {len(misplaced)} файл(ов) лежат в самой {tables_dir}, "
            "мимо папок:"
        )
        lines += [f"      {p.name}" for p in misplaced]
    lines.append(
        f"\n  Создайте в {tables_dir} папку с названием предмета и положите\n"
        "  туда списки групп — по файлу на группу."
    )
    return "\n".join(lines)


def report_misplaced(tables_dir: Path) -> None:
    misplaced = misplaced_rosters(tables_dir)
    if not misplaced:
        return
    print(f"\nЛежат в самой {tables_dir}, мимо папок предметов, и не используются:")
    for path in misplaced:
        print(f"   {path.name}")


def require_rosters(folder: SubjectFolder) -> None:
    if not folder.rosters:
        raise Interrupted(
            f"  В папке «{folder.name}» нет ни одного файла со списком группы.\n"
            f"  Положите туда .xlsx со списком и запустите снова."
        )


# --------------------------------------------------------- сквозной список


def all_candidates(folder: SubjectFolder) -> list[Candidate]:
    """Сквозной список предмета (enrollment.read_candidates) с отказами для консоли."""
    require_rosters(folder)
    found = enrollment.read_candidates(folder)
    for path, problem in found.problems:
        print(f"  {path.name}: {problem}")
    if not found.items:
        raise Interrupted(f"  В «{folder.name}» не нашлось ни одного студента.")
    return found.items


def print_candidates(
    candidates: list[Candidate], storage: Storage, *, without_card_only: bool = False
) -> None:
    """Список с подписями групп, но сквозной нумерацией.

    without_card_only — только те, у кого карты ещё нет: на паре выбирать
    приходится из них, и короткий список легче пробежать глазами. Номера
    остаются сквозными, как в полном списке.
    """
    known = enrollment.people_with_cards(storage)
    group = None
    print()
    for item in candidates:
        carded = enrollment.has_card(item, storage, known)
        if without_card_only and carded:
            continue
        if item.group_name != group:
            group = item.group_name
            print(f"\n  {group}")
        mark = "  карта есть" if carded else ""
        print(f"    {item.number:>3}. {item.full_name:<38}{mark}")
