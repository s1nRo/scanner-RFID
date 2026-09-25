"""Разбор строк считывателя IronLogic и канонический код карты.

Формат протокола считывателя; идентификаторы в примерах вымышлены:

    Em-Marine[A100] 007,42\r\n      карта поднесена
    No card\r\n                        карта убрана

Раскладка идентификатора:

    [HHHH]  — старшие 2 байта
    FFF     — серия, 1 байт
    NNNNN   — номер, 2 байта
    ---------------------------------
    всего 40 бит — размер идентификатора EM4100

    007,42    ->  7 * 65536 + 42 = 458794 = 0x07002A
    [A100]     ->  0xA100
    полный ID  ->  0xA10007002A

ВАЖНО: префикс в скобках — часть идентификатора, а не служебное поле.
Проверено на двух картах: он различается между ними и стабилен для каждой.
Канонический код обязан его включать, иначе две карты с разными префиксами
сольются в одного студента.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Строка, которую считыватель шлёт один раз после того, как карту убрали.
NO_CARD = "No card"

# Em-Marine[A100] 007,42
# Тип карты не фиксируем жёстко: поле очевидно есть, а прошивки бывают разные.
_CARD_RE = re.compile(
    r"^(?P<kind>[^\[\]]{1,32})"
    r"\[(?P<prefix>[0-9A-Fa-f]{4})\]"
    r"\s+(?P<facility>\d{1,3}),(?P<number>\d{1,5})$"
)

# Канонический вид: 10 шестнадцатеричных цифр, ровно 5 байт.
_CANONICAL_RE = re.compile(r"^(?P<hex>[0-9A-Fa-f]{10})$")

_MAX_PREFIX = 0xFFFF
_MAX_FACILITY = 0xFF
_MAX_NUMBER = 0xFFFF


class CardCodeError(ValueError):
    """Строку можно было принять за код карты, но значения не сходятся."""


@dataclass(frozen=True, slots=True)
class CardCode:
    """Идентификатор карты, приведённый к одному виду."""

    prefix: int
    facility: int
    number: int
    kind: str = "Em-Marine"

    def __post_init__(self) -> None:
        if not 0 <= self.prefix <= _MAX_PREFIX:
            raise CardCodeError(f"префикс вне диапазона: {self.prefix}")
        if not 0 <= self.facility <= _MAX_FACILITY:
            raise CardCodeError(f"серия вне диапазона 0..255: {self.facility}")
        if not 0 <= self.number <= _MAX_NUMBER:
            raise CardCodeError(f"номер вне диапазона 0..65535: {self.number}")

    @property
    def value(self) -> int:
        """Полный 40-битный идентификатор одним числом."""
        return (self.prefix << 24) | (self.facility << 16) | self.number

    @property
    def canonical(self) -> str:
        """Как код хранится в базе: 10 шестнадцатеричных цифр."""
        return f"{self.value:010X}"

    @property
    def pretty(self) -> str:
        """Как код показывают человеку — в виде с самого считывателя."""
        return f"{self.facility:03d},{self.number}"

    @property
    def bytes5(self) -> bytes:
        return self.value.to_bytes(5, "big")

    def __str__(self) -> str:
        return self.canonical

    @classmethod
    def from_canonical(cls, text: str) -> CardCode:
        """Собрать код обратно из канонической записи."""
        m = _CANONICAL_RE.match(text.strip())
        if not m:
            raise CardCodeError(
                f"не канонический код: {text!r} (ожидается 10 шестнадцатеричных цифр)"
            )
        value = int(m.group("hex"), 16)
        return cls(
            prefix=value >> 24,
            facility=(value >> 16) & 0xFF,
            number=value & 0xFFFF,
        )


def parse_line(line: str) -> CardCode | None:
    """Разобрать одну строку от считывателя.

    Возвращает CardCode, если это код карты, иначе None — так отсеиваются
    и `No card`, и любой посторонний шум в порту.

    CardCodeError бросается только когда строка по форме похожа на код,
    но числа в ней невозможны: это признак того, что формат прошивки другой,
    и молчать о таком нельзя.
    """
    text = line.strip()
    if not text or text == NO_CARD:
        return None

    m = _CARD_RE.match(text)
    if not m:
        return None

    return CardCode(
        prefix=int(m.group("prefix"), 16),
        facility=int(m.group("facility")),
        number=int(m.group("number")),
        kind=m.group("kind").strip(),
    )


def parse_manual(text: str) -> CardCode:
    """Разобрать код, введённый человеком с клавиатуры.

    Принимает либо целую строку считывателя, либо канонические 10 цифр.
    Короткая форма `007,42` намеренно НЕ принимается: в ней нет префикса,
    а без него идентификатор неполон и две разные карты могут совпасть.
    """
    text = text.strip()
    if not text:
        raise CardCodeError("пустой ввод")

    code = parse_line(text)
    if code is not None:
        return code

    if _CANONICAL_RE.match(text):
        return CardCode.from_canonical(text)

    if re.match(r"^\d{1,3},\d{1,5}$", text):
        raise CardCodeError(
            f"{text!r} — короткая форма без префикса. "
            "Нужен полный код: 10 шестнадцатеричных цифр или строка целиком "
            "вида 'Em-Marine[A100] 007,42'"
        )

    raise CardCodeError(f"не удалось разобрать код: {text!r}")
