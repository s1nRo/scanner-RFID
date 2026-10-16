"""Точка входа для оконной Windows-сборки PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path

from rfid.gui import run_gui


def application_home() -> Path:
    """Папка рядом с EXE, в которой хранятся пользовательские данные."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def main() -> int:
    home = application_home()
    data_dir = home / "data"
    tables_dir = home / "tables"
    data_dir.mkdir(exist_ok=True)
    tables_dir.mkdir(exist_ok=True)
    return run_gui(data_dir / "rfid.db", tables_dir)


if __name__ == "__main__":
    raise SystemExit(main())
