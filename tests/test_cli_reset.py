"""Тесты очистки базы.

Команда удаляет данные, поэтому проверяется и что удаляет нужное,
и что НЕ трогает лишнего — прежде всего файлы групп.
"""

from datetime import datetime

import pytest

from rfid import cli
from rfid.codes import parse_line
from rfid.storage import Storage
from test_roster import make_roster_file

CARD_A = parse_line("Em-Marine[A100] 007,42")
MORNING = datetime(2026, 9, 20, 9, 2)


class ResetArgs:
    def __init__(self, db, what, yes=True):
        self.db = db
        self.reset_what = what
        self.yes = yes


@pytest.fixture
def filled(tmp_path):
    path = tmp_path / "att.db"
    with Storage(path) as s:
        subject = s.add_subject("АСУ")
        s.add_student(CARD_A, "Иванов Иван", "ИС-21")
        s.mark(CARD_A, subject, at=MORNING)
    return path


def counts(path):
    with Storage(path) as s:
        return (
            s.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0],
            len(s.list_subjects()),
            s.count_students(),
        )


class TestReset:
    def test_marks_clears_marks_and_subjects(self, filled):
        cli.cmd_reset(ResetArgs(filled, "marks"))
        assert counts(filled) == (0, 0, 1)  # привязка карты осталась

    def test_cards_clears_only_bindings(self, filled):
        cli.cmd_reset(ResetArgs(filled, "cards"))
        marks, subjects, cards = counts(filled)
        assert (subjects, cards) == (1, 0)
        assert marks == 1  # сама отметка не удалена, стала безымянной

    def test_all_clears_everything(self, filled):
        cli.cmd_reset(ResetArgs(filled, "all"))
        assert counts(filled) == (0, 0, 0)

    def test_declining_changes_nothing(self, filled, monkeypatch):
        monkeypatch.setattr(cli, "_ask", lambda _p: "нет")
        cli.cmd_reset(ResetArgs(filled, "all", yes=False))
        assert counts(filled) == (1, 1, 1)

    def test_confirming_proceeds(self, filled, monkeypatch):
        monkeypatch.setattr(cli, "_ask", lambda _p: "да")
        cli.cmd_reset(ResetArgs(filled, "all", yes=False))
        assert counts(filled) == (0, 0, 0)

    def test_group_files_are_untouched(self, filled, tmp_path):
        """Списки групп — не наши данные, стирать их команда не должна."""
        roster = make_roster_file(tmp_path / "группа.xlsx")
        before = roster.read_bytes()
        cli.cmd_reset(ResetArgs(filled, "all"))
        assert roster.read_bytes() == before

    def test_empty_database_survives(self, tmp_path):
        path = tmp_path / "пустая.db"
        with Storage(path):
            pass
        cli.cmd_reset(ResetArgs(path, "all"))
        assert counts(path) == (0, 0, 0)
