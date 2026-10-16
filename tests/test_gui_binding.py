"""Окно привязки незнакомой карты во время сканирования.

Нужен PyQt5: в MSYS-venv его нет, и тесты пропускаются; гоняются
в build-venv и в CI. Экран не нужен — платформа Qt «offscreen».
"""

import os
from datetime import datetime

import pytest

pytest.importorskip("PyQt5")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from rfid.db import MarkStatus, Storage  # noqa: E402
from rfid.gui import app as gui_app  # noqa: E402
from rfid.gui.binding import BindDialog  # noqa: E402
from rfid.scanner import Scan  # noqa: E402
from tests.helpers import ROSTER_NAMES, card, make_roster_file, must  # noqa: E402

UNKNOWN = card("Em-Marine[B200] 008,43")
GROUP = "1000000/10001"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeWorker:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


@pytest.fixture
def window(tmp_path, qapp, monkeypatch):
    folder = tmp_path / "tables" / "Предмет"
    folder.mkdir(parents=True)
    make_roster_file(folder / "group.xlsx", group=GROUP)
    db = tmp_path / "data" / "attendance.db"
    db.parent.mkdir()
    monkeypatch.setattr(gui_app, "error_beep", lambda: None)
    win = gui_app.AttendanceWindow(db, tmp_path / "tables", mode="mock")
    win.scan_worker = FakeWorker()  # type: ignore[assignment]
    yield win
    win.scan_worker = None
    win.close()


def unknown_mark(win):
    """Как это делает поток считывателя: безымянная отметка уже в базе."""
    with Storage(win.db_path) as storage:
        subject = storage.get_or_create_subject("Предмет")
        return storage.mark(UNKNOWN, subject, at=datetime.now())


def answer(monkeypatch, pick: str | None):
    """Оператор выбирает студента по ФИО (None — «Оставить без привязки»)."""
    def exec_(dialog):
        if pick is None:
            return BindDialog.Rejected
        dialog.search.setText(pick)
        return BindDialog.Accepted if dialog.chosen() else BindDialog.Rejected
    monkeypatch.setattr(BindDialog, "exec_", exec_)


def test_unknown_card_is_bound_and_reader_released(window, monkeypatch):
    result = unknown_mark(window)
    assert result.status is MarkStatus.UNKNOWN
    window._refresh_today()
    answer(monkeypatch, ROSTER_NAMES[1])
    window._ask_binding(result)
    with Storage(window.db_path) as storage:
        assert must(storage.find_student(UNKNOWN)).full_name == ROSTER_NAMES[1]
        assert storage.unknown_cards() == []  # отметка стала именной
    assert window.scan_worker.released == 1


def test_skip_keeps_the_mark_and_releases(window, monkeypatch):
    result = unknown_mark(window)
    window._refresh_today()
    answer(monkeypatch, None)
    window._ask_binding(result)
    with Storage(window.db_path) as storage:
        assert storage.find_student(UNKNOWN) is None
        assert [u.card_code for u in storage.unknown_cards()] == [UNKNOWN.canonical]
    assert window.scan_worker.released == 1


def test_reader_is_released_even_if_the_dialog_fails(window, monkeypatch):
    # Что бы ни случилось с окном, считыватель не должен остаться закрытым.
    result = unknown_mark(window)
    window._refresh_today()

    def broken(dialog):
        raise RuntimeError("сбой окна")
    monkeypatch.setattr(BindDialog, "exec_", broken)
    with pytest.raises(RuntimeError):
        window._ask_binding(result)
    assert window.scan_worker.released == 1


def test_no_candidates_means_no_question(window, monkeypatch):
    # У всех карты есть — эта чужая; спрашивать некого, очередь не держим.
    with Storage(window.db_path) as storage:
        for index, name in enumerate(ROSTER_NAMES):
            storage.bind_card(card(f"Em-Marine[A{index:03d}] 001,{index:05d}"), name, GROUP)
    result = unknown_mark(window)
    window._refresh_today()

    def never(dialog):
        raise AssertionError("спрашивать некого")
    monkeypatch.setattr(BindDialog, "exec_", never)
    window._ask_binding(result)
    assert window.scan_worker.released == 1


def test_rejected_card_is_shown_in_the_dialog(qapp):
    dialog = BindDialog(None, UNKNOWN.canonical, [])
    scan = Scan(code=card("Em-Marine[A100] 007,42"), at=datetime(2026, 10, 16, 9, 5))
    dialog.note_rejected(scan)
    dialog.note_rejected(scan)
    assert "НЕ ПРИНЯТА" in dialog.rejected_label.text()
    assert "Не принято карт: 2" in dialog.rejected_label.text()
    assert not dialog.rejected_label.isHidden()
