"""Драйвер считывателя: найти пакет рядом с программой и поставить его.

Нужен портабельной сборке: exe и папка driver/ лежат на флешке, а на новом
компьютере считыватель видится как «неизвестное устройство». Программа
ставит драйвер сама — то же, что человек делал руками в диспетчере
устройств (instruction.md, шаг 1):

    этап 1  USB\\VID_0403&PID_1234\\...        ftdibus.inf   «USB Serial Converter»
    этап 2  FTDIBUS\\COMPORT&VID_0403&PID_1234  ftdiport.inf  «USB Serial Port»

Подписанный пакет FTDI про PID 1234 не знает, поэтому драйвер навязывается
устройству принудительно (SetupAPI: DI_ENUMSINGLEINF, разрешение
«несовместимых» драйверов, DiInstallDevice с выбранным драйвером). Подпись
пакета при этом настоящая, Secure Boot трогать не нужно.

Без прав администратора драйвер в Windows не поставить никак — это
ограничение системы, а не программы. Поэтому установка один раз на каждом
компьютере спрашивает разрешение (UAC). Дальше считыватель работает
и без флешки-с-правами: драйвер остаётся в системе.

Код Windows-only (ctypes к setupapi/newdev/cfgmgr32). Решения — что ставить
и на какой узел — вынесены в чистые функции и проверяются тестами без Windows.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .ports import KNOWN_PIDS, PID_IRONLOGIC, SERIAL_PREFIX, VID

BUS_INF = "ftdibus.inf"
PORT_INF = "ftdiport.inf"
BUS_MODEL = "USB Serial Converter"
PORT_MODEL = "USB Serial Port"

DRIVER_DIR = Path("driver")

# Проблема «драйвер не установлен» у узла (CM_PROB_FAILED_INSTALL).
PROBLEM_NO_DRIVER = 28


class DriverError(RuntimeError):
    """Установка не удалась — с объяснением для человека."""


# --------------------------------------------------------------- пакет драйвера


def is_package(folder: Path) -> bool:
    return (folder / BUS_INF).is_file() and (folder / PORT_INF).is_file()


def find_package(bases: Iterable[Path | None]) -> Path | None:
    """Папка с ftdibus.inf и ftdiport.inf.

    В каждой базе смотрим саму базу, driver/ и driver/<любая папка>:
    в релизе пакет лежит как driver/CDM_2.12.36.20. Из нескольких версий
    берём самую новую по имени.
    """
    for base in bases:
        if base is None:
            continue
        for candidate in (base, base / DRIVER_DIR):
            if is_package(candidate):
                return candidate.resolve()
        holder = base / DRIVER_DIR
        if holder.is_dir():
            found = sorted((d for d in holder.iterdir() if d.is_dir() and is_package(d)),
                           key=lambda d: d.name, reverse=True)
            if found:
                return found[0].resolve()
    return None


def default_bases(home: Path | None = None) -> list[Path | None]:
    """Где искать пакет: у exe (флешка), в рабочей папке, в текущей."""
    bases: list[Path | None] = []
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    bases += [home, Path.cwd()]
    return bases


# ------------------------------------------------------------ узлы и решения


@dataclass(frozen=True, slots=True)
class DeviceNode:
    instance_id: str
    hardware_ids: tuple[str, ...]
    has_driver: bool
    problem: int = 0

    def matches(self, prefix: str) -> bool:
        return any(hwid.upper().startswith(prefix) for hwid in self.hardware_ids)

    @property
    def looks_ironlogic(self) -> bool:
        """Серийник IL… в instance id: USB\\VID…\\IL040PWZ, FTDIBUS\\VID…+IL040PWZA\\0000."""
        return any(part.upper().startswith(SERIAL_PREFIX)
                   for part in re.split(r"[\\+]", self.instance_id))

    @property
    def stage(self) -> int | None:
        """1 — сам USB-адаптер, 2 — порт, который порождает ftdibus. None — не наш.

        PID 6001 носит любой FTDI-переходник — наш только с серийником IL…,
        как и при поиске порта (ports.PortInfo.rank).
        """
        for pid in KNOWN_PIDS:
            if pid != PID_IRONLOGIC and not self.looks_ironlogic:
                continue
            ids = f"VID_{VID:04X}&PID_{pid:04X}"
            if self.matches(f"FTDIBUS\\COMPORT&{ids}"):
                return 2
            # MI_xx — интерфейсы многоканальных чипов, у считывателя их нет.
            if any(h.upper() == f"USB\\{ids}" or h.upper().startswith(f"USB\\{ids}&REV_")
                   for h in self.hardware_ids):
                return 1
        return None

    @property
    def needs_driver(self) -> bool:
        return not self.has_driver or self.problem == PROBLEM_NO_DRIVER


@dataclass(frozen=True, slots=True)
class Step:
    node: DeviceNode
    inf: str
    model: str


def plan(nodes: Iterable[DeviceNode], stage: int) -> list[Step]:
    """Что ставить на этом этапе: только нашим узлам и только без драйвера.

    Узел с работающим драйвером не трогаем — даже если там стоит не та
    модель (на машине разработчика этап 1 сделан как «FT4222 Interface A»,
    и всё работает).
    """
    inf, model = (BUS_INF, BUS_MODEL) if stage == 1 else (PORT_INF, PORT_MODEL)
    return [Step(n, inf, model) for n in nodes if n.stage == stage and n.needs_driver]


def describe(nodes: Iterable[DeviceNode]) -> list[str]:
    lines = []
    for node in nodes:
        if node.stage is None:
            continue
        what = "адаптер (этап 1)" if node.stage == 1 else "порт (этап 2)"
        state = "нет драйвера" if node.needs_driver else "драйвер стоит"
        lines.append(f"{what}: {state}   {node.instance_id}")
    return lines


# ------------------------------------------------------------------ установка


Log = Callable[[str], None]


def install(
    package: Path,
    *,
    backend: Backend | None = None,
    log: Log = print,
    wait_child: float = 20.0,
    poll: float = 0.5,
) -> bool:
    """Поставить драйвер считывателю в два этапа. True — что-то поставлено.

    После этапа 1 узел порта появляется не сразу: ждём его до wait_child
    секунд. Узел, у которого драйвер уже есть, пропускаем.
    """
    api = backend or WindowsBackend()
    if not api.is_admin():
        raise DriverError("Для установки драйвера нужны права администратора.")
    if not is_package(package):
        raise DriverError(f"В папке {package} нет {BUS_INF} и {PORT_INF}.")

    nodes = api.reader_nodes()
    if not any(n.stage for n in nodes):
        raise DriverError(
            "Считыватель не найден среди устройств. Воткните его в USB и повторите."
        )

    done = False
    for step in plan(nodes, 1):
        log(f"Этап 1: {step.model} -> {step.node.instance_id}")
        api.force_install(step.node, package / step.inf, step.model)
        done = True

    # Узел порта порождает драйвер шины — после этапа 1 он появляется не сразу.
    deadline = time.monotonic() + wait_child
    nodes = api.reader_nodes()
    while done and not any(n.stage == 2 for n in nodes) and time.monotonic() < deadline:
        time.sleep(poll)
        nodes = api.reader_nodes()

    if not any(n.stage == 2 for n in nodes):
        first = ("Этап 1 прошёл" if done else
                 "Драйвер адаптера уже стоит (этап 1 не нужен)")
        raise DriverError(
            f"{first}, но узел порта (FTDIBUS\\COMPORT…) не появился.\n"
            "Выньте считыватель, воткните снова и повторите установку."
        )
    for step in plan(nodes, 2):
        log(f"Этап 2: {step.model} -> {step.node.instance_id}")
        api.force_install(step.node, package / step.inf, step.model)
        done = True

    if not done:
        log("Драйвер уже установлен, ставить нечего.")
    return done


# ------------------------------------------------------------ работа с Windows


class Backend:
    """Что установке нужно от системы. В тестах подменяется."""

    def is_admin(self) -> bool:
        raise NotImplementedError

    def reader_nodes(self) -> list[DeviceNode]:
        raise NotImplementedError

    def force_install(self, node: DeviceNode, inf: Path, model: str) -> None:
        raise NotImplementedError

    def models_in(self, inf: Path) -> list[str]:
        raise NotImplementedError


class WindowsBackend(Backend):
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DriverError("Установка драйвера возможна только в Windows.")
        from . import _setupapi

        self._api = _setupapi

    def is_admin(self) -> bool:
        return self._api.is_admin()

    def reader_nodes(self) -> list[DeviceNode]:
        found = []
        for instance_id, hwids, has_driver, problem in self._api.present_devices():
            node = DeviceNode(instance_id, tuple(hwids), has_driver, problem)
            if node.stage is not None:
                found.append(node)
        return found

    def force_install(self, node: DeviceNode, inf: Path, model: str) -> None:
        try:
            self._api.force_install(node.instance_id, str(inf), model)
        except OSError as exc:
            raise DriverError(
                f"Windows отказала в установке {inf.name} ({model}) на "
                f"{node.instance_id}: {exc}"
            ) from exc

    def models_in(self, inf: Path) -> list[str]:
        return self._api.models_in(str(inf))


def check_package(package: Path, backend: Backend | None = None) -> list[str]:
    """Проблемы пакета: нет файлов или Windows не видит в нём нужных моделей."""
    if not is_package(package):
        return [f"в {package} нет {BUS_INF} и {PORT_INF}"]
    api = backend or WindowsBackend()
    problems = []
    for inf, model in ((BUS_INF, BUS_MODEL), (PORT_INF, PORT_MODEL)):
        if model not in api.models_in(package / inf):
            problems.append(f"в {inf} нет модели «{model}»")
    return problems


# ------------------------------------------- запуск с правами: итог для окна


def wait_for_port(seconds: float = 15.0) -> str | None:
    from .ports import find_reader_port

    deadline = time.monotonic() + seconds
    while True:
        port = find_reader_port()
        if port or time.monotonic() >= deadline:
            return port
        time.sleep(0.5)


def install_and_report(package: Path, *, backend: Backend | None = None) -> tuple[bool, str]:
    """Установить и вернуть (успех, текст для окна). Зовётся уже с правами."""
    lines: list[str] = []
    try:
        install(package, backend=backend, log=lines.append)
    except DriverError as exc:
        return False, f"Драйвер не установлен.\n\n{exc}"
    port = wait_for_port()
    if port:
        return True, f"Драйвер установлен, считыватель работает на {port}."
    return False, ("Драйвер установлен, но COM-порт пока не появился.\n"
                   "Выньте считыватель, воткните снова и нажмите «Начать сканирование».")


# ---------------------------------------------------- повышение прав (UAC)


def run_elevated(executable: str, args: list[str], *, wait: bool = True) -> int | None:
    """Запустить с правами администратора и дождаться. None — человек отказался."""
    from . import _setupapi

    return _setupapi.run_elevated(executable, args, wait=wait)


def elevated_command(package: Path) -> tuple[str, list[str]]:
    """Чем перезапустить себя с правами для установки из package."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--install-driver", "--from", str(package)]
    return sys.executable, ["-m", "rfid.scanner.driver", "--from", str(package)]


def install_from_argv(argv: list[str]) -> tuple[bool, str]:
    """Установка с правами по аргументам «--from <папка>». Показ — дело вызывающего:
    exe показывает итог окном (packaging/rfid_gui.py), python -m — в консоли.
    """
    try:
        package = Path(argv[argv.index("--from") + 1])
    except (ValueError, IndexError):
        return False, "Не указана папка драйвера (--from <папка>)."
    return install_and_report(package)


if __name__ == "__main__":  # окно, запущенное без exe: rfid-gui.cmd
    ok, text = install_from_argv(sys.argv[1:])
    print(text)
    try:
        input("\nНажмите Enter, чтобы закрыть окно…")
    except (EOFError, KeyboardInterrupt):
        pass
    sys.exit(0 if ok else 1)
