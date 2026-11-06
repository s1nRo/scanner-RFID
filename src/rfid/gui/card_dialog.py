"""Окно «Заменить карту»: новая карта для выбранного студента.

Откуда взять новую карту:
  - приложить к считывателю (если он не занят сканированием);
  - выбрать из неизвестных — её уже прикладывали, но не привязали;
  - ввести код руками (строка считывателя или 10 цифр) — на крайний случай.

Показываем короткий код (007,42), но в нём нет префикса: выбранная или
прочитанная карта хранится полным кодом в _picked, а не берётся из поля.

Окно только выбирает карту. Саму замену делает Storage.replace_card.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import Event

from PyQt5.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..db import Student, UnknownCard
from ..scanner import CardCodeError, ReaderUnavailable, make_reader, parse_manual, pretty_code


class CardGrabber(QObject):
    """Прочитать одну карту со считывателя вне GUI-потока."""

    got = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, mode: str, port: str | None) -> None:
        super().__init__()
        self.mode = mode
        self.port = port
        self._stop = Event()

    def stop(self) -> None:
        self._stop.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            reader = make_reader(self.mode, port=self.port, should_stop=self._stop.is_set)
            for scan in reader.scans():
                self.got.emit(scan.code.canonical)
                break
        except ReaderUnavailable as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — показать, а не уронить окно
            self.failed.emit(f"Считыватель: {exc}")
        finally:
            self.finished.emit()


def _unknown_text(item: UnknownCard) -> str:
    return (f"{pretty_code(item.card_code)}   прикладывали {item.times} раз, "
            f"последний {item.last_seen:%d.%m %H:%M}")


class ReplaceCardDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        student: Student,
        unknown: Sequence[UnknownCard],
        *,
        mode: str = "auto",
        port: str | None = None,
        reader_free: bool = True,
    ) -> None:
        super().__init__(parent)
        self.student = student
        self.code: str | None = None
        self._picked: str | None = None
        self.mode = mode
        self.port = port
        self._thread: QThread | None = None
        self._grabber: CardGrabber | None = None

        self.setWindowTitle("Замена карты")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        current = pretty_code(student.card_code) if student.card_code else "нет"
        title = QLabel(f"<b>{student.full_name}</b> ({student.group_name})<br>"
                       f"Сейчас карта: {current}")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        read_row = QHBoxLayout()
        self.read_button = QPushButton("Приложить новую карту к считывателю")
        self.read_button.clicked.connect(self._start_reading)
        self.read_button.setEnabled(reader_free)
        if not reader_free:
            self.read_button.setToolTip(
                "Считыватель занят сканированием. Остановите его или выберите "
                "карту из неизвестных ниже."
            )
        read_row.addWidget(self.read_button)
        read_row.addStretch(1)
        layout.addLayout(read_row)

        layout.addWidget(QLabel("Или неизвестная карта — её прикладывали, но не привязали:"))
        self.unknown_list = QListWidget()
        for item in unknown:
            row = QListWidgetItem(_unknown_text(item))
            row.setData(Qt.UserRole, item.card_code)
            self.unknown_list.addItem(row)
        if not unknown:
            self.unknown_list.addItem("Неизвестных карт нет")
            self.unknown_list.setEnabled(False)
        self.unknown_list.currentItemChanged.connect(self._unknown_chosen)
        layout.addWidget(self.unknown_list, 1)

        code_row = QHBoxLayout()
        code_row.addWidget(QLabel("Новая карта:"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("приложите карту или выберите выше")
        code_row.addWidget(self.code_edit, 1)
        layout.addLayout(code_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Заменить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -------------------------------------------------------------- выбор карты

    def _unknown_chosen(self, item: QListWidgetItem | None, _previous=None) -> None:
        code = item.data(Qt.UserRole) if item is not None else None
        if code:
            self._pick(code)

    def _pick(self, code: str) -> None:
        self._picked = code
        self.code_edit.setText(pretty_code(code))

    def _start_reading(self) -> None:
        if self._thread is not None:
            return
        self.read_button.setEnabled(False)
        self._say("Приложите новую карту к считывателю…")
        thread = QThread(self)
        grabber = CardGrabber(self.mode, self.port)
        grabber.moveToThread(thread)
        thread.started.connect(grabber.run)
        grabber.got.connect(self._card_read)
        grabber.failed.connect(lambda text: self._say(text, error=True))
        grabber.finished.connect(thread.quit)
        grabber.finished.connect(grabber.deleteLater)
        thread.finished.connect(self._reading_done)
        self._thread, self._grabber = thread, grabber
        thread.start()

    def _card_read(self, code: str) -> None:
        self._pick(code)
        self._say(f"Прочитана карта {pretty_code(code)}. Нажмите «Заменить».")

    def _reading_done(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = self._grabber = None
        self.read_button.setEnabled(True)

    def _stop_reading(self) -> None:
        """Отпустить порт: окно закрывается, а чтение ещё ждёт карту."""
        if self._grabber is not None:
            self._grabber.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)

    def _say(self, text: str, *, error: bool = False) -> None:
        self.status.setStyleSheet("color: #b00020;" if error else "")
        self.status.setText(text)

    # -------------------------------------------------------------- итог

    def _accept(self) -> None:
        text = self.code_edit.text().strip()
        if self._picked is not None and text == pretty_code(self._picked):
            code = self._picked
        else:
            try:
                code = parse_manual(text).canonical
            except CardCodeError as exc:
                self._say(f"Не похоже на код карты: {exc}", error=True)
                return
        if code == self.student.card_code:
            self._say("Это та же самая карта, что уже привязана.", error=True)
            return
        self.code = code
        self._stop_reading()
        self.accept()

    def reject(self) -> None:
        self._stop_reading()
        super().reject()
