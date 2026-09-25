"""Тесты опроса клавиши выхода.

Цикл отметки ждёт порт, а не клавиатуру, поэтому раньше выходили только
через Ctrl+C — а под Windows он вдобавок заставляет cmd.exe спрашивать
«Завершить выполнение пакетного файла?».
"""

import builtins

import pytest

from rfid import scanner
from rfid.cli import keys
from tests.helpers import FakeSerial
from tests.test_readers import CARD_A


class FakeMsvcrt:
    """Подделка консоли: отдаёт заранее заданные нажатия."""

    def __init__(self, typed=""):
        self.buffer = list(typed)

    def kbhit(self):
        return len(self.buffer)

    def getwch(self):
        return self.buffer.pop(0)


@pytest.fixture
def fake_console(monkeypatch):
    def install(typed=""):
        fake = FakeMsvcrt(typed)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "msvcrt":
                return fake
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        return fake

    return install


class TestStopWatcher:
    @pytest.mark.parametrize("key", ["q", "Q", "й", "Й", "\x1b"])
    def test_stop_keys(self, fake_console, key):
        fake_console(key)
        assert keys.make_stop_watcher()() is True

    def test_other_keys_do_not_stop(self, fake_console):
        fake_console("abc123")
        assert keys.make_stop_watcher()() is False

    def test_nothing_pressed(self, fake_console):
        fake_console("")
        assert keys.make_stop_watcher()() is False

    def test_buffer_is_drained(self, fake_console):
        """Случайные нажатия не должны всплыть в следующем вопросе."""
        fake = fake_console("abc")
        keys.make_stop_watcher()()
        assert fake.buffer == []

    def test_stop_key_among_others_still_works(self, fake_console):
        fake_console("abqcd")
        assert keys.make_stop_watcher()() is True

    def test_without_console_never_stops(self, monkeypatch):
        """Под пайпом и не на Windows msvcrt нет — выходят по Ctrl+C."""
        real_import = builtins.__import__

        def no_msvcrt(name, *args, **kwargs):
            if name == "msvcrt":
                raise ImportError("no console")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_msvcrt)
        assert keys.make_stop_watcher()() is False


class TestReaderStops:
    def test_reader_asks_the_watcher(self):
        reader = scanner.SerialCardReader("COM_TEST", should_stop=lambda: True)
        assert reader._expired() is True

    def test_reader_runs_while_watcher_is_quiet(self):
        reader = scanner.SerialCardReader("COM_TEST", should_stop=lambda: False)
        assert reader._expired() is False

    def test_default_watcher_never_stops(self):
        assert scanner.SerialCardReader("COM_TEST")._expired() is False

    def test_watcher_stops_the_read_loop(self):
        """Нажатие прерывает цикл, даже если порт продолжает слать данные."""

        line = (CARD_A + "\r\n").encode()
        pressed = {"yes": False}
        reader = scanner.SerialCardReader(
            "COM_TEST", should_stop=lambda: pressed["yes"]
        )
        scans = []
        # Кусок ровно в одну строку: чтение прерывается сразу после карты.
        for scan in reader._read_forever(FakeSerial(line * 5, len(line))):
            scans.append(scan)
            pressed["yes"] = True
        assert len(scans) == 1

    def test_cards_already_read_are_not_dropped(self):
        """Карты, пришедшие ДО нажатия, обязаны быть записаны.

        Они физически приложены, и потерять их нельзя: буфер дочитывается
        до конца, и только потом цикл останавливается.
        """

        line = (CARD_A + "\r\n").encode()
        reader = scanner.SerialCardReader("COM_TEST", should_stop=lambda: True)
        # Три строки пришли одним куском, клавиша нажата с самого начала.
        scans = list(reader._read_forever(FakeSerial(line * 3, len(line) * 3)))
        assert len(scans) == 0, "до первого чтения цикл выходит сразу"

        pressed = {"yes": False}
        reader = scanner.SerialCardReader("COM_TEST", should_stop=lambda: pressed["yes"])
        got = []
        for scan in reader._read_forever(FakeSerial(line * 3, len(line) * 3)):
            got.append(scan)
            pressed["yes"] = True
        assert len(got) == 3, "весь прочитанный кусок должен дойти до записи"
