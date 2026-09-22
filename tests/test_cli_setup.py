"""Тесты выбора предмета.

tables/ — корень, в котором лежат папки предметов. Сама она предметом
не является. Раскладку ведёт пользователь: программа папок не создаёт
и файлов не переносит.
"""

import pytest

from rfid import cli
from rfid import roster as R
from test_roster import make_roster_file


@pytest.fixture
def answers(monkeypatch):
    """Подменяет ввод заранее заготовленными ответами."""
    queue: list[str] = []

    def fake_ask(_prompt: str) -> str:
        if not queue:
            raise cli.Interrupted("ответы кончились")
        return queue.pop(0)

    monkeypatch.setattr(cli, "_ask", fake_ask)
    return queue


class Args:
    subject = None


def with_subject(value):
    args = Args()
    args.subject = value
    return args


def make_subject(root, name, groups=("г.xlsx",)):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for file_name in groups:
        make_roster_file(folder / file_name)
    return folder


class TestPicking:
    def test_subfolders_are_the_subjects(self, tmp_path, answers):
        make_subject(tmp_path, "Алгебра")
        make_subject(tmp_path, "Физика")
        answers += ["2"]
        assert cli.choose_subject(Args(), tmp_path).name == "Физика"

    def test_folders_without_lists_are_skipped(self, tmp_path, answers):
        (tmp_path / "Пустая").mkdir()
        make_subject(tmp_path, "Физика")
        answers += ["1"]
        assert cli.choose_subject(Args(), tmp_path).name == "Физика"

    def test_root_is_not_a_subject(self, tmp_path, answers):
        """tables — корень, а не предмет: файлы в ней не образуют предмета."""
        make_roster_file(tmp_path / "россыпью.xlsx")
        make_subject(tmp_path, "Физика")
        answers += ["1"]
        chosen = cli.choose_subject(Args(), tmp_path)
        assert chosen.name == "Физика"
        assert chosen.path != tmp_path

    def test_only_loose_files_means_no_subjects(self, tmp_path, answers):
        make_roster_file(tmp_path / "россыпью.xlsx")
        with pytest.raises(cli.Interrupted, match="нет ни одной папки предмета"):
            cli.choose_subject(Args(), tmp_path)

    def test_loose_files_are_mentioned_when_nothing_found(self, tmp_path, answers):
        make_roster_file(tmp_path / "забытый.xlsx")
        with pytest.raises(cli.Interrupted, match="забытый.xlsx"):
            cli.choose_subject(Args(), tmp_path)

    def test_bad_number_refuses(self, tmp_path, answers):
        make_subject(tmp_path, "Физика")
        answers += ["7"]
        with pytest.raises(cli.Interrupted, match="Нет такого пункта"):
            cli.choose_subject(Args(), tmp_path)

    def test_empty_tables_explains_what_to_do(self, tmp_path):
        with pytest.raises(cli.Interrupted, match="Создайте"):
            cli.choose_subject(Args(), tmp_path)

    def test_nothing_is_created_or_moved(self, tmp_path, answers):
        make_subject(tmp_path, "Физика")
        make_roster_file(tmp_path / "россыпью.xlsx")
        before = sorted(p.name for p in tmp_path.rglob("*"))
        answers += ["1"]
        cli.choose_subject(Args(), tmp_path)
        assert sorted(p.name for p in tmp_path.rglob("*")) == before


class TestSubjectFlag:
    def test_by_folder_name(self, tmp_path):
        make_subject(tmp_path, "Алгебра")
        assert cli.choose_subject(with_subject("алгебра"), tmp_path).name == "Алгебра"

    def test_unknown_lists_what_exists(self, tmp_path):
        make_subject(tmp_path, "Алгебра")
        with pytest.raises(cli.Interrupted, match="Алгебра"):
            cli.choose_subject(with_subject("Астрология"), tmp_path)


class TestMisplacedFiles:
    def test_reported_but_not_touched(self, tmp_path):
        make_roster_file(tmp_path / "забытый.xlsx")
        make_subject(tmp_path, "Физика")
        assert [p.name for p in R.misplaced_rosters(tmp_path)] == ["забытый.xlsx"]
        assert (tmp_path / "забытый.xlsx").exists()

    def test_none_when_everything_is_in_folders(self, tmp_path):
        make_subject(tmp_path, "Физика")
        assert R.misplaced_rosters(tmp_path) == ()


class TestMockGuard:
    """Заглушка не должна писать в рабочую базу.

    Урок из практики: показательный прогон с --mode mock привязал настоящие
    карты к случайно выбранным людям прямо в боевых данных.
    """

    class MockArgs:
        mode = "mock"
        db = cli.DEFAULT_DB

    def test_mock_into_default_db_refused(self):
        with pytest.raises(cli.Interrupted, match="mock"):
            cli.guard_mock(self.MockArgs())

    def test_mock_into_other_db_allowed(self, tmp_path):
        args = self.MockArgs()
        args.db = tmp_path / "demo.db"
        cli.guard_mock(args)  # не должно бросать

    def test_real_modes_are_not_restricted(self):
        for mode in ("auto", "serial", "keyboard"):
            args = self.MockArgs()
            args.mode = mode
            cli.guard_mock(args)
