"""Заслонка окна: пока незнакомую карту не привязали, другие не принимаются.

Прогоняется настоящий pipeline.run с базой. Вид ведёт себя как ScanWorker:
на карте без владельца закрывает заслонку. «Оператор» открывает её между
прикладываниями — так считыватель-заглушка и устроен.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from rfid import pipeline
from rfid.db import MarkResult, MarkStatus, Storage
from rfid.gui.gate import ScanGate
from rfid.scanner import Scan
from tests.helpers import card

KNOWN = card("Em-Marine[A100] 007,42")
OTHER = card("Em-Marine[C300] 009,44")
UNKNOWN = card("Em-Marine[B200] 008,43")
START = datetime(2026, 10, 16, 9, 0)

Step = object  # CardCode — приложить карту, callable — действие оператора


class ScriptedReader:
    """Карты и действия оператора по порядку, с шагом в минуту."""

    description = "сценарий"

    def __init__(self, steps: list[Step]):
        self.steps = steps

    def flush(self) -> None:
        pass

    def scans(self) -> Iterator[Scan]:
        at = START
        for step in self.steps:
            if callable(step):
                step()
                continue
            at += timedelta(minutes=1)
            yield Scan(code=step, at=at, raw=step.canonical)  # type: ignore[attr-defined]


class GateView:
    """Как ScanWorker.show: незнакомая карта закрывает заслонку."""

    def __init__(self) -> None:
        self.gate: ScanGate | None = None
        self.shown: list[MarkResult] = []

    def status(self, message: str) -> None:
        pass

    def show(self, result: MarkResult) -> None:
        self.shown.append(result)
        if result.student is None and self.gate is not None:
            self.gate.close()


def run(tmp_path, steps: Callable[[ScanGate, Storage], list[Step]]):
    storage = Storage(tmp_path / "a.db")
    storage.bind_card(KNOWN, "Иванов Иван", "1")
    storage.bind_card(OTHER, "Петров Пётр", "1")
    subject = storage.get_or_create_subject("Предмет")
    rejected: list[Scan] = []
    view = GateView()
    gate = ScanGate(ScriptedReader([]), rejected.append)
    gate.reader = ScriptedReader(steps(gate, storage))
    view.gate = gate
    tally = pipeline.run(gate, storage, view, subject, debounce=0)
    return storage, subject, view, rejected, tally


def test_next_card_is_rejected_until_unknown_is_bound(tmp_path):
    def steps(gate, storage):
        def bind():
            storage.bind_card(UNKNOWN, "Сидоров Сидор", "1")
            gate.open()
        return [UNKNOWN, KNOWN, bind, KNOWN]

    storage, subject, view, rejected, _ = run(tmp_path, steps)
    # Пока не привязали — карту Иванова не приняли, и об этом сказали.
    assert [scan.code for scan in rejected] == [KNOWN]
    # После привязки он приложил снова — и отмечен.
    assert [r.status for r in view.shown] == [MarkStatus.UNKNOWN, MarkStatus.MARKED]
    names = {row.student.full_name for row in storage.day_rows(START.date(), subject)
             if row.present}
    assert names == {"Иванов Иван", "Сидоров Сидор"}


def test_unknown_card_mark_is_kept_before_binding(tmp_path):
    # Сама незнакомая карта записана сразу — блокировка её отметку не трогает.
    storage, subject, *_ = run(tmp_path, lambda gate, storage: [UNKNOWN])
    assert [code for code, _at in storage.unknown_marks_on(START.date(), subject)] == [
        UNKNOWN.canonical
    ]


def test_every_rejected_card_is_reported(tmp_path):
    storage, subject, view, rejected, tally = run(
        tmp_path, lambda gate, storage: [UNKNOWN, KNOWN, OTHER, KNOWN]
    )
    assert [scan.code for scan in rejected] == [KNOWN, OTHER, KNOWN]
    assert tally[MarkStatus.MARKED] == 0
    assert not [row for row in storage.day_rows(START.date(), subject)
                if row.present and row.student.card_code == KNOWN.canonical]


def test_skipping_opens_the_gate_and_keeps_the_mark(tmp_path):
    # «Оставить без привязки» — заслонка открыта, отметка карты на месте.
    def steps(gate, storage):
        return [UNKNOWN, gate.open, KNOWN]

    storage, subject, view, rejected, _ = run(tmp_path, steps)
    assert rejected == []
    assert [r.status for r in view.shown] == [MarkStatus.UNKNOWN, MarkStatus.MARKED]
    assert len(storage.unknown_marks_on(START.date(), subject)) == 1


def test_known_cards_pass_while_nothing_is_pending(tmp_path):
    _, _, view, rejected, _ = run(tmp_path, lambda gate, storage: [KNOWN, OTHER])
    assert rejected == []
    assert [r.status for r in view.shown] == [MarkStatus.MARKED, MarkStatus.MARKED]
