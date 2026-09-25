"""Тесты парсера на вымышленных кодах в формате протокола считывателя."""

from pathlib import Path

import pytest

from rfid.scanner import NO_CARD, CardCode, CardCodeError, parse_line, parse_manual
from tests.helpers import card

# Синтетические идентификаторы двух разных карт, не связанные со студентами.
CARD_A_LINE = "Em-Marine[A100] 007,42"
CARD_B_LINE = "Em-Marine[B200] 008,43"

SAMPLES = Path(__file__).parent / "data" / "reader_lines.txt"


class TestSampleLines:
    def test_card_a(self):
        code = parse_line(CARD_A_LINE)
        assert code is not None
        assert code.prefix == 0xA100
        assert code.facility == 7
        assert code.number == 42
        assert code.kind == "Em-Marine"
        assert code.canonical == "A10007002A"
        assert code.bytes5 == bytes([0xA1, 0x00, 0x07, 0x00, 0x2A])

    def test_card_b(self):
        code = parse_line(CARD_B_LINE)
        assert code is not None
        assert code.prefix == 0xB200
        assert code.facility == 8
        assert code.number == 43
        assert code.canonical == "B20008002B"
        assert code.bytes5 == bytes([0xB2, 0x00, 0x08, 0x00, 0x2B])

    def test_cards_do_not_collide(self):
        """Главная причина, по которой префикс входит в код."""
        a = card(CARD_A_LINE)
        b = card(CARD_B_LINE)
        assert a != b
        assert a.canonical != b.canonical

    def test_prefix_actually_matters(self):
        """Один и тот же 007,42 с разными префиксами — разные карты."""
        one = card("Em-Marine[A100] 007,42")
        two = card("Em-Marine[B200] 007,42")
        assert one.canonical != two.canonical

    def test_line_endings_are_stripped(self):
        assert parse_line(CARD_A_LINE + "\r\n") == parse_line(CARD_A_LINE)

    def test_every_sample_line_is_handled(self):
        """Все строки синтетического набора должны разбираться."""
        for raw in SAMPLES.read_text(encoding="utf-8").splitlines():
            code = parse_line(raw)
            if raw.strip() == NO_CARD:
                assert code is None
            else:
                assert code is not None, f"не разобрана тестовая строка: {raw!r}"


class TestNonCardLines:
    @pytest.mark.parametrize(
        "line", ["No card", "No card\r\n", "", "   ", "\r\n", "мусор из порта", "\x00\xff"]
    )
    def test_returns_none(self, line):
        assert parse_line(line) is None


class TestCanonicalRoundTrip:
    @pytest.mark.parametrize("line", [CARD_A_LINE, CARD_B_LINE])
    def test_round_trip(self, line):
        code = card(line)
        assert CardCode.from_canonical(code.canonical) == CardCode(
            prefix=code.prefix, facility=code.facility, number=code.number
        )

    def test_canonical_is_always_ten_chars(self):
        assert CardCode(prefix=0, facility=0, number=0).canonical == "0000000000"
        assert CardCode(prefix=0xFFFF, facility=0xFF, number=0xFFFF).canonical == "FFFFFFFFFF"

    def test_leading_zeros_survive(self):
        """Ведущие нули терялись в прошлой версии проекта — здесь не должны."""
        code = CardCode(prefix=0x0001, facility=2, number=3)
        assert code.canonical == "0001020003"
        assert CardCode.from_canonical(code.canonical) == code

    @pytest.mark.parametrize("bad", ["A10007002", "A10007002A5", "ZZZZZZZZZZ", "007,42", ""])
    def test_rejects_malformed(self, bad):
        with pytest.raises(CardCodeError):
            CardCode.from_canonical(bad)


class TestManualEntry:
    def test_accepts_full_line(self):
        assert parse_manual(CARD_A_LINE).canonical == "A10007002A"

    def test_accepts_canonical(self):
        assert parse_manual("a10007002a").canonical == "A10007002A"

    def test_rejects_short_form_with_explanation(self):
        """Короткая форма неполна — принять её значило бы рисковать коллизией."""
        with pytest.raises(CardCodeError, match="префикса"):
            parse_manual("007,42")

    @pytest.mark.parametrize("bad", ["", "   ", "привет"])
    def test_rejects_garbage(self, bad):
        with pytest.raises(CardCodeError):
            parse_manual(bad)


class TestRangeChecks:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"prefix": -1, "facility": 0, "number": 0},
            {"prefix": 0x10000, "facility": 0, "number": 0},
            {"prefix": 0, "facility": 256, "number": 0},
            {"prefix": 0, "facility": 0, "number": 0x10000},
        ],
    )
    def test_out_of_range_rejected(self, kwargs):
        with pytest.raises(CardCodeError):
            CardCode(**kwargs)

    def test_impossible_values_from_reader_are_loud(self):
        """Строка похожа на код, но 999 не влезает в байт — молчать нельзя."""
        with pytest.raises(CardCodeError):
            parse_line("Em-Marine[A100] 999,42")
