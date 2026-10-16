"""Память о выбранной рабочей папке — в реестре, там же, где у QSettings."""

import sys

import pytest

from rfid import workdir

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="реестр есть только в Windows")


@pytest.fixture
def registry(monkeypatch):
    """Свой ключ вместо настоящего, с уборкой после теста."""
    import winreg

    # conftest глушит память для всех тестов; здесь нужна настоящая.
    monkeypatch.undo()
    key = r"Software\RFID Attendance Tests\RFID Journal\workspace"
    monkeypatch.setattr(workdir, "_REGISTRY_KEY", key)
    yield key
    for path in (key, key.rsplit("\\", 1)[0], key.rsplit("\\", 2)[0]):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


def test_remembered_folder_comes_back(registry, tmp_path):
    (tmp_path / "tables").mkdir()
    workdir.remember(tmp_path)
    assert workdir.remembered() == tmp_path.resolve()


def test_nothing_remembered(registry):
    assert workdir.remembered() is None


def test_folder_without_data_is_not_trusted(registry, tmp_path):
    # Папку потом вычистили — пустую базу там заводить нельзя.
    workdir.remember(tmp_path)
    assert workdir.remembered() is None


def test_qsettings_layout(registry, tmp_path):
    # Так значение записывал QSettings первой оконной сборки.
    import winreg

    (tmp_path / "data").mkdir()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry) as key:
        winreg.SetValueEx(key, "home", 0, winreg.REG_SZ, str(tmp_path))
    assert workdir.remembered() == tmp_path.resolve()
