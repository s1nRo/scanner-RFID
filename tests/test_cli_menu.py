"""Тесты меню: цикл и выходы.

Раньше программа завершалась после каждого действия — ради второго
приходилось запускать её заново.
"""

import pytest

from rfid import cli
from rfid.cli import common, subjects
from tests.helpers import make_roster_file


class Args:
    def __init__(self, tmp_path):
        self.db = tmp_path / "att.db"
        self.tables = tmp_path / "tables"
        self.home = None
        self.subject = None


@pytest.fixture
def answers(monkeypatch):
    queue: list[str] = []
    monkeypatch.setattr(common, "ask", lambda _p: queue.pop(0) if queue else "0")
    return queue


@pytest.fixture
def ran(monkeypatch):
    """Запоминает, какие подкоманды запускало меню, ничего не выполняя."""
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(argv) or 0)
    return calls


class TestExiting:
    @pytest.mark.parametrize("word", ["0", "q", "выход", "exit", ""])
    def test_exit_words(self, tmp_path, answers, ran, word):
        answers += [word]
        assert cli.cmd_menu(Args(tmp_path)) == 0
        assert ran == []

    def test_ctrl_c_at_the_menu(self, tmp_path, monkeypatch, ran):
        def boom(_p):
            raise KeyboardInterrupt

        monkeypatch.setattr(common, "ask", boom)
        assert cli.cmd_menu(Args(tmp_path)) == 0

    def test_zero_at_the_pause_exits(self, tmp_path, answers, ran):
        answers += ["5", "0"]
        assert cli.cmd_menu(Args(tmp_path)) == 0
        assert len(ran) == 1


class TestLooping:
    def test_returns_to_menu_after_action(self, tmp_path, answers, ran):
        answers += ["5", "", "5", "", "0"]
        cli.cmd_menu(Args(tmp_path))
        assert len(ran) == 2, "меню должно пережить несколько действий подряд"

    def test_unknown_item_does_not_exit(self, tmp_path, answers, ran):
        answers += ["99", "5", "", "0"]
        cli.cmd_menu(Args(tmp_path))
        assert len(ran) == 1

    def test_common_flags_are_passed_on(self, tmp_path, answers, ran):
        answers += ["5", "", "0"]
        args = Args(tmp_path)
        cli.cmd_menu(args)
        assert "--db" in ran[0] and str(args.db) in ran[0]
        assert "--tables" in ran[0] and str(args.tables) in ran[0]

    def test_home_is_passed_on(self, tmp_path, answers, ran):
        answers += ["5", "", "0"]
        args = Args(tmp_path)
        args.home = tmp_path
        cli.cmd_menu(args)
        assert "--home" in ran[0] and str(tmp_path) in ran[0]


class TestBackFromPicker:
    def test_zero_cancels_without_an_error_message(self, tmp_path, answers, capsys):
        folder = tmp_path / "Физика"
        folder.mkdir()
        make_roster_file(folder / "г.xlsx")
        answers += ["0"]

        with pytest.raises(cli.Cancelled):
            subjects.choose_subject(Args(tmp_path), tmp_path)

        assert "Нет такого пункта" not in capsys.readouterr().out

    def test_cancelled_is_silent(self):
        assert str(cli.Cancelled()) == ""
        assert isinstance(cli.Cancelled(), cli.Interrupted)
