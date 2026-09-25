"""Работа со считывателем IronLogic.

    codes.py          строка считывателя → канонический код карты
    ports.py          поиск COM-порта считывателя, объяснение, если его нет
    base.py           Scan и интерфейс CardReader
    serial_reader.py  чтение из COM-порта с переподключением
    manual.py         ручной ввод и заглушка — источники без железа
    factory.py        make_reader: источник по режиму

Снаружи пользуются этим фасадом, а не модулями внутри.
"""

from .base import CardReader, Scan
from .codes import NO_CARD, CardCode, CardCodeError, parse_line, parse_manual
from .factory import MODES, ReaderUnavailable, make_reader
from .manual import KeyboardCardReader, MockCardReader
from .ports import (
    KNOWN_PIDS, PID_FTDI, PID_IRONLOGIC, VID, PortInfo,
    available_ports, find_reader_port, no_port_explanation,
)
from .serial_reader import SerialCardReader

__all__ = [
    "KNOWN_PIDS", "MODES", "NO_CARD", "PID_FTDI", "PID_IRONLOGIC", "VID",
    "CardCode", "CardCodeError", "CardReader", "KeyboardCardReader", "MockCardReader",
    "PortInfo", "ReaderUnavailable", "Scan", "SerialCardReader",
    "available_ports", "find_reader_port", "make_reader",
    "no_port_explanation", "parse_line", "parse_manual",
]
