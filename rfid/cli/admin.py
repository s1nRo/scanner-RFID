"""Обслуживание: диагностика, список портов, очистка базы."""

from __future__ import annotations

from pathlib import Path

from ..db import Storage
from ..excel import discover_subjects, misplaced_rosters
from ..scanner import available_ports, find_reader_port, no_port_explanation
from . import common

_RULE = "─" * 60


def cmd_ports(_args) -> int:
    ports = available_ports()
    if not ports:
        print("COM-портов в системе нет.")
        return 1
    print(f"Найдено портов: {len(ports)}")
    for port in ports:
        print(f"  {port}{'   <-- считыватель' if port.is_reader else ''}")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    print("Проверка системы")
    print(_RULE)
    print(f"[ OK ] рабочая папка: {args.home or '(не задана, пути указаны явно)'}")

    port = find_reader_port()
    if port:
        print(f"[ OK ] считыватель найден: {port}")
    else:
        ok = False
        print("[ !! ] считыватель не найден")
        print(no_port_explanation())

    for module, package in (("serial", "pyserial"), ("openpyxl", "openpyxl")):
        try:
            __import__(module)
            print(f"[ OK ] {package} установлен")
        except ImportError:
            ok = False
            print(f"[ !! ] нет {package}:  py -m pip install -r requirements.txt")

    folders = discover_subjects(args.tables)
    if folders:
        print(f"[ OK ] предметов (папок в {args.tables}): {len(folders)}")
        for folder in folders:
            print(f"         {folder.name} — {folder.group_count} групп(ы)")
    else:
        print(f"[ -- ] в {args.tables} нет папок предметов со списками")

    misplaced = misplaced_rosters(args.tables)
    if misplaced:
        print(f"[ ?? ] {len(misplaced)} файл(ов) лежат в самой {args.tables}, "
              "мимо папок предметов:")
        for path in misplaced:
            print(f"         {path.name}")

    db_path = Path(args.db)
    if db_path.exists():
        with Storage(db_path) as storage:
            print(f"[ OK ] база: {db_path}  карт привязано: {storage.count_cards()}")
            unknown = storage.unknown_cards()
            if unknown:
                print(f"[ ?? ] непривязанных карт: {len(unknown)} — см. rfid unknown")
    else:
        print(f"[ -- ] базы пока нет, будет создана: {db_path}")

    print(_RULE)
    print("Готово к работе." if ok else "Есть проблемы, см. выше.")
    return 0 if ok else 1


def _counts(storage: Storage) -> dict[str, int]:
    return {
        "отметок": storage.count_marks(),
        "предметов в базе": len(storage.list_subjects()),
        "привязок карт": storage.count_cards(),
    }


def cmd_reset(args) -> int:
    """Очистить базу. Показывает, что удаляет, и спрашивает подтверждение.

    Файлы групп не трогаются никогда — они не наши.
    """
    what = args.reset_what
    with Storage(args.db) as storage:
        before = _counts(storage)
        doomed = []
        if what in ("marks", "all"):
            doomed += ["отметок", "предметов в базе"]
        if what in ("cards", "all"):
            doomed.append("привязок карт")

        print("Будет удалено:")
        for key in doomed:
            print(f"   {key}: {before[key]}")
        print("\nФайлы групп в tables/ не трогаются.")

        if not args.yes and not common.confirm("\nПродолжить?"):
            print("Отменено.")
            return 0

        if what == "all":
            storage.clear_all()
        elif what == "marks":
            storage.clear_marks()
        else:
            # Люди остаются: за ними могут числиться отметки из таблиц.
            storage.unbind_all_cards()

        print("\nГотово. Сейчас в базе:")
        for key, value in _counts(storage).items():
            print(f"   {key}: {value}")
    return 0
