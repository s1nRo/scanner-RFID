"""Фоновое чтение карт для PyQt5."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from .. import pipeline
from ..db import MarkResult, Storage
from ..excel import SubjectFolder
from ..journal import fill_subject
from ..scanner import ReaderUnavailable, make_reader


class ScanWorker(QObject):
    """Запускает блокирующий считыватель вне GUI-потока."""

    mark_received = pyqtSignal(object)
    status_changed = pyqtSignal(str)
    failed = pyqtSignal(str)
    export_finished = pyqtSignal(str)
    finished = pyqtSignal(object)

    def __init__(
        self,
        db_path: Path,
        folder: SubjectFolder,
        *,
        mode: str = "auto",
        port: str | None = None,
        debounce: float = pipeline.DEFAULT_DEBOUNCE,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.folder = folder
        self.mode = mode
        self.port = port
        self.debounce = debounce
        self._stop = Event()

    def stop(self) -> None:
        self._stop.set()

    def status(self, message: str) -> None:
        self.status_changed.emit(message)

    def show(self, result: MarkResult) -> None:
        self.mark_received.emit(result)

    @pyqtSlot()
    def run(self) -> None:
        tally: Counter = Counter()
        try:
            reader = make_reader(
                self.mode,
                port=self.port,
                on_status=self.status,
                should_stop=self._stop.is_set,
            )
            self.status_changed.emit(f"Считыватель подключён: {reader.description}")
            with Storage(self.db_path) as storage:
                subject = storage.get_or_create_subject(self.folder.name)
                try:
                    tally = pipeline.run(
                        reader,
                        storage,
                        self,
                        subject,
                        debounce=self.debounce,
                    )
                finally:
                    # Даже ошибка порта не должна оставить уже принятые отметки
                    # только в базе: повторяем гарантию консольного режима.
                    self.status_changed.emit("Сохраняем отметки в таблицы…")
                    results = fill_subject(storage, subject, self.folder)
                    errors = [result for result in results if not result.ok]
                    if errors:
                        self.export_finished.emit(
                            "Отметки в базе сохранены, но не обновлено файлов: "
                            f"{len(errors)}"
                        )
                    else:
                        self.export_finished.emit("Отметки сохранены в базе и таблицах")
        except ReaderUnavailable as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Сеанс сканирования завершён с ошибкой: {exc}")
        finally:
            self.finished.emit(tally)
