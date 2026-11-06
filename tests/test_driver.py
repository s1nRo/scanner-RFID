"""Установка драйвера с флешки: что ставить, куда и в каком порядке.

Сама система (SetupAPI) здесь подменена: проверяются решения, а не Windows.
Вызовы Windows проверяет CI на windows-latest (шаг «Check driver package»).
"""

from pathlib import Path

import pytest

from rfid.scanner import driver as D

ADAPTER = D.DeviceNode("USB\\VID_0403&PID_1234\\IL040PWZ",
                       ("USB\\VID_0403&PID_1234&REV_0600", "USB\\VID_0403&PID_1234"), False, 28)
PORT = D.DeviceNode("FTDIBUS\\COMPORT&VID_0403&PID_1234\\7&1",
                    ("FTDIBUS\\COMPORT&VID_0403&PID_1234",), False, 28)


def with_driver(node: D.DeviceNode) -> D.DeviceNode:
    return D.DeviceNode(node.instance_id, node.hardware_ids, True, 0)


def make_package(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / D.BUS_INF).write_text("bus")
    (folder / D.PORT_INF).write_text("port")
    return folder


class FakeWindows(D.Backend):
    """Узлы меняются так, как в жизни: после этапа 1 появляется порт."""

    def __init__(self, nodes, *, admin=True, port_appears=True):
        self.nodes = list(nodes)
        self.admin = admin
        self.port_appears = port_appears
        self.installed: list[tuple[str, str, str]] = []

    def is_admin(self):
        return self.admin

    def reader_nodes(self):
        return list(self.nodes)

    def force_install(self, node, inf, model):
        self.installed.append((node.instance_id, inf.name, model))
        self.nodes = [with_driver(n) if n == node else n for n in self.nodes]
        if node.stage == 1 and self.port_appears and not any(n.stage == 2 for n in self.nodes):
            self.nodes.append(PORT)


class TestStage:
    def test_adapter_and_port(self):
        assert ADAPTER.stage == 1
        assert PORT.stage == 2

    def test_standard_ftdi_pid(self):
        node = D.DeviceNode("USB\\VID_0403&PID_6001\\ILX", ("USB\\VID_0403&PID_6001",), False)
        assert node.stage == 1

    def test_foreign_devices_are_ignored(self):
        other = D.DeviceNode("USB\\VID_0403&PID_6010&MI_00\\x",
                             ("USB\\VID_0403&PID_6010&MI_00",), False)
        mouse = D.DeviceNode("HID\\VID_046D", ("HID\\VID_046D&PID_C077",), True)
        assert other.stage is None
        assert mouse.stage is None


class TestPlan:
    def test_only_nodes_without_driver(self):
        assert [s.node for s in D.plan([ADAPTER, PORT], 1)] == [ADAPTER]
        assert D.plan([with_driver(ADAPTER)], 1) == []

    def test_working_driver_is_left_alone(self):
        # У разработчика этап 1 сделан «не той» моделью, и всё работает — не трогать.
        assert D.plan([with_driver(ADAPTER), with_driver(PORT)], 2) == []

    def test_models(self):
        (step,) = D.plan([PORT], 2)
        assert (step.inf, step.model) == (D.PORT_INF, D.PORT_MODEL)
        (step,) = D.plan([ADAPTER], 1)
        assert (step.inf, step.model) == (D.BUS_INF, D.BUS_MODEL)


class TestInstall:
    def test_two_stages_in_order(self, tmp_path):
        api = FakeWindows([ADAPTER])
        assert D.install(make_package(tmp_path / "drv"), backend=api, log=lambda _: None,
                         poll=0)
        assert api.installed == [
            (ADAPTER.instance_id, D.BUS_INF, D.BUS_MODEL),
            (PORT.instance_id, D.PORT_INF, D.PORT_MODEL),
        ]

    def test_only_second_stage_left(self, tmp_path):
        # Человек сделал руками этап 1 и бросил — программа доделывает этап 2.
        api = FakeWindows([with_driver(ADAPTER), PORT])
        D.install(make_package(tmp_path / "drv"), backend=api, log=lambda _: None)
        assert api.installed == [(PORT.instance_id, D.PORT_INF, D.PORT_MODEL)]

    def test_nothing_to_do(self, tmp_path):
        api = FakeWindows([with_driver(ADAPTER), with_driver(PORT)])
        messages: list[str] = []
        assert not D.install(make_package(tmp_path / "drv"), backend=api, log=messages.append)
        assert api.installed == []
        assert "уже установлен" in messages[-1]

    def test_needs_admin(self, tmp_path):
        with pytest.raises(D.DriverError, match="администратора"):
            D.install(make_package(tmp_path / "drv"), backend=FakeWindows([ADAPTER], admin=False))

    def test_reader_not_plugged(self, tmp_path):
        with pytest.raises(D.DriverError, match="не найден"):
            D.install(make_package(tmp_path / "drv"), backend=FakeWindows([]))

    def test_port_node_never_appears(self, tmp_path):
        api = FakeWindows([ADAPTER], port_appears=False)
        with pytest.raises(D.DriverError, match="не появился"):
            D.install(make_package(tmp_path / "drv"), backend=api, log=lambda _: None,
                      wait_child=0.05, poll=0.01)

    def test_broken_package(self, tmp_path):
        (tmp_path / "drv").mkdir()
        with pytest.raises(D.DriverError, match="ftdibus.inf"):
            D.install(tmp_path / "drv", backend=FakeWindows([ADAPTER]))


class TestFindPackage:
    def test_release_layout(self, tmp_path):
        # Так лежит на флешке: RFID\driver\CDM_2.12.36.20\ftdibus.inf
        package = make_package(tmp_path / "driver" / "CDM_2.12.36.20")
        assert D.find_package([tmp_path]) == package.resolve()

    def test_newest_version_wins(self, tmp_path):
        make_package(tmp_path / "driver" / "CDM_2.12.28")
        newest = make_package(tmp_path / "driver" / "CDM_2.12.36.20")
        assert D.find_package([tmp_path]) == newest.resolve()

    def test_flat_driver_folder(self, tmp_path):
        package = make_package(tmp_path / "driver")
        assert D.find_package([tmp_path]) == package.resolve()

    def test_order_of_bases(self, tmp_path):
        first = make_package(tmp_path / "a" / "driver" / "X")
        make_package(tmp_path / "b" / "driver" / "Y")
        assert D.find_package([None, tmp_path / "a", tmp_path / "b"]) == first.resolve()

    def test_nothing(self, tmp_path):
        assert D.find_package([tmp_path, None]) is None

    def test_exe_folder_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(D.sys, "frozen", True, raising=False)
        monkeypatch.setattr(D.sys, "executable", str(tmp_path / "RFID-Scanner.exe"))
        assert D.default_bases(None)[0] == tmp_path.resolve()


def test_elevated_command_of_the_exe(tmp_path, monkeypatch):
    # Окно перезапускает exe с правами — тем же флагом, что понимает packaging/rfid_gui.py.
    monkeypatch.setattr(D.sys, "frozen", True, raising=False)
    monkeypatch.setattr(D.sys, "executable", "F:\\RFID\\RFID-Scanner.exe")
    exe, args = D.elevated_command(tmp_path)
    assert exe == "F:\\RFID\\RFID-Scanner.exe"
    assert args == ["--install-driver", "--from", str(tmp_path)]



def test_report_after_install(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "wait_for_port", lambda seconds=15.0: "COM3")
    ok, text = D.install_and_report(make_package(tmp_path / "drv"),
                                    backend=FakeWindows([ADAPTER]))
    assert ok and "COM3" in text


def test_report_on_failure(tmp_path):
    ok, text = D.install_and_report(make_package(tmp_path / "drv"),
                                    backend=FakeWindows([ADAPTER], admin=False))
    assert not ok and "администратора" in text


def test_standard_pid_without_il_serial_is_not_ours():
    # 6001 носит любой FTDI-переходник — ставить на него драйвер считывателя нельзя.
    node = D.DeviceNode("USB" + "\\" + "VID_0403&PID_6001" + "\\" + "A50285BI",
                        ("USB" + "\\" + "VID_0403&PID_6001",), False)
    assert node.stage is None


def test_standard_pid_port_node_of_the_reader():
    node = D.DeviceNode("FTDIBUS" + "\\" + "VID_0403+PID_6001+IL040PWZA" + "\\" + "0000",
                        ("FTDIBUS" + "\\" + "COMPORT&VID_0403&PID_6001",), False)
    assert node.stage == 2


def test_install_flag_without_folder():
    ok, text = D.install_from_argv(["--from"])
    assert not ok and "--from" in text


def test_exe_install_flag_shows_result_window():
    """--install-driver без папки — отказ окном, а не IndexError в exe без консоли."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "packaging" / "rfid_gui.py"
    spec = importlib.util.spec_from_file_location("rfid_gui_entry", path)
    assert spec and spec.loader
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    shown: list[tuple[str, int]] = []
    entry.__dict__["_show_error"] = lambda text, icon=0: shown.append((text, icon))
    assert entry.run(["--install-driver"]) == 1
    assert shown and shown[0][1] == entry.MB_ICONERROR
