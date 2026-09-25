"""Список опоздавших — тех, кто показывает конспект."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from rfid import cli, lessons
from rfid.cli import common
from rfid.db import Storage
from tests.helpers import card, make_roster_file

CARDS = [card(f"Em-Marine[A100] 007,{n}") for n in range(40, 45)]
GROUP = "1000000/10001"
NAMES = ["Первый Пришедший", "Вовремя Вера", "Опоздавший Олег", "Поздний Павел"]


def at(hour, minute):
    return datetime(2026, 9, 20, hour, minute)


class TestLateArrivals:
    def test_counted_from_lesson_start(self):
        late = lessons.late_arrivals({"a": at(9, 0), "b": at(9, 9), "c": at(9, 10), "d": at(9, 40)})
        assert [(who, start) for who, _, start in late] == [("c", at(9, 0)), ("d", at(9, 0))]

    def test_same_rule_as_yellow_fill(self):
        """Список обязан совпадать с подсветкой в таблице."""
        arrivals = {"a": at(9, 0), "b": at(9, 12), "c": at(11, 0), "d": at(11, 30)}
        starts = lessons.session_starts(arrivals.values())
        expected = {who for who, t in arrivals.items() if lessons.is_late(t, starts)}
        assert {who for who, _, _ in lessons.late_arrivals(arrivals)} == expected

    def test_each_lesson_has_its_own_start(self):
        late = lessons.late_arrivals({"a": at(9, 0), "b": at(11, 0), "c": at(11, 15)})
        assert [(who, start) for who, _, start in late] == [("c", at(11, 0))]

    def test_sorted_by_arrival(self):
        late = lessons.late_arrivals({"x": at(9, 50), "y": at(9, 0), "z": at(9, 20)})
        assert [who for who, _, _ in late] == ["z", "x"]

    def test_nobody(self):
        assert lessons.late_arrivals({}) == []


@pytest.fixture
def setup(tmp_path):
    tables = tmp_path / "tables"
    folder = tables / "Бургеростроение"
    folder.mkdir(parents=True)
    make_roster_file(folder / "10001.xlsx", names=NAMES, group=GROUP)
    db = tmp_path / "late.db"
    with Storage(db) as s:
        subject = s.add_subject("Бургеростроение")
        for code, name in zip(CARDS, NAMES, strict=False):
            s.add_student(code, name, GROUP)
        times = [at(9, 0), at(9, 5), at(9, 25), at(10, 2)]
        for code, t in zip(CARDS, times, strict=False):
            s.mark(code, subject, at=t)
    return SimpleNamespace(db=db, tables=tables, subject="Бургеростроение", date=None)


class TestCommand:
    def test_lists_only_late(self, setup, capsys):
        setup.date = "20.09.2026"
        assert cli.cmd_late(setup) == 0
        out = capsys.readouterr().out
        assert "Опоздавший Олег" in out and "+25 мин" in out
        assert "Поздний Павел" in out and "+62 мин" in out
        assert "Вовремя Вера" not in out and "Первый Пришедший" not in out
        assert "Всего: 2" in out

    def test_enter_picks_the_latest_day(self, setup, monkeypatch, capsys):
        monkeypatch.setattr(common, "ask", lambda _p: "")
        cli.cmd_late(setup)
        assert "20.09.2026" in capsys.readouterr().out

    def test_day_without_marks(self, setup, capsys):
        setup.date = "21.09.2026"
        cli.cmd_late(setup)
        assert "никто не отмечен" in capsys.readouterr().out

    def test_subject_without_marks(self, setup, tmp_path, capsys):
        (setup.tables / "Физика").mkdir()
        make_roster_file(setup.tables / "Физика" / "г.xlsx")
        setup.subject = "Физика"
        assert cli.cmd_late(setup) == 0
        assert "отметок ещё не было" in capsys.readouterr().out

    def test_back_from_day_picker(self, setup, monkeypatch):
        monkeypatch.setattr(common, "ask", lambda _p: "0")
        with pytest.raises(cli.Cancelled):
            cli.cmd_late(setup)

    def test_in_menu(self):
        assert any(argv == ["late"] for _, _, argv in cli.MENU)
