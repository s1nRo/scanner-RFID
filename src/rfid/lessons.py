"""Занятия и опоздания.

Расписание со слов пользователя: пара 1 час 40 минут, перемена 20 минут,
то есть занятия начинаются ровно каждые два часа.

Одно правило на всех: жёлтая подсветка в файлах групп (excel) и список
опоздавших (rfid late) считаются здесь, иначе таблица и экран разошлись бы.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TypeVar

LATE_AFTER = timedelta(minutes=10)

LESSON_LENGTH = timedelta(hours=1, minutes=40)
BREAK_LENGTH = timedelta(minutes=20)

# По циклу приходы режутся на занятия: отметки приходят общие на предмет,
# а у групп пара бывает в разное время — без разделения дневная группа
# целиком считалась бы опоздавшей. Общая лекция двух групп при этом
# остаётся одним занятием, и опоздавшая группа на ней видна.
#
# Порогом служит ВЕСЬ цикл, а не одна пара: иначе карта, приложенная
# на перемене, создала бы фантомное занятие, и следующая пара целиком
# оказалась бы опоздавшей. Приход на перемене лучше отнести к предыдущей
# паре, чем испортить следующую.
LESSON_CYCLE = LESSON_LENGTH + BREAK_LENGTH

K = TypeVar("K")


def session_starts(arrivals: Iterable[datetime]) -> list[datetime]:
    """Начала занятий за день.

    Новое занятие начинается, когда приход отстоит от начала текущего
    на LESSON_CYCLE или больше. Именно от начала, а не от предыдущего
    прихода: опоздавший на час не должен объявлять себя началом новой пары.

    Сравнение нестрогое, и это существенно: пары идут ровно через цикл,
    поэтому приход точно через два часа — это уже следующая пара.
    """
    starts: list[datetime] = []
    current: datetime | None = None
    for at in sorted(arrivals):
        if current is None or at - current >= LESSON_CYCLE:
            starts.append(at)
            current = at
    return starts


def session_start_for(at: datetime, starts: list[datetime]) -> datetime | None:
    """Начало того занятия, к которому относится этот приход."""
    found = None
    for start in starts:
        if start > at:
            break
        found = start
    return found


def late_start(at: datetime, starts: list[datetime]) -> datetime | None:
    """Начало пары, если на неё опоздали; None — пришёл вовремя."""
    start = session_start_for(at, starts)
    if start is not None and at - start >= LATE_AFTER:
        return start
    return None


def is_late(at: datetime, starts: list[datetime]) -> bool:
    return late_start(at, starts) is not None


def late_arrivals(arrivals: dict[K, datetime]) -> list[tuple[K, datetime, datetime]]:
    """Опоздавшие за день: (кто, пришёл, начало его пары), по времени прихода.

    arrivals — все приходы предмета за день, всех групп: начало пары
    считается по ним всем.
    """
    starts = session_starts(arrivals.values())
    late = []
    for who, at in arrivals.items():
        start = late_start(at, starts)
        if start is not None:
            late.append((who, at, start))
    return sorted(late, key=lambda item: item[1])
