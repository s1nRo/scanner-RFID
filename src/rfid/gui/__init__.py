"""Графический интерфейс RFID-журнала."""

from __future__ import annotations

from pathlib import Path


def run_gui(
    db_path: str | Path,
    tables_path: str | Path,
    *,
    mode: str = "auto",
    port: str | None = None,
) -> int:
    # Ленивый импорт оставляет консольные команды рабочими, даже если PyQt5
    # ещё не установлен и пользователь не запускал install.cmd после обновления.
    from .app import run_gui as start

    return start(db_path, tables_path, mode=mode, port=port)


__all__ = ["run_gui"]
