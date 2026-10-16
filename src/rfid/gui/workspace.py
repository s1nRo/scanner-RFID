"""Единая рабочая папка для GUI, EXE и консольной версии."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QSettings

SETTINGS_ORGANIZATION = "RFID Attendance"
SETTINGS_APPLICATION = "RFID Journal"
WORKSPACE_KEY = "workspace/home"


def workspace_paths(home: str | Path) -> tuple[Path, Path]:
    root = Path(home).expanduser().resolve()
    return root / "data" / "attendance.db", root / "tables"


def looks_like_workspace(home: str | Path) -> bool:
    root = Path(home).expanduser()
    return (root / "data").is_dir() or (root / "tables").is_dir()


def workspace_has_content(home: str | Path) -> bool:
    """Есть ли в рабочей папке настоящая база или хотя бы один список."""
    root = Path(home).expanduser()
    if (root / "data" / "attendance.db").is_file():
        return True
    tables = root / "tables"
    return tables.is_dir() and any(tables.rglob("*.xlsx"))


def saved_workspace() -> Path | None:
    value = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION).value(WORKSPACE_KEY)
    if not value:
        return None
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_dir() else None


def save_workspace(home: str | Path) -> Path:
    root = Path(home).expanduser().resolve()
    QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION).setValue(
        WORKSPACE_KEY, str(root)
    )
    return root
