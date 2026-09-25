"""Файлы групп: запись отметок в них и обновление базы из них.

Направление по умолчанию — база → файлы, при расхождении главнее база.
Полная синхронизация, где главнее таблицы, — отдельное явное действие
с пробным прогоном и подтверждением.
"""

from __future__ import annotations

from .. import journal
from ..db import Storage, Subject
from ..excel import SubjectFolder, discover_subjects
from . import common
from .subjects import nothing_found, subject_by_name


def fill_and_report(storage: Storage, subject: Subject, folder: SubjectFolder) -> int:
    """Заполнить файлы предмета и показать итог. Возвращает число сбоев."""
    return print_fill({folder.name: journal.fill_subject(storage, subject, folder)})


def print_fill(filled: dict[str, list[journal.FillResult]]) -> int:
    """Показать итог записи в файлы. Возвращает число файлов с ошибкой."""
    problems = 0
    for subject_name, results in filled.items():
        print(f"\n{subject_name}")
        for result in results:
            if not result.ok:
                problems += 1
                print(f"   {result.path.name}: {result.error}")
                continue
            print(f"   {result.path.name}  ({result.group_name}, "
                  f"{result.students} чел., дат {len(result.dates)})")
            if result.missing:
                print("      Не нашлись в списке — связь с картой разорвана:")
                for name in result.missing:
                    print(f"         {name}")
    return problems


def cmd_export(args) -> int:
    """Проставить отметки во всех списках групп."""
    with Storage(args.db) as storage:
        filled = journal.fill_all(storage, args.tables)
    if not filled:
        print("Заполнять нечего: нет предметов с отметками.")
        return 0
    return 1 if print_fill(filled) else 0


# --------------------------------------------------------------------- import


def cmd_import(args) -> int:
    """Обновить базу из файлов групп.

    Обычно — добавить недостающее, главнее база. --sync — полная
    синхронизация, главнее таблицы: сперва пробный прогон и список того,
    что будет удалено, потом подтверждение, и только тогда запись.
    """
    if args.subject:
        folders = [subject_by_name(args.subject, args.tables)]
    else:
        folders = discover_subjects(args.tables)
    if not folders:
        print(nothing_found(args.tables))
        return 1

    sync = args.sync or (args.choose and _choose_sync())

    with Storage(args.db) as storage:
        if sync:
            return _sync(storage, folders, assume_yes=args.yes)
        return _add_missing(storage, folders)


def _add_missing(storage: Storage, folders: list[SubjectFolder]) -> int:
    results = {f.name: journal.import_subject(storage, f) for f in folders}
    problems = print_import(results)
    everything = [r for rs in results.values() for r in rs]
    print(f"\nДобавлено в базу: {sum(r.added for r in everything)}.")
    conflicts = sum(r.conflicts for r in everything)
    if conflicts:
        print(f"Время расходится с базой: {conflicts} — оставлено как в базе. "
              "Взять из таблиц: полная синхронизация.")
    return 1 if problems else 0


def _sync(storage: Storage, folders: list[SubjectFolder], *, assume_yes: bool) -> int:
    plan = {f.name: journal.import_subject(storage, f, sync=True, dry_run=True)
            for f in folders}
    print("\nПолная синхронизация — главнее таблицы. Что изменится в базе:")
    print_import(plan)
    removals = [(r.group_name, rm) for rs in plan.values() for r in rs for rm in r.removed]
    if removals:
        print("\nБудут УДАЛЕНЫ отметки, против которых в таблице прочерк или пусто:")
        for group, rm in removals:
            print(f"   {rm.day.strftime('%d.%m')} {rm.at.strftime('%H:%M')}  "
                  f"{rm.full_name}  ({group})")
    if not assume_yes and not common.confirm("\nВыполнить синхронизацию?"):
        print("Отменено, база не изменилась.")
        return 0

    problems = 0
    for folder in folders:
        imported, filled = journal.sync_subject(storage, folder)
        problems += sum(1 for r in imported if not r.ok)
        problems += print_fill({folder.name: filled})
    print("\nГотово: база и файлы групп совпадают." if not problems
          else "\nГотово, но не со всеми файлами — см. выше.")
    return 1 if problems else 0


def _choose_sync() -> bool:
    """Спросить режим обновления. True — полная синхронизация."""
    modes = [
        "Добавить недостающее — главнее база\n"
        "берутся отметки из таблиц, которых в базе нет;\n"
        "если время расходится, остаётся как в базе",
        "Полная синхронизация — главнее таблицы\n"
        "время берётся из таблиц, отметки против прочерков удаляются,\n"
        "затем файлы переписываются из базы; перед удалением спросит",
    ]
    return common.pick("Как обновить базу", modes) == 1


def print_import(results: dict[str, list[journal.ImportResult]]) -> int:
    """Показать итог импорта по файлам. Возвращает число файлов с ошибкой."""
    problems = 0
    for subject_name, items in results.items():
        print(f"\n{subject_name}")
        for result in items:
            if not result.ok:
                problems += 1
                print(f"   {result.path.name}: {result.error}")
                continue
            parts = [f"новых {result.added}"]
            if result.updated:
                parts.append(f"время из таблицы {result.updated}")
            if result.conflicts:
                parts.append(f"расходится, оставлено из базы {result.conflicts}")
            if result.removed:
                parts.append(f"удалить {len(result.removed)}")
            parts.append(f"без изменений {result.same}")
            days = ", ".join(d.strftime("%d.%m") for d in result.dates) or "дат нет"
            print(f"   {result.path.name}  ({result.group_name}): "
                  f"{', '.join(parts)}  [{days}]")
            if result.newer:
                print(f"      сделаны позже сохранения файла, не трогаются: {result.newer}")
            for where, value in result.skipped:
                print(f"      не понял ячейку «{value}» ({where}) — оставлена как есть")
    return problems
