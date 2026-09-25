"""Совмещённый режим: пара идёт, а незнакомая карта сразу привязывается.

Заглушка считывателя проигрывает DEMO_LINES: карта A, карта B, снова A.
"""

from datetime import date

import pytest

from rfid import cli
from rfid.cli import common
from rfid.cli.marking import DEMO_LINES
from rfid.db import Storage
from tests.helpers import ROSTER_NAMES, card, make_roster_file, must

SUBJECT = "Бургеростроение"
GROUP = "1000000/10001"
CARD_A = card(DEMO_LINES[0])
CARD_B = card(DEMO_LINES[2])


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "tables" / SUBJECT
    folder.mkdir(parents=True)
    make_roster_file(folder / "10001.xlsx", group=GROUP)
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def answers(monkeypatch):
    """Ответы оператора по очереди; заодно считаем, сколько раз спросили."""
    queue: list[str] = []
    asked: list[str] = []

    def fake_ask(prompt):
        asked.append(prompt)
        return queue.pop(0) if queue else ""

    monkeypatch.setattr(common, "ask", fake_ask)
    return queue, asked


def run(home):
    return cli.main([
        "--home", str(home), "--db", "data/demo.db",
        "scan", "--enroll", "--mode", "mock", "--subject", SUBJECT,
        "--debounce", "0", "--no-sound", "--no-export",
    ])


def db(home):
    return Storage(home / "data" / "demo.db")


class TestBindingOnTheFly:
    def test_unknown_card_bound_and_marked(self, home, answers):
        queue, asked = answers
        queue += ["1", ""]  # A — первому по списку, B — пропустить
        assert run(home) == 0

        with db(home) as s:
            assert must(s.find_student(CARD_A)).full_name == ROSTER_NAMES[0]
            subject = must(s.find_subject(SUBJECT))
            rows = {r.student.full_name: r for r in s.day_rows(date.today(), subject)}
            assert rows[ROSTER_NAMES[0]].present
            # Спрашивают только про незнакомые: второе прикладывание A — уже его.
            assert len(asked) == 2

    def test_summary_counts_bound_card_as_marked(self, home, answers, capsys):
        queue, _ = answers
        queue += ["1", ""]
        run(home)
        out = capsys.readouterr().out
        assert "Отмечено: 1   повторов: 1   неизвестных карт: 1" in out
        # Только что сделанная отметка — не «прошлая».
        assert "Прошлых отметок" not in out

    def test_skipped_card_keeps_its_mark(self, home, answers):
        queue, _ = answers
        queue += ["1", ""]
        run(home)
        with db(home) as s:
            assert [u.card_code for u in s.unknown_cards()] == [CARD_B.canonical]

    def test_wrong_number_loses_nothing(self, home, answers):
        queue, _ = answers
        queue += ["99", "нет такого"]
        run(home)
        with db(home) as s:
            assert s.count_cards() == 0
            assert s.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 2
            assert len(s.unknown_cards()) == 2

    def test_known_card_is_not_asked_about(self, home, answers):
        _, asked = answers
        with db(home) as s:
            s.bind_card(CARD_A, ROSTER_NAMES[0], GROUP)
            s.bind_card(CARD_B, ROSTER_NAMES[1], GROUP)
        run(home)
        assert asked == []

    def test_person_with_a_card_is_refused(self, home, answers, capsys):
        queue, _ = answers
        with db(home) as s:
            s.bind_card(CARD_B, ROSTER_NAMES[0], GROUP)
        queue += ["1"]  # A — тому, у кого уже есть B
        run(home)
        assert "уже есть карта" in capsys.readouterr().out
        with db(home) as s:
            assert s.find_student(CARD_A) is None
            assert [u.card_code for u in s.unknown_cards()] == [CARD_A.canonical]

    def test_list_shows_only_students_without_cards(self, home, answers, capsys):
        with db(home) as s:
            s.bind_card(CARD_B, ROSTER_NAMES[1], GROUP)
        run(home)
        out = capsys.readouterr().out
        # Список — между подсказкой и ответом «оставить без привязки».
        listing = out.split("Кто это?")[1].split("Карта осталась")[0]
        assert ROSTER_NAMES[0] in listing and ROSTER_NAMES[1] not in listing

    def test_plain_scan_does_not_ask(self, home, answers):
        _, asked = answers
        cli.main([
            "--home", str(home), "--db", "data/demo.db",
            "scan", "--mode", "mock", "--subject", SUBJECT,
            "--debounce", "0", "--no-sound", "--no-export",
        ])
        assert asked == []

    def test_in_menu(self):
        assert any(argv == ["scan", "--enroll"] for _, _, argv in cli.MENU)
