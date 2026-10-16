"""Окно привязки незнакомой карты во время сканирования.

Пока оно открыто, считыватель не принимает другие карты (gate.ScanGate).
Каждая отвергнутая карта показывается здесь же красным и со звуком:
студент должен видеть, что его не отметили, и приложить карту ещё раз.

«Оставить без привязки» есть нарочно. Без него чужая карта — преподавателя,
студента другой группы — остановила бы всю пару. Отметка карты к этому
моменту уже в базе, пропуск её не теряет: привязать можно и позже,
на вкладке «Студенты и карты».
"""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..enrollment import Candidate
from ..names import normalize_name
from ..scanner import Scan


def error_beep() -> None:
    """Низкий сигнал «не принято». Без него в очереди не слышно отказа."""
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.Beep(400, 250)
    except Exception:
        pass


class BindDialog(QDialog):
    """Кто приложил эту карту? Список — студенты предмета без карт
    (enrollment.without_card), с теми же сквозными номерами, что в консоли."""

    def __init__(self, parent: QWidget | None, code: str, candidates: list[Candidate]):
        super().__init__(parent)
        self.setWindowTitle("Неизвестная карта")
        self.setModal(True)
        self.setMinimumSize(520, 520)
        self.candidates = candidates
        self.rejected_count = 0

        layout = QVBoxLayout(self)
        title = QLabel(f"Карта {code} не привязана. Кто это?")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        title.setWordWrap(True)
        layout.addWidget(title)
        hint = QLabel(
            "Отметка этой карты уже сохранена. Пока вы выбираете, другие карты "
            "не принимаются — студенты подождут и приложат их ещё раз."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.rejected_label = QLabel("")
        self.rejected_label.setWordWrap(True)
        self.rejected_label.setStyleSheet(
            "background: #c62828; color: white; font-weight: 600; padding: 8px;"
        )
        self.rejected_label.hide()
        layout.addWidget(self.rejected_label)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ФИО, группе или номеру")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        for candidate in candidates:
            item = QListWidgetItem(
                f"{candidate.number:>3}. {candidate.full_name} — {candidate.group_name}"
            )
            item.setData(Qt.UserRole, candidate.number)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Оставить без привязки")
        self.skip_button.clicked.connect(self.reject)
        self.bind_button = QPushButton("Привязать")
        self.bind_button.setDefault(True)
        self.bind_button.clicked.connect(self.accept)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.bind_button)
        layout.addLayout(buttons)
        self._update_buttons()
        self.search.setFocus()

    def chosen(self) -> Candidate | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        number = item.data(Qt.UserRole)
        return next((c for c in self.candidates if c.number == number), None)

    def note_rejected(self, scan: Scan) -> None:
        """Пока ждали, приложили ещё карту — она не принята."""
        self.rejected_count += 1
        self.rejected_label.setText(
            f"КАРТА НЕ ПРИНЯТА ({scan.at:%H:%M:%S}). Пусть студент подождёт "
            "и приложит её ещё раз, когда это окно закроется."
            + (f"  Не принято карт: {self.rejected_count}." if self.rejected_count > 1 else "")
        )
        self.rejected_label.show()

    def accept(self) -> None:
        if self.chosen() is None:
            return  # ничего не выбрано — не закрываться молча
        super().accept()

    def _filter(self, text: str) -> None:
        needle = normalize_name(text)
        first_visible = None
        for index in range(self.list.count()):
            item = self.list.item(index)
            visible = not needle or needle in normalize_name(item.text())
            item.setHidden(not visible)
            if visible and first_visible is None:
                first_visible = item
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self.list.setCurrentItem(first_visible)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.bind_button.setEnabled(self.chosen() is not None)
