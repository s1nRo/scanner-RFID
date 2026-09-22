"""Тесты источников событий.

Главное здесь — сборка строк из байтов. Реальный COM-порт отдаёт данные
произвольными кусками: строка может прийти по одному байту, а может
склеиться с половиной следующей. Парсер это переживать обязан.
"""

import pytest

from rfid.pipeline import Debounce
from rfid import readers
from rfid.readers import MockCardReader, Scan, SerialCardReader

CARD_A = "Em-Marine[A100] 007,42"
CARD_B = "Em-Marine[B200] 008,43"

# Ровно то, что шлёт железо: два поднесения и уборка карты между ними.
REAL_STREAM = (CARD_A + "\r\n" + "No card\r\n" + CARD_B + "\r\n" + "No card\r\n").encode()


class _Exhausted(Exception):
    """Данные в заглушке кончились — способ выйти из бесконечного цикла."""


class FakeSerial:
    """Отдаёт заранее заданный поток кусками указанного размера."""

    def __init__(self, data: bytes, chunk: int):
        self.data = data
        self.chunk = chunk
        self.pos = 0

    def read(self, _size: int) -> bytes:
        if self.pos >= len(self.data):
            raise _Exhausted
        piece = self.data[self.pos : self.pos + self.chunk]
        self.pos += self.chunk
        return piece


def collect(data: bytes, chunk: int) -> list[Scan]:
    reader = SerialCardReader("COM_TEST")
    scans = []
    try:
        for scan in reader._read_forever(FakeSerial(data, chunk)):
            scans.append(scan)
    except _Exhausted:
        pass
    return scans


class TestLineFraming:
    @pytest.mark.parametrize("chunk", [1, 2, 3, 7, 13, 26, 27, 64, 4096])
    def test_any_chunk_size_gives_same_result(self, chunk):
        """Нарезка потока не должна влиять на результат."""
        scans = collect(REAL_STREAM, chunk)
        assert [s.code.canonical for s in scans] == ["A10007002A", "B20008002B"]

    def test_raw_line_preserved(self):
        scans = collect(REAL_STREAM, 5)
        assert scans[0].raw == CARD_A

    def test_no_card_produces_nothing(self):
        assert collect(b"No card\r\nNo card\r\n", 4) == []

    def test_garbage_is_ignored(self):
        """Мусор и не-ASCII байты не должны ни падать, ни превращаться в отметку."""
        garbage = b"\x00\xff###\r\n" + "чепуха".encode("utf-8") + b"\r\n"
        assert collect(garbage, 3) == []

    def test_lone_lf_also_terminates_line(self):
        """Не полагаемся на то, что окончание строки всегда \\r\\n."""
        scans = collect((CARD_A + "\n").encode(), 8)
        assert len(scans) == 1

    def test_partial_line_without_terminator_is_not_emitted(self):
        """Обрывок без конца строки не должен превращаться в отметку."""
        assert collect(CARD_A.encode(), 6) == []

    def test_recovers_after_garbage(self):
        stream = b"\x01\x02\r\n" + (CARD_A + "\r\n").encode()
        assert len(collect(stream, 3)) == 1


class TestMockReader:
    def test_yields_cards_and_skips_no_card(self):
        reader = MockCardReader(lines=[CARD_A, "No card", CARD_B])
        codes = [s.code.canonical for s in reader.scans()]
        assert codes == ["A10007002A", "B20008002B"]

    def test_empty(self):
        assert list(MockCardReader(lines=[]).scans()) == []


class TestReaderChoice:
    """Режим auto не должен подменять чтение карты набором кода."""

    def test_auto_without_port_refuses(self, monkeypatch):
        monkeypatch.setattr(readers, "find_reader_port", lambda: None)
        with pytest.raises(readers.ReaderUnavailable):
            readers.make_reader("auto")

    def test_auto_with_port_uses_serial(self, monkeypatch):
        monkeypatch.setattr(readers, "find_reader_port", lambda: "COM9")
        reader = readers.make_reader("auto")
        assert isinstance(reader, readers.SerialCardReader)
        assert reader.port == "COM9"

    def test_serial_without_port_refuses(self, monkeypatch):
        monkeypatch.setattr(readers, "find_reader_port", lambda: None)
        with pytest.raises(readers.ReaderUnavailable):
            readers.make_reader("serial")

    def test_manual_entry_only_when_asked(self):
        assert isinstance(readers.make_reader("keyboard"), readers.KeyboardCardReader)

    def test_explanation_mentions_manual_mode(self):
        assert "--mode keyboard" in readers.no_port_explanation()

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            readers.make_reader("телепатия")


class TestDebounce:
    def test_repeat_within_window_suppressed(self):
        guard = Debounce(window=2.0)
        assert guard.is_repeat("A", 100.0) is False
        assert guard.is_repeat("A", 101.0) is True

    def test_after_window_allowed(self):
        guard = Debounce(window=2.0)
        guard.is_repeat("A", 100.0)
        assert guard.is_repeat("A", 102.5) is False

    def test_different_cards_independent(self):
        guard = Debounce(window=2.0)
        guard.is_repeat("A", 100.0)
        assert guard.is_repeat("B", 100.1) is False

    def test_zero_window_never_suppresses(self):
        guard = Debounce(window=0.0)
        guard.is_repeat("A", 100.0)
        assert guard.is_repeat("A", 100.0) is False


class TestPortMatching:
    """Считыватель бывает на родном PID 1234 и на стандартном FTDI 6001.

    Второй появляется после перепрошивки FT-Prog ради plug-and-play:
    для 6001 Windows ставит драйвер сама. Но 6001 носит любой FTDI-переходник,
    поэтому там опираемся ещё и на серийный номер IronLogic.
    """

    @staticmethod
    def port(device, pid, serial=None):
        return readers.PortInfo(device, readers.VID, pid, serial, "")

    def test_ironlogic_pid_recognised(self):
        assert self.port("COM3", readers.PID_IRONLOGIC, "ILTEST01").is_reader

    def test_standard_ftdi_pid_recognised(self):
        assert self.port("COM3", readers.PID_FTDI, "ILTEST01").is_reader

    def test_foreign_device_is_not_a_reader(self):
        assert not readers.PortInfo("COM9", 0x1A86, 0x7523, None, "CH340").is_reader

    def test_ironlogic_pid_wins(self, monkeypatch):
        ports = [
            self.port("COM9", readers.PID_FTDI, "FTTEST01"),
            self.port("COM3", readers.PID_IRONLOGIC, "ILTEST01"),
        ]
        monkeypatch.setattr(readers, "available_ports", lambda: ports)
        assert readers.find_reader_port() == "COM3"

    def test_serial_prefix_breaks_the_tie_on_6001(self, monkeypatch):
        """Два FTDI на 6001: наш тот, чей серийник начинается с IL."""
        ports = [
            self.port("COM9", readers.PID_FTDI, "FTTEST01"),
            self.port("COM4", readers.PID_FTDI, "ILTEST01"),
        ]
        monkeypatch.setattr(readers, "available_ports", lambda: ports)
        assert readers.find_reader_port() == "COM4"

    def test_lone_ftdi_still_accepted(self, monkeypatch):
        ports = [self.port("COM7", readers.PID_FTDI, None)]
        monkeypatch.setattr(readers, "available_ports", lambda: ports)
        assert readers.find_reader_port() == "COM7"

    def test_nothing_suitable(self, monkeypatch):
        monkeypatch.setattr(readers, "available_ports",
                            lambda: [readers.PortInfo("COM1", None, None, None, "")])
        assert readers.find_reader_port() is None
