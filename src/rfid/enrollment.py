"""Привязка карт к студентам: кому можно привязать и как.

Общее для консоли (rfid enroll, меню 2) и окна. Раньше это было написано
дважды, и правила разошлись: консоль считала «карта есть» по одному ФИО,
окно — по ФИО и группе; консоль писала «прошлых отметок зачтено 1», окно
на то же действие — «2». Теперь обе стороны зовут функции отсюда, а у себя
держат только вопрос и показ.

Основа — консольный код (cli.subjects, cli.marking._bind): он проверен на
живом считывателе. Исключение — правило «у кого уже есть карта»: по ФИО
и группе, как в окне. Человек в базе — это (ФИО, группа) (схема 3), и по
одному ФИО тёзка с картой из другой группы прятал бы человека из списка.

Мост между excel (списки групп) и db (карты), как journal; без печати.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import excel
from .db import Storage, Student
from .excel import SubjectFolder
from .names import normalize_name
from .scanner import CardCode

RosterReader = Callable[[Path], excel.Roster]


@dataclass(frozen=True)
class Candidate:
    """Строка сквозного списка: студент со своей группой."""

    number: int
    full_name: str
    group_name: str

    @property
    def person(self) -> tuple[str, str]:
        return normalize_name(self.full_name), self.group_name.strip()


@dataclass(frozen=True)
class Candidates:
    """Сквозной список предмета и файлы, которые прочитать не удалось."""

    items: list[Candidate]
    problems: list[tuple[Path, str]]


def read_candidates(
    folder: SubjectFolder, *, read: RosterReader = excel.read_roster
) -> Candidates:
    """Все студенты предмета одним списком со сквозной нумерацией.

    Группу при привязке не спрашиваем: важен человек, а не то, в каком файле
    он записан. Номера сквозные, поэтому ввод однозначен.
    read — чем читать список; окно передаёт свой кэш.
    """
    items: list[Candidate] = []
    problems: list[tuple[Path, str]] = []
    for path in folder.rosters:
        try:
            roster = read(path)
        except (excel.RosterError, OSError) as exc:
            problems.append((path, str(exc)))
            continue
        for student in roster.students:
            items.append(Candidate(len(items) + 1, student.full_name, roster.group_name))
    return Candidates(items, problems)


def by_number(candidates: list[Candidate], choice: str) -> Candidate | None:
    if not choice.isdigit():
        return None
    number = int(choice)
    return next((c for c in candidates if c.number == number), None)


def people_with_cards(storage: Storage) -> set[tuple[str, str]]:
    """Кто уже с картой: (ФИО без регистра и «ё», группа)."""
    return {
        (normalize_name(s.full_name), s.group_name.strip())
        for s in storage.list_students(with_card=True)
    }


def has_card(candidate: Candidate, storage: Storage,
             known: set[tuple[str, str]] | None = None) -> bool:
    return candidate.person in (known if known is not None else people_with_cards(storage))


def without_card(candidates: list[Candidate], storage: Storage) -> list[Candidate]:
    """Кому карту ещё можно привязать. Номера остаются сквозными."""
    known = people_with_cards(storage)
    return [c for c in candidates if c.person not in known]


def bound_count(candidates: list[Candidate], storage: Storage) -> int:
    known = people_with_cards(storage)
    return sum(1 for c in candidates if c.person in known)


@dataclass(frozen=True)
class Bound:
    student: Student
    past: int  # сколько ПРОШЛЫХ отметок карты стали его


def bind(
    storage: Storage, code: CardCode | str, full_name: str, group_name: str,
    *, includes_current: bool = False,
) -> Bound:
    """Закрепить карту за человеком. CardConflict — у него уже другая карта.

    includes_current — карту привязывают прямо на паре, и среди зачтённых
    отметок есть только что сделанная. «Прошлой» её называть нельзя.
    """
    saved, credited = storage.bind_card(code, full_name, group_name)
    return Bound(saved, max(0, credited - 1) if includes_current else credited)
