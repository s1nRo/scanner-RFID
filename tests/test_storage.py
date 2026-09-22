"""Тесты хранилища: предметы, отметки, дедуп, журнал, миграция."""

import sqlite3
from datetime import date, datetime

import pytest

from rfid.codes import parse_line
from rfid.storage import NO_SUBJECT, MarkStatus, Storage

CARD_A = parse_line("Em-Marine[A100] 007,42")
CARD_B = parse_line("Em-Marine[B200] 008,43")

MORNING = datetime(2026, 9, 20, 9, 2, 13)
NOON = datetime(2026, 9, 20, 12, 30, 0)
NEXT_DAY = datetime(2026, 9, 21, 9, 5, 0)


@pytest.fixture
def db(tmp_path):
    with Storage(tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def math(db):
    return db.add_subject("Матанализ", "1 курс")


@pytest.fixture
def physics(db):
    return db.add_subject("Физика", "1 курс")


class TestSubjects:
    def test_add_and_find(self, db):
        subject = db.add_subject("Матанализ", "1 курс")
        assert db.find_subject("Матанализ", "1 курс") == subject
        assert subject.title == "Матанализ — 1 курс"

    def test_course_is_optional(self, db):
        subject = db.add_subject("Философия")
        assert subject.course == ""
        assert subject.title == "Философия"

    def test_same_name_different_course_are_different(self, db):
        first = db.add_subject("Матанализ", "1 курс")
        second = db.add_subject("Матанализ", "2 курс")
        assert first.id != second.id

    def test_duplicate_rejected(self, db):
        db.add_subject("Матанализ", "1 курс")
        with pytest.raises(sqlite3.IntegrityError):
            db.add_subject("Матанализ", "1 курс")

    def test_get_or_create_is_idempotent(self, db):
        first = db.get_or_create_subject("Матанализ", "1 курс")
        assert db.get_or_create_subject("Матанализ", "1 курс").id == first.id

    def test_empty_name_rejected(self, db):
        with pytest.raises(ValueError):
            db.add_subject("   ")

    def test_remove_takes_marks_with_it(self, db, math):
        db.mark(CARD_A, math, at=MORNING)
        assert db.remove_subject(math) is True
        assert db.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0


class TestMarking:
    def test_unknown_card_is_still_recorded(self, db, math):
        result = db.mark(CARD_A, math, at=MORNING)
        assert result.status is MarkStatus.UNKNOWN
        assert result.student is None
        assert result.recorded

    def test_known_student(self, db, math):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        result = db.mark(CARD_A, math, at=MORNING)
        assert result.status is MarkStatus.MARKED
        assert result.student.full_name == "Иванов Иван"

    def test_second_tap_same_subject_same_day_is_duplicate(self, db, math):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        db.mark(CARD_A, math, at=MORNING)
        again = db.mark(CARD_A, math, at=NOON)
        assert again.status is MarkStatus.DUPLICATE
        assert again.first_at == MORNING

    def test_same_day_other_subject_is_a_new_mark(self, db, math, physics):
        """Две пары в один день — две отметки. Ради этого и заводили предметы."""
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        assert db.mark(CARD_A, math, at=MORNING).status is MarkStatus.MARKED
        assert db.mark(CARD_A, physics, at=NOON).status is MarkStatus.MARKED

    def test_duplicate_survives_restart(self, tmp_path):
        """Дедуп живёт в базе, а не в памяти процесса."""
        path = tmp_path / "restart.db"
        with Storage(path) as s:
            subject = s.add_subject("Матанализ")
            s.add_student(CARD_A, "Иванов Иван", "ИС-21")
            assert s.mark(CARD_A, subject, at=MORNING).status is MarkStatus.MARKED
        with Storage(path) as s:
            subject = s.find_subject("Матанализ")
            assert s.mark(CARD_A, subject, at=NOON).status is MarkStatus.DUPLICATE

    def test_next_day_is_a_new_mark(self, db, math):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        db.mark(CARD_A, math, at=MORNING)
        assert db.mark(CARD_A, math, at=NEXT_DAY).status is MarkStatus.MARKED

    def test_raw_line_is_kept(self, db, math):
        raw = "Em-Marine[A100] 007,42"
        db.mark(CARD_A, math, at=MORNING, raw=raw)
        assert db.conn.execute("SELECT raw FROM attendance").fetchone()["raw"] == raw


class TestStudents:
    def test_add_and_find(self, db):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        found = db.find_student(CARD_A)
        assert found.full_name == "Иванов Иван"
        assert found.group_name == "ИС-21"

    def test_card_code_stored_canonically(self, db):
        db.add_student(CARD_A, "Иванов Иван")
        assert db.find_student(CARD_A).card_code == "A10007002A"

    def test_duplicate_card_rejected(self, db):
        db.add_student(CARD_A, "Иванов Иван")
        with pytest.raises(sqlite3.IntegrityError):
            db.add_student(CARD_A, "Петров Пётр")

    def test_empty_name_rejected(self, db):
        with pytest.raises(ValueError):
            db.add_student(CARD_A, "   ")

    def test_empty_group_filter_means_ungrouped(self, db):
        """"" — это «без группы», а не «фильтра нет»."""
        db.add_student(CARD_A, "Безгруппный", "")
        db.add_student(CARD_B, "Иванов Иван", "ИС-21")
        assert [s.full_name for s in db.list_students("")] == ["Безгруппный"]
        assert len(db.list_students()) == 2

    def test_remove(self, db):
        db.add_student(CARD_A, "Иванов Иван")
        assert db.remove_student(CARD_A) is True
        assert db.remove_student(CARD_A) is False


class TestUnknownResolution:
    def test_listed_with_counts(self, db, math, physics):
        db.mark(CARD_A, math, at=MORNING)
        db.mark(CARD_A, physics, at=NOON)
        db.mark(CARD_B, math, at=MORNING)
        unknown = {u.card_code: u for u in db.unknown_cards()}
        assert set(unknown) == {"A10007002A", "B20008002B"}
        assert unknown["A10007002A"].times == 2

    def test_resolving_backfills_across_subjects(self, db, math, physics):
        db.mark(CARD_A, math, at=MORNING)
        db.mark(CARD_A, physics, at=NOON)
        student, backfilled = db.resolve_unknown(CARD_A, "Иванов Иван", "ИС-21")
        assert backfilled == 2
        assert db.unknown_cards() == []
        assert db.day_rows(date(2026, 9, 20), math)[0].present

    def test_resolving_does_not_touch_other_cards(self, db, math):
        db.mark(CARD_A, math, at=MORNING)
        db.mark(CARD_B, math, at=MORNING)
        _, backfilled = db.resolve_unknown(CARD_A, "Иванов Иван")
        assert backfilled == 1
        assert [u.card_code for u in db.unknown_cards()] == ["B20008002B"]


class TestEnrollingAfterUnknownMarks:
    """Регистрация карты, которую уже видели как неизвестную.

    Частый порядок: студент приложил карту до того, как его завели
    в справочник. Отметка сохранилась безымянной. Когда его потом
    регистрируют, прошлые отметки обязаны стать его — иначе карта
    остаётся в «неизвестных», а человек числится отсутствующим,
    хотя повторное прикладывание говорит «уже отмечен».
    """

    def test_card_leaves_unknown_list(self, db, math):
        db.mark(CARD_A, math, at=MORNING)
        assert len(db.unknown_cards()) == 1

        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        assert db.unknown_cards() == []

    def test_past_mark_is_credited(self, db, math):
        db.mark(CARD_A, math, at=MORNING)
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")

        row = db.day_rows(date(2026, 9, 20), math)[0]
        assert row.student.full_name == "Иванов Иван"
        assert row.present, "отметка есть, но студент числится отсутствующим"
        assert row.at == MORNING

    def test_reaches_the_group_file(self, db, math):
        """Именно marks_by_day уходит в файл группы — там отметка и нужна."""
        db.mark(CARD_A, math, at=MORNING)
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        assert db.marks_by_day(math) == {date(2026, 9, 20): {"Иванов Иван": MORNING}}

    def test_marks_across_subjects_all_credited(self, db, math, physics):
        db.mark(CARD_A, math, at=MORNING)
        db.mark(CARD_A, physics, at=NOON)
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        assert db.unknown_cards() == []
        assert db.day_rows(date(2026, 9, 20), physics)[0].present

    def test_other_cards_untouched(self, db, math):
        db.mark(CARD_A, math, at=MORNING)
        db.mark(CARD_B, math, at=MORNING)
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        assert [u.card_code for u in db.unknown_cards()] == ["B20008002B"]

    def test_import_also_credits(self, db, math):
        """Импорт справочника из Excel идёт тем же путём."""
        db.mark(CARD_A, math, at=MORNING)
        db.add_student("A10007002A", "Иванов Иван", "ИС-21")
        assert db.unknown_cards() == []

    def test_damage_from_older_versions_is_repaired_on_open(self, tmp_path):
        """Записи, испорченные прежней версией, чинятся при открытии базы.

        Воспроизводит реальный случай: студент заведён, но его отметка
        осталась ничьей, потому что старая add_student не привязывала прошлое.
        """
        path = tmp_path / "damaged.db"
        with Storage(path) as s:
            subject = s.add_subject("Бургеростроение", "-4")
            s.mark(CARD_A, subject, at=MORNING)
            s._insert_student(CARD_A, "Примеров Пример", "1000000/10002")  # без привязки
            assert len(s.unknown_cards()) == 1, "подготовка: отметка должна быть ничьей"

        with Storage(path) as s:
            assert s.unknown_cards() == []
            row = s.day_rows(date(2026, 9, 20), s.find_subject("Бургеростроение", "-4"))[0]
            assert row.present
            assert row.student.full_name == "Примеров Пример"

    def test_repair_leaves_truly_unknown_alone(self, tmp_path):
        path = tmp_path / "mixed.db"
        with Storage(path) as s:
            subject = s.add_subject("Матанализ")
            s.mark(CARD_A, subject, at=MORNING)
            s.mark(CARD_B, subject, at=MORNING)
            s._insert_student(CARD_A, "Иванов Иван", "ИС-21")
        with Storage(path) as s:
            assert [u.card_code for u in s.unknown_cards()] == ["B20008002B"]


class TestDayReport:
    def test_absent_students_are_listed(self, db, math):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        db.add_student(CARD_B, "Петров Пётр", "ИС-21")
        db.mark(CARD_A, math, at=MORNING)

        rows = {r.student.full_name: r for r in db.day_rows(date(2026, 9, 20), math)}
        assert rows["Иванов Иван"].at == MORNING
        assert not rows["Петров Пётр"].present

    def test_marks_of_other_subject_do_not_leak(self, db, math, physics):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        db.mark(CARD_A, physics, at=MORNING)
        rows = db.day_rows(date(2026, 9, 20), math)
        assert not rows[0].present

    def test_filter_by_group(self, db, math):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        db.add_student(CARD_B, "Сидоров Сидор", "БИ-11")
        rows = db.day_rows(date(2026, 9, 20), math, group_name="ИС-21")
        assert [r.student.full_name for r in rows] == ["Иванов Иван"]


class TestGroupStats:
    def test_counts_students_not_join_rows(self, db, math):
        db.add_student(CARD_A, "Иванов Иван", "ИС-21")
        db.add_student(CARD_B, "Петров Пётр", "ИС-21")
        db.add_student("0000000003", "Сидоров Сидор", "БИ-11")
        db.mark(CARD_A, math, at=MORNING)

        stats = {g.group_name: g for g in db.group_stats(date(2026, 9, 20), math)}
        assert (stats["ИС-21"].total, stats["ИС-21"].present) == (2, 1)
        assert stats["ИС-21"].percent == 50.0
        assert stats["БИ-11"].present == 0


class TestMigrationFromV1:
    """Схема 1 не знала предметов. Обновление не должно терять отметки."""

    def _make_v1(self, path):
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY, card_code TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL, group_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL);
            CREATE TABLE attendance (
                id INTEGER PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL,
                card_code TEXT NOT NULL, student_id INTEGER REFERENCES students(id),
                raw TEXT, UNIQUE(day, card_code));
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta VALUES('schema_version','1');
            INSERT INTO students VALUES(1,'A10007002A','Иванов Иван','ИС-21','2026-09-20T09:00:00');
            INSERT INTO attendance VALUES(1,'2026-09-20','2026-09-20T09:02:13','A10007002A',1,'raw');
            """
        )
        conn.commit()
        conn.close()

    def test_marks_survive(self, tmp_path):
        path = tmp_path / "v1.db"
        self._make_v1(path)
        with Storage(path) as s:
            assert s.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
            assert s.count_students() == 1
            assert [x.name for x in s.list_subjects()] == [NO_SUBJECT]

    def test_old_marks_land_in_placeholder_subject(self, tmp_path):
        path = tmp_path / "v1.db"
        self._make_v1(path)
        with Storage(path) as s:
            legacy = s.find_subject(NO_SUBJECT)
            rows = s.day_rows(date(2026, 9, 20), legacy)
            assert rows[0].student.full_name == "Иванов Иван"
            assert rows[0].present

    def test_schema_version_updated(self, tmp_path):
        path = tmp_path / "v1.db"
        self._make_v1(path)
        with Storage(path) as s:
            version = s.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            assert version == "2"

    def test_migration_is_idempotent(self, tmp_path):
        path = tmp_path / "v1.db"
        self._make_v1(path)
        for _ in range(3):
            with Storage(path) as s:
                assert s.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
                assert len(s.list_subjects()) == 1
