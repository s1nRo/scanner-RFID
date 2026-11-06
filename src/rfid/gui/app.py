"""Операторский интерфейс учёта посещаемости на PyQt5."""

from __future__ import annotations

import shutil
import stat
import sys
import tempfile
import threading
import traceback
from datetime import date, datetime
from pathlib import Path

from PyQt5.QtCore import QDate, QDateTime, Qt, QThread, QUrl
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QFileDialog,
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

from .. import enrollment, workdir
from ..db import (
    MANUAL_RAW,
    CardConflict,
    CardTaken,
    ImportStatus,
    MarkResult,
    MarkStatus,
    ReplacePreview,
    Storage,
    Student,
)
from ..enrollment import Candidate
from ..excel import RosterError, SubjectFolder, discover_subjects
from ..journal import (
    fill_all,
    fill_problems,
    fill_subject,
    import_subject,
    renamed_groups,
    retract_card_marks,
    unbind_card,
)
from ..scanner import driver, find_reader_port, pretty_code
from ..workdir import DB_FILE, TABLES_DIR
from .binding import BindDialog, error_beep
from .card_dialog import ReplaceCardDialog
from .data import AttendanceSnapshot, cached_roster, choose_random_late, load_snapshot
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
        self.pending_cards: list[str] = []
        self.session_problems: list[str] = []
        self._reader_is_lost = False
        # Оператор сказал, что это не те же люди, — не спрашивать снова за сеанс.
        self._declined_moves: set[int] = set()
        # Поле времени не трогали: отметка ставится на «сейчас», а не на момент,
        # когда окно открылось. Окно живёт часами, а иногда и сутками.
        self._manual_time_touched = False
        self.bind_dialog: BindDialog | None = None
        # Драйвер поставили из окна — запустить сканирование, когда поток закроется.
        self._restart_scanning = False
        # Идёт установка драйвера: второе нажатие не должно звать второй UAC.
        self._installing_driver = False

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
        self.students_page = self._build_students_page()
        self.pages.addTab(self.students_page, "Студенты и карты")
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
        self.workspace_button = QPushButton("Папка данных…")
        self.workspace_button.setToolTip(str(self.tables_path.parent))
        self.workspace_button.clicked.connect(self._choose_workspace)
        # На флешке папку задаёт метка: выбор продержался бы только до закрытия,
        # и отметки сеанса «пропали» бы при следующем запуске.
        self.workspace_button.setVisible(workdir.portable_folder() is None)
        layout.addWidget(self.workspace_button)
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

    def _build_students_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        roster_controls = QHBoxLayout()
        roster_controls.addWidget(QLabel("Таблица группы:"))
        self.roster_file_combo = QComboBox()
        self.roster_file_combo.setMinimumWidth(260)
        roster_controls.addWidget(self.roster_file_combo, 1)
        self.view_roster_button = QPushButton("Просмотр в Excel")
        self.view_roster_button.setToolTip(
            "Открыть отдельную копию: рабочая таблица останется свободной для записи"
        )
        self.view_roster_button.clicked.connect(self._open_roster_readonly)
        roster_controls.addWidget(self.view_roster_button)
        self.edit_roster_button = QPushButton("Редактировать в Excel")
        self.edit_roster_button.clicked.connect(self._open_roster_for_editing)
        roster_controls.addWidget(self.edit_roster_button)
        self.reload_roster_button = QPushButton("Загрузить изменения")
        self.reload_roster_button.clicked.connect(self._reload_rosters)
        roster_controls.addWidget(self.reload_roster_button)
        self.write_tables_button = QPushButton("Записать в таблицы")
        self.write_tables_button.setToolTip(
            "Записать отметки из базы в файлы групп всех предметов. Нужно, если при "
            "остановке файл был открыт в Excel: закройте его и нажмите эту кнопку."
        )
        self.write_tables_button.clicked.connect(self._write_tables)
        roster_controls.addWidget(self.write_tables_button)
        layout.addLayout(roster_controls)

        self.students_table = QTableWidget(0, 4)
        self.students_table.setHorizontalHeaderLabels(
            ("ФИО", "Группа", "Карта", "Отметка сегодня")
        )
        self.students_table.verticalHeader().setVisible(False)
        self.students_table.setAlternatingRowColors(True)
        self.students_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.students_table.setSelectionMode(QTableWidget.SingleSelection)
        self.students_table.setEditTriggers(QTableWidget.NoEditTriggers)
        students_header = self.students_table.horizontalHeader()
        students_header.setSectionResizeMode(0, QHeaderView.Stretch)
        students_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        students_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        students_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.students_table, 1)

        manual_group = QGroupBox("Ручная отметка выбранного студента")
        manual_layout = QHBoxLayout(manual_group)
        manual_layout.addWidget(QLabel("Дата и время:"))
        self.manual_datetime = QDateTimeEdit(QDateTime.currentDateTime())
        self.manual_datetime.setCalendarPopup(True)
        self.manual_datetime.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.manual_datetime.dateTimeChanged.connect(self._manual_time_edited)
        manual_layout.addWidget(self.manual_datetime)
        self.manual_mark_button = QPushButton("Поставить отметку")
        self.manual_mark_button.clicked.connect(self._mark_selected_student)
        manual_layout.addWidget(self.manual_mark_button)
        manual_layout.addStretch(1)
        layout.addWidget(manual_group)

        card_group = QGroupBox("Карта выбранного студента")
        card_layout = QHBoxLayout(card_group)
        self.replace_card_button = QPushButton("Заменить карту…")
        self.replace_card_button.setToolTip(
            "Потерял карту или выдали новую: прошлые отметки останутся за студентом.\n"
            "Привязали чужую карту по ошибке — это «Снять карту», иначе её отметки "
            "останутся за этим студентом."
        )
        self.replace_card_button.clicked.connect(self._replace_card_of_selected)
        card_layout.addWidget(self.replace_card_button)
        self.unbind_card_button = QPushButton("Снять карту")
        self.unbind_card_button.setToolTip(
            "Студент останется без карты; отметки этой картой вернутся в неизвестные "
            "и уйдут из файлов групп. Так исправляют ошибочную привязку; если перепутаны "
            "две карты — снимите обе и привяжите заново."
        )
        self.unbind_card_button.clicked.connect(self._unbind_card_of_selected)
        card_layout.addWidget(self.unbind_card_button)
        card_layout.addStretch(1)
        layout.addWidget(card_group)

        binding_group = QGroupBox("Привязка неизвестной карты")
        binding_layout = QVBoxLayout(binding_group)
        binding_controls = QHBoxLayout()
        binding_controls.addWidget(QLabel("Карта:"))
        self.pending_card_combo = QComboBox()
        self.pending_card_combo.setMinimumWidth(180)
        binding_controls.addWidget(self.pending_card_combo)
        self.bind_card_button = QPushButton("Привязать к выбранному студенту")
        self.bind_card_button.clicked.connect(self._bind_card_to_selected_student)
        binding_controls.addWidget(self.bind_card_button)
        binding_controls.addStretch(1)
        binding_layout.addLayout(binding_controls)
        self.pending_cards_label = QLabel("Нет непривязанных карт в текущем сеансе")
        binding_layout.addWidget(self.pending_cards_label)
        layout.addWidget(binding_group)
        self._refresh_pending_cards()
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

    def _manual_time_edited(self, _value: QDateTime) -> None:
        self._manual_time_touched = True

    def _reset_manual_time(self) -> None:
        self.manual_datetime.blockSignals(True)
        self.manual_datetime.setDateTime(QDateTime.currentDateTime())
        self.manual_datetime.blockSignals(False)
        self._manual_time_touched = False

    def _show_page(self, index: int) -> None:
        if self.pages.widget(index) is self.students_page and not self._manual_time_touched:
            self._reset_manual_time()
        if index == 0:
            self.page_subtitle.setText(self._date_text(date.today()))
        elif index == 1:
            self.page_subtitle.setText(self._date_text(self.date_edit.date().toPyDate()))
            self._refresh_report()
        elif index == 2:
            self.page_subtitle.setText(self._date_text(self.date_edit.date().toPyDate()))
            self._refresh_report()
        elif index == 3:
            self.page_subtitle.setText(self._date_text(date.today()))
            self._refresh_today()

    def _load_subjects(self) -> None:
        self.folders = discover_subjects(self.tables_path)
        self.workspace_button.setToolTip(str(self.tables_path.parent))
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        self.subject_combo.setEnabled(True)
        for folder in self.folders:
            self.subject_combo.addItem(folder.name)
        self.subject_combo.blockSignals(False)
        enabled = bool(self.folders)
        self.scan_button.setEnabled(enabled)
        if enabled:
            self.hero_title.setText("Готово к занятию")
            self.hero_hint.setText(
                "Нажмите «Начать сканирование», затем прикладывайте карты студентов."
            )
            self.subject_combo.setCurrentIndex(0)
            self._subject_changed(0)
        else:
            self._refresh_roster_files(None)
            self.subject_combo.addItem("Предметы не найдены")
            self.subject_combo.setEnabled(False)
            self.hero_title.setText("Нет списков групп")
            self.hero_hint.setText(
                f"Создайте папку предмета в {self.tables_path}, положите туда .xlsx и нажмите "
                "«Загрузить изменения» на вкладке «Студенты и карты»"
            )
            self.statusBar().showMessage("В папке tables не найдены предметы")

    def _subject_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.folders):
            return
        folder = self.folders[index]
        self._refresh_roster_files(folder)
        self.pending_cards.clear()
        self._refresh_pending_cards()
        self.session_subject.setText(folder.name)
        self._import_roster(folder)
        self._offer_group_moves()
        self._refresh_today()
        self._refresh_report()

    def _choose_workspace(self) -> None:
        if self._scanning_active():
            QMessageBox.information(
                self,
                "Сканирование запущено",
                "Сначала остановите сканирование, затем смените папку данных.",
            )
            return
        home = pick_home(self, self.tables_path.parent)
        if home is None:
            return
        self.db_path = home / DB_FILE
        self.tables_path = home / TABLES_DIR
        self.pending_cards.clear()
        self._declined_moves.clear()
        self._load_subjects()
        self.statusBar().showMessage(f"Папка данных: {home}", 8000)

    def _refresh_roster_files(self, folder: SubjectFolder | None) -> None:
        if not hasattr(self, "roster_file_combo"):
            return
        self.roster_file_combo.clear()
        if folder is not None:
            for path in folder.rosters:
                try:
                    group_name = cached_roster(path).group_name
                except (RosterError, OSError):
                    group_name = path.stem
                self.roster_file_combo.addItem(
                    f"{group_name} — {path.name}", str(path)
                )
        enabled = self.roster_file_combo.count() > 0
        self.roster_file_combo.setEnabled(enabled)
        self.view_roster_button.setEnabled(enabled)
        self.edit_roster_button.setEnabled(enabled)
        # Без предметов кнопка особенно нужна: так видна только что созданная папка.
        self.reload_roster_button.setEnabled(True)
        self.write_tables_button.setEnabled(folder is not None)

    def _selected_roster_path(self) -> Path | None:
        value = self.roster_file_combo.currentData()
        return Path(value) if value else None

    def _open_file(self, path: Path, title: str) -> bool:
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            return True
        QMessageBox.warning(
            self,
            title,
            "Windows не смог открыть файл. Проверьте, что Excel установлен "
            "и связан с файлами .xlsx.",
        )
        return False

    def _open_roster_readonly(self) -> None:
        source = self._selected_roster_path()
        if source is None:
            return
        try:
            preview_dir = Path(tempfile.mkdtemp(prefix="rfid-roster-preview-"))
            preview = preview_dir / f"Просмотр — {source.name}"
            shutil.copy2(source, preview)
            preview.chmod(stat.S_IREAD)
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось открыть таблицу", str(exc))
            return
        if self._open_file(preview, "Не удалось открыть копию"):
            self.statusBar().showMessage(
                "Открыта копия для просмотра; рабочая таблица не заблокирована.",
                8000,
            )

    def _open_roster_for_editing(self) -> None:
        source = self._selected_roster_path()
        if source is None:
            return
        if self._scanning_active():
            QMessageBox.information(
                self,
                "Сканирование запущено",
                "Остановите сканирование перед редактированием списка.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Редактировать рабочую таблицу?",
            "Excel откроет оригинал файла. Можно добавить студента без карты — "
            "после сохранения закройте Excel и нажмите «Загрузить изменения».\n\n"
            "Не меняйте ФИО студента с уже привязанной картой: связь карты "
            "хранится по ФИО. Для обычного просмотра используйте соседнюю кнопку.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self._open_file(source, "Не удалось открыть таблицу"):
            self.statusBar().showMessage(
                "После редактирования закройте Excel и нажмите «Загрузить изменения».",
                12_000,
            )

    def _reload_rosters(self) -> None:
        if self._scanning_active():
            QMessageBox.information(
                self,
                "Сканирование запущено",
                "Остановите сканирование перед загрузкой изменённого списка.",
            )
            return
        subject_name = self.subject_combo.currentText()
        refreshed = discover_subjects(self.tables_path)
        folder = next((item for item in refreshed if item.name == subject_name), None)
        if folder is None:
            self._load_subjects()
            return
        self.folders = refreshed
        # Список предметов обязан совпасть с папками: новый предмет должен быть
        # виден, а выбор остаётся тем же предметом, на какой бы строке он ни оказался.
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        self.subject_combo.addItems([item.name for item in refreshed])
        self.subject_combo.setCurrentText(subject_name)
        self.subject_combo.blockSignals(False)
        self._refresh_roster_files(folder)
        problems = self._import_roster(folder)
        self._offer_group_moves()
        self._refresh_today()
        self._refresh_report()
        if problems:
            QMessageBox.warning(self, "Загружено не всё", "\n\n".join(problems))
        else:
            self.statusBar().showMessage(
                "Изменения загружены. Новые студенты добавлены без карты.", 8000
            )

    def _import_roster(self, folder: SubjectFolder) -> list[str]:
        """Добавить в БД людей и ручные отметки из таблиц без удаления данных.

        Вернуть, что сказать человеку: какие файлы не прочитались и какие ячейки
        не поняты. Такие ячейки не угадываем: студент числится отсутствующим.
        """
        problems: list[str] = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with Storage(self.db_path) as storage:
                results = import_subject(storage, folder)
            for result in results:
                if not result.ok:
                    problems.append(f"{result.path.name}: {result.error}")
                problems += [
                    f"{result.path.name}: не понял ячейку «{value}» ({where}), "
                    "оставлена как есть, отметка не поставлена"
                    for where, value in result.skipped
                ]
            if problems:
                self.statusBar().showMessage(
                    f"Вопросов по файлам групп: {len(problems)}. "
                    "Подробности — кнопка «Загрузить изменения».", 7000
                )
        except Exception as exc:
            problems.append(f"Не удалось обновить список студентов: {exc}")
            self.statusBar().showMessage(problems[-1], 7000)
        finally:
            QApplication.restoreOverrideCursor()
        return problems

    def _offer_group_moves(self) -> None:
        """Группу в файлах переименовали: спросить, те же ли это люди.

        Сами не переносим: человек с тем же ФИО может оказаться тёзкой.
        Без переноса он остаётся без карты и отметок в окне, а в файле — прочерк.
        """
        try:
            with Storage(self.db_path) as storage:
                moves = [move for move in renamed_groups(storage, self.tables_path, cached_roster)
                         if move.student.id not in self._declined_moves]
                if not moves:
                    return
                lines = "\n".join(
                    f"{move.student.full_name}: «{move.student.group_name}» → «{move.new_group}»"
                    for move in moves[:15]
                )
                more = f"\n… и ещё {len(moves) - 15}" if len(moves) > 15 else ""
                answer = QMessageBox.question(
                    self,
                    "Изменилось название группы?",
                    f"Этих людей больше нет в их группе, но в файлах есть то же ФИО "
                    f"в другой:\n\n{lines}{more}\n\nЭто те же люди? Перенести их вместе "
                    "с картами и отметками?\n\nЕсли это тёзки, нажмите «Нет».",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    self._declined_moves.update(move.student.id for move in moves)
                    return
                failed = []
                for move in moves:
                    try:
                        storage.move_to_group(move.student.id, move.new_group)
                    except CardConflict as exc:
                        failed.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — сказать, не терять молча
            QMessageBox.warning(self, "Группа не перенесена", str(exc))
            return
        if failed:
            QMessageBox.warning(self, "Перенесены не все", "\n".join(failed))

    def _current_folder(self) -> SubjectFolder | None:
        # По имени, а не по номеру строки: отметки идут в тот предмет, что на экране.
        name = self.subject_combo.currentText()
        return next((folder for folder in self.folders if folder.name == name), None)

    # --------------------------------------------------------------- scanning

    def _toggle_scanning(self) -> None:
        if self._installing_driver:
            self.statusBar().showMessage("Дождитесь окончания установки драйвера", 5000)
            return
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
        worker.rejected.connect(self._card_rejected)
        worker.not_saved.connect(self._mark_not_saved)
        worker.reader_lost.connect(self._reader_lost)
        worker.reader_ready.connect(self._reader_ready)
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
        self.session_problems = worker.problems
        thread.start()

    def _stop_scanning(self) -> None:
        if self.scan_worker is None:
            return
        self.scan_button.setEnabled(False)
        self.hero_title.setText("Завершаем занятие…")
        self.hero_hint.setText("Сохраняем отметки в файлы групп")
        self.scan_worker.stop()

    def _mark_not_saved(self, message: str) -> None:
        """Отметка не записалась: прошлый «Отмечен» на экране оставлять нельзя."""
        error_beep()
        self.person_name.setStyleSheet("color: #b00020; font-weight: 600;")
        self.person_name.setText("НЕ ЗАПИСАНО")
        self.person_meta.setText(message)
        self.scan_status_chip.setText("Пусть студент приложит карту ещё раз")
        self.statusBar().showMessage(message)

    def _show_session_problems(self) -> None:
        """Сказать о потерянных отметках и незаписанных файлах один раз."""
        problems, self.session_problems = self.session_problems, []
        if problems:
            QMessageBox.warning(
                self,
                "Занятие завершено с ошибками",
                "\n\n".join(problems) + "\n\nНезаписанных студентов отметьте вручную на "
                "вкладке «Студенты и карты». Когда файлы освободятся, нажмите там же "
                "«Записать в таблицы».",
            )

    def _mark_received(self, result: MarkResult) -> None:
        self.person_name.setStyleSheet("")
        self._refresh_today()
        if result.student is None:
            name = "Карта не привязана"
            meta = f"{result.code.pretty} · {result.at:%H:%M}"
            chip = "Неизвестная карта"
            if result.code.canonical not in self.pending_cards:
                self.pending_cards.append(result.code.canonical)
                self._refresh_pending_cards()
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
        if result.student is None:
            self._ask_binding(result)

    def _ask_binding(self, result: MarkResult) -> None:
        """Незнакомая карта: привязать сразу, как в меню 2 консоли.

        Считыватель уже не принимает карты (ScanWorker закрыл заслонку)
        и откроется только здесь, в finally, — что бы ни случилось с окном.
        """
        try:
            candidates = self._candidates_without_card()
            if not candidates:
                self.statusBar().showMessage(
                    "У всех студентов предмета карта уже есть — эта, видимо, чужая. "
                    "Отметка сохранена.", 10_000,
                )
                return
            self._set_waiting(True)
            while True:
                dialog = BindDialog(self, result.code.canonical, candidates)
                self.bind_dialog = dialog
                try:
                    accepted = dialog.exec_() == BindDialog.Accepted
                finally:
                    self.bind_dialog = None
                chosen = dialog.chosen() if accepted else None
                if chosen is None:
                    self.statusBar().showMessage(
                        "Карта оставлена без привязки, отметка сохранена. "
                        "Привязать можно на вкладке «Студенты и карты».", 10_000,
                    )
                    return
                saved = self._bind_code(result.code.canonical, chosen.full_name,
                                        chosen.group_name, includes_current=True)
                if saved is not None:
                    self.person_name.setText(saved.full_name)
                    self.person_meta.setText(
                        f"Группа {saved.group_name} · {result.at:%H:%M}"
                    )
                    self.scan_status_chip.setText("Карта привязана, отметка сохранена")
                    return
                # Привязка не удалась (у студента уже есть карта и т. п.) —
                # предупреждение показано, спрашиваем снова.
        finally:
            self._set_waiting(False)
            if self.scan_worker is not None:
                self.scan_worker.release()
            self._refresh_today()

    def _card_rejected(self, scan) -> None:
        """Карту приложили, пока ждём привязки, — громко сказать, что не принята."""
        error_beep()
        if self.bind_dialog is not None:
            self.bind_dialog.note_rejected(scan)
        self.statusBar().showMessage(
            f"Карта {scan.code.pretty} не принята: сначала привяжите предыдущую. "
            "Пусть студент приложит её ещё раз.", 10_000,
        )

    def _set_waiting(self, waiting: bool) -> None:
        if waiting:
            self.hero_title.setText("Ждём привязку карты")
            self.hero_hint.setText(
                "Другие карты сейчас не принимаются. Выберите студента в окне привязки."
            )
        elif self._scanning_active():
            self.hero_title.setText("Сканирование запущено")
            self.hero_hint.setText("Приложите карту студента к считывателю")

    def _candidates_without_card(self) -> list[Candidate]:
        """Кому можно привязать карту — по тому же правилу, что в консоли."""
        folder = self._current_folder()
        if folder is None:
            return []
        found = enrollment.read_candidates(folder, read=cached_roster)
        with Storage(self.db_path) as storage:
            return enrollment.without_card(found.items, storage)

    def _bind_code(
        self, code: str, full_name: str, group_name: str, *, includes_current: bool
    ) -> Student | None:
        """Привязать карту (enrollment.bind). Возвращает студента или None, показав причину."""
        try:
            with Storage(self.db_path) as storage:
                bound = enrollment.bind(storage, code, full_name, group_name,
                                        includes_current=includes_current)
        except CardConflict as exc:
            QMessageBox.warning(
                self, "Не удалось привязать карту",
                f"Не привязано: {exc}. Чтобы выдать ему другую карту, выберите "
                "его во вкладке «Студенты и карты» и нажмите «Заменить карту…».",
            )
            return None
        except Exception as exc:  # что угодно — сказать, не терять молча
            QMessageBox.warning(self, "Не удалось привязать карту", str(exc))
            return None
        if code in self.pending_cards:
            self.pending_cards.remove(code)
            self._refresh_pending_cards()
        past = f" Прошлых отметок зачтено: {bound.past}." if bound.past > 0 else ""
        self.statusBar().showMessage(
            f"Привязано: {bound.student.full_name} ({bound.student.group_name}).{past}",
            8000,
        )
        return bound.student

    def _scan_status(self, message: str) -> None:
        self.reader_detail.setText(message)
        self.statusBar().showMessage(message)

    def _reader_ready(self, message: str) -> None:
        self._reader_is_lost = False
        self.reader_title.setStyleSheet("")
        self.reader_title.setText("Считыватель работает")
        self.hero_title.setText("Сканирование запущено")
        self.hero_hint.setText("Приложите карту студента к считывателю")
        self._scan_status(message)

    def _reader_lost(self, message: str) -> None:
        """Связи нет, карты не читаются: студенты не должны уходить, думая, что отмечены."""
        if not self._reader_is_lost:
            error_beep()
        self._reader_is_lost = True
        self.reader_title.setStyleSheet("color: #b00020; font-weight: 600;")
        self.reader_title.setText("Считыватель отключён")
        self.hero_title.setText("Считыватель отключён")
        self.hero_hint.setText(
            "Карты сейчас не читаются. Проверьте кабель: связь восстановится сама."
        )
        self._scan_status(message)

    def _scan_failed(self, message: str) -> None:
        self.reader_title.setText("Ошибка считывателя")
        self.reader_detail.setText("Проверьте подключение")
        self.statusBar().showMessage(message, 12000)
        if self._offer_driver():
            return
        QMessageBox.warning(self, "Сканирование не запущено", message)

    def _offer_driver(self) -> bool:
        """Считыватель воткнут, а драйвера нет — предложить поставить его с флешки.

        True — человеку уже всё показано, обычное «порт не найден» не нужно.
        """
        if sys.platform != "win32":
            return False
        try:
            nodes = driver.WindowsBackend().reader_nodes()
        except Exception:
            return False
        if not any(node.needs_driver for node in nodes):
            return False
        package = driver.find_package(driver.default_bases(self.db_path.parent.parent))
        if package is None:
            QMessageBox.warning(
                self,
                "Нет драйвера считывателя",
                "Считыватель подключён, но драйвер для него на этом компьютере "
                "не установлен.\n\nПоложите папку driver (из архива "
                "ironlogic-driver-*.zip) рядом с программой и нажмите "
                "«Начать сканирование» ещё раз.",
            )
            return True
        answer = QMessageBox.question(
            self,
            "Установить драйвер считывателя?",
            "Считыватель подключён, но драйвер для него на этом компьютере "
            f"не установлен.\n\nУстановить его из папки\n{package}?\n\n"
            "Windows попросит права администратора. Это нужно один раз "
            "на каждом компьютере.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return True
        self.statusBar().showMessage("Ставим драйвер считывателя…")
        executable, args = driver.elevated_command(package)
        outcome: dict[str, object] = {}

        def work() -> None:
            try:
                outcome["code"] = driver.run_elevated(executable, args)
            except Exception as exc:  # noqa: BLE001 — показать человеку, а не упасть
                outcome["error"] = exc

        # Ждём установщик, не замораживая окно: он может идти десятки секунд.
        # Пока ждём, окно живёт — и поток сканирования успевает закрыться.
        thread = threading.Thread(target=work, daemon=True)
        self._installing_driver = True
        thread.start()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            while thread.is_alive():
                QApplication.processEvents()
                thread.join(0.05)
        finally:
            QApplication.restoreOverrideCursor()
            self._installing_driver = False
        code = outcome.get("code")
        if "error" in outcome:
            QMessageBox.warning(self, "Драйвер не установлен", str(outcome["error"]))
        elif code is None:
            self.statusBar().showMessage("Установка драйвера отменена", 8000)
        elif code != 0:
            # Причину установщик уже показал своим окном.
            self.statusBar().showMessage("Драйвер не установлен", 8000)
        elif find_reader_port():
            self.statusBar().showMessage("Драйвер установлен, запускаем сканирование", 8000)
            if self.scan_thread is None:
                self._start_scanning()
            else:
                self._restart_scanning = True  # после того, как старый поток закроется
        else:
            QMessageBox.information(
                self,
                "Драйвер установлен",
                "COM-порт пока не появился. Выньте считыватель, воткните снова "
                "и нажмите «Начать сканирование».",
            )
        return True

    def _scan_finished(self, _tally: object) -> None:
        self._reader_is_lost = False
        self.reader_title.setStyleSheet("")
        self.subject_combo.setEnabled(True)
        self.scan_button.setEnabled(bool(self.folders))
        self.scan_button.setText("Начать сканирование")
        self.hero_title.setText("Готово к занятию")
        self.hero_hint.setText("Нажмите «Начать сканирование» и прикладывайте карты")
        self.reader_title.setText("Сканирование остановлено")
        self._refresh_today()
        self._refresh_report()
        self._show_session_problems()

    def _thread_deleted(self) -> None:
        self.scan_thread = None
        self.scan_worker = None
        if self._restart_scanning:
            self._restart_scanning = False
            self._start_scanning()

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
        self.pending_cards = list(snapshot.unknown_cards)
        self._refresh_pending_cards()
        self.present_value.setText(str(snapshot.present))
        self.late_value.setText(str(len(snapshot.late)))
        self.absent_value.setText(str(snapshot.absent))
        self.unknown_label.setText(
            f"неизвестных: {snapshot.unknown_count}" if snapshot.unknown_count else ""
        )
        self.recent_list.clear()
        recent = sorted(
            (row for row in snapshot.rows if row.at is not None),
            key=lambda row: row.at or datetime.min,  # None отсеяны выше
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
        self._render_students()

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

    # ------------------------------------------------------ students and cards

    def _render_students(self) -> None:
        if not hasattr(self, "students_table"):
            return
        rows = list(self.today_snapshot.rows)
        self.students_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                row.student.full_name,
                row.student.group_name,
                pretty_code(row.student.card_code) if row.student.card_code else "Не привязана",
                row.at.strftime("%H:%M") if row.at else "Нет",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row.student.id)
                self.students_table.setItem(row_index, column, item)
        self.students_table.resizeRowsToContents()
        has_students = bool(rows)
        self.manual_mark_button.setEnabled(has_students)
        self.replace_card_button.setEnabled(has_students)
        self.unbind_card_button.setEnabled(has_students)
        self.bind_card_button.setEnabled(has_students and bool(self.pending_cards))

    def _selected_student(self) -> Student | None:
        selected = self.students_table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(
                self,
                "Студент не выбран",
                "Сначала выберите строку студента в таблице.",
            )
            return None
        student_id = self.students_table.item(selected[0].row(), 0).data(Qt.UserRole)
        return next(
            (row.student for row in self.today_snapshot.rows if row.student.id == student_id),
            None,
        )

    def _mark_selected_student(self) -> None:
        student = self._selected_student()
        folder = self._current_folder()
        if student is None or folder is None:
            return
        at = (self.manual_datetime.dateTime().toPyDateTime()
              if self._manual_time_touched else datetime.now())
        if at > datetime.now():
            # В файле дата без года: будущая прочиталась бы как прошлогодняя
            # и дала бы вторую отметку.
            QMessageBox.warning(self, "Отметка не сохранена", "Дата и время ещё не наступили.")
            return
        try:
            with Storage(self.db_path) as storage:
                subject = storage.get_or_create_subject(folder.name)
                current = storage.student_by_name(student.full_name, student.group_name)
                if current is None:
                    raise ValueError("студент не найден в базе")
                result = storage.import_mark(
                    current,
                    subject,
                    at,
                    raw=MANUAL_RAW,
                )
        except Exception as exc:
            QMessageBox.warning(self, "Отметка не сохранена", str(exc))
            return

        messages = {
            ImportStatus.ADDED: "Отметка сохранена.",
            ImportStatus.SAME: "У студента уже есть такая отметка.",
            ImportStatus.KEPT: "Студент уже был отмечен; сохранено прежнее время.",
            ImportStatus.UPDATED: "Время отметки обновлено.",
        }
        self.statusBar().showMessage(messages[result], 7000)
        self._reset_manual_time()
        if not self._scanning_active():
            self._export_subject_now(folder)
        self.date_edit.setDate(QDate(at.year, at.month, at.day))
        self._refresh_today()
        self._refresh_report()

    def _bind_card_to_selected_student(self) -> None:
        student = self._selected_student()
        code = self.pending_card_combo.currentData()
        if student is None or not code:
            if not code:
                QMessageBox.information(
                    self,
                    "Нет карты",
                    "Сначала отсканируйте непривязанную карту.",
                )
            return
        saved = self._bind_code(code, student.full_name, student.group_name,
                                includes_current=False)
        if saved is None:
            return
        self.person_name.setText(saved.full_name)
        self.person_meta.setText(f"Группа {saved.group_name} · карта {pretty_code(code)}")
        self.scan_status_chip.setText("Карта привязана, отметка сохранена")
        folder = self._current_folder()
        if folder is not None and not self._scanning_active():
            self._export_subject_now(folder)
        self._refresh_today()
        self._refresh_report()

    def _replace_card_of_selected(self) -> None:
        student = self._selected_student()
        if student is None:
            return
        try:
            with Storage(self.db_path) as storage:
                unknown = storage.unknown_cards()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Карта не заменена", str(exc))
            return
        dialog = ReplaceCardDialog(
            self, student, unknown, mode=self.mode, port=self.port,
            reader_free=not self._scanning_active(),
        )
        if dialog.exec_() != ReplaceCardDialog.Accepted or dialog.code is None:
            return
        self._apply_replace(student, dialog.code)

    def _apply_replace(self, student: Student, code: str) -> None:
        """Заменить карту, сперва показав, что станет с отметками.

        Отметки чужой карты станут отметками этого студента, а совпавшие по
        занятию удалятся — обратной заменой это не вернуть, поэтому спрашиваем.
        """
        try:
            with Storage(self.db_path) as storage:
                owner = None
                try:
                    preview = storage.preview_replace(student.id, code)
                except CardTaken as exc:
                    owner = exc.owner
                    if not self._confirm_take(student, owner, code):
                        return
                    preview = storage.preview_replace(student.id, code, take=True)
                if not self._confirm_marks(student, code, preview):
                    return
                if owner is not None:
                    retract_card_marks(storage, owner, self.tables_path)
                result = storage.replace_card(student.id, code, take=owner is not None)
        except Exception as exc:  # noqa: BLE001 — сказать, не терять молча
            QMessageBox.warning(self, "Карта не заменена", str(exc))
            return
        if code in self.pending_cards:
            self.pending_cards.remove(code)
            self._refresh_pending_cards()
        parts = [f"{result.student.full_name}: карта {pretty_code(code)}"]
        if result.old_code:
            parts.append(f"вместо {pretty_code(result.old_code)}")
        if result.moved:
            parts.append(f"отметок этой картой зачтено: {result.moved}")
        if result.previous_owner is not None:
            parts.append(f"снята с {result.previous_owner.full_name}")
        self.statusBar().showMessage(", ".join(parts), 10000)
        self._after_card_change()

    def _confirm_take(self, student: Student, owner: Student, code: str) -> bool:
        swapped = (
            "\n\nЕсли карты двух студентов перепутаны, нажмите «Нет»: снимите карту "
            "с обоих и привяжите заново, иначе все отметки останутся за одним."
            if student.card_code else ""
        )
        answer = QMessageBox.question(
            self,
            "Карта уже привязана",
            f"Карта {pretty_code(code)} сейчас закреплена за студентом\n"
            f"{owner.full_name} ({owner.group_name}).\n\n"
            "Если её привязали по ошибке — забрать карту и отдать "
            f"{student.full_name}? Отметки, сделанные этой картой, перейдут "
            f"к {student.full_name} и уйдут из файла у {owner.full_name}, "
            f"а {owner.full_name} останется без карты.{swapped}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _confirm_marks(self, student: Student, code: str, preview: ReplacePreview) -> bool:
        if not preview.moved and not preview.dropped:
            return True
        dropped = (
            f"\n\nНа занятиях, где отметка оказалась бы второй ({preview.dropped}), останется "
            "более раннее время, а другая отметка будет удалена насовсем."
            if preview.dropped else ""
        )
        answer = QMessageBox.question(
            self,
            "Отметки этой карты",
            f"Картой {pretty_code(code)} уже отмечались. Отметок, которые перейдут "
            f"к {student.full_name}: {preview.moved}.{dropped}\n\n"
            "Если это карта другого человека, нажмите «Нет» и привяжите её ему: "
            "заменой обратно это не исправить. Продолжить?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _unbind_card_of_selected(self) -> None:
        student = self._selected_student()
        if student is None:
            return
        if not student.card_code:
            QMessageBox.information(self, "Карты нет",
                                    f"У {student.full_name} карта не привязана.")
            return
        answer = QMessageBox.question(
            self,
            "Снять карту?",
            f"Снять карту {pretty_code(student.card_code)} со студента {student.full_name}?\n\n"
            "Отметки, сделанные этой картой, вернутся в «неизвестные» и уйдут из файлов "
            "групп — их можно будет отдать другому студенту. Отметки, вписанные в таблицу "
            "руками или поставленные вручную в окне, останутся за студентом.\n\n"
            "Если студенту просто выдали новую карту, лучше «Заменить карту…»: "
            "тогда его отметки останутся при нём.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            with Storage(self.db_path) as storage:
                unbind_card(storage, student, self.tables_path)
        except Exception as exc:  # noqa: BLE001 — в том числе CardFilesBusy
            QMessageBox.warning(self, "Карта не снята", str(exc))
            return
        self.statusBar().showMessage(
            f"Карта {pretty_code(student.card_code)} снята с {student.full_name}", 8000)
        self._after_card_change()

    def _after_card_change(self) -> None:
        folder = self._current_folder()
        if folder is not None and not self._scanning_active():
            self._export_subject_now(folder)
        self._refresh_today()
        self._refresh_report()

    def _refresh_pending_cards(self) -> None:
        if not hasattr(self, "pending_card_combo"):
            return
        selected = self.pending_card_combo.currentData()
        self.pending_card_combo.clear()
        for code in self.pending_cards:
            self.pending_card_combo.addItem(pretty_code(code), code)
        if selected in self.pending_cards:
            self.pending_card_combo.setCurrentIndex(self.pending_card_combo.findData(selected))
        count = len(self.pending_cards)
        self.pending_cards_label.setText(
            f"Ожидают привязки: {count}"
            if count
            else "Нет непривязанных карт в текущем сеансе"
        )
        has_students = bool(self.today_snapshot.rows)
        self.bind_card_button.setEnabled(has_students and count > 0)

    def _scanning_active(self) -> bool:
        return self.scan_thread is not None and self.scan_thread.isRunning()

    def _export_subject_now(self, folder: SubjectFolder) -> None:
        try:
            with Storage(self.db_path) as storage:
                subject = storage.find_subject(folder.name)
                if subject is None:
                    return
                results = fill_subject(storage, subject, folder)
        except Exception as exc:
            self.statusBar().showMessage(
                f"Отметка в базе сохранена, но таблица не обновлена: {exc}",
                10_000,
            )
            return
        problems = fill_problems(results)
        if problems:
            self.statusBar().showMessage(
                f"Отметка в базе сохранена, но: {problems[0]} "
                "Потом нажмите «Записать в таблицы».",
                10_000,
            )

    def _write_tables(self) -> None:
        """Повторить запись в файлы: при остановке файл мог быть открыт в Excel."""
        if self._scanning_active():
            QMessageBox.information(
                self,
                "Сканирование запущено",
                "При остановке сканирования отметки запишутся сами.",
            )
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with Storage(self.db_path) as storage:
                written = fill_all(storage, self.tables_path)
        except Exception as exc:  # noqa: BLE001 — сказать, не терять молча
            QMessageBox.warning(self, "Таблицы не записаны", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        results = [result for group in written.values() for result in group]
        problems = fill_problems(results)
        self._refresh_today()
        self._refresh_report()
        if problems:
            QMessageBox.warning(self, "Записаны не все таблицы", "\n\n".join(problems))
        else:
            self.statusBar().showMessage(f"Записано файлов групп: {len(results)}", 8000)

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
        if self._installing_driver:
            QMessageBox.information(
                self, "Идёт установка драйвера", "Дождитесь окончания установки драйвера."
            )
            event.ignore()
            return
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
            self._show_session_problems()
        event.accept()


def pick_home(parent: QWidget | None, start: Path | None = None) -> Path | None:
    """Спросить рабочую папку. Возвращает её или None, если человек передумал.

    Новую пустую папку заводим только с явного согласия: молча созданная
    база в случайном месте увела бы отметки мимо журнала.
    """
    selected = QFileDialog.getExistingDirectory(
        parent,
        "Выберите рабочую папку с data и tables",
        str(start or Path.home()),
    )
    if not selected:
        return None
    home = Path(selected)
    if home.name.casefold() in (TABLES_DIR.name, DB_FILE.parent.name):
        home = home.parent  # выбрали саму tables или data — нужна папка над ней
    if not workdir.looks_like_home(home):
        answer = QMessageBox.question(
            parent,
            "Создать новую рабочую папку?",
            f"В папке {home} нет data и tables, то есть нет ни базы, ни списков.\n\n"
            "Если программа уже работала на этом компьютере, выберите ту папку, "
            "где лежит rfid.cmd, — иначе отметки начнут писаться в новую пустую "
            "базу.\n\nСоздать здесь новую рабочую папку?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None
        try:
            (home / DB_FILE.parent).mkdir(parents=True, exist_ok=True)
            (home / TABLES_DIR).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(parent, "Папка не создана", f"{home}\n\n{exc}")
            return None
    home = home.resolve()
    workdir.remember(home)
    return home


def ask_home() -> Path | None:
    """Рабочая папка не нашлась: объяснить и предложить выбрать."""
    _application()
    QMessageBox.information(
        None,
        "RFID Журнал",
        "Не найдена папка с данными программы (data и tables).\n\n"
        "Укажите папку, где лежит rfid.cmd, — там база и списки групп. "
        "Выбор запомнится, и окно, и консоль будут работать с ней.",
    )
    return pick_home(None)


def report_crash(kind, exc, trace) -> None:
    """Необработанная ошибка: сказать и работать дальше.

    Без этого PyQt5 завершает процесс молча: окно просто исчезает посреди пары.
    Отметки к этому моменту в базе, а окно нужно следующему студенту.
    """
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, exc, trace)
        return
    try:
        traceback.print_exception(kind, exc, trace)
    except Exception:  # noqa: BLE001 — у оконного exe вывода может не быть
        pass
    try:
        QMessageBox.critical(
            None,
            "Ошибка программы",
            f"{kind.__name__}: {exc}\n\nСохранённые отметки целы. Программа продолжает "
            "работу; если окно ведёт себя странно, закройте и откройте его снова.",
        )
    except Exception:  # noqa: BLE001 — ошибка в показе ошибки не должна уронить окно
        pass


def _application() -> QApplication:
    sys.excepthook = report_crash
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(sys.argv[:1])
        app.setApplicationName("RFID Журнал")
    return app


def run_gui(
    db_path: str | Path,
    tables_path: str | Path,
    *,
    mode: str = "auto",
    port: str | None = None,
) -> int:
    app = _application()
    window = AttendanceWindow(Path(db_path), Path(tables_path), mode=mode, port=port)
    window.show()
    return app.exec_()
