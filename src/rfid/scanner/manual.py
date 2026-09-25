"""Источники карт без железа: ручной ввод и заглушка.

Режим keyboard — не «эмуляция клавиатуры» считывателя (этот чип так не умеет),
а запасной ручной ввод кода, если железо откажет прямо на паре.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from .base import Scan, StatusFn, silent
from .codes import CardCodeError, parse_line, parse_manual


class KeyboardCardReader:
    """Запасной режим: код набирают руками, если считыватель отказал."""

    description = "ручной ввод кода с клавиатуры"

    def __init__(self, *, on_status: StatusFn = silent, stream=None):
        self.on_status = on_status
        self.stream = stream if stream is not None else sys.stdin

    def scans(self) -> Iterator[Scan]:
        self.on_status(
            "Введите код карты и нажмите Enter. "
            "Годится и строка целиком, и 10 шестнадцатеричных цифр. Ctrl+C — выход."
        )
        for line in self.stream:
            text = line.strip()
            if not text:
                continue
            try:
                code = parse_manual(text)
            except CardCodeError as exc:
                self.on_status(str(exc))
                continue
            yield Scan(code=code, at=datetime.now(), raw=text)

    def flush(self) -> None:
        pass


@dataclass
class MockCardReader:
    """Проигрывает заранее заданные строки — для тестов и демонстрации."""

    lines: Iterable[str]
    delay: float = 0.0
    description: str = "заглушка (без железа)"
    on_status: StatusFn = field(default=silent)

    def flush(self) -> None:
        pass

    def scans(self) -> Iterator[Scan]:
        for raw in self.lines:
            if self.delay:
                time.sleep(self.delay)
            code = parse_line(raw)
            if code is None:
                continue
            yield Scan(code=code, at=datetime.now(), raw=raw.strip())
