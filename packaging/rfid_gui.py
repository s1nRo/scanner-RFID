"""Точка входа RFID-Scanner.exe: то же, что rfid-gui.cmd, — окно без консоли.

Идёт через общий rfid.cli.main, а не мимо него: так exe берёт ту же базу
data/attendance.db, ту же рабочую папку и ту же защиту от заглушки, что
и rfid.cmd. Своя логика путей здесь однажды уже завела новую пустую базу
рядом со старой — прежние отметки и привязки карт «пропали».

Консоли у оконного exe нет, а отказ (нет рабочей папки и т. п.) печатается
в неё. Поэтому вывод перехватывается и при ошибке показывается окном.
"""

import contextlib
import io
import sys

from rfid.cli import main

TITLE = "RFID Журнал"


def _show_error(text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def run(argv: list[str]) -> int:
    if not getattr(sys, "frozen", False):  # python packaging\rfid_gui.py — консоль есть
        return main(["gui", *argv])
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            code = main(["gui", *argv])
        except SystemExit as exc:  # argparse: неверные аргументы
            code = exc.code if isinstance(exc.code, int) else 1
    if code and captured.getvalue().strip():
        _show_error(captured.getvalue().strip())
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
