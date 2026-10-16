"""Рабочая папка: где лежат база (data/) и списки групп (tables/).

Пути не зависят от того, из какой папки запущена программа. Раньше база
искалась относительно текущей папки, и запуск «не оттуда» тихо завёл бы
новую пустую базу в чужом месте — отметки писались бы туда, а не в журнал.
Теперь рабочая папка определяется явно, а если её не найти — отказ
с объяснением.

Порядок поиска:

    1. --home                  явно указано в командной строке
    2. переменная RFID_HOME    её выставляет rfid.cmd: папка, где он лежит
    3. папка exe               у собранного rfid.exe вместо текущей папки,
                               и тоже только если в ней есть data/ или tables/:
                               exe в папке программы работает с её данными
    4. текущая папка           только если в ней уже есть data/ или tables/
                               (у exe не смотрится: он ищет данные у себя)
    5. выбранная в окне        папка, которую человек указал в RFID-Scanner.exe
                               кнопкой «Папка данных…» (rfid.workdir)

Относительные --db и --tables считаются от рабочей папки, а не от текущей.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import workdir
from ..workdir import DB_FILE, TABLES_DIR, looks_like_home
from .common import Interrupted

HOME_ENV = "RFID_HOME"


def find_home(explicit: str | Path | None = None) -> Path | None:
    """Рабочая папка или None, если её не найти."""
    if explicit:
        return _existing(Path(explicit), "--home")
    from_env = os.environ.get(HOME_ENV)
    if from_env:
        return _existing(Path(from_env), HOME_ENV)
    # Рядом с собой — только похожая на рабочую папка. exe, запущенный
    # из «Загрузок», не должен завести там пустую базу.
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
    else:
        beside = Path.cwd().resolve()
    if looks_like_home(beside):
        return beside
    # Через модуль, а не импортом имени: тесты подменяют память о папке.
    return workdir.remembered()


def _existing(path: Path, source: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise Interrupted(f"  Рабочей папки {path} нет (указана через {source}).")
    return path


def resolve_paths(args) -> None:
    """Превратить --home, --db и --tables в абсолютные пути.

    После этого все команды получают готовые пути и о рабочей папке
    не думают. Абсолютный --db или --tables рабочей папки не требует.
    """
    home = find_home(args.home)
    args.home = home
    args.db = _place(args.db, DB_FILE, home)
    args.tables = _place(args.tables, TABLES_DIR, home)


def _place(given: str | Path | None, default: Path, home: Path | None) -> Path:
    path = Path(given) if given else default
    if path.is_absolute():
        return path
    if home is None:
        raise Interrupted(no_home_explanation())
    return home / path


def guard_mock(args) -> None:
    """Не давать заглушке писать в рабочую базу.

    Урок из практики: показательный прогон с --mode mock проигрывает
    вымышленные карты, но пишет туда же, куда настоящая работа, — и молча
    привязывает чужие карты к случайным людям. Требуем отдельную базу.
    Сравниваются настоящие пути: «data/attendance.db», набранное из другой
    папки или через «..», — всё равно рабочая база.
    """
    if getattr(args, "mode", None) != "mock":
        return
    home = getattr(args, "home", None) or find_home()
    if home is None:
        return  # рабочей папки нет — и портить нечего
    working = (home / DB_FILE).resolve()
    if Path(args.db).resolve() == working:
        raise Interrupted(
            "  Режим --mode mock проигрывает вымышленные карты и испортил бы\n"
            f"  рабочую базу {working}. Укажите отдельную:\n"
            "      rfid --db data\\demo.db ... --mode mock"
        )


def no_home_explanation() -> str:
    if getattr(sys, "frozen", False):
        exe_folder = Path(sys.executable).resolve().parent
        return (
            f"  Рядом с программой ({exe_folder}) нет папок {DB_FILE.parent} и {TABLES_DIR}.\n\n"
            "  Положите exe в папку программы — туда, где уже лежат data и tables.\n"
            "  Для первой установки создайте рядом с exe папку tables, а в ней —\n"
            "  папку предмета со списками групп."
        )
    return (
        f"  Не найдена рабочая папка — там, где лежат {DB_FILE.parent}/ и {TABLES_DIR}/.\n"
        f"  Текущая папка ({Path.cwd()}) на неё не похожа, а заводить базу\n"
        "  в случайном месте нельзя — отметки ушли бы мимо журнала.\n\n"
        "  Запустите программу через rfid.cmd из её папки или укажите папку явно:\n"
        f"      rfid --home D:\\путь\\к\\папке ...\n"
        f"  или переменной окружения {HOME_ENV}."
    )
