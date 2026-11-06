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


MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40


def _show_error(text: str, icon: int = MB_ICONERROR) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)
    except Exception:
        pass


# Установка драйвера с флешки: окно перезапускает exe с правами
# администратора (UAC) и этим флагом. Консоли нет — итог показываем окном.
INSTALL_DRIVER = "--install-driver"


# Общие флаги rfid стоят до подкоманды, а exe их получает как есть:
# RFID-Scanner.exe --home D:/rfid  ->  rfid --home D:/rfid gui
_GLOBAL_FLAGS = ("--home", "--db", "--tables")


def gui_argv(argv: list[str]) -> list[str]:
    front: list[str] = []
    rest: list[str] = []
    items = iter(argv)
    for item in items:
        name = item.split("=", 1)[0]
        if name in _GLOBAL_FLAGS:
            front.append(item)
            if "=" not in item:
                front.append(next(items, ""))
        else:
            rest.append(item)
    return [*front, "gui", *rest]


def run(argv: list[str]) -> int:
    if argv[:1] == [INSTALL_DRIVER]:
        from rfid.scanner import driver

        ok, text = driver.install_from_argv(argv[1:])
        _show_error(text, MB_ICONINFORMATION if ok else MB_ICONERROR)
        return 0 if ok else 1
    command = gui_argv(argv)
    if not getattr(sys, "frozen", False):  # python packaging\rfid_gui.py — консоль есть
        return main(command)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            code = main(command)
        except SystemExit as exc:  # argparse: неверные аргументы
            code = exc.code if isinstance(exc.code, int) else 1
    text = captured.getvalue().strip()
    if code and text:
        _show_error(text)
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
