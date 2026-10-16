"""Точка входа для оконной Windows-сборки PyInstaller."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rfid.gui import run_gui
from rfid.gui.workspace import (
    looks_like_workspace,
    save_workspace,
    saved_workspace,
    workspace_has_content,
    workspace_paths,
)


def application_home() -> Path:
    """Папка самого EXE, а при обычном Python-запуске — папка проекта."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def choose_workspace(explicit: str | Path | None = None) -> Path:
    """Найти те же ``data/`` и ``tables/``, которыми пользуется CLI."""
    if explicit:
        return Path(explicit).expanduser().resolve()

    from_env = os.environ.get("RFID_HOME")
    if from_env:
        return Path(from_env).expanduser().resolve()

    beside_exe = application_home()
    if workspace_has_content(beside_exe):
        return beside_exe

    saved = saved_workspace()
    if saved is not None and looks_like_workspace(saved):
        return saved

    current = Path.cwd().resolve()
    if workspace_has_content(current):
        return current
    return beside_exe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--home")
    args, _unknown = parser.parse_known_args(argv)

    home = save_workspace(choose_workspace(args.home))
    db_path, tables_dir = workspace_paths(home)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)
    return run_gui(db_path, tables_dir)


if __name__ == "__main__":
    raise SystemExit(main())
