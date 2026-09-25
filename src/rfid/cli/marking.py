"""Команды, которые слушают считыватель: отметка, привязка карт, «чья карта».

scan --enroll совмещает первые две: пара идёт как обычно, а незнакомая
карта сразу привязывается к студенту из списка — отдельный заход
с rfid enroll перед парой не нужен.
"""

from __future__ import annotations

from datetime import date

from .. import pipeline
from ..db import CardConflict, MarkResult, MarkStatus, Storage, Student
from ..scanner import CardCode, CardReader, ReaderUnavailable, make_reader
from . import common
from .common import DATE_INPUT, Interrupted
from .home import guard_mock
from .keys import STOP_HINT, make_stop_watcher
from .subjects import (
    Candidate,
    all_candidates,
    bound_count,
    by_number,
    choose_subject,
    print_candidates,
    require_rosters,
)
from .tables import fill_and_report
from .view import ConsoleView

# Синтетические коды карт для демонстрации --mode mock.
DEMO_LINES = [
    "Em-Marine[A100] 007,42",
    "No card",
    "Em-Marine[B200] 008,43",
    "No card",
    "Em-Marine[A100] 007,42",
]

_NEXT_CARD = "\n  Приложите карту следующего студента…"


def _open_reader(args, view: ConsoleView) -> CardReader:
    try:
        return make_reader(
            args.mode, port=args.port, on_status=view.status,
            mock_lines=DEMO_LINES, stop_after=args.seconds,
            should_stop=make_stop_watcher(),
        )
    except ReaderUnavailable as exc:
        raise Interrupted(str(exc)) from None


# ----------------------------------------------------------------------- scan


class BindingView(ConsoleView):
    """Вывод пары, который незнакомую карту сразу предлагает привязать.

    Отметка к этому моменту уже в базе — пока безымянная. Привязка делает
    её именной: bind_card забирает себе прошлые отметки карты, включая эту.
    Поэтому ни пропуск, ни опечатка в номере отметку не теряют: карта
    останется в «неизвестных», и её можно привязать позже.
    """

    unknown_hint = "записано. Кто это? Выберите номер из списка ниже."

    def __init__(self, storage: Storage, candidates: list[Candidate],
                 reader: CardReader | None = None, **kwargs):
        super().__init__(**kwargs)
        self.storage = storage
        self.candidates = candidates
        self.reader = reader
        self.bound = 0
        # Сколько «неизвестных» по счётчику цикла на самом деле стали
        # отмеченными: в итоге они должны числиться отмеченными.
        self.bound_from_unknown = 0

    def summary(self, marked: int, duplicates: int, unknown: int) -> None:
        moved = self.bound_from_unknown
        super().summary(marked + moved, duplicates, unknown - moved)

    def show(self, result: MarkResult) -> None:
        super().show(result)
        if result.student is not None:
            return
        if bound_count(self.candidates, self.storage) == len(self.candidates):
            print("  У всех студентов предмета карта уже есть — эта, видимо, чужая. "
                  "Отметка сохранена.")
            return
        try:
            print_candidates(self.candidates, self.storage, without_card_only=True)
            choice = common.ask("\n  Номер студента (Enter — оставить без привязки): ")
        finally:
            # Пока оператор выбирал, к считывателю могли приложить карту:
            # эти строки не должны всплыть уже после выбора, как чужие.
            if self.reader is not None:
                self.reader.flush()

        student = _bind(self.storage, result.code, self.candidates, choice,
                        includes_current=True)
        if student is None:
            print("  Карта осталась непривязанной, отметка сохранена.")
            return
        self.bound += 1
        if result.status is MarkStatus.UNKNOWN:
            self.bound_from_unknown += 1
        # Та же отметка, но теперь именная.
        super().show(MarkResult(MarkStatus.MARKED, result.code, result.at,
                                student, result.first_at or result.at))


def cmd_scan(args) -> int:
    view = ConsoleView(sound=not args.no_sound)
    guard_mock(args)
    folder = choose_subject(args, args.tables)
    require_rosters(folder)
    candidates = all_candidates(folder) if args.enroll else []
    reader = _open_reader(args, view)

    with Storage(args.db) as storage:
        subject = storage.get_or_create_subject(folder.name)
        if args.enroll:
            view = BindingView(storage, candidates, reader, sound=not args.no_sound)
        title = " — отметка и привязка новых карт" if args.enroll else ""
        view.banner(f"{folder.name} — {date.today().strftime(DATE_INPUT)}{title}")
        view.status(f"Источник: {reader.description}")
        view.status(f"Групп в предмете: {folder.group_count}")
        if args.enroll:
            view.status(f"С картами: {bound_count(candidates, storage)} "
                        f"из {len(candidates)}. Незнакомую карту привяжете сразу.")
        view.status(f"{STOP_HINT}. Ctrl+C тоже работает.")
        print()

        tally = None
        failure: BaseException | None = None
        try:
            tally = pipeline.run(reader, storage, view, subject, debounce=args.debounce)
        except KeyboardInterrupt:
            print()
        except Exception as exc:
            # Что бы ни сорвалось, отметки уже в базе — их надо выгрузить,
            # а не потерять вместе с трассировкой.
            failure = exc

        view.summary(*(pipeline.tally_line(tally) if tally else (0, 0, 0)))
        if isinstance(view, BindingView):
            print(f"Из неизвестных привязано на ходу: {view.bound}.  "
                  f"С картами: {bound_count(candidates, storage)} из {len(candidates)}.")

        if tally and pipeline.failed_count(tally):
            print(f"\n!!! Не удалось записать отметок: {pipeline.failed_count(tally)}.")
            print("    Эти студенты НЕ отмечены, их надо провести заново.")

        if failure is not None:
            print(f"\n!!! Сеанс прерван ошибкой: {failure}")
            print("    Записанное до этого момента сохранено и будет выгружено.")

        if not args.no_export:
            fill_and_report(storage, subject, folder)
    return 1 if failure is not None else 0


# --------------------------------------------------------------------- enroll


def cmd_enroll(args) -> int:
    """Привязать карты к студентам предмета.

    Список сквозной по всем группам: прикладываешь карту и вводишь номер.
    ФИО набирать не надо, группу выбирать тоже — она известна из файла.
    """
    view = ConsoleView(sound=not args.no_sound)
    guard_mock(args)
    folder = choose_subject(args, args.tables)
    candidates = all_candidates(folder)
    reader = _open_reader(args, view)

    with Storage(args.db) as storage:
        view.banner(f"Привязка карт — {folder.name}")
        view.status(f"Источник: {reader.description}")
        view.status(f"Групп: {folder.group_count}, студентов всего: {len(candidates)}")

        added = 0
        try:
            print_candidates(candidates, storage)
            print(f"\n  Приложите карту студента…   ({STOP_HINT})")
            for scan in reader.scans():
                known = storage.find_student(scan.code)
                if known:
                    print(f"  Эта карта уже за студентом: {known.full_name} "
                          f"({known.group_name})")
                    reader.flush()
                    print(_NEXT_CARD)
                    continue

                print(f"  Карта прочитана: {scan.code.canonical}")
                try:
                    choice = common.ask(
                        "  Номер студента (Enter — пропустить, 0 — закончить): "
                    )
                finally:
                    # Пока оператор думал, кто-то мог приложить карту:
                    # эти строки не должны всплыть следующим шагом.
                    reader.flush()

                if choice in ("0", "q"):
                    break
                if _bind(storage, scan.code, candidates, choice) is not None:
                    added += 1
                print(_NEXT_CARD)
        except KeyboardInterrupt:
            print()
        except Interrupted as exc:
            print(exc)

        print("─" * 78)
        print(f"Привязано за сеанс: {added}.  "
              f"Всего с картами: {bound_count(candidates, storage)} из {len(candidates)}.")
    return 0


def _bind(
    storage: Storage, code: CardCode, candidates: list[Candidate], choice: str,
    *, includes_current: bool = False,
) -> Student | None:
    """Привязать карту к выбранному номеру. Возвращает студента или None.

    includes_current — карту привязывают прямо на паре, и среди зачтённых
    отметок есть только что сделанная. «Прошлыми» её называть нельзя.
    """
    chosen = by_number(candidates, choice)
    if chosen is None:
        print("  пропущено" if not choice else "  нет такого номера")
        return None
    try:
        saved, credited = storage.bind_card(code, chosen.full_name, chosen.group_name)
    except CardConflict as exc:
        print(f"  Не привязано: {exc}. Сначала снимите старую: "
              f"rfid students remove {exc.student.card_code}")
        return None
    print(f"  Привязано: {saved.full_name} ({saved.group_name})")
    past = credited - 1 if includes_current else credited
    if past > 0:
        print(f"  Прошлых отметок зачтено: {past}")
    return saved


# ---------------------------------------------------------------------- whois


def cmd_whois(args) -> int:
    view = ConsoleView(sound=False)
    reader = _open_reader(args, view)

    with Storage(args.db) as storage:
        view.banner("Чья карта")
        view.status(f"Прикладывайте карту. Отметки не записываются. {STOP_HINT}.")
        print()
        try:
            for scan in reader.scans():
                student = storage.find_student(scan.code)
                owner = (f"{student.full_name} ({student.group_name})"
                         if student else "КАРТА НЕ ПРИВЯЗАНА")
                print(f"  {scan.code.canonical}  →  {owner}")
        except KeyboardInterrupt:
            print()
    return 0
