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

# Ключи счётчика для бед, у которых нет MarkStatus.
FAILED = "failed"            # отметку записать не удалось — она потеряна
SHOW_FAILED = "show_failed"  # записана, но не показалась на экране


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
    """Крутить цикл отметок до выхода. Возвращает счётчик по статусам.

    Цикл обязан пережить любую единичную беду. Идёт живая очередь: если
    сеанс оборвётся на пятом студенте, остальные двадцать не отметятся,
    а человек у считывателя этого даже не заметит. Поэтому сбой записи
    и сбой вывода обрабатываются по отдельности и громко, но цикл живёт.
    """
    guard = Debounce(debounce)
    tally: Counter = Counter()

    for scan in reader.scans():
        if guard.is_repeat(scan.code.canonical, scan.at.timestamp()):
            continue

        try:
            result = process(scan, storage, subject)
        except Exception as exc:
            # Эту отметку записать не вышло — сказать обязаны, она потеряна.
            tally[FAILED] += 1
            view.status(
                f"!!! НЕ ЗАПИСАНО: карта {scan.code.canonical} "
                f"в {scan.at.strftime('%H:%M:%S')} — {exc}"
            )
            continue

        tally[result.status] += 1

        try:
            view.show(result)
        except Exception as exc:
            # Отметка уже в базе; сорвался только показ. Не повод бросать пару.
            tally[SHOW_FAILED] += 1
            view.status(f"(сбой вывода, отметка записана: {exc})")

    return tally


def tally_line(tally: Counter) -> tuple[int, int, int]:
    return (
        tally[MarkStatus.MARKED],
        tally[MarkStatus.DUPLICATE],
        tally[MarkStatus.UNKNOWN],
    )


def failed_count(tally: Counter) -> int:
    """Сколько отметок записать не удалось. Ноль — обычное состояние."""
    return tally[FAILED]
