"""Тесты моста «база → присланные списки групп»."""

from datetime import datetime

import pytest
from openpyxl import load_workbook

from rfid import journal, roster as R
from rfid.codes import parse_line
from rfid.storage import Storage
from test_roster import make_roster_file

CARD_A = parse_line("Em-Marine[A100] 007,42")
CARD_B = parse_line("Em-Marine[B200] 008,43")
CARD_C = parse_line("Em-Marine[0001] 002,00003")

GROUP_ONE = ["Тестов Тест Тестович", "Примеров Пример Примерович"]
GROUP_TWO = ["Образцова Проба Тестовна"]

MORNING = datetime(2026, 9, 20, 9, 2)
LATER = datetime(2026, 9, 20, 9, 15)


@pytest.fixture
def db(tmp_path):
    with Storage(tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def subject_folder(tmp_path):
    folder = tmp_path / "Бургеростроение 1 курс"
    folder.mkdir()
    make_roster_file(folder / "10001.xlsx", names=GROUP_ONE, group="1000000/10001")
    make_roster_file(folder / "10002.xlsx", names=GROUP_TWO, group="1000000/10002")
    return R.discover_subjects(tmp_path)[0]


class TestFilling:
    def test_marks_land_in_the_right_file(self, db, subject_folder):
        subject = db.add_subject(subject_folder.name)
        db.add_student(CARD_A, GROUP_ONE[0], "1000000/10001")
        db.mark(CARD_A, subject, at=MORNING)

        results = journal.fill_subject(db, subject, subject_folder)
        assert all(r.ok for r in results)

        sheet = load_workbook(subject_folder.path / "10001.xlsx").active
        assert sheet.cell(row=2, column=3).value == "20.09"
        assert sheet.cell(row=3, column=3).value == "09:02"

    def test_absent_get_a_dash(self, db, subject_folder):
        subject = db.add_subject(subject_folder.name)
        db.add_student(CARD_A, GROUP_ONE[0], "1000000/10001")
        db.mark(CARD_A, subject, at=MORNING)

        journal.fill_subject(db, subject, subject_folder)
        sheet = load_workbook(subject_folder.path / "10001.xlsx").active
        assert sheet.cell(row=4, column=3).value == R.ABSENT_MARK

    def test_each_group_gets_its_own_file(self, db, subject_folder):
        subject = db.add_subject(subject_folder.name)
        db.add_student(CARD_A, GROUP_ONE[0], "1000000/10001")
        db.add_student(CARD_B, GROUP_TWO[0], "1000000/10002")
        db.mark(CARD_A, subject, at=MORNING)
        db.mark(CARD_B, subject, at=LATER)

        journal.fill_subject(db, subject, subject_folder)

        first = load_workbook(subject_folder.path / "10001.xlsx").active
        second = load_workbook(subject_folder.path / "10002.xlsx").active
        assert first.cell(row=3, column=3).value == "09:02"
        assert second.cell(row=3, column=3).value == "09:15"
        # Первый человек может быть из другой группы того же предмета.
        assert first.cell(row=3, column=3).fill.patternType is None
        assert second.cell(row=3, column=3).fill.patternType == "solid"
        assert second.cell(row=3, column=3).fill.fgColor.rgb == "FFFFFF00"

    def test_other_group_does_not_pollute(self, db, subject_folder):
        """Студент чужой группы не должен появиться в этом файле."""
        subject = db.add_subject(subject_folder.name)
        db.add_student(CARD_B, GROUP_TWO[0], "1000000/10002")
        db.mark(CARD_B, subject, at=MORNING)

        results = {r.group_name: r for r in journal.fill_subject(db, subject, subject_folder)}
        assert results["1000000/10001"].missing == ()

        sheet = load_workbook(subject_folder.path / "10001.xlsx").active
        names = [sheet.cell(row=r, column=2).value for r in (3, 4)]
        assert names == GROUP_ONE

    def test_unknown_cards_are_not_written(self, db, subject_folder):
        """Неизвестная карта в файл не попадает — её не с кем сопоставить."""
        subject = db.add_subject(subject_folder.name)
        db.mark(CARD_C, subject, at=MORNING)
        results = journal.fill_subject(db, subject, subject_folder)
        assert all(r.ok and r.missing == () for r in results)


class TestBrokenLink:
    def test_renamed_student_is_reported(self, db, subject_folder):
        """Правка фамилии в файле рвёт связь — программа обязана сказать."""
        subject = db.add_subject(subject_folder.name)
        db.add_student(CARD_A, "Тестов Тест ТЕСТОВИЧ-старший", "1000000/10001")
        db.mark(CARD_A, subject, at=MORNING)

        results = {r.group_name: r for r in journal.fill_subject(db, subject, subject_folder)}
        assert results["1000000/10001"].missing == ("Тестов Тест ТЕСТОВИЧ-старший",)

    def test_case_and_yo_do_not_break_the_link(self, db, subject_folder):
        subject = db.add_subject(subject_folder.name)
        db.add_student(CARD_A, "тестов тест тестович", "1000000/10001")
        db.mark(CARD_A, subject, at=MORNING)

        results = {r.group_name: r for r in journal.fill_subject(db, subject, subject_folder)}
        assert results["1000000/10001"].missing == ()
        sheet = load_workbook(subject_folder.path / "10001.xlsx").active
        assert sheet.cell(row=3, column=3).value == "09:02"


class TestProblems:
    def test_unreadable_file_reported_not_raised(self, db, tmp_path):
        folder = tmp_path / "Физика"
        folder.mkdir()
        bad = folder / "мусор.xlsx"
        from openpyxl import Workbook

        wb = Workbook()
        wb.active["A1"] = "ничего похожего на список"
        wb.save(bad)

        subject = db.add_subject("Физика")
        result = journal.fill_subject(db, subject, R.discover_subjects(tmp_path)[0])[0]
        assert not result.ok
        assert "шапку" in result.error

    def test_fill_all_skips_subjects_without_marks(self, db, tmp_path):
        folder = tmp_path / "Физика"
        folder.mkdir()
        make_roster_file(folder / "г.xlsx", names=GROUP_ONE)
        assert journal.fill_all(db, tmp_path) == {}

    def test_fill_all_covers_every_folder_with_marks(self, db, tmp_path):
        for name in ("Физика", "Химия"):
            folder = tmp_path / name
            folder.mkdir()
            make_roster_file(folder / "г.xlsx", names=GROUP_ONE, group="1000000/10001")
            subject = db.add_subject(name)
            db.add_student(CARD_A, GROUP_ONE[0], "1000000/10001") if name == "Физика" else None
            db.mark(CARD_A, subject, at=MORNING)

        out = journal.fill_all(db, tmp_path)
        assert set(out) == {"Физика", "Химия"}
