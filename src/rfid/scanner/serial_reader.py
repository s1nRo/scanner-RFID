"""Чтение карт из COM-порта — основной режим.

9600 бод, 8 бит, без чётности, 2 стоп-бита, без управления потоком.
Параметры подтверждены дампом с живого устройства.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Protocol

import serial

from .base import Scan, StatusFn, silent
from .codes import CardCodeError, parse_line

BAUD = 9600
STOPBITS = 2

# Короткий таймаут чтения, чтобы Ctrl+C срабатывал сразу, а не через секунды.
READ_TIMEOUT = 0.1

# Паузы между попытками переподключения, если считыватель выдернули.
_RECONNECT_DELAYS = (1, 2, 5, 10)

# Защита от мусорящего устройства: столько байт без конца строки — сброс.
_MAX_BUFFER = 4096


class _Port(Protocol):
    """Всё, что цикл чтения берёт от порта. Тесты подставляют свою заглушку."""

    def read(self, size: int, /) -> bytes: ...


class SerialCardReader:
    """Чтение карт из COM-порта с переподключением при потере устройства."""

    def __init__(
        self,
        port: str,
        *,
        baud: int = BAUD,
        stopbits: int = STOPBITS,
        on_status: StatusFn = silent,
        reconnect: bool = True,
        stop_after: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.port = port
        self.baud = baud
        self.stopbits = stopbits
        self.on_status = on_status
        self.reconnect = reconnect
        self.stop_after = stop_after
        # Спрашивается между чтениями порта: цикл ждёт карту, а не клавиатуру,
        # поэтому опрос обязан быть неблокирующим.
        self.should_stop = should_stop or (lambda: False)
        self._deadline: float | None = None
        self._serial: serial.Serial | None = None
        self.description = f"COM-порт {port}, {baud} бод, {stopbits} стоп-бит(а)"

    def flush(self) -> None:
        """Выбросить всё, что накопилось в порту.

        Нужно после диалога с оператором: пока он набирал ФИО, кто-то мог
        приложить карту, и эти строки не должны всплыть следующим шагом.
        """
        if self._serial is not None:
            try:
                self._serial.reset_input_buffer()
            except serial.SerialException:
                pass

    def _expired(self) -> bool:
        """Пора заканчивать: вышло время или оператор нажал клавишу выхода."""
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return True
        return self.should_stop()

    def _open(self) -> serial.Serial:
        return serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO if self.stopbits == 2 else serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )

    def scans(self) -> Iterator[Scan]:
        if self.stop_after is not None:
            self._deadline = time.monotonic() + self.stop_after
        attempt = 0
        while not self._expired():
            try:
                with self._open() as ser:
                    if attempt:
                        self.on_status(f"Считыватель снова на связи ({self.port}).")
                    attempt = 0
                    self._serial = ser
                    ser.reset_input_buffer()
                    try:
                        yield from self._read_forever(ser)
                    finally:
                        self._serial = None
                    if self._expired():
                        return
            except serial.SerialException as exc:
                if not self.reconnect:
                    raise
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                attempt += 1
                self.on_status(
                    f"Считыватель недоступен ({exc.__class__.__name__}). "
                    f"Повтор через {delay} с. Проверьте кабель."
                )
                time.sleep(delay)

    def _read_forever(self, ser: _Port) -> Iterator[Scan]:
        buffer = bytearray()
        while True:
            if self._expired():
                return
            chunk = ser.read(256)
            if not chunk:
                continue
            buffer.extend(chunk)
            # Считыватель завершает строки \r\n, но не полагаемся на это жёстко.
            while True:
                index = _find_line_end(buffer)
                if index is None:
                    break
                line, buffer = buffer[:index], buffer[index + 1:]
                scan = self._to_scan(line)
                if scan is not None:
                    yield scan
            if len(buffer) > _MAX_BUFFER:
                buffer.clear()

    def _to_scan(self, raw_bytes: bytes | bytearray) -> Scan | None:
        text = raw_bytes.decode("ascii", errors="replace").strip()
        if not text:
            return None
        try:
            code = parse_line(text)
        except CardCodeError as exc:
            self.on_status(f"Непонятная строка от считывателя: {text!r} — {exc}")
            return None
        if code is None:
            return None
        return Scan(code=code, at=datetime.now(), raw=text)


def _find_line_end(buffer: bytearray) -> int | None:
    for i, byte in enumerate(buffer):
        if byte in (0x0A, 0x0D):
            return i
    return None
