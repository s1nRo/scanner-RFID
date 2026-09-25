"""Просмотр: кто пришёл, опоздавшие, предметы, привязки карт."""

from __future__ import annotations

from datetime import date

from .. import excel
from ..db import Storage
from ..excel import discover_subjects
from ..lessons import LATE_AFTER, late_arrivals
from ..scanner import CardCodeError, parse_manual
from . import common
from .common import DATE_INPUT, Interrupted
from .subjects import choose_subject, report_misplaced

_RULE = "─" * 70


def cmd_report(args) -> int:
    day = common.parse_date(args.date)
    folder = choose_subject(args, args.tables)

    with Storage(args.db) as storage:
        subject = storage.find_subject(folder.name)
        if subject is None:
            print(f"По предмету «{folder.name}» отметок ещё не было.")
            return 0

        print()
        print(f"{folder.name} — {day.strftime(DATE_INPUT)}")
        print(_RULE)
        print(f"{'ФИО':<38} {'Группа':<16} {'Время':<8} Статус")
        for row in storage.day_rows(day, subject, args.group):
            time_text = row.at.strftime("%H:%M") if row.at else "—"
            status = "отметился" if row.at else "НЕ отметился"
            print(f"{row.student.full_name:<38} {row.student.group_name:<16} "
                  f"{time_text:<8} {status}")

        print(_RULE)
        for stat in storage.group_stats(day, subject):
            print(f"{stat.group_name or '(без группы)':<16} "
                  f"{stat.present}/{stat.total}  ({stat.percent:.0f}%)")

        unknown = storage.unknown_marks_on(day, subject)
        if unknown:
            print(f"\nНепривязанные карты в этот день: {len(unknown)}")
            for card_code, at in unknown:
                print(f"  {card_code}  {at.strftime('%H:%M')}")
    return 0


def cmd_late(args) -> int:
    """Опоздавшие на занятии — те, кто показывает конспект."""
    folder = choose_subject(args, args.tables)

    with Storage(args.db) as storage:
        subject = storage.find_subject(folder.name)
        days = storage.subject_dates(subject) if subject else []
        if subject is None or not days:
            print(f"По предмету «{folder.name}» отметок ещё не было.")
            return 0
        day = common.parse_date(args.date) if args.date else _choose_day(days)

        # Только именные приходы — ровно те, что попадают в файлы групп,
        # чтобы список совпадал с жёлтой подсветкой в таблице.
        arrivals = {row.student: row.at for row in storage.day_rows(day, subject)
                    if row.at is not None}
    late = late_arrivals(arrivals)

    minutes = int(LATE_AFTER.total_seconds() // 60)
    print()
    print(f"{folder.name} — {day.strftime(DATE_INPUT)}")
    print(f"Опоздали ({minutes} мин и больше от начала пары) — показывают конспект")
    print(_RULE)
    if not late:
        print("Опоздавших нет." if arrivals else "В этот день никто не отмечен.")
        return 0

    start = None
    for student, at, lesson_start in late:
        if lesson_start != start:
            start = lesson_start
            print(f"\nПара с {start.strftime('%H:%M')}")
        delay = int((at - lesson_start).total_seconds() // 60)
        print(f"   {student.full_name:<38} {student.group_name:<16} "
              f"{at.strftime('%H:%M')}   +{delay} мин")
    print(_RULE)
    print(f"Всего: {len(late)}")
    return 0


def _choose_day(days: list[date]) -> date:
    """Выбрать день из тех, когда были отметки. Enter — последний."""
    recent = sorted(days, reverse=True)[:10]
    items = [day.strftime(DATE_INPUT) for day in recent]
    return recent[common.pick("День занятия", items, default="последний")]


def cmd_subjects(args) -> int:
    folders = discover_subjects(args.tables)
    if not folders:
        print(f"В {args.tables} нет ни одной папки предмета со списками групп.")
        print("Создайте папку с названием предмета и положите туда списки.")
    for folder in folders:
        print(f"\n{folder.name}")
        for path in folder.rosters:
            try:
                parsed = excel.read_roster(path)
                print(f"   {parsed.group_name:<18} {len(parsed.students):>3} чел.   {path.name}")
            except excel.RosterError as exc:
                print(f"   {path.name}: {exc}")
    report_misplaced(args.tables)
    return 0


def cmd_students(args) -> int:
    with Storage(args.db) as storage:
        if args.students_action == "remove":
            try:
                code = parse_manual(args.code)
            except CardCodeError as exc:
                raise Interrupted(str(exc))
            print("Привязка снята." if storage.remove_student(code) else "Такой карты нет.")
            return 0

        students = storage.list_students(with_card=True)
        if not students:
            print("Ни одна карта ещё не привязана.")
            return 0
        print(f"{'Код карты':<12} {'ФИО':<38} Группа")
        print(_RULE)
        for student in students:
            print(f"{student.card_code:<12} {student.full_name:<38} {student.group_name}")
        print(f"\nВсего: {len(students)}")
    return 0


def cmd_unknown(args) -> int:
    with Storage(args.db) as storage:
        cards = storage.unknown_cards()
    if not cards:
        print("Непривязанных карт нет.")
        return 0
    print(f"{'Код карты':<12} {'Раз':>4}  Последний раз")
    print("─" * 50)
    for card in cards:
        print(f"{card.card_code:<12} {card.times:>4}  "
              f"{card.last_seen.strftime('%d.%m.%Y %H:%M')}")
    print("\nПривязать: rfid enroll — приложите карту и выберите номер из списка.")
    return 0
