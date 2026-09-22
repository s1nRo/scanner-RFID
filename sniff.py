"""Диагностика IronLogic RFID Adapter: что РЕАЛЬНО приходит в COM-порт.

Инструмент ничего не разбирает и ничего не предполагает о формате.
Он печатает сырые байты — hex и ASCII — чтобы формат можно было увидеть,
а не угадать. Парсер пишется уже после того, как дамп получен.

    py sniff.py                 автопоиск порта, дамп до Ctrl+C
    py sniff.py --list          какие COM-порты есть в системе
    py sniff.py --port COM3     если автопоиск не сработал
    py sniff.py --scan          перебрать скорости, если 9600 не подошло
    py sniff.py --save dump.txt сохранить дамп в файл
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("Не установлен pyserial.  Установите:  py -m pip install pyserial")

VID = 0x0403
PID = 0x1234

# Гипотеза из документации, НЕ подтверждённая на живом устройстве.
DEFAULT_BAUD = 9600
DEFAULT_STOPBITS = 2

# Скорости для --scan: сначала самые вероятные.
SCAN_BAUDS = [9600, 19200, 38400, 57600, 115200, 4800, 2400]

# Пауза в потоке, после которой считаем, что пакет закончился.
IDLE_GAP = 0.08


def describe(port) -> str:
    bits = [port.device]
    if port.vid is not None:
        bits.append("VID:PID={:04X}:{:04X}".format(port.vid, port.pid))
    if port.serial_number:
        bits.append("SN=" + port.serial_number)
    if port.description and port.description != "n/a":
        bits.append(port.description)
    return "  ".join(bits)


def all_ports() -> list:
    return sorted(list_ports.comports(), key=lambda p: p.device)


def find_reader() -> str | None:
    for p in all_ports():
        if p.vid == VID and p.pid == PID:
            return p.device
    return None


def cmd_list() -> int:
    ports = all_ports()
    if not ports:
        print("COM-портов в системе нет вообще.")
        return 1
    print("Найдено портов: {}".format(len(ports)))
    for p in ports:
        mark = "  <-- считыватель" if (p.vid == VID and p.pid == PID) else ""
        print("  " + describe(p) + mark)
    return 0


def no_port_help() -> int:
    """Порт не найден. Объяснить почему, опираясь на факты, а не на догадки."""
    print("Считыватель как COM-порт не найден.\n")
    ports = all_ports()
    if ports:
        print("Порты в системе есть, но ни один не совпал с VID:PID 0403:1234:")
        for p in ports:
            print("  " + describe(p))
        print()
    print("Скорее всего не установлен драйвер. Проверьте по порядку:")
    print("  1. Считыватель физически воткнут в USB?")
    print("  2. Диспетчер устройств: есть ли 'USB IronLogic RFID Adapter'")
    print("     в разделе 'Другие устройства' с жёлтым значком?")
    print("  3. Драйвер ставится в ДВА этапа из папки driver\\DRV_Win_All:")
    print("     сначала на сам адаптер (появится 'USB Serial Converter'),")
    print("     затем на возникшее следом устройство 'USB Serial Port'.")
    print("     COM-порт создаёт только ВТОРОЙ этап.")
    print()
    print("  Важно: пакет cdm-v2.12.36.20-whql-certified.zip НЕ подходит —")
    print("  в его INF нет PID_1234. Нужен drv_z2_z397_v2.12.26 (driver\\DRV_Win_All).")
    return 1


def open_port(port: str, baud: int, stopbits: int) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_TWO if stopbits == 2 else serial.STOPBITS_ONE,
        timeout=0.03,
        rtscts=False,
        dsrdtr=False,
        xonxoff=False,
    )


def render(chunk: bytes) -> tuple[str, str]:
    hexed = " ".join("{:02X}".format(b) for b in chunk)
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    return hexed, text


def dump(port: str, baud: int, stopbits: int, seconds, save) -> int:
    print("Порт {}: {} бод, 8 бит, без чётности, {} стоп-бит(а).".format(port, baud, stopbits))
    print("Приложите карту. Ctrl+C — выход.\n")
    header = "{:>8}  {:>4}  {:<47}  ASCII".format("время", "байт", "HEX")
    print(header)
    print("-" * 100)

    frames: Counter = Counter()
    total = 0
    started = time.monotonic()
    buf = bytearray()
    last_rx = None

    def emit(line: str) -> None:
        print(line)
        if save:
            save.write(line + "\n")

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        chunk = bytes(buf)
        buf = bytearray()
        frames[chunk] += 1
        # Длинные пакеты переносим по 16 байт, чтобы колонки не разъезжались.
        for i in range(0, len(chunk), 16):
            h, t = render(chunk[i:i + 16])
            stamp = "{:7.3f}".format(time.monotonic() - started) if i == 0 else ""
            size = "{:4d}".format(len(chunk)) if i == 0 else "    "
            emit("{:>8}  {}  {:<47}  {}".format(stamp, size, h, t))

    try:
        with open_port(port, baud, stopbits) as ser:
            ser.reset_input_buffer()
            while True:
                data = ser.read(4096)
                now = time.monotonic()
                if data:
                    buf.extend(data)
                    total += len(data)
                    last_rx = now
                elif last_rx is not None and now - last_rx >= IDLE_GAP:
                    flush()
                    last_rx = None
                if seconds is not None and now - started >= seconds:
                    flush()
                    break
    except KeyboardInterrupt:
        flush()
        print("\n(остановлено)")
    except serial.SerialException as exc:
        print("\nОшибка порта: {}".format(exc))
        return 1

    elapsed = time.monotonic() - started
    print("-" * 100)
    print("Принято {} байт за {:.1f} с.".format(total, elapsed))
    if not total:
        print("\nНи одного байта. Возможные причины:")
        print("  - считыватель молчит, пока не поднесена карта (приложите карту);")
        print("  - не та скорость — попробуйте  py sniff.py --scan;")
        print("  - порт занят другой программой.")
        return 1

    print("\nРазных пакетов: {}. Самые частые:".format(len(frames)))
    for chunk, count in frames.most_common(12):
        hexed, text = render(chunk[:24])
        tail = " ..." if len(chunk) > 24 else ""
        print("  x{:<5} {:3d} байт  {}{}".format(count, len(chunk), text, tail))
        print("              {}{}".format(hexed, tail))
    return 0


def scan(port: str, stopbits: int, seconds: float) -> int:
    print("Перебор скоростей на {}, по {:g} с на каждую.".format(port, seconds))
    print("Держите карту у считывателя всё это время.\n")
    print("{:>7}  {:>6}  {:>9}  вывод".format("бод", "байт", "печатных"))
    print("-" * 78)
    best = []
    for baud in SCAN_BAUDS:
        try:
            with open_port(port, baud, stopbits) as ser:
                ser.reset_input_buffer()
                deadline = time.monotonic() + seconds
                data = bytearray()
                while time.monotonic() < deadline:
                    data.extend(ser.read(4096))
        except serial.SerialException as exc:
            print("{:>7}  ошибка: {}".format(baud, exc))
            continue
        if not data:
            print("{:>7}  {:>6}  {:>9}  тишина".format(baud, 0, "-"))
            continue
        printable = sum(1 for b in data if 32 <= b < 127 or b in (10, 13))
        ratio = printable / len(data)
        _, text = render(bytes(data[:40]))
        print("{:>7}  {:>6}  {:>8.0%}  {}".format(baud, len(data), ratio, text))
        best.append((ratio, len(data), baud))
    print("-" * 78)
    if not best:
        print("Ни на одной скорости данных не пришло.")
        return 1
    best.sort(reverse=True)
    ratio, _, baud = best[0]
    print("Вероятнее всего: {} бод (печатных символов {:.0%}).".format(baud, ratio))
    print("Проверить:  py sniff.py --port {} --baud {}".format(port, baud))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Показать, что реально присылает RFID-считыватель.")
    ap.add_argument("--list", action="store_true", help="показать COM-порты и выйти")
    ap.add_argument("--port", help="COM-порт (по умолчанию ищется по VID:PID 0403:1234)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                    help="скорость, по умолчанию {}".format(DEFAULT_BAUD))
    ap.add_argument("--stopbits", type=int, choices=(1, 2), default=DEFAULT_STOPBITS,
                    help="стоп-биты, по умолчанию {}".format(DEFAULT_STOPBITS))
    ap.add_argument("--seconds", type=float, help="остановиться через N секунд")
    ap.add_argument("--scan", action="store_true", help="перебрать скорости")
    ap.add_argument("--save", help="записать дамп в файл")
    args = ap.parse_args()

    if args.list:
        return cmd_list()

    port = args.port or find_reader()
    if not port:
        return no_port_help()
    if not args.port:
        print("Считыватель найден: {}\n".format(port))

    if args.scan:
        return scan(port, args.stopbits, args.seconds or 3.0)

    save = open(args.save, "w", encoding="utf-8") if args.save else None
    try:
        return dump(port, args.baud, args.stopbits, args.seconds, save)
    finally:
        if save:
            save.close()
            print("Дамп сохранён: {}".format(args.save))


if __name__ == "__main__":
    sys.exit(main())
