"""Общее для всех источников карт: одно поднесение и интерфейс считывателя."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .codes import CardCode

StatusFn = Callable[[str], None]


def silent(_message: str) -> None:
    """Статус, который никуда не выводится."""


@dataclass(frozen=True, slots=True)
class Scan:
    """Одно поднесение карты."""

    code: CardCode
    at: datetime
    raw: str = ""


class CardReader(Protocol):
    description: str

    def scans(self) -> Iterator[Scan]: ...

    def flush(self) -> None:
        """Сбросить накопленный ввод. Осмысленно только для порта."""
