"""Тесты выбора предмета.

tables/ — корень, в котором лежат папки предметов. Сама она предметом
не является. Раскладку ведёт пользователь: программа папок не создаёт
и файлов не переносит.
"""

from types import SimpleNamespace

import pytest

from rfid import cli, excel
from rfid.cli import common, subjects
from tests.helpers import make_roster_file


@pytest.fixture
def answers(monkeypatch):
    """Подменяет ввод заранее заготовленными ответами."""
    queue: list[str] = []

    def fake_ask(_prompt: str) -> str:
        if not queue:
            raise cli.Interrupted("ответы кончились")
        return queue.pop(0)

    monkeypatch.setattr(common, "ask", fake_ask)
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
        assert subjects.choose_subject(Args(), tmp_path).name == "Физика"

    def test_folders_without_lists_are_skipped(self, tmp_path, answers):
        (tmp_path / "Пустая").mkdir()
        make_subject(tmp_path, "Физика")
        answers += ["1"]
        assert subjects.choose_subject(Args(), tmp_path).name == "Физика"

    def test_root_is_not_a_subject(self, tmp_path, answers):
        """tables — корень, а не предмет: файлы в ней не образуют предмета."""
        make_roster_file(tmp_path / "россыпью.xlsx")
        make_subject(tmp_path, "Физика")
        answers += ["1"]
        chosen = subjects.choose_subject(Args(), tmp_path)
        assert chosen.name == "Физика"
        assert chosen.path != tmp_path

    def test_only_loose_files_means_no_subjects(self, tmp_path, answers):
        make_roster_file(tmp_path / "россыпью.xlsx")
        with pytest.raises(cli.Interrupted, match="нет ни одной папки предмета"):
            subjects.choose_subject(Args(), tmp_path)

    def test_loose_files_are_mentioned_when_nothing_found(self, tmp_path, answers):
        make_roster_file(tmp_path / "забытый.xlsx")
        with pytest.raises(cli.Interrupted, match="забытый.xlsx"):
            subjects.choose_subject(Args(), tmp_path)

    def test_bad_number_refuses(self, tmp_path, answers):
        make_subject(tmp_path, "Физика")
        answers += ["7"]
        with pytest.raises(cli.Interrupted, match="Нет такого пункта"):
            subjects.choose_subject(Args(), tmp_path)

    def test_empty_tables_explains_what_to_do(self, tmp_path):
        with pytest.raises(cli.Interrupted, match="Создайте"):
            subjects.choose_subject(Args(), tmp_path)

    def test_nothing_is_created_or_moved(self, tmp_path, answers):
        make_subject(tmp_path, "Физика")
        make_roster_file(tmp_path / "россыпью.xlsx")
        before = sorted(p.name for p in tmp_path.rglob("*"))
        answers += ["1"]
        subjects.choose_subject(Args(), tmp_path)
        assert sorted(p.name for p in tmp_path.rglob("*")) == before

class TestSubjectFlag:
    def test_by_folder_name(self, tmp_path):
        make_subject(tmp_path, "Алгебра")
        assert subjects.choose_subject(with_subject("алгебра"), tmp_path).name == "Алгебра"

    def test_unknown_lists_what_exists(self, tmp_path):
        make_subject(tmp_path, "Алгебра")
        with pytest.raises(cli.Interrupted, match="Алгебра"):
            subjects.choose_subject(with_subject("Астрология"), tmp_path)

class TestMisplacedFiles:
    def test_reported_but_not_touched(self, tmp_path):
        make_roster_file(tmp_path / "забытый.xlsx")
        make_subject(tmp_path, "Физика")
        assert [p.name for p in excel.misplaced_rosters(tmp_path)] == ["забытый.xlsx"]
        assert (tmp_path / "забытый.xlsx").exists()

    def test_none_when_everything_is_in_folders(self, tmp_path):
        make_subject(tmp_path, "Физика")
        assert excel.misplaced_rosters(tmp_path) == ()


class TestMockGuard:
    """Заглушка не должна писать в рабочую базу.

    Урок из практики: показательный прогон с --mode mock привязал настоящие
    карты к случайно выбранным людям прямо в боевых данных.
    """

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
        return tmp_path

    def args(self, db, mode="mock"):
        return SimpleNamespace(mode=mode, db=db, home=None)

    def test_mock_into_working_db_refused(self, home):
        with pytest.raises(cli.Interrupted, match="mock"):
            cli.guard_mock(self.args(home / cli.DB_FILE))

    def test_same_db_by_another_spelling_refused(self, home):
        """«data/../data/attendance.db» — всё та же рабочая база."""
        sneaky = home / "data" / ".." / cli.DB_FILE
        with pytest.raises(cli.Interrupted, match="mock"):
            cli.guard_mock(self.args(sneaky))

    def test_mock_into_other_db_allowed(self, home):
        cli.guard_mock(self.args(home / "data" / "demo.db"))  # не должно бросать

    def test_real_modes_are_not_restricted(self, home):
        for mode in ("auto", "serial", "keyboard"):
            cli.guard_mock(self.args(home / cli.DB_FILE, mode))
