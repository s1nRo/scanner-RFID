"""Окно берёт ту же базу, что и консоль, а без рабочей папки спрашивает её.

Окно само не открывается: run_gui и ask_home подменены, проверяется только,
какие пути им достаются. PyQt5 для этого не нужен.
"""

import pytest

from rfid import cli, gui


@pytest.fixture
def opened(monkeypatch):
    """Что получил run_gui: (база, папка списков)."""
    calls: list[tuple] = []
    monkeypatch.setattr(gui, "run_gui",
                        lambda db, tables, **kw: calls.append((db, tables)) or 0)
    return calls


@pytest.fixture
def stranger(tmp_path, monkeypatch):
    folder = tmp_path / "Загрузки"
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.delenv(cli.HOME_ENV, raising=False)
    return folder


def _never_ask():
    raise AssertionError("папка найдена — спрашивать нельзя")


def test_found_home_is_used_without_asking(tmp_path, monkeypatch, opened):
    (tmp_path / "tables").mkdir()
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
    monkeypatch.setattr(gui, "ask_home", _never_ask)
    assert cli.main(["gui"]) == 0
    assert opened == [(tmp_path.resolve() / "data" / "attendance.db",
                       tmp_path.resolve() / "tables")]


def test_no_home_asks_and_uses_the_choice(tmp_path, stranger, monkeypatch, opened):
    chosen = tmp_path / "rfid"
    (chosen / "data").mkdir(parents=True)
    monkeypatch.setattr(gui, "ask_home", lambda: chosen)
    assert cli.main(["gui"]) == 0
    assert opened == [(chosen.resolve() / "data" / "attendance.db",
                       chosen.resolve() / "tables")]


def test_refused_choice_opens_nothing_and_creates_nothing(stranger, monkeypatch, opened):
    monkeypatch.setattr(gui, "ask_home", lambda: None)
    assert cli.main(["gui"]) == 1
    assert opened == []
    assert list(stranger.iterdir()) == []


def test_explicit_home_is_not_asked(tmp_path, stranger, monkeypatch, opened):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(gui, "ask_home", _never_ask)
    assert cli.main(["--home", str(tmp_path), "gui"]) == 0
    assert opened[0][0] == tmp_path.resolve() / "data" / "attendance.db"
