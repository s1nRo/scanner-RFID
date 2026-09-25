"""Вывод отметок в консоль.

ConsoleView реализует pipeline.View: цикл отметки отдаёт сюда готовый
MarkResult и ничего не знает о том, как он показан. Панель на Tkinter,
если её делать, реализует тот же View в своём пакете, без правок ядра.
"""

from __future__ import annotations

import sys

from ..db import MarkResult, MarkStatus

_GREEN = "\033[42;30m"
_YELLOW = "\033[43;30m"
_RED = "\033[41;37m"
_DIM = "\033[90m"
_RESET = "\033[0m"

WIDTH = 78
_LABEL_WIDTH = 20


def _layout(label: str, main: str, right: str) -> str:
    """Три колонки в строке фиксированной ширины.

    Урезается только середина — метка статуса и время должны быть видны
    целиком в любом случае.
    """
    middle_width = WIDTH - _LABEL_WIDTH - len(right) - 2
    if middle_width < 8:  # правая часть неожиданно длинная
        return f"{label:<{_LABEL_WIDTH}}{main} {right}"[:WIDTH]
    if len(main) > middle_width:
        main = main[: middle_width - 1] + "…"
    return f"{label:<{_LABEL_WIDTH}}{main:<{middle_width}}  {right}"


def enable_ansi() -> bool:
    """Включить ANSI-последовательности в консоли Windows.

    Возвращает False, если не вышло — тогда печатаем без цвета.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def beep(ok: bool) -> None:
    """Короткий сигнал. Без него в потоке студентов не слышно, прошла ли отметка."""
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.Beep(1200 if ok else 400, 90)
    except Exception:
        pass


class ConsoleView:
    def __init__(self, *, color: bool | None = None, sound: bool = True):
        self.color = enable_ansi() if color is None else color
        self.sound = sound

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if self.color else text

    def status(self, message: str) -> None:
        print(self._paint(message, _DIM) if self.color else message)

    def banner(self, text: str) -> None:
        print()
        print(text)
        print("─" * WIDTH)

    def show(self, result: MarkResult) -> None:
        code = result.code
        student = result.student
        hint = ""

        if result.status is MarkStatus.MARKED and student is not None:
            label = " ОТМЕЧЕН"
            main = student.full_name
            right = f"{student.group_name}  {result.at.strftime('%H:%M')}"
            paint, ok = _GREEN, True
        elif result.status is MarkStatus.DUPLICATE:
            label = " УЖЕ ОТМЕЧЕН"
            main = student.full_name if student else code.pretty
            when = result.first_at.strftime("%H:%M") if result.first_at else "сегодня"
            right = f"с {when}"
            paint, ok = _YELLOW, True
        else:
            label = " НЕИЗВЕСТНАЯ КАРТА"
            main = f"{code.canonical}  ({code.pretty})"
            right = result.at.strftime("%H:%M")
            hint = "записано. Привязать карту к студенту:  rfid enroll"
            paint, ok = _RED, False

        print(self._paint(_layout(label, main, right), paint))
        if hint:
            print(self._paint("  " + hint, _DIM) if self.color else "  " + hint)
        if self.sound:
            beep(ok)

    def summary(self, marked: int, duplicates: int, unknown: int) -> None:
        print("─" * WIDTH)
        print(f"Отмечено: {marked}   повторов: {duplicates}   неизвестных карт: {unknown}")
