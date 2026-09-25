"""Рабочая папка: пути не зависят от того, откуда запущена программа.

Главная опасность, от которой это защищает: запуск «не из той папки»
тихо заводит новую пустую базу в чужом месте, и отметки уходят мимо
журнала. Лучше отказ с объяснением.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from rfid import cli
from rfid.cli import home as H


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    """Переменная с машины разработчика не должна влиять на тесты."""
    monkeypatch.delenv(H.HOME_ENV, raising=False)


@pytest.fixture
def stranger(tmp_path, monkeypatch):
    """Чужая текущая папка: ни data/, ни tables/."""
    folder = tmp_path / "где-то"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


@pytest.fixture
def workdir(tmp_path):
    folder = tmp_path / "rfid"
    (folder / "tables").mkdir(parents=True)
    return folder


def args(**kw):
    base = dict(home=None, db=None, tables=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestFindHome:
    def test_explicit_flag_wins(self, workdir, monkeypatch, tmp_path):
        monkeypatch.setenv(H.HOME_ENV, str(tmp_path))
        assert H.find_home(workdir) == workdir.resolve()

    def test_env_variable(self, workdir, stranger, monkeypatch):
        monkeypatch.setenv(H.HOME_ENV, str(workdir))
        assert H.find_home() == workdir.resolve()

    def test_current_folder_if_it_looks_like_home(self, workdir, monkeypatch):
        monkeypatch.chdir(workdir)
        assert H.find_home() == workdir.resolve()

    def test_stranger_folder_is_not_home(self, stranger):
        assert H.find_home() is None

    def test_missing_folder_is_refused(self, tmp_path):
        with pytest.raises(cli.Interrupted, match="нет"):
            H.find_home(tmp_path / "нет-такой")


class TestResolvePaths:
    def test_defaults_are_inside_home(self, workdir, stranger, monkeypatch):
        monkeypatch.setenv(H.HOME_ENV, str(workdir))
        a = args()
        H.resolve_paths(a)
        assert a.db == workdir.resolve() / "data" / "attendance.db"
        assert a.tables == workdir.resolve() / "tables"

    def test_relative_paths_count_from_home_not_cwd(self, workdir, stranger, monkeypatch):
        monkeypatch.setenv(H.HOME_ENV, str(workdir))
        a = args(db=Path("data/demo.db"))
        H.resolve_paths(a)
        assert a.db == workdir.resolve() / "data" / "demo.db"

    def test_absolute_paths_need_no_home(self, stranger, tmp_path):
        a = args(db=tmp_path / "x.db", tables=tmp_path / "t")
        H.resolve_paths(a)
        assert (a.db, a.tables) == (tmp_path / "x.db", tmp_path / "t")

    def test_no_home_is_refused_with_explanation(self, stranger):
        with pytest.raises(cli.Interrupted, match="rfid.cmd"):
            H.resolve_paths(args())


class TestMainFromStrangerFolder:
    def test_refuses_and_creates_nothing(self, stranger, capsys):
        """Главный случай: ни базы, ни папок в чужом месте появиться не должно."""
        assert cli.main(["doctor"]) == 1
        assert "рабочая папка" in capsys.readouterr().out
        assert list(stranger.iterdir()) == []

    def test_ports_works_anywhere(self, stranger, monkeypatch):
        """Список портов не про данные — рабочая папка ему не нужна."""
        monkeypatch.setattr(cli.admin, "available_ports", lambda: [])
        assert cli.main(["ports"]) == 1  # портов нет, но и отказа по папке нет

    def test_works_through_env(self, workdir, stranger, monkeypatch, capsys):
        monkeypatch.setenv(H.HOME_ENV, str(workdir))
        cli.main(["subjects"])
        assert "нет ни одной папки предмета" in capsys.readouterr().out
