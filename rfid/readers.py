"""Источники событий: COM-порт, ручной ввод, заглушка для тестов.

Основной режим — serial: считыватель работает как виртуальный COM-порт
(FTDI, VID 0403, PID 1234 или 6001), 9600 бод, 8 бит, без чётности,
2 стоп-бита.
Параметры подтверждены дампом с живого устройства.

Режим keyboard — не «эмуляция клавиатуры» считывателя (этот чип так не умеет),
а запасной ручной ввод кода, если железо откажет прямо на паре.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import serial
from serial.tools import list_ports

from .codes import CardCode, CardCodeError, parse_line, parse_manual

VID = 0x0403

# У считывателя два возможных PID.
#   1234 — собственный PID IronLogic. Windows про него не знает: в каталоге
#          Microsoft Update по IronLogic пусто, драйвер ставится вручную
#          и форсированно.
#   6001 — стандартный PID чипа FT232. Windows ставит драйвер сама
#          (в каталоге обновлений сотня записей, свежая 2.12.36.20).
# Перепрошивка PID с 1234 на 6001 утилитой FT-Prog превращает устройство
# в plug-and-play, поэтому ищем по обоим.
PID_IRONLOGIC = 0x1234
PID_FTDI = 0x6001
KNOWN_PIDS = (PID_IRONLOGIC, PID_FTDI)

# Серийные номера IronLogic начинаются с IL (тестовый пример: «ILTEST01»).
# Помогает не спутать считыватель с посторонним FTDI-переходником,
# когда оба сидят на стандартном PID 6001.
SERIAL_PREFIX = "IL"

BAUD = 9600
STOPBITS = 2

# Короткий таймаут чтения, чтобы Ctrl+C срабатывал сразу, а не через секунды.
READ_TIMEOUT = 0.1

# Паузы между попытками переподключения, если считыватель выдернули.
_RECONNECT_DELAYS = (1, 2, 5, 10)

StatusFn = Callable[[str], None]


def _silent(_message: str) -> None:
    pass


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


# ------------------------------------------------------------------ поиск порта


@dataclass(frozen=True, slots=True)
class PortInfo:
    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    description: str

    @property
    def is_reader(self) -> bool:
        return self.vid == VID and self.pid in KNOWN_PIDS

    @property
    def looks_ironlogic(self) -> bool:
        return bool(self.serial_number and self.serial_number.startswith(SERIAL_PREFIX))

    @property
    def rank(self) -> int:
        """Чем меньше, тем вероятнее, что это наш считыватель.

        Родной PID IronLogic не спутать ни с чем. Стандартный PID 6001
        бывает у любого FTDI-переходника, поэтому там смотрим на серийный номер.
        """
        if self.pid == PID_IRONLOGIC:
            return 0
        return 1 if self.looks_ironlogic else 2

    def __str__(self) -> str:
        bits = [self.device]
        if self.vid is not None:
            bits.append(f"VID:PID={self.vid:04X}:{self.pid:04X}")
        if self.serial_number:
            bits.append(f"SN={self.serial_number}")
        if self.description and self.description != "n/a":
            bits.append(self.description)
        return "  ".join(bits)


def available_ports() -> list[PortInfo]:
    return sorted(
        (
            PortInfo(p.device, p.vid, p.pid, p.serial_number, p.description or "")
            for p in list_ports.comports()
        ),
        key=lambda p: p.device,
    )


def reader_candidates() -> list[PortInfo]:
    """Подходящие порты, самый вероятный первым."""
    return sorted(
        (p for p in available_ports() if p.is_reader),
        key=lambda p: (p.rank, p.device),
    )


def find_reader_port() -> str | None:
    """Найти COM-порт считывателя."""
    candidates = reader_candidates()
    return candidates[0].device if candidates else None


# ---------------------------------------------------------------- COM-считыватель


class SerialCardReader:
    """Чтение карт из COM-порта с переподключением при потере устройства."""

    def __init__(
        self,
        port: str,
        *,
        baud: int = BAUD,
        stopbits: int = STOPBITS,
        on_status: StatusFn = _silent,
        reconnect: bool = True,
        stop_after: float | None = None,
    ):
        self.port = port
        self.baud = baud
        self.stopbits = stopbits
        self.on_status = on_status
        self.reconnect = reconnect
        self.stop_after = stop_after
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
        return self._deadline is not None and time.monotonic() >= self._deadline

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

    def _read_forever(self, ser: serial.Serial) -> Iterator[Scan]:
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
            if len(buffer) > 4096:  # защита от мусорящего устройства
                buffer.clear()

    def _to_scan(self, raw_bytes: bytes) -> Scan | None:
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


# --------------------------------------------------------------- ручной ввод


class KeyboardCardReader:
    """Запасной режим: код набирают руками, если считыватель отказал."""

    description = "ручной ввод кода с клавиатуры"

    def __init__(self, *, on_status: StatusFn = _silent, stream=None):
        self.on_status = on_status
        self.stream = stream if stream is not None else sys.stdin

    def scans(self) -> Iterator[Scan]:
        self.on_status(
            "Введите код карты и нажмите Enter. "
            "Годится и строка целиком, и 10 шестнадцатеричных цифр. Ctrl+C — выход."
        )
        for line in self.stream:
            text = line.strip()
            if not text:
                continue
            try:
                code = parse_manual(text)
            except CardCodeError as exc:
                self.on_status(str(exc))
                continue
            yield Scan(code=code, at=datetime.now(), raw=text)

    def flush(self) -> None:
        pass


# ------------------------------------------------------------------- заглушка


@dataclass
class MockCardReader:
    """Проигрывает заранее заданные строки — для тестов и демонстрации."""

    lines: Iterable[str]
    delay: float = 0.0
    description: str = "заглушка (без железа)"
    on_status: StatusFn = field(default=_silent)

    def flush(self) -> None:
        pass

    def scans(self) -> Iterator[Scan]:
        for raw in self.lines:
            if self.delay:
                time.sleep(self.delay)
            code = parse_line(raw)
            if code is None:
                continue
            yield Scan(code=code, at=datetime.now(), raw=raw.strip())


# --------------------------------------------------------------------- фабрика


class ReaderUnavailable(RuntimeError):
    """Нужного источника событий нет — с объяснением, что делать."""


def make_reader(
    mode: str = "auto",
    *,
    port: str | None = None,
    on_status: StatusFn = _silent,
    mock_lines: Iterable[str] | None = None,
    stop_after: float | None = None,
) -> CardReader:
    if mode == "mock":
        return MockCardReader(lines=mock_lines or [], on_status=on_status)
    if mode == "keyboard":
        return KeyboardCardReader(on_status=on_status)

    device = port or find_reader_port()

    if mode == "serial":
        if not device:
            raise ReaderUnavailable(no_port_explanation())
        return SerialCardReader(device, on_status=on_status, stop_after=stop_after)

    if mode == "auto":
        if device:
            return SerialCardReader(device, on_status=on_status, stop_after=stop_after)
        # Молча переходить на ручной ввод нельзя: человек ждёт, что приложит
        # карту, а у него вдруг просят набрать код. Лучше честно отказать.
        raise ReaderUnavailable(no_port_explanation())

    raise ValueError(f"неизвестный режим: {mode!r}")


def no_port_explanation() -> str:
    """Понятное объяснение вместо голого «порт не найден»."""
    lines = ["Считыватель как COM-порт не найден."]
    ports = available_ports()
    if ports:
        lines.append(
            "Порты в системе есть, но ни один не похож на считыватель "
            "(VID 0403, PID 1234 или 6001):"
        )
        lines += [f"  {p}" for p in ports]
    lines += [
        "",
        "Проверьте:",
        "  1. Считыватель воткнут в USB?",
        "  2. Диспетчер устройств → «Порты (COM и LPT)» → есть ли USB Serial Port?",
        "  3. Драйвер ставится в ДВА этапа из driver\\CDM_2.12.36.20 —",
        "     сначала ftdibus.inf на адаптер, затем ftdiport.inf на возникший",
        "     следом узел. COM-порт создаёт только второй этап.",
        "",
        "Если считыватель недоступен, а работать нужно прямо сейчас, код карты",
        "можно вводить руками — но только осознанно:  --mode keyboard",
    ]
    return "\n".join(lines)
