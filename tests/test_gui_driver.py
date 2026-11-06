"""Окно: считыватель воткнут, драйвера нет — предложить поставить его с флешки.

Нужен PyQt5 (как и test_gui_binding.py). Windows и UAC подменены.
"""

import os

import pytest

pytest.importorskip("PyQt5")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rfid.gui import app as gui_app  # noqa: E402
from rfid.scanner import driver as D  # noqa: E402

NO_DRIVER = D.DeviceNode("USB\\VID_0403&PID_1234\\IL1", ("USB\\VID_0403&PID_1234",), False, 28)
WORKING = D.DeviceNode("USB\\VID_0403&PID_1234\\IL1", ("USB\\VID_0403&PID_1234",), True, 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeBackend:
    nodes: list = []

    def reader_nodes(self):
        return list(self.nodes)


@pytest.fixture
def window(tmp_path, qapp, monkeypatch):
    (tmp_path / "tables").mkdir()
    (tmp_path / "data").mkdir()
    package = tmp_path / "driver" / "CDM_2.12.36.20"
    package.mkdir(parents=True)
    (package / D.BUS_INF).write_text("bus")
    (package / D.PORT_INF).write_text("port")
    monkeypatch.setattr(gui_app.sys, "platform", "win32")
    monkeypatch.setattr(gui_app.driver, "WindowsBackend", FakeBackend)
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: asked.append("warning"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: asked.append("info"))
    win = gui_app.AttendanceWindow(tmp_path / "data" / "attendance.db", tmp_path / "tables",
                                   mode="mock")
    win.asked = asked  # type: ignore[attr-defined]
    win.package = package.resolve()  # type: ignore[attr-defined]
    yield win
    win.close()


def answer(monkeypatch, button):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: button)


def test_installs_from_the_folder_beside_and_restarts(window, monkeypatch):
    FakeBackend.nodes = [NO_DRIVER]
    answer(monkeypatch, QMessageBox.Yes)
    calls = []
    monkeypatch.setattr(gui_app.driver, "run_elevated",
                        lambda exe, args: calls.append(args) or 0)
    monkeypatch.setattr(gui_app, "find_reader_port", lambda: "COM3")
    assert window._offer_driver()
    assert calls and str(window.package) in calls[0]
    assert window._restart_scanning
    assert window.asked == []


def test_refused_uac_does_not_restart(window, monkeypatch):
    FakeBackend.nodes = [NO_DRIVER]
    answer(monkeypatch, QMessageBox.Yes)
    monkeypatch.setattr(gui_app.driver, "run_elevated", lambda exe, args: None)
    assert window._offer_driver()
    assert not window._restart_scanning


def test_driver_present_means_usual_message(window, monkeypatch):
    # Драйвер стоит — дело не в нём, показываем обычное «порт не найден».
    FakeBackend.nodes = [WORKING]
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("про драйвер спрашивать нельзя"))
    assert not window._offer_driver()


def test_no_package_explains_where_to_put_it(window, monkeypatch, tmp_path):
    FakeBackend.nodes = [NO_DRIVER]
    monkeypatch.setattr(gui_app.driver, "find_package", lambda bases: None)
    assert window._offer_driver()
    assert window.asked == ["warning"]
