"""Окно: замена и снятие карты у студента.

Нужен PyQt5 (как и test_gui_binding.py). Диалоги подменены, база настоящая.
"""

import os
from datetime import datetime

import pytest

pytest.importorskip("PyQt5")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rfid.db import Storage, UnknownCard  # noqa: E402
from rfid.gui import app as gui_app  # noqa: E402
from rfid.gui import card_dialog  # noqa: E402
from rfid.scanner import MockCardReader  # noqa: E402
from tests.helpers import ROSTER_NAMES, card, make_roster_file, must  # noqa: E402

GROUP = "1000000/10001"
OLD = card("Em-Marine[A100] 007,42")
NEW = card("Em-Marine[B200] 008,43")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, qapp, monkeypatch):
    folder = tmp_path / "tables" / "Предмет"
    folder.mkdir(parents=True)
    make_roster_file(folder / "group.xlsx", group=GROUP)
    db = tmp_path / "data" / "attendance.db"
    db.parent.mkdir()
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append("warning"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append("info"))
    win = gui_app.AttendanceWindow(db, tmp_path / "tables", mode="mock")
    win.shown = shown  # type: ignore[attr-defined]
    yield win
    win.close()


def student(window, name, code=None):
    with Storage(window.db_path) as storage:
        person = storage.get_or_create_student(name, GROUP)
        if code is not None:
            storage.bind_card(code, name, GROUP)
        return must(storage.student_by_id(person.id))


def owner_of(window, code):
    with Storage(window.db_path) as storage:
        return storage.find_student(code)


def answer(monkeypatch, button):
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or button)
    return asked


def choose_in_dialog(monkeypatch, code):
    class Fake:
        Accepted = 1

        def __init__(self, *args, **kwargs):
            self.code = code

        def exec_(self):
            return 1

    monkeypatch.setattr(gui_app, "ReplaceCardDialog", Fake)


def test_replace_keeps_past_marks(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    with Storage(window.db_path) as storage:
        storage.mark(OLD, storage.get_or_create_subject("Предмет"), at=datetime(2026, 11, 2, 9))
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    choose_in_dialog(monkeypatch, NEW.canonical)
    window._replace_card_of_selected()
    assert must(owner_of(window, NEW)).id == ivan.id
    assert owner_of(window, OLD) is None
    with Storage(window.db_path) as storage:
        assert storage.unknown_cards() == []  # отметка осталась за ним
    assert window.shown == []


def test_taken_card_asks_and_takes(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    petr = student(window, ROSTER_NAMES[1], NEW)
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    choose_in_dialog(monkeypatch, NEW.canonical)
    asked = answer(monkeypatch, QMessageBox.Yes)
    window._replace_card_of_selected()
    assert asked and petr.full_name in asked[0]
    assert must(owner_of(window, NEW)).id == ivan.id
    with Storage(window.db_path) as storage:
        assert must(storage.student_by_id(petr.id)).card_code is None


def test_taken_card_refused_changes_nothing(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    petr = student(window, ROSTER_NAMES[1], NEW)
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    choose_in_dialog(monkeypatch, NEW.canonical)
    answer(monkeypatch, QMessageBox.No)
    window._replace_card_of_selected()
    assert must(owner_of(window, NEW)).id == petr.id
    assert must(owner_of(window, OLD)).id == ivan.id


def test_unbind_after_confirmation(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    answer(monkeypatch, QMessageBox.Yes)
    window._unbind_card_of_selected()
    assert owner_of(window, OLD) is None
    with Storage(window.db_path) as storage:
        assert storage.student_by_id(ivan.id) is not None  # человек остался


def test_unbind_refused(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    answer(monkeypatch, QMessageBox.No)
    window._unbind_card_of_selected()
    assert must(owner_of(window, OLD)).id == ivan.id


def test_unbind_without_card_explains(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0])
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("спрашивать нечего"))
    window._unbind_card_of_selected()
    assert window.shown == ["info"]


# ------------------------------------------------------------------- диалог


@pytest.fixture
def dialog(window):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    unknown = [UnknownCard(NEW.canonical, 2, datetime(2026, 11, 2, 9), datetime(2026, 11, 3, 9))]
    dlg = card_dialog.ReplaceCardDialog(window, ivan, unknown, mode="mock")
    yield dlg
    dlg.reject()


def test_unknown_card_fills_the_code(dialog):
    dialog.unknown_list.setCurrentRow(0)
    assert dialog.code_edit.text() == "008,43"  # показ десятичный
    assert "B20008002B" not in dialog.unknown_list.item(0).text()
    dialog._accept()
    assert dialog.code == NEW.canonical  # а выбран полный код


def test_edited_field_does_not_keep_the_picked_card(dialog):
    dialog.unknown_list.setCurrentRow(0)
    dialog.code_edit.setText("009,44")  # руками и без префикса — неполный код
    dialog._accept()
    assert dialog.code is None
    assert "Не похоже" in dialog.status.text()


def test_pending_combo_keeps_full_code_of_lookalike_cards(window, monkeypatch):
    """Короткий вид совпал, но привязаться должна именно выбранная карта."""
    twin = card("Em-Marine[C300] 008,43")
    ivan = student(window, ROSTER_NAMES[0])
    window.pending_cards = [NEW.canonical, twin.canonical]
    window._refresh_pending_cards()
    combo = window.pending_card_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["008,43", "008,43"]
    combo.setCurrentIndex(1)
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    window._bind_card_to_selected_student()
    assert must(owner_of(window, twin)).id == ivan.id
    assert owner_of(window, NEW) is None


def test_garbage_is_refused(dialog):
    dialog.code_edit.setText("007,42")  # без префикса — неполный код
    dialog._accept()
    assert dialog.code is None
    assert "Не похоже" in dialog.status.text()


def test_same_card_is_refused(dialog):
    dialog.code_edit.setText(OLD.canonical)
    dialog._accept()
    assert dialog.code is None


def test_status_bar_shows_decimal_code(window, monkeypatch):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    monkeypatch.setattr(window, "_selected_student", lambda: ivan)
    choose_in_dialog(monkeypatch, NEW.canonical)
    window._replace_card_of_selected()
    message = window.statusBar().currentMessage()
    assert "008,43" in message and "007,42" in message
    assert NEW.canonical not in message and OLD.canonical not in message


def test_reader_busy_while_scanning(window):
    ivan = student(window, ROSTER_NAMES[0], OLD)
    dlg = card_dialog.ReplaceCardDialog(window, ivan, [], reader_free=False)
    assert not dlg.read_button.isEnabled()
    dlg.reject()


def test_grabber_reads_one_card(monkeypatch):
    lines = ["Em-Marine[B200] 008,43", "Em-Marine[C300] 009,44"]
    monkeypatch.setattr(card_dialog, "make_reader",
                        lambda *a, **k: MockCardReader(lines=lines))
    grabber = card_dialog.CardGrabber("mock", None)
    got: list[str] = []
    grabber.got.connect(got.append)
    grabber.run()
    assert got == [NEW.canonical]  # только первая карта
