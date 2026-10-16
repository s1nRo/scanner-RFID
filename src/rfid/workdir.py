"""Рабочая папка: что в ней лежит и какую выбрал пользователь.

Общее для консоли (cli.home) и окна (gui), поэтому без зависимостей: окно
и консоль обязаны видеть одну и ту же базу. Раньше у окна был свой поиск
папки, и они расходились — окно помнило выбранную папку, а консоль о ней
не знала.

Память о выбранной папке лежит в реестре там же, где её хранил QSettings
первой оконной сборки (HKCU\\Software\\RFID Attendance\\RFID Journal,
ключ workspace, значение home): выбор, сделанный в ней, не теряется.
PyQt5 для этого не нужен: консоль (rfid.cmd) читает её без Qt.
"""

from __future__ import annotations

import sys
from pathlib import Path

DB_FILE = Path("data") / "attendance.db"
TABLES_DIR = Path("tables")

_REGISTRY_KEY = r"Software\RFID Attendance\RFID Journal\workspace"
_REGISTRY_VALUE = "home"


def looks_like_home(folder: Path) -> bool:
    """Похожа ли папка на рабочую: в ней уже есть data/ или tables/."""
    return (folder / DB_FILE.parent).is_dir() or (folder / TABLES_DIR).is_dir()


def remembered() -> Path | None:
    """Папка, выбранная в окне программы, если она ещё на месте и похожа на рабочую."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, _REGISTRY_VALUE)
    except OSError:
        return None
    if not isinstance(value, str) or not value:
        return None
    folder = Path(value).expanduser()
    return folder.resolve() if looks_like_home(folder) else None


def remember(folder: Path) -> None:
    """Запомнить папку, выбранную человеком. Сбой записи — не повод падать."""
    if sys.platform != "win32":
        return
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
            winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_SZ,
                              str(folder.expanduser().resolve()))
    except OSError:
        pass
