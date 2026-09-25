"""Выбор источника карт по режиму: auto, serial, keyboard, mock."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from . import ports
from .base import CardReader, StatusFn, silent
from .manual import KeyboardCardReader, MockCardReader
from .serial_reader import SerialCardReader

MODES = ("auto", "serial", "keyboard", "mock")


class ReaderUnavailable(RuntimeError):
    """Нужного источника событий нет — с объяснением, что делать."""


def make_reader(
    mode: str = "auto",
    *,
    port: str | None = None,
    on_status: StatusFn = silent,
    mock_lines: Iterable[str] | None = None,
    stop_after: float | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> CardReader:
    if mode == "mock":
        return MockCardReader(lines=mock_lines or [], on_status=on_status)
    if mode == "keyboard":
        return KeyboardCardReader(on_status=on_status)
    if mode not in ("auto", "serial"):
        raise ValueError(f"неизвестный режим: {mode!r}")

    # Молча переходить в auto на ручной ввод нельзя: человек ждёт, что
    # приложит карту, а у него вдруг просят набрать код. Лучше честно отказать.
    device = port or ports.find_reader_port()
    if not device:
        raise ReaderUnavailable(ports.no_port_explanation())
    return SerialCardReader(
        device, on_status=on_status, stop_after=stop_after, should_stop=should_stop
    )
