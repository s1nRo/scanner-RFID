"""Что пишется в ячейки и как это читается обратно.

Без openpyxl: только значения. Правила должны совпадать в обе стороны —
что записал экспорт, то импорт обязан понять.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time

# Заголовок колонки занятия: «дд.мм», без года.
DATE_HEADER_FORMAT = "%d.%m"
# Время прихода в ячейке.
TIME_FORMAT = "%H:%M"
# Не был на занятии.
ABSENT_MARK = "—"

# Что ещё люди ставят руками вместо прочерка.
_ABSENT_VALUES = {ABSENT_MARK, "-", "–", "н", "Н", "нб", "НБ"}

_TIME_RE = re.compile(r"^(?P<h>\d{1,2})[:.](?P<m>\d{2})(?::\d{2})?$")
_DAY_RE = re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{2}|\d{4}))?$")


def header_date(value, today: date) -> date | None:
    """Дата из заголовка колонки.

    Мы пишем «дд.мм» без года. Год восстанавливается так, чтобы дата
    не оказалась в будущем: «12.09», прочитанное в январе, — прошлый сентябрь.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    match = _DAY_RE.match(value.strip())
    if not match:
        return None
    day, month = int(match["d"]), int(match["m"])
    year = match["y"]
    try:
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        guess = date(today.year, month, day)
        return guess if guess <= today else date(today.year - 1, month, day)
    except ValueError:
        return None


def cell_time(value) -> time | None | str:
    """Время прихода из ячейки.

    time — пришёл; None — пусто или прочерк, то есть не был; строка —
    непонятное значение, которое нельзя молча принять ни за приход,
    ни за пропуск.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    if not text or text in _ABSENT_VALUES:
        return None
    match = _TIME_RE.match(text)
    if match and int(match["h"]) < 24 and int(match["m"]) < 60:
        return time(int(match["h"]), int(match["m"]))
    return text
