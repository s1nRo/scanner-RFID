"""Проверка, не попросил ли оператор закончить, не блокируя цикл отметки.

Цикл чтения карт ждёт порт, а не клавиатуру, поэтому единственным выходом
был Ctrl+C. Под Windows это неудобно вдвойне: Ctrl+C уходит всей группе
процессов, и после завершения программы cmd.exe спрашивает «Завершить
выполнение пакетного файла?» — выход превращается в два действия.

Здесь опрос клавиатуры без ожидания: цикл заглядывает сюда между чтениями
порта и, если нажата клавиша выхода, завершается сам.
"""

from __future__ import annotations

from collections.abc import Callable

# Esc и «q» в обеих раскладках: на клавише q в русской лежит «й».
STOP_KEYS = frozenset({"\x1b", "q", "Q", "й", "Й"})

STOP_HINT = "q или Esc — закончить"


def _no_keyboard() -> bool:
    """Заглушка там, где консоли нет: под пайпом, в тестах, не на Windows."""
    return False


def make_stop_watcher() -> Callable[[], bool]:
    """Вернуть функцию «нажата ли клавиша выхода».

    Опрос неблокирующий и съедает всё, что накопилось в буфере: иначе
    случайные нажатия во время пары всплыли бы потом в следующем вопросе.
    """
    try:
        import msvcrt
    except ImportError:
        return _no_keyboard  # не Windows — выходят по Ctrl+C

    def pressed() -> bool:
        stop = False
        try:
            while msvcrt.kbhit():
                if msvcrt.getwch() in STOP_KEYS:
                    stop = True
        except OSError:
            # Консоли нет (запуск из-под обёртки или с перенаправленным вводом).
            return False
        return stop

    return pressed
