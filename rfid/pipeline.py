"""Связка: считыватель → база → вывод.

Дедупликация двухуровневая, и уровни решают разные задачи:

1. Debounce здесь, в памяти — гасит повторные срабатывания, если карту
   быстро провели дважды. Отвечает за то, чтобы экран не мигал одинаковыми
   строками. База при этом не трогается.
2. UNIQUE(day, card_code) в SQLite — «уже отмечен сегодня». Гарантируется
   базой, поэтому переживает перезапуск программы среди дня.

Считыватель шлёт одну строку на одно поднесение (проверено дампом), так что
первый уровень нужен реже, чем казалось на этапе планирования, — но провести
картой дважды подряд человек вполне может.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .readers import CardReader, Scan
from .storage import MarkResult, MarkStatus, Storage, Subject

DEFAULT_DEBOUNCE = 2.0


@dataclass
class Debounce:
    """Гасит повтор одного и того же кода в пределах окна."""

    window: float = DEFAULT_DEBOUNCE
    _last: dict[str, float] = field(default_factory=dict)

    def is_repeat(self, key: str, now: float) -> bool:
        previous = self._last.get(key)
        self._last[key] = now
        return previous is not None and now - previous < self.window


class View:
    """Минимальный интерфейс отображения (см. console.ConsoleView)."""

    def status(self, message: str) -> None: ...
    def show(self, result: MarkResult) -> None: ...


def process(scan: Scan, storage: Storage, subject: Subject) -> MarkResult:
    return storage.mark(scan.code, subject, at=scan.at, raw=scan.raw)


def run(
    reader: CardReader,
    storage: Storage,
    view: View,
    subject: Subject,
    *,
    debounce: float = DEFAULT_DEBOUNCE,
) -> Counter:
    """Крутить цикл отметок до Ctrl+C. Возвращает счётчик по статусам."""
    guard = Debounce(debounce)
    tally: Counter = Counter()

    for scan in reader.scans():
        if guard.is_repeat(scan.code.canonical, scan.at.timestamp()):
            continue
        result = process(scan, storage, subject)
        tally[result.status] += 1
        view.show(result)

    return tally


def tally_line(tally: Counter) -> tuple[int, int, int]:
    return (
        tally[MarkStatus.MARKED],
        tally[MarkStatus.DUPLICATE],
        tally[MarkStatus.UNKNOWN],
    )
