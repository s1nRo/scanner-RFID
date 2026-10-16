"""Заслонка считывателя: пока незнакомую карту не привязали, другие не принимаются.

Просьба пользователя: в окне, как в меню 2 консоли, незнакомую карту
привязывают сразу, и пока это не сделано, следующий студент не отмечается.

Отказ обязан быть громким: студент, чью карту не приняли, должен это
увидеть и приложить её ещё раз, а не уйти, думая, что отмечен. Поэтому
каждая отвергнутая карта уходит в on_rejected — окно показывает её
красным и подаёт сигнал.

Заслонка стоит в потоке считывателя, а не в окне. Окно узнаёт о незнакомой
карте с задержкой, и карта, приложенная в эти доли секунды, проскочила бы
мимо привязки. Здесь же следующую карту проверяют уже после того, как
предыдущая записана и показана, — в том же потоке, без гонки.

Без Qt: проверяется обычными тестами.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from threading import Event

from ..scanner import CardReader, Scan


class ScanGate:
    """Обёртка над считывателем, которая умеет временно не пропускать карты."""

    def __init__(self, reader: CardReader, on_rejected: Callable[[Scan], None]):
        self.reader = reader
        self.on_rejected = on_rejected
        self.description = reader.description
        self._closed = Event()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        """Не принимать карты, пока не вызовут open()."""
        self._closed.set()

    def open(self) -> None:
        self._closed.clear()

    def flush(self) -> None:
        self.reader.flush()

    def scans(self) -> Iterator[Scan]:
        for scan in self.reader.scans():
            if self._closed.is_set():
                self.on_rejected(scan)
                continue
            yield scan
