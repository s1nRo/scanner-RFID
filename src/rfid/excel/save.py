"""Запись книги без риска потерять файл."""

from __future__ import annotations

import os
from pathlib import Path

from openpyxl import Workbook


def save_atomically(workbook: Workbook, path: Path) -> None:
    """Сохранить через временный файл рядом, потом подменить.

    Прямой workbook.save(path) сначала обнуляет файл и только потом пишет:
    сорвавшаяся запись — блокировка, падение, нет места на диске — оставляет
    от списка группы пустышку. А список дал пользователь, восстановить его
    неоткуда: в базе лежат отметки, а не состав групп.

    Здесь оригинал не трогается, пока новый файл не собран целиком.
    """
    temp = path.with_name(path.name + ".tmp")
    try:
        workbook.save(temp)
        os.replace(temp, path)
    except BaseException:
        # Не оставляем мусор рядом со списком, каким бы ни был сбой.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
