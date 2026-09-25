"""Предметы — это папки.

    tables/                          <- корень, сам предметом не является
      Бургеростроение 1 курс/        <- предмет
        1000000.10001 список.xlsx    <- одна группа
        1000000.10002 список.xlsx    <- другая группа

Раскладку ведёт пользователь: здесь только поиск, ничего не создаётся
и не переносится.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SubjectFolder:
    """Предмет — это папка. Её имя и есть название предмета с курсом."""

    name: str
    path: Path
    rosters: tuple[Path, ...]

    @property
    def group_count(self) -> int:
        return len(self.rosters)


def _roster_files(folder: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            p for p in folder.glob("*.xlsx")
            if not p.name.startswith("~$")  # временные файлы открытого Excel
        )
    )


def as_folder(path: str | Path) -> SubjectFolder:
    """Считать конкретную папку предметом: её имя — название, .xlsx внутри — группы."""
    path = Path(path)
    return SubjectFolder(name=path.name, path=path, rosters=_roster_files(path))


def discover_subjects(tables_dir: str | Path) -> list[SubjectFolder]:
    """Предметы — это подпапки tables/.

    Сама tables/ предметом не является: она корень, в котором эти папки лежат.
    Подпапки без .xlsx не показываем — выбирать там нечего.
    """
    root = Path(tables_dir)
    if not root.is_dir():
        return []
    return [
        as_folder(folder)
        for folder in sorted(root.iterdir())
        if folder.is_dir() and _roster_files(folder)
    ]


def find_subject(tables_dir: str | Path, name: str) -> SubjectFolder | None:
    """Папка-предмет по имени, без учёта регистра."""
    for folder in discover_subjects(tables_dir):
        if folder.name.casefold() == name.casefold():
            return folder
    return None


def misplaced_rosters(tables_dir: str | Path) -> tuple[Path, ...]:
    """Списки, лежащие в корне tables/ мимо папок-предметов.

    Они не попадут ни в один предмет. Сообщаем об этом в диагностике,
    но не трогаем: раскладку ведёт пользователь.
    """
    root = Path(tables_dir)
    return _roster_files(root) if root.is_dir() else ()
