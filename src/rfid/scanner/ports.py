"""Поиск COM-порта считывателя.

Считыватель работает как виртуальный COM-порт FTDI (VID 0403).
"""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools import list_ports

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
