"""Окно: предмет на экране, потерянная отметка, повторная запись в таблицы.

Нужен PyQt5. Сценарии — из поиска ошибок 07.11.2026.
"""

import os
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import load_workbook  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from PyQt5.QtCore import QDateTime  # noqa: E402
from PyQt5.QtGui import QCloseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rfid import excel, pipeline  # noqa: E402
from rfid.db import MarkStatus, Storage  # noqa: E402
from rfid.excel import SubjectFolder, as_folder  # noqa: E402
from rfid.gui import app as gui_app  # noqa: E402
from rfid.gui import card_dialog  # noqa: E402
from rfid.gui import worker as gui_worker  # noqa: E402
from rfid.scanner import READER_BACK, READER_LOST, MockCardReader  # noqa: E402
from tests.helpers import ROSTER_NAMES, card, make_roster_file, must  # noqa: E402

GROUP = "1000000/10001"
LINE = "Em-Marine[A100] 007,42"
CARD = card(LINE)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tables(tmp_path):
    root = tmp_path / "tables"
    for name in ("А предмет", "В предмет"):
        (root / name).mkdir(parents=True)
        make_roster_file(root / name / "group.xlsx", group=GROUP)
    return root


@pytest.fixture
def window(tmp_path, tables, qapp, monkeypatch):
    db = tmp_path / "data" / "attendance.db"
    db.parent.mkdir()
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda _parent, _title, text, *a, **k: warnings.append(text))
    monkeypatch.setattr(QMessageBox, "information",
                        lambda _parent, _title, text, *a, **k: warnings.append(text))
    monkeypatch.setattr(gui_app, "error_beep", lambda: None)
    win = gui_app.AttendanceWindow(db, tables, mode="mock")
    win.warnings = warnings  # type: ignore[attr-defined]
    yield win
    win.scan_thread = None
    win.close()


def add_subject(tables, name):
    (tables / name).mkdir()
    make_roster_file(tables / name / "group.xlsx", group=GROUP)


# ---------------------------------------------------------------- предмет


def names_in_combo(window):
    combo = window.subject_combo
    return [combo.itemText(i) for i in range(combo.count())]


def test_new_folder_does_not_switch_the_subject(window, tables):
    window.subject_combo.setCurrentIndex(1)
    add_subject(tables, "Б предмет")  # встанет между А и В
    window._reload_rosters()
    assert window.subject_combo.currentText() == "В предмет"
    assert window._current_folder().name == "В предмет"
    assert names_in_combo(window) == ["А предмет", "Б предмет", "В предмет"]


def test_removed_folder_does_not_switch_the_subject(window, tables):
    window.subject_combo.setCurrentIndex(1)
    (tables / "А предмет" / "group.xlsx").unlink()
    window._reload_rosters()
    assert window._current_folder().name == "В предмет"
    assert names_in_combo(window) == ["В предмет"]


def test_marks_go_to_the_subject_on_screen(window, tables, monkeypatch):
    window.subject_combo.setCurrentIndex(1)
    add_subject(tables, "Б предмет")
    window._reload_rosters()
    folder = window._current_folder()
    monkeypatch.setattr(gui_worker, "make_reader",
                        lambda *a, **k: MockCardReader(lines=[LINE]))
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, ROSTER_NAMES[0], GROUP)
    gui_worker.ScanWorker(window.db_path, folder, mode="mock", debounce=0).run()
    with Storage(window.db_path) as storage:
        assert storage.find_subject("В предмет") is not None
        marked = storage.marks_by_day(storage.get_or_create_subject("В предмет"))
        other = storage.marks_by_day(storage.get_or_create_subject("Б предмет"))
    assert marked
    assert not other


def test_no_subjects_means_no_folder(tmp_path, qapp):
    db = tmp_path / "attendance.db"
    win = gui_app.AttendanceWindow(db, tmp_path / "нет", mode="mock")
    assert win._current_folder() is None
    win.close()


# ------------------------------------------------- потерянная отметка


@pytest.fixture
def run_failing(window, monkeypatch):
    """Настоящий ScanWorker: база отказывает, файлы пишутся как обычно."""
    def run():
        monkeypatch.setattr(gui_worker, "make_reader",
                            lambda *a, **k: MockCardReader(lines=[LINE]))

        def locked(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(Storage, "mark", locked)
        worker = gui_worker.ScanWorker(
            window.db_path, window._current_folder(), mode="mock", debounce=0
        )
        said: dict[str, list] = {"not_saved": [], "export": [], "tally": []}
        worker.not_saved.connect(said["not_saved"].append)
        worker.export_finished.connect(said["export"].append)
        worker.finished.connect(said["tally"].append)
        worker.run()
        return worker, said
    return run


def test_worker_shouts_and_does_not_claim_success(run_failing):
    worker, said = run_failing()
    assert len(said["not_saved"]) == 1
    assert "НЕ ЗАПИСАНО" in said["not_saved"][0]
    assert "007,42" in said["not_saved"][0]
    assert worker.problems == said["not_saved"]
    assert "сохранены в базе и таблицах" not in said["export"][0]
    assert said["tally"][0][pipeline.FAILED] == 1


def test_window_replaces_the_previous_student(window):
    window.person_name.setText("Прошлый Студент")
    window.scan_status_chip.setText("Отмечен")
    window._mark_not_saved(f"{pipeline.NOT_SAVED}: карта 007,42")
    assert window.person_name.text() == "НЕ ЗАПИСАНО"
    assert "007,42" in window.person_meta.text()
    assert window.scan_status_chip.text() != "Отмечен"


def test_next_good_mark_clears_the_red(window):
    window._mark_not_saved(f"{pipeline.NOT_SAVED}: карта 007,42")
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, ROSTER_NAMES[0], GROUP)
        result = storage.mark(CARD, storage.get_or_create_subject(window._current_folder().name))
    assert result.status is MarkStatus.MARKED
    window._mark_received(result)
    assert window.person_name.styleSheet() == ""
    assert window.person_name.text() == ROSTER_NAMES[0]


def test_session_end_lists_lost_marks_once(window, run_failing):
    worker, _said = run_failing()
    window.session_problems = worker.problems
    window._scan_finished(Counter())
    assert len(window.warnings) == 1
    assert "007,42" in window.warnings[0]
    window._scan_finished(Counter())
    assert len(window.warnings) == 1


def test_session_without_problems_is_silent(window):
    window._scan_finished(Counter())
    assert window.warnings == []


class StoppedThread:
    def isRunning(self):  # noqa: N802 - как у QThread
        return True

    def quit(self):
        pass

    def wait(self, _ms):
        return True


def test_closing_during_session_still_tells(window, monkeypatch):
    """Сигнал _scan_finished при закрытии никто не обработает, а сказать надо."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window.scan_thread = StoppedThread()
    window.session_problems = [f"{pipeline.NOT_SAVED}: карта 007,42"]
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert any("007,42" in text for text in window.warnings)


def test_locked_file_goes_to_session_problems(window, tables, monkeypatch):
    monkeypatch.setattr(gui_worker, "make_reader",
                        lambda *a, **k: MockCardReader(lines=[LINE]))
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, ROSTER_NAMES[0], GROUP)

    def locked(*_a, **_k):
        raise PermissionError("файл занят")

    monkeypatch.setattr("rfid.excel.attendance.save_atomically", locked)
    worker = gui_worker.ScanWorker(
        window.db_path, window._current_folder(), mode="mock", debounce=0
    )
    worker.run()
    assert len(worker.problems) == 1
    assert "group.xlsx" in worker.problems[0]
    assert "Excel" in worker.problems[0]


# ------------------------------------------------- запись в таблицы


def mark_today(window):
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, ROSTER_NAMES[0], GROUP)
        storage.mark(CARD, storage.get_or_create_subject(window._current_folder().name),
                     at=datetime.now())


def in_file(window, folder_name):
    folder = as_folder(window.tables_path / folder_name)
    roster = excel.read_roster(folder.rosters[0])
    found = excel.read_attendance(roster, date.today())
    return {student.full_name for day in found.marks.values() for student, _at in day}


def test_button_writes_after_excel_is_closed(window, monkeypatch):
    mark_today(window)
    name = window._current_folder().name
    assert in_file(window, name) == set()
    window._write_tables()
    assert in_file(window, name) == {ROSTER_NAMES[0]}
    assert window.warnings == []
    assert "Записано" in window.statusBar().currentMessage()


def test_button_reports_a_file_still_open(window, monkeypatch):
    mark_today(window)

    def locked(*_a, **_k):
        raise PermissionError("файл занят")

    monkeypatch.setattr("rfid.excel.attendance.save_atomically", locked)
    window._write_tables()
    assert len(window.warnings) == 1
    assert "group.xlsx" in window.warnings[0]
    with Storage(window.db_path) as storage:
        assert storage.count_marks() == 1


def test_button_covers_other_subjects(window):
    """Карту сменили на одном предмете, а отметки есть и на другом."""
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, ROSTER_NAMES[0], GROUP)
        storage.mark(CARD, storage.get_or_create_subject("В предмет"), at=datetime.now())
    assert window._current_folder().name == "А предмет"
    window._write_tables()
    assert in_file(window, "В предмет") == {ROSTER_NAMES[0]}


def test_button_waits_for_the_session(window):
    window.scan_thread = StoppedThread()
    window._write_tables()
    assert len(window.warnings) == 1
    window.scan_thread = None
    assert window.write_tables_button.isEnabled()


def test_button_is_off_without_subjects(tmp_path, qapp):
    win = gui_app.AttendanceWindow(tmp_path / "attendance.db", tmp_path / "нет", mode="mock")
    assert not win.write_tables_button.isEnabled()
    win.close()


def test_write_reports_broken_link(window):
    """Фамилию поправили в файле: «Отметки сохранены» было бы неправдой."""
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, "Старая Фамилия", GROUP)
        storage.mark(CARD, storage.get_or_create_subject(window._current_folder().name),
                     at=datetime.now())
    window._write_tables()
    assert any("Старая Фамилия" in text for text in window.warnings)


def test_session_reports_broken_link(window, monkeypatch):
    monkeypatch.setattr(gui_worker, "make_reader",
                        lambda *a, **k: MockCardReader(lines=[LINE]))
    with Storage(window.db_path) as storage:
        storage.bind_card(CARD, "Старая Фамилия", GROUP)
    worker = gui_worker.ScanWorker(
        window.db_path, window._current_folder(), mode="mock", debounce=0
    )
    worker.run()
    assert any("Старая Фамилия" in text for text in worker.problems)


# ------------------------------------------------- связь со считывателем


LOST = f"{READER_LOST} (SerialException). Повтор через 1 с. Проверьте кабель."


def test_lost_reader_is_not_called_working(window):
    window._reader_ready("Считыватель подключён: COM3")
    assert window.reader_title.text() == "Считыватель работает"
    window._reader_lost(LOST)
    assert window.reader_title.text() == "Считыватель отключён"
    assert "не читаются" in window.hero_hint.text()
    window._scan_status("Сохраняем отметки в таблицы…")
    assert window.reader_title.text() == "Считыватель отключён"


def test_reader_back_restores_the_window(window):
    window._reader_lost(LOST)
    window._reader_ready(f"{READER_BACK} (COM3).")
    assert window.reader_title.text() == "Считыватель работает"
    assert window.reader_title.styleSheet() == ""
    assert window.hero_title.text() == "Сканирование запущено"


def test_lost_reader_beeps_once_per_outage(window, monkeypatch):
    beeps = []
    monkeypatch.setattr(gui_app, "error_beep", lambda: beeps.append(1))
    window._reader_lost(LOST)
    window._reader_lost(LOST)
    assert len(beeps) == 1
    window._reader_ready(f"{READER_BACK} (COM3).")
    window._reader_lost(LOST)
    assert len(beeps) == 2


def test_worker_routes_reader_messages():
    worker = gui_worker.ScanWorker(Path("x"), SubjectFolder("П", Path("x"), ()), mode="mock")
    said: dict[str, list] = {"lost": [], "ready": [], "other": []}
    worker.reader_lost.connect(said["lost"].append)
    worker.reader_ready.connect(said["ready"].append)
    worker.status_changed.connect(said["other"].append)
    worker.status(LOST)
    worker.status(f"{READER_BACK} (COM3).")
    worker.status("Непонятная строка")
    assert [len(said[key]) for key in ("lost", "ready", "other")] == [1, 1, 1]


def test_card_grabber_says_the_reader_is_busy(monkeypatch):
    def reader(*_a, on_status, **_k):
        on_status(LOST)
        return MockCardReader(lines=[LINE])

    monkeypatch.setattr(card_dialog, "make_reader", reader)
    grabber = card_dialog.CardGrabber("mock", None)
    said: list[str] = []
    grabber.status.connect(said.append)
    grabber.run()
    assert said == [LOST]


def test_card_dialog_shows_the_problem_in_red(window):
    with Storage(window.db_path) as storage:
        person = storage.get_or_create_student(ROSTER_NAMES[0], GROUP)
    dialog = card_dialog.ReplaceCardDialog(window, person, [], mode="mock")
    dialog._reader_said(LOST)
    assert LOST in dialog.status.text()
    assert "b00020" in dialog.status.styleSheet()
    dialog.reject()


# ----------------------------------------------- загрузка изменений


def test_reload_reports_a_broken_file(window, tables):
    (tables / "А предмет" / "broken.xlsx").write_bytes(b"")
    window._reload_rosters()
    assert any("broken.xlsx" in text for text in window.warnings)
    assert "Изменения загружены" not in window.statusBar().currentMessage()


def test_reload_reports_a_cell_it_did_not_understand(window, tables):
    path = tables / "А предмет" / "group.xlsx"
    roster = excel.read_roster(path)
    workbook = load_workbook(path)
    sheet = workbook[roster.sheet_title]
    col = get_column_letter(roster.first_date_col)
    sheet[f"{col}{roster.header_row}"] = "02.11"
    sheet[f"{col}{roster.students[0].row}"] = "+"
    workbook.save(path)
    workbook.close()
    window._reload_rosters()
    assert any("«+»" in text for text in window.warnings)


def test_new_subject_appears_without_restart(tmp_path, qapp, monkeypatch):
    """Первый запуск: окно просит создать папку — и обязано её увидеть."""
    root = tmp_path / "tables"
    root.mkdir()
    win = gui_app.AttendanceWindow(tmp_path / "a.db", root, mode="mock")
    assert win._current_folder() is None
    assert win.reload_roster_button.isEnabled()
    add_subject(root, "Новый предмет")
    win._reload_rosters()
    assert must(win._current_folder()).name == "Новый предмет"
    assert win.scan_button.isEnabled()
    assert win.subject_combo.isEnabled()
    win.close()


class TestGroupMoveQuestion:
    @pytest.fixture
    def renamed(self, window):
        with Storage(window.db_path) as storage:
            student, _ = storage.bind_card(CARD, ROSTER_NAMES[0], "Старая группа")
        return student

    def answer(self, monkeypatch, button):
        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: asked.append(a[2]) or button)
        return asked

    def test_yes_moves(self, window, renamed, monkeypatch):
        asked = self.answer(monkeypatch, QMessageBox.Yes)
        window._reload_rosters()
        assert len(asked) == 1
        assert "Старая группа" in asked[0] and GROUP in asked[0]
        with Storage(window.db_path) as storage:
            assert must(storage.find_student(CARD)).group_name == GROUP
        assert window.students_table.rowCount() > 0

    def test_no_is_remembered_for_the_session(self, window, renamed, monkeypatch):
        asked = self.answer(monkeypatch, QMessageBox.No)
        window._reload_rosters()
        window._reload_rosters()
        assert len(asked) == 1
        with Storage(window.db_path) as storage:
            assert must(storage.find_student(CARD)).group_name == "Старая группа"


# ------------------------------------------------- ручная отметка


def manual_mark(window, monkeypatch):
    with Storage(window.db_path) as storage:
        person = storage.get_or_create_student(ROSTER_NAMES[0], GROUP)
    monkeypatch.setattr(window, "_selected_student", lambda: person)
    window._mark_selected_student()
    with Storage(window.db_path) as storage:
        subject = storage.get_or_create_subject(window._current_folder().name)
        return storage.marks_by_day(subject)


def test_untouched_field_means_now_even_if_it_went_stale(window, monkeypatch):
    """Окно открыто со вчера, время не меняли: отметка на сегодня, а не на вчера."""
    stale = QDateTime.currentDateTime().addDays(-1)
    window.manual_datetime.blockSignals(True)
    window.manual_datetime.setDateTime(stale)
    window.manual_datetime.blockSignals(False)
    assert set(manual_mark(window, monkeypatch)) == {date.today()}


def test_edited_field_is_respected(window, monkeypatch):
    chosen = QDateTime.currentDateTime().addDays(-3)
    window.manual_datetime.setDateTime(chosen)
    assert set(manual_mark(window, monkeypatch)) == {chosen.date().toPyDate()}


def test_field_goes_back_to_now_after_a_mark(window, monkeypatch):
    window.manual_datetime.setDateTime(QDateTime.currentDateTime().addDays(-3))
    manual_mark(window, monkeypatch)
    assert window.manual_datetime.date().toPyDate() == date.today()
    assert not window._manual_time_touched


def test_opening_the_tab_refreshes_the_time(window):
    window.manual_datetime.blockSignals(True)
    window.manual_datetime.setDateTime(QDateTime.currentDateTime().addDays(-1))
    window.manual_datetime.blockSignals(False)
    window.pages.setCurrentWidget(window.students_page)
    assert window.manual_datetime.date().toPyDate() == date.today()


def test_opening_the_tab_keeps_a_time_being_edited(window):
    chosen = QDateTime.currentDateTime().addDays(-3)
    window.manual_datetime.setDateTime(chosen)
    window.pages.setCurrentWidget(window.students_page)
    assert window.manual_datetime.date().toPyDate() == chosen.date().toPyDate()


def test_future_is_refused(window, monkeypatch):
    window.manual_datetime.setDateTime(QDateTime.currentDateTime().addDays(2))
    assert manual_mark(window, monkeypatch) == {}
    assert len(window.warnings) == 1


# ------------------------------------------------- ошибки и закрытие


def test_unhandled_error_is_shown_and_does_not_escape(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda _p, _t, text, *a, **k: shown.append(text))
    try:
        raise OSError("нет прав")
    except OSError as exc:
        gui_app.report_crash(OSError, exc, exc.__traceback__)
    assert "нет прав" in shown[0]


def test_new_folder_without_rights_is_explained(tmp_path, qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(gui_app.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(tmp_path / "закрытая"))

    def denied(*_a, **_k):
        raise PermissionError("нет прав")

    monkeypatch.setattr(Path, "mkdir", denied)
    assert gui_app.pick_home(None) is None
    assert "нет прав" in shown[0]


def test_closing_during_driver_install_is_refused(window):
    window._installing_driver = True
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.warnings
    window._installing_driver = False


def test_workspace_button_hidden_on_a_flash_drive(tmp_path, tables, qapp, monkeypatch):
    monkeypatch.setattr(gui_app.workdir, "portable_folder", lambda: tmp_path)
    win = gui_app.AttendanceWindow(tmp_path / "a.db", tables, mode="mock")
    assert win.workspace_button.isHidden()
    win.close()


def test_workspace_button_kept_otherwise(window):
    assert not window.workspace_button.isHidden()
