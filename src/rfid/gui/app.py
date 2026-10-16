"""Операторский интерфейс учёта посещаемости на PyQt5."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PyQt5.QtCore import QDate, Qt, QThread
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..db import MarkResult, MarkStatus, Storage
from ..excel import SubjectFolder, discover_subjects
from ..journal import import_subject
from .data import AttendanceSnapshot, choose_random_late, load_snapshot
from .worker import ScanWorker


class AttendanceWindow(QMainWindow):
    def __init__(
        self,
        db_path: Path,
        tables_path: Path,
        *,
        mode: str = "auto",
        port: str | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.tables_path = tables_path
        self.mode = mode
        self.port = port
        self.folders: list[SubjectFolder] = []
        self.today_snapshot = AttendanceSnapshot(None, date.today(), (), (), ())
        self.report_snapshot = self.today_snapshot
        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None

        self.setWindowTitle("RFID Журнал — учёт посещаемости")
        self.setMinimumSize(960, 640)
        self.resize(1100, 720)
        self._build_ui()
        self._load_subjects()

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 6)
        root_layout.setSpacing(8)
        root_layout.addWidget(self._build_topbar())

        self.pages = QTabWidget()
        self.pages.addTab(self._build_scan_page(), "Сканирование")
        self.pages.addTab(self._build_attendance_page(), "Посещаемость")
        self.pages.addTab(self._build_late_page(), "Опоздавшие")
        self.pages.currentChanged.connect(self._show_page)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        status.showMessage("Готово")
        self.setStatusBar(status)

    def _build_topbar(self) -> QWidget:
        bar = QGroupBox("Параметры занятия")
        layout = QHBoxLayout(bar)
        layout.addWidget(QLabel("Предмет:"))
        self.subject_combo = QComboBox()
        self.subject_combo.setMinimumWidth(320)
        self.subject_combo.currentIndexChanged.connect(self._subject_changed)
        layout.addWidget(self.subject_combo)
        layout.addSpacing(20)
        self.page_subtitle = QLabel(self._date_text(date.today()))
        layout.addWidget(self.page_subtitle)
        layout.addStretch(1)
        self.reader_title = QLabel("Сканирование остановлено")
        self.reader_detail = QLabel(
            self.port or ("автопоиск порта" if self.mode == "auto" else self.mode)
        )
        layout.addWidget(self.reader_title)
        layout.addWidget(QLabel("—"))
        layout.addWidget(self.reader_detail)
        return bar

    def _build_scan_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        scan_card = QGroupBox("Сканирование карт")
        scan_layout = QVBoxLayout(scan_card)
        self.hero_title = QLabel("Готово к занятию")
        self.hero_title.setAlignment(Qt.AlignCenter)
        self.hero_hint = QLabel(
            "Нажмите «Начать сканирование», затем прикладывайте карты студентов."
        )
        self.hero_hint.setAlignment(Qt.AlignCenter)
        self.hero_hint.setWordWrap(True)
        self.scan_button = QPushButton("Начать сканирование")
        self.scan_button.setMinimumSize(230, 42)
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self._toggle_scanning)
        scan_layout.addStretch(1)
        scan_layout.addWidget(self.hero_title)
        scan_layout.addWidget(self.hero_hint)
        scan_layout.addSpacing(8)
        scan_layout.addWidget(self.scan_button, 0, Qt.AlignHCenter)
        scan_layout.addStretch(1)

        last_scan = QGroupBox("Последнее сканирование")
        person_layout = QFormLayout(last_scan)
        self.person_name = QLabel("Карты ещё не сканировались")
        self.person_meta = QLabel("—")
        self.scan_status_chip = QLabel("Ожидание")
        person_layout.addRow("Студент:", self.person_name)
        person_layout.addRow("Группа и время:", self.person_meta)
        person_layout.addRow("Результат:", self.scan_status_chip)
        scan_layout.addWidget(last_scan)
        layout.addWidget(scan_card, 3)

        session_card = QGroupBox("Текущий сеанс")
        session_card.setMinimumWidth(300)
        session_card.setMaximumWidth(400)
        session_layout = QVBoxLayout(session_card)
        session_layout.addWidget(QLabel("Сегодня"))
        self.session_subject = QLabel("Выберите предмет")
        session_layout.addWidget(self.session_subject)

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        self.present_value = self._metric(metrics, "0", "пришли")
        self.late_value = self._metric(metrics, "0", "опоздали")
        self.absent_value = self._metric(metrics, "0", "не пришли")
        session_layout.addLayout(metrics)
        recent_title = QHBoxLayout()
        recent_title.addWidget(QLabel("Последние отметки"))
        recent_title.addStretch(1)
        self.unknown_label = QLabel("")
        recent_title.addWidget(self.unknown_label)
        session_layout.addLayout(recent_title)
        self.recent_list = QListWidget()
        self.recent_list.setSpacing(2)
        session_layout.addWidget(self.recent_list, 1)
        layout.addWidget(session_card, 2)
        return page

    def _build_attendance_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filters = QGroupBox("Фильтр")
        controls = QHBoxLayout(filters)
        date_label = QLabel("Дата:")
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.dateChanged.connect(self._report_date_changed)
        group_label = QLabel("Группа:")
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(190)
        self.group_combo.currentIndexChanged.connect(self._render_attendance_table)
        controls.addWidget(date_label)
        controls.addWidget(self.date_edit)
        controls.addSpacing(10)
        controls.addWidget(group_label)
        controls.addWidget(self.group_combo)
        controls.addStretch(1)
        self.attendance_summary = QLabel("Нет данных")
        controls.addWidget(self.attendance_summary)
        layout.addWidget(filters)

        self.attendance_table = QTableWidget(0, 4)
        self.attendance_table.setHorizontalHeaderLabels(("ФИО", "Группа", "Время", "Статус"))
        self.attendance_table.verticalHeader().setVisible(False)
        self.attendance_table.setAlternatingRowColors(True)
        self.attendance_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.attendance_table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.attendance_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.attendance_table, 1)
        return page

    def _build_late_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        all_card = QGroupBox("Все опоздавшие")
        all_layout = QVBoxLayout(all_card)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Опоздание считается от 10 минут после начала пары"))
        heading.addStretch(1)
        self.late_count = QLabel("0 человек")
        heading.addWidget(self.late_count)
        all_layout.addLayout(heading)
        self.late_list = QListWidget()
        all_layout.addWidget(self.late_list, 1)
        layout.addWidget(all_card, 3)

        random_card = QGroupBox("Случайный список")
        random_layout = QVBoxLayout(random_card)
        random_note = QLabel("До 10 человек из списка опоздавших без повторов")
        random_note.setWordWrap(True)
        random_layout.addWidget(random_note)
        self.random_list = QListWidget()
        random_layout.addWidget(self.random_list, 1)
        self.random_button = QPushButton("Выбрать 10 случайных")
        self.random_button.clicked.connect(self._choose_random_late)
        random_layout.addWidget(self.random_button)
        layout.addWidget(random_card, 2)
        return page

    @staticmethod
    def _metric(layout: QHBoxLayout, value: str, label: str) -> QLabel:
        frame = QGroupBox(label.capitalize())
        inner = QVBoxLayout(frame)
        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignCenter)
        inner.addWidget(value_label)
        layout.addWidget(frame)
        return value_label

    # --------------------------------------------------------------- navigation

    def _show_page(self, index: int) -> None:
        if index == 0:
            self.page_subtitle.setText(self._date_text(date.today()))
        elif index == 1:
            self.page_subtitle.setText(self._date_text(self.date_edit.date().toPyDate()))
            self._refresh_report()
        elif index == 2:
            self.page_subtitle.setText(self._date_text(self.date_edit.date().toPyDate()))
            self._refresh_report()

    def _load_subjects(self) -> None:
        self.folders = discover_subjects(self.tables_path)
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        for folder in self.folders:
            self.subject_combo.addItem(folder.name)
        self.subject_combo.blockSignals(False)
        enabled = bool(self.folders)
        self.scan_button.setEnabled(enabled)
        if enabled:
            self.subject_combo.setCurrentIndex(0)
            self._subject_changed(0)
        else:
            self.subject_combo.addItem("Предметы не найдены")
            self.subject_combo.setEnabled(False)
            self.hero_title.setText("Нет списков групп")
            self.hero_hint.setText(
                f"Создайте папку предмета в {self.tables_path} и положите туда .xlsx"
            )
            self.statusBar().showMessage("В папке tables не найдены предметы")

    def _subject_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.folders):
            return
        folder = self.folders[index]
        self.session_subject.setText(folder.name)
        self._import_roster(folder)
        self._refresh_today()
        self._refresh_report()

    def _import_roster(self, folder: SubjectFolder) -> None:
        """Добавить в БД людей и ручные отметки из таблиц без удаления данных."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with Storage(self.db_path) as storage:
                results = import_subject(storage, folder)
            errors = [result for result in results if not result.ok]
            if errors:
                self.statusBar().showMessage(
                    f"Не удалось прочитать файлов групп: {len(errors)}", 7000
                )
        except Exception as exc:
            self.statusBar().showMessage(f"Не удалось обновить список студентов: {exc}", 7000)
        finally:
            QApplication.restoreOverrideCursor()

    def _current_folder(self) -> SubjectFolder | None:
        index = self.subject_combo.currentIndex()
        return self.folders[index] if 0 <= index < len(self.folders) else None

    # --------------------------------------------------------------- scanning

    def _toggle_scanning(self) -> None:
        if self.scan_thread is not None and self.scan_thread.isRunning():
            self._stop_scanning()
        else:
            self._start_scanning()

    def _start_scanning(self) -> None:
        folder = self._current_folder()
        if folder is None:
            return
        self.subject_combo.setEnabled(False)
        self.scan_button.setText("Остановить сканирование")
        self.hero_title.setText("Сканирование запущено")
        self.hero_hint.setText("Приложите карту студента к считывателю")
        self.reader_title.setText("Подключение…")
        self.statusBar().showMessage("Подключаем считыватель…")

        thread = QThread(self)
        worker = ScanWorker(
            self.db_path,
            folder,
            mode=self.mode,
            port=self.port,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.mark_received.connect(self._mark_received)
        worker.status_changed.connect(self._scan_status)
        worker.failed.connect(self._scan_failed)
        worker.export_finished.connect(self._scan_status)
        worker.finished.connect(self._scan_finished)
        worker.finished.connect(lambda _tally: thread.quit())
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_deleted)
        self.scan_thread = thread
        self.scan_worker = worker
        thread.start()

    def _stop_scanning(self) -> None:
        if self.scan_worker is None:
            return
        self.scan_button.setEnabled(False)
        self.hero_title.setText("Завершаем занятие…")
        self.hero_hint.setText("Сохраняем отметки в файлы групп")
        self.scan_worker.stop()

    def _mark_received(self, result: MarkResult) -> None:
        self._refresh_today()
        if result.student is None:
            name = "Карта не привязана"
            meta = f"{result.code.canonical} · {result.at:%H:%M}"
            chip = "Неизвестная карта"
        else:
            name = result.student.full_name
            meta = f"Группа {result.student.group_name or 'не указана'} · {result.at:%H:%M}"
            if result.status is MarkStatus.DUPLICATE:
                first = result.first_at.strftime("%H:%M") if result.first_at else "ранее"
                chip = f"Уже отмечен · {first}"
            else:
                late = next(
                    (
                        item
                        for item in self.today_snapshot.late
                        if item.student.id == result.student.id
                    ),
                    None,
                )
                chip = f"Опоздание +{late.delay_minutes} мин" if late else "Отмечен"
        self.person_name.setText(name)
        self.person_meta.setText(meta)
        self.scan_status_chip.setText(chip)

    def _scan_status(self, message: str) -> None:
        self.reader_title.setText("Считыватель работает")
        self.reader_detail.setText(message)
        self.statusBar().showMessage(message)

    def _scan_failed(self, message: str) -> None:
        self.reader_title.setText("Ошибка считывателя")
        self.reader_detail.setText("Проверьте подключение")
        self.statusBar().showMessage(message, 12000)
        QMessageBox.warning(self, "Сканирование не запущено", message)

    def _scan_finished(self, _tally: object) -> None:
        self.subject_combo.setEnabled(True)
        self.scan_button.setEnabled(bool(self.folders))
        self.scan_button.setText("Начать сканирование")
        self.hero_title.setText("Готово к занятию")
        self.hero_hint.setText("Нажмите «Начать сканирование» и прикладывайте карты")
        self.reader_title.setText("Сканирование остановлено")
        self._refresh_today()
        self._refresh_report()

    def _thread_deleted(self) -> None:
        self.scan_thread = None
        self.scan_worker = None

    # ---------------------------------------------------------------- reports

    def _refresh_today(self) -> None:
        folder = self._current_folder()
        if folder is None:
            return
        try:
            self.today_snapshot = load_snapshot(
                self.db_path, folder.name, date.today(), folder
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Не удалось обновить отчёт: {exc}", 7000)
            return
        snapshot = self.today_snapshot
        self.present_value.setText(str(snapshot.present))
        self.late_value.setText(str(len(snapshot.late)))
        self.absent_value.setText(str(snapshot.absent))
        self.unknown_label.setText(
            f"неизвестных: {snapshot.unknown_count}" if snapshot.unknown_count else ""
        )
        self.recent_list.clear()
        recent = sorted(
            (row for row in snapshot.rows if row.at is not None),
            key=lambda row: row.at,
            reverse=True,
        )[:8]
        for row in recent:
            self.recent_list.addItem(
                f"{row.at:%H:%M}   {row.student.full_name}\n"
                f"             {row.student.group_name}"
            )
        if not recent:
            item = QListWidgetItem("Отметок сегодня пока нет")
            item.setForeground(QColor("#7b8698"))
            self.recent_list.addItem(item)

    def _report_date_changed(self, value: QDate) -> None:
        self.page_subtitle.setText(self._date_text(value.toPyDate()))
        self._refresh_report()

    def _refresh_report(self) -> None:
        folder = self._current_folder()
        if folder is None:
            return
        day = self.date_edit.date().toPyDate()
        try:
            self.report_snapshot = load_snapshot(self.db_path, folder.name, day, folder)
        except Exception as exc:
            self.statusBar().showMessage(f"Не удалось открыть журнал: {exc}", 7000)
            return

        selected_group = self.group_combo.currentText()
        groups = sorted({row.student.group_name for row in self.report_snapshot.rows})
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("Все группы")
        self.group_combo.addItems(groups)
        if selected_group in groups:
            self.group_combo.setCurrentText(selected_group)
        self.group_combo.blockSignals(False)
        self._render_attendance_table()
        self._render_late()

    def _render_attendance_table(self) -> None:
        group = self.group_combo.currentText()
        rows = [
            row for row in self.report_snapshot.rows
            if group in ("", "Все группы") or row.student.group_name == group
        ]
        late_by_id = {item.student.id: item for item in self.report_snapshot.late}
        self.attendance_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            late = late_by_id.get(row.student.id)
            if row.at is None:
                values = (row.student.full_name, row.student.group_name, "—", "Не пришёл")
                color = QColor("#9a4250")
            elif late is not None:
                values = (
                    row.student.full_name,
                    row.student.group_name,
                    row.at.strftime("%H:%M"),
                    f"Опоздал +{late.delay_minutes} мин",
                )
                color = QColor("#9b5e08")
            else:
                values = (
                    row.student.full_name,
                    row.student.group_name,
                    row.at.strftime("%H:%M"),
                    "Вовремя",
                )
                color = QColor("#16734a")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setForeground(color)
                self.attendance_table.setItem(row_index, column, item)
        present = sum(row.at is not None for row in rows)
        percent = present / len(rows) * 100 if rows else 0
        self.attendance_summary.setText(f"Пришли {present} из {len(rows)} · {percent:.0f}%")
        self.attendance_table.resizeRowsToContents()

    def _render_late(self) -> None:
        self.late_list.clear()
        self.random_list.clear()
        for index, item in enumerate(self.report_snapshot.late, 1):
            self.late_list.addItem(
                f"{index}.  {item.student.full_name}\n"
                f"     {item.student.group_name} · {item.arrived_at:%H:%M} · "
                f"+{item.delay_minutes} мин"
            )
        count = len(self.report_snapshot.late)
        self.late_count.setText(self._people_text(count))
        self.random_button.setEnabled(count > 0)
        self.random_button.setText("Выбрать 10 случайных")
        if not count:
            item = QListWidgetItem("В этот день опоздавших нет")
            item.setForeground(QColor("#7b8698"))
            self.late_list.addItem(item)

    def _choose_random_late(self) -> None:
        chosen = choose_random_late(self.report_snapshot.late)
        self.random_list.clear()
        for index, item in enumerate(chosen, 1):
            self.random_list.addItem(
                f"{index}.  {item.student.full_name}\n"
                f"     {item.student.group_name} · +{item.delay_minutes} мин"
            )
        self.random_button.setText(
            "Выбрать заново" if len(self.report_snapshot.late) > 10 else "Показаны все"
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _date_text(day: date) -> str:
        months = (
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря",
        )
        weekdays = (
            "Понедельник", "Вторник", "Среда", "Четверг",
            "Пятница", "Суббота", "Воскресенье",
        )
        return f"{day.day} {months[day.month - 1]} {day.year} · {weekdays[day.weekday()]}"

    @staticmethod
    def _people_text(count: int) -> str:
        if 11 <= count % 100 <= 14:
            word = "человек"
        elif count % 10 == 1:
            word = "человек"
        elif count % 10 in (2, 3, 4):
            word = "человека"
        else:
            word = "человек"
        return f"{count} {word}"

    def closeEvent(self, event) -> None:  # noqa: N802 - имя Qt API
        if self.scan_thread is not None and self.scan_thread.isRunning():
            answer = QMessageBox.question(
                self,
                "Завершить сканирование?",
                "Сеанс ещё идёт. Остановить его, сохранить отметки и закрыть окно?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            if self.scan_worker is not None:
                self.scan_worker.stop()
            self.scan_thread.quit()
            if not self.scan_thread.wait(12_000):
                QMessageBox.warning(
                    self,
                    "Считыватель ещё завершается",
                    "Не удалось быстро остановить считыватель. Попробуйте закрыть окно ещё раз.",
                )
                event.ignore()
                return
        event.accept()


def run_gui(
    db_path: str | Path,
    tables_path: str | Path,
    *,
    mode: str = "auto",
    port: str | None = None,
) -> int:
    app = QApplication.instance()
    if app is None:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(sys.argv[:1])
    app.setApplicationName("RFID Журнал")
    window = AttendanceWindow(Path(db_path), Path(tables_path), mode=mode, port=port)
    window.show()
    return app.exec_()
