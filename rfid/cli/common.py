"""Общее для всех команд: ввод, подтверждения, выбор из списка.

Весь ввод идёт через ask() — тесты подменяют именно её, поэтому остальные
модули зовут её как common.ask(...), а не импортируют по имени.
"""

from __future__ import annotations

import io
import sys
from datetime import date, datetime

DATE_INPUT = "%d.%m.%Y"

# Выход из меню и из выбора. «й» — это q в русской раскладке.
EXIT_WORDS = ("0", "q", "й", "выход", "exit", "quit")

_NO_INPUT_HINT = (
    "\n  Ввод недоступен — здесь нет интерактивного терминала.\n"
    "  Откройте обычное окно PowerShell и запустите rfid.cmd там."
)

_console_ready = False


class Interrupted(Exception):
    """Дальше работать нельзя — с объяснением для человека."""


class Cancelled(Interrupted):
    """Пользователь сам отказался. Не ошибка, сообщения быть не должно."""

    def __init__(self) -> None:
        super().__init__("")


def setup_console() -> None:
    """Привести ввод и вывод к UTF-8.

    Про stdin забыть нельзя: ФИО и названия вводят кириллицей, а в Windows
    поток по умолчанию открывается в cp1251 с surrogateescape — UTF-8 из
    пайпа превращается в суррогаты, и SQLite такую строку не принимает.
    Вызывается один раз: меню запускает main() повторно, а перенастроить
    поток после первого чтения уже нельзя.
    """
    global _console_ready
    if _console_ready:
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, io.UnsupportedOperation):
                pass
    _console_ready = True


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        raise Interrupted(_NO_INPUT_HINT)


def confirm(question: str) -> bool:
    """Вопрос «да или нет». Согласие — только y, всё остальное — отказ.

    Отказ по умолчанию нарочно: подтверждают удаление. В русской раскладке
    клавиша y даёт «н» — пусть это лучше будет отказом, чем случайным
    согласием у того, кто начал печатать «нет».
    """
    return ask(f"{question} (y/n): ").lower() in ("y", "yes")


def pick(title: str, items: list[str], *, default: str | None = None) -> int:
    """Выбрать пункт нумерованного списка. Возвращает его индекс.

    Пункт может быть многострочным: первая строка — название, остальные —
    пояснение под ним. «0» — назад (Cancelled). Пустой ввод — тоже назад,
    если не задан default: тогда он выбирает первый пункт, а default —
    подпись к этому в приглашении («последний» и т. п.).
    """
    print()
    print(f"  {title}:")
    for number, item in enumerate(items, start=1):
        first, *rest = item.split("\n")
        print(f"    {number}. {first}")
        for line in rest:
            print(f"       {line}")
    print("    0. назад")
    print()

    hint = f" (Enter — {default})" if default else ""
    choice = ask(f"  Выберите номер{hint}: ")
    if not choice and default:
        return 0
    if choice in ("0", "", "q"):
        raise Cancelled
    if choice.isdigit() and 1 <= int(choice) <= len(items):
        return int(choice) - 1
    raise Interrupted("  Нет такого пункта.")


def parse_date(text: str | None) -> date:
    if not text:
        return date.today()
    try:
        return datetime.strptime(text, DATE_INPUT).date()
    except ValueError:
        raise Interrupted(f"  Дата должна быть в виде ДД.ММ.ГГГГ, получено: {text!r}")
