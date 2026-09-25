"""Командная строка.

    python -m rfid                  меню: спросит, что делать
    python -m rfid enroll           привязать карты к студентам из списка
    python -m rfid scan             отмечать студентов
    python -m rfid whois            чья это карта
    python -m rfid subjects         предметы (папки в tables/)
    python -m rfid students ...     кому какая карта принадлежит
    python -m rfid unknown          неизвестные карты
    python -m rfid report           кто пришёл, кого нет
    python -m rfid late             опоздавшие — показывают конспект
    python -m rfid export           проставить отметки в файлах групп
    python -m rfid import [--sync]  обновить базу из файлов групп
    python -m rfid reset ...        очистить базу
    python -m rfid doctor           проверка железа, драйвера и базы
    python -m rfid ports            какие COM-порты есть

Предмет — это папка со списками групп, её имя и есть название предмета.
Программа спрашивает, где искать, и заполняет найденные файлы. Папок она
не создаёт и файлов не переносит: структуру ведёт пользователь.

Раскладка пакета:

    common.py    пути по умолчанию, ввод, подтверждения, выбор из списка
    subjects.py  выбор предмета, сквозной список студентов
    marking.py   scan, enroll, whois — всё, что слушает считыватель
    reports.py   report, late, subjects, students, unknown
    tables.py    export, import — файлы групп
    admin.py     doctor, ports, reset
    view.py      вывод отметок в консоль: цвет, звук (реализует pipeline.View)
    keys.py      клавиша выхода из цикла отметки, без ожидания
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import pipeline, scanner
from . import common
from .admin import cmd_doctor, cmd_ports, cmd_reset
from .common import DEFAULT_DB, EXIT_WORDS, TABLES_DIR, Cancelled, Interrupted, guard_mock
from .marking import cmd_enroll, cmd_scan, cmd_whois
from .reports import cmd_late, cmd_report, cmd_students, cmd_subjects, cmd_unknown
from .tables import cmd_export, cmd_import

__all__ = [
    "DEFAULT_DB", "MENU", "TABLES_DIR", "Cancelled", "Interrupted",
    "build_parser", "guard_mock", "main",
    "cmd_doctor", "cmd_enroll", "cmd_export", "cmd_import", "cmd_late", "cmd_menu",
    "cmd_ports", "cmd_report", "cmd_reset", "cmd_scan", "cmd_students",
    "cmd_subjects", "cmd_unknown", "cmd_whois",
]

# ------------------------------------------------------------------------ меню

# (клавиша, название, аргументы подкоманды)
MENU = [
    ("1", "Отмечать студентов", ["scan"]),
    ("2", "Привязать карты к студентам", ["enroll"]),
    ("3", "Кто пришёл сегодня", ["report"]),
    ("4", "Опоздавшие — показывают конспект", ["late"]),
    ("5", "Чья это карта", ["whois"]),
    ("6", "Предметы и группы", ["subjects"]),
    ("7", "Кому какая карта принадлежит", ["students", "list"]),
    ("8", "Заполнить файлы групп", ["export"]),
    # Из меню режим спрашивается, а не берётся обычный молча.
    ("9", "Обновить базу из файлов групп", ["import", "--choose"]),
    ("10", "Проверить оборудование", ["doctor"]),
]


def cmd_menu(args) -> int:
    """Меню работает циклом: после действия возвращаемся сюда, а не выходим.

    Раньше программа завершалась после каждой команды, и ради второго
    действия приходилось запускать её заново.
    """
    while True:
        print()
        print("  Учёт посещаемости по RFID-картам")
        print("  " + "─" * 44)
        for key, title, _ in MENU:
            print(f"   {key:>2}. {title}")
        print("    0. Выход        (или Ctrl+C)")
        print()

        try:
            choice = common.ask("  Что делаем? ").lower()
        except Interrupted as exc:
            print(exc)
            return 1
        except KeyboardInterrupt:
            print("\nДо свидания.")
            return 0

        if choice in EXIT_WORDS or not choice:
            print("До свидания.")
            return 0

        command = next((argv for key, _, argv in MENU if key == choice), None)
        if command is None:
            print("  Нет такого пункта.")
            continue

        # Общие флаги надо протащить: иначе выбранный каталог потеряется.
        try:
            main(["--db", str(args.db), "--tables", str(args.tables), *command])
        except KeyboardInterrupt:
            print()

        try:
            again = common.ask("\n  Enter — в меню, 0 — выход: ").lower()
        except (Interrupted, KeyboardInterrupt):
            again = "0"
        if again in EXIT_WORDS:
            print("До свидания.")
            return 0


# --------------------------------------------------------------------- разбор


def _add_reader_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=scanner.MODES, default="auto")
    parser.add_argument("--port", help="COM-порт, если автопоиск не сработал")
    parser.add_argument("--seconds", type=float, help="остановиться через N секунд")
    parser.add_argument("--no-sound", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfid", description="Учёт посещаемости по RFID-картам IronLogic."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"файл базы (умолч. {DEFAULT_DB})")
    parser.add_argument("--tables", type=Path, default=TABLES_DIR,
                        help=f"папка с предметами (умолч. {TABLES_DIR})")
    parser.set_defaults(func=cmd_menu)
    sub = parser.add_subparsers(dest="command")

    def command(name: str, func, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    scan = command("scan", cmd_scan, "отмечать студентов")
    _add_reader_flags(scan)
    scan.add_argument("--subject", help="имя папки-предмета; иначе будет предложен выбор")
    scan.add_argument("--debounce", type=float, default=pipeline.DEFAULT_DEBOUNCE)
    scan.add_argument("--no-export", action="store_true",
                      help="не трогать файлы групп при выходе")

    enroll = command("enroll", cmd_enroll, "привязать карты к студентам из списка")
    _add_reader_flags(enroll)
    enroll.add_argument("--subject", help="имя папки-предмета")

    whois = command("whois", cmd_whois, "приложить карту и увидеть, чья она")
    _add_reader_flags(whois)

    command("subjects", cmd_subjects, "предметы и группы")

    students = command("students", cmd_students, "кому какая карта принадлежит")
    students_sub = students.add_subparsers(dest="students_action", required=True)
    students_sub.add_parser("list", help="показать привязки")
    students_sub.add_parser("remove", help="снять привязку карты").add_argument("code")

    command("unknown", cmd_unknown, "непривязанные карты")

    report = command("report", cmd_report, "кто пришёл, кого нет")
    report.add_argument("--date", help="ДД.ММ.ГГГГ, по умолчанию сегодня")
    report.add_argument("--group", help="только эта группа")
    report.add_argument("--subject", help="имя папки-предмета")

    late = command("late", cmd_late, "опоздавшие — показывают конспект")
    late.add_argument("--date", help="ДД.ММ.ГГГГ; иначе будет предложен выбор")
    late.add_argument("--subject", help="имя папки-предмета")

    command("export", cmd_export, "проставить отметки в файлах групп")

    import_cmd = command(
        "import", cmd_import, "обновить базу из файлов групп (по умолчанию главнее база)"
    )
    import_cmd.add_argument("--subject", help="только эта папка-предмет")
    import_cmd.add_argument(
        "--sync", action="store_true",
        help="полная синхронизация: главнее таблицы, затем файлы переписываются из базы",
    )
    import_cmd.add_argument("--yes", action="store_true", help="без подтверждения")
    import_cmd.add_argument("--choose", action="store_true", help=argparse.SUPPRESS)

    reset = command("reset", cmd_reset, "очистить базу (файлы групп не трогает)")
    reset.add_argument("reset_what", choices=("marks", "cards", "all"),
                       help="marks — отметки и предметы, cards — привязки карт, all — всё")
    reset.add_argument("--yes", action="store_true", help="без подтверждения")

    command("ports", cmd_ports, "список COM-портов")
    command("doctor", cmd_doctor, "проверить железо, зависимости и базу")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.setup_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Interrupted as exc:
        if str(exc):
            print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано.")
        return 130
