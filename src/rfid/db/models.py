"""Что хранится в базе и что возвращают её запросы.

Отдельно от Storage: этими типами пользуются вывод на экран, цикл отметки
и CLI, которым соединение с базой не нужно.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..scanner.codes import CardCode, pretty_code


class MarkStatus(Enum):
    MARKED = "marked"        # записано, студент найден в справочнике
    UNKNOWN = "unknown"      # записано, но карта неизвестна
    DUPLICATE = "duplicate"  # уже отмечен на этом занятии сегодня


class ImportStatus(Enum):
    """Что сделал import_mark с отметкой из таблицы."""

    ADDED = "added"      # в базе не было — добавлена
    UPDATED = "updated"  # было другое время — взято из файла (синхронизация)
    KEPT = "kept"        # было другое время — оставлено из базы
    SAME = "same"        # совпадает, делать нечего


@dataclass(frozen=True, slots=True)
class Student:
    id: int
    card_code: str | None   # None — человек есть, карты ещё нет
    full_name: str
    group_name: str


@dataclass(frozen=True, slots=True)
class Subject:
    id: int
    name: str
    course: str = ""

    @property
    def title(self) -> str:
        return f"{self.name} — {self.course}" if self.course else self.name


@dataclass(frozen=True, slots=True)
class MarkResult:
    status: MarkStatus
    code: CardCode
    at: datetime
    student: Student | None = None
    # Для повтора — когда эта карта была отмечена на этом занятии.
    first_at: datetime | None = None

    @property
    def recorded(self) -> bool:
        return self.status is not MarkStatus.DUPLICATE


@dataclass(frozen=True, slots=True)
class DayRow:
    """Строка отчёта за день: студент и была ли отметка."""

    student: Student
    at: datetime | None

    @property
    def present(self) -> bool:
        return self.at is not None


@dataclass(frozen=True, slots=True)
class GroupStat:
    group_name: str
    total: int
    present: int

    @property
    def percent(self) -> float:
        return self.present / self.total * 100 if self.total else 0.0


@dataclass(frozen=True, slots=True)
class UnknownCard:
    card_code: str
    times: int
    first_seen: datetime
    last_seen: datetime


class CardTaken(Exception):
    """Новая карта уже закреплена за другим человеком."""

    def __init__(self, owner: Student):
        self.owner = owner
        super().__init__(
            f"карта {pretty_code(owner.card_code or '')} "
            f"уже за {owner.full_name} ({owner.group_name})"
        )


@dataclass(frozen=True, slots=True)
class CardReplaced:
    student: Student
    old_code: str | None
    moved: int                       # чужих (безымянных) отметок новой карты стало его
    previous_owner: Student | None   # у кого карту забрали, если забрали


@dataclass(frozen=True, slots=True)
class Arrival:
    """Именной приход на предмет: кто, из какой группы, когда."""

    full_name: str
    group_name: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ReplacePreview:
    """Что сделает replace_card с отметками — показать до того, как делать."""

    moved: int     # отметок новой карты станет его
    dropped: int   # на одном занятии оказалось две: сколько удалится насовсем


@dataclass(frozen=True, slots=True)
class CardMark:
    """Приход, сделанный картой: вместе с картой он уйдёт от этого человека."""

    subject_name: str
    at: datetime


class CardConflict(Exception):
    """У человека уже есть другая карта."""

    def __init__(self, student: Student):
        super().__init__(
            f"у студента {student.full_name} "
            f"уже есть карта {pretty_code(student.card_code or '')}"
        )
        self.student = student
