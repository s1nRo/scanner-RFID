"""Командная строка.

    python -m rfid                  меню: спросит, что делать
    python -m rfid enroll           привязать карты к студентам из списка
    python -m rfid scan             отмечать студентов
    python -m rfid whois            чья это карта
    python -m rfid subjects         предметы (папки в tables/)
    python -m rfid students ...     кому какая карта принадлежит
    python -m rfid unknown ...      неизвестные карты
    python -m rfid report           кто пришёл, кого нет
    python -m rfid late             опоздавшие — показывают конспект
    python -m rfid export           проставить отметки в файлах групп
    python -m rfid import [--sync]  обновить базу из файлов групп
    python -m rfid doctor           проверка железа, драйвера и базы
    python -m rfid ports            какие COM-порты есть

Предмет — это папка со списками групп, её имя и есть название предмета.
Программа спрашивает, где искать, и заполняет найденные файлы. Папок она
не создаёт и файлов не переносит: структуру ведёт пользователь.
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import journal, pipeline
from . import roster as R
from .codes import CardCodeError, parse_manual
from .console import ConsoleView
from .keys import STOP_HINT, make_stop_watcher
from .readers import (
    ReaderUnavailable,
    available_ports,
    find_reader_port,
    make_reader,
    no_port_explanation,
)
from .storage import CardConflict, Storage, Subject

DEFAULT_DB = Path("data/attendance.db")
TABLES_DIR = Path("tables")
DATE_INPUT = "%d.%m.%Y"

# Синтетические коды карт для демонстрации --mode mock.
DEMO_LINES = [
    "Em-Marine[A100] 007,42",
    "No card",
    "Em-Marine[B200] 008,43",
    "No card",
    "Em-Marine[A100] 007,42",
]

_NO_INPUT_HINT = (
    "\n  Ввод недоступен — здесь нет интерактивного терминала.\n"
    "  Откройте обычное окно PowerShell в папке проекта и запустите там."
)

_console_ready = False


def _setup_console() -> None:
    """Привести ввод и вывод к UTF-8.

    Про stdin забыть нельзя: ФИО и названия вводят кириллицей, а в Windows
    поток по умолчанию открывается в cp1251 с surrogateescape — UTF-8 из
    пайпа превращается в суррогаты, и SQLite такую строку не принимает.
    Вызывается один раз: меню запускает main() повторно, а перенастроить
    поток после первого чтения уже нельзя.
    """
    global _console_ready
    if _console_ready:
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, io.UnsupportedOperation):
                pass
    _console_ready = True


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        raise Interrupted(_NO_INPUT_HINT)


def _confirm(question: str) -> bool:
    """Вопрос «да или нет». Согласие — только y, всё остальное — отказ.

    Отказ по умолчанию нарочно: подтверждают удаление. В русской раскладке
    клавиша y даёт «н» — пусть это лучше будет отказом, чем случайным
    согласием у того, кто начал печатать «нет».
    """
    return _ask(f"{question} (y/n): ").lower() in ("y", "yes")


class Interrupted(Exception):
    """Дальше работать нельзя — с объяснением для человека."""


class Cancelled(Interrupted):
    """Пользователь сам отказался. Не ошибка, сообщения быть не должно."""

    def __init__(self) -> None:
        super().__init__("")


def guard_mock(args) -> None:
    """Не давать заглушке писать в рабочую базу.

    Урок из практики: показательный прогон с --mode mock проигрывает
    вымышленные карты, но пишет туда же, куда настоящая работа, — и молча
    привязывает чужие карты к случайным людям. Требуем отдельную базу.
    """
    if getattr(args, "mode", None) != "mock":
        return
    if Path(args.db) == DEFAULT_DB:
        raise Interrupted(
            "  Режим --mode mock проигрывает вымышленные карты и испортил бы\n"
            f"  рабочую базу {DEFAULT_DB}. Укажите отдельную:\n"
            "      rfid --db data/demo.db ... --mode mock"
        )


def _parse_date(text: str | None) -> date:
    if not text:
        return date.today()
    try:
        return datetime.strptime(text, DATE_INPUT).date()
    except ValueError:
        raise SystemExit(f"Дата должна быть в виде ДД.ММ.ГГГГ, получено: {text!r}")


# ------------------------------------------------------------------- предметы


def _describe_folder(folder: R.SubjectFolder) -> str:
    """Что лежит в папке — чтобы выбирать осознанно, а не по имени."""
    groups = []
    for path in folder.rosters[:4]:
        try:
            parsed = R.read_roster(path)
            groups.append(f"{parsed.group_name} ({len(parsed.students)})")
        except R.RosterError:
            groups.append(f"{path.name} — не список")
    if len(folder.rosters) > 4:
        groups.append(f"и ещё {len(folder.rosters) - 4}")
    return ", ".join(groups)


def choose_subject(args, tables_dir: Path) -> R.SubjectFolder:
    """Выбрать предмет — подпапку tables/ со списками групп.

    Раскладку ведёт пользователь: программа папок не создаёт и файлов
    не переносит, только показывает найденное и даёт выбрать. Корень,
    в котором искать, меняется флагом --tables.
    """
    if getattr(args, "subject", None):
        return _subject_by_name(args.subject, tables_dir)

    folders = R.discover_subjects(tables_dir)
    if not folders:
        raise Interrupted(_nothing_found(tables_dir))

    print()
    print(f"  Предмет (папки в {tables_dir}):")
    for index, folder in enumerate(folders, start=1):
        print(f"    {index}. {folder.name}")
        print(f"       {_describe_folder(folder)}")
    print("    0. назад")
    print()

    choice = _ask("  Выберите номер: ")
    if choice in ("0", "", "q"):
        raise Cancelled
    if choice.isdigit() and 1 <= int(choice) <= len(folders):
        return folders[int(choice) - 1]
    raise Interrupted("  Нет такого пункта.")


def _nothing_found(tables_dir: Path) -> str:
    lines = [f"  В {tables_dir} нет ни одной папки предмета со списками групп."]
    misplaced = R.misplaced_rosters(tables_dir)
    if misplaced:
        lines.append(
            f"  При этом {len(misplaced)} файл(ов) лежат в самой {tables_dir}, "
            "мимо папок:"
        )
        lines += [f"      {p.name}" for p in misplaced]
    lines.append(
        f"\n  Создайте в {tables_dir} папку с названием предмета и положите\n"
        "  туда списки групп — по файлу на группу."
    )
    return "\n".join(lines)


def _subject_by_name(name: str, tables_dir: Path) -> R.SubjectFolder:
    for folder in R.discover_subjects(tables_dir):
        if folder.name.casefold() == name.casefold():
            return folder
    known = ", ".join(f.name for f in R.discover_subjects(tables_dir)) or "ни одной"
    raise Interrupted(f"  Папки «{name}» в {tables_dir} нет. Известные: {known}")


def require_rosters(folder: R.SubjectFolder) -> None:
    if not folder.rosters:
        raise Interrupted(
            f"  В папке «{folder.name}» нет ни одного файла со списком группы.\n"
            f"  Положите туда .xlsx со списком и запустите снова."
        )


@dataclass(frozen=True)
class Candidate:
    """Строка сквозного списка: студент со своей группой."""

    number: int
    full_name: str
    group_name: str


def all_candidates(folder: R.SubjectFolder) -> list[Candidate]:
    """Все студенты предмета одним списком со сквозной нумерацией.

    Группу при привязке не спрашиваем: важен человек, а не то, в каком файле
    он записан. Номера сквозные, поэтому ввод однозначен.
    """
    require_rosters(folder)
    candidates: list[Candidate] = []
    for path in folder.rosters:
        try:
            roster = R.read_roster(path)
        except R.RosterError as exc:
            print(f"  {path.name}: {exc}")
            continue
        for student in roster.students:
            candidates.append(
                Candidate(len(candidates) + 1, student.full_name, roster.group_name)
            )
    if not candidates:
        raise Interrupted(f"  В «{folder.name}» не нашлось ни одного студента.")
    return candidates


def _norm(name: str) -> str:
    return R.normalize_name(name)


def _print_candidates(candidates: list[Candidate], storage: Storage) -> None:
    """Список с подписями групп, но сквозной нумерацией."""
    known = {_norm(s.full_name) for s in storage.list_students(with_card=True)}
    group = None
    print()
    for item in candidates:
        if item.group_name != group:
            group = item.group_name
            print(f"\n  {group}")
        mark = "  карта есть" if _norm(item.full_name) in known else ""
        print(f"    {item.number:>3}. {item.full_name:<38}{mark}")


def _by_number(candidates: list[Candidate], choice: str) -> Candidate | None:
    if not choice.isdigit():
        return None
    number = int(choice)
    return next((c for c in candidates if c.number == number), None)


def _bound_count(candidates: list[Candidate], storage: Storage) -> int:
    known = {_norm(s.full_name) for s in storage.list_students(with_card=True)}
    return sum(1 for c in candidates if _norm(c.full_name) in known)


# --------------------------------------------------------------------- enroll


def cmd_enroll(args) -> int:
    """Привязать карты к студентам предмета.

    Список сквозной по всем группам: прикладываешь карту и вводишь номер.
    ФИО набирать не надо, группу выбирать тоже — она известна из файла.
    """
    view = ConsoleView(sound=not args.no_sound)
    try:
        guard_mock(args)
        folder = choose_subject(args, args.tables)
        candidates = all_candidates(folder)
    except Interrupted as exc:
        if str(exc):
            print(exc)
        return 1

    try:
        reader = make_reader(
            args.mode, port=args.port, on_status=view.status,
            mock_lines=DEMO_LINES, stop_after=args.seconds,
            should_stop=make_stop_watcher(),
        )
    except ReaderUnavailable as exc:
        print(exc)
        return 1

    with Storage(args.db) as storage:
        view.banner(f"Привязка карт — {folder.name}")
        view.status(f"Источник: {reader.description}")
        view.status(f"Групп: {folder.group_count}, студентов всего: {len(candidates)}")

        added = 0
        try:
            _print_candidates(candidates, storage)
            print(f"\n  Приложите карту студента…   ({STOP_HINT})")
            for scan in reader.scans():
                code = scan.code
                known = storage.find_student(code)
                if known:
                    print(f"  Эта карта уже за студентом: {known.full_name} "
                          f"({known.group_name})")
                    reader.flush()
                    print("\n  Приложите карту следующего студента…")
                    continue

                print(f"  Карта прочитана: {code.canonical}")
                try:
                    choice = _ask(
                        "  Номер студента (Enter — пропустить, 0 — закончить): "
                    )
                finally:
                    reader.flush()

                if choice in ("0", "q"):
                    break

                chosen = _by_number(candidates, choice)
                if chosen is None:
                    print("  пропущено" if not choice else "  нет такого номера")
                else:
                    try:
                        saved, credited = storage.resolve_unknown(
                            code, chosen.full_name, chosen.group_name
                        )
                    except CardConflict as exc:
                        print(f"  Не привязано: {exc}. Сначала снимите старую: "
                              f"rfid students remove {exc.student.card_code}")
                        print("\n  Приложите карту следующего студента…")
                        continue
                    added += 1
                    print(f"  Привязано: {saved.full_name} ({saved.group_name})")
                    if credited:
                        print(f"  Прошлых отметок зачтено: {credited}")

                print("\n  Приложите карту следующего студента…")
        except KeyboardInterrupt:
            print()
        except Interrupted as exc:
            print(exc)

        bound = _bound_count(candidates, storage)
        print("─" * 78)
        print(f"Привязано за сеанс: {added}.  "
              f"Всего с картами: {bound} из {len(candidates)}.")
    return 0


# ----------------------------------------------------------------------- scan


def cmd_scan(args) -> int:
    view = ConsoleView(sound=not args.no_sound)
    try:
        guard_mock(args)
        folder = choose_subject(args, args.tables)
        require_rosters(folder)
    except Interrupted as exc:
        if str(exc):
            print(exc)
        return 1

    try:
        reader = make_reader(
            args.mode, port=args.port, on_status=view.status,
            mock_lines=DEMO_LINES, stop_after=args.seconds,
            should_stop=make_stop_watcher(),
        )
    except ReaderUnavailable as exc:
        print(exc)
        return 1

    with Storage(args.db) as storage:
        subject = storage.get_or_create_subject(folder.name)
        view.banner(f"{folder.name} — {date.today().strftime(DATE_INPUT)}")
        view.status(f"Источник: {reader.description}")
        view.status(f"Групп в предмете: {folder.group_count}")
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

        marked, duplicates, unknown = pipeline.tally_line(tally) if tally else (0, 0, 0)
        view.summary(marked, duplicates, unknown)

        if tally and pipeline.failed_count(tally):
            print(f"\n!!! Не удалось записать отметок: {pipeline.failed_count(tally)}.")
            print("    Эти студенты НЕ отмечены, их надо провести заново.")

        if failure is not None:
            print(f"\n!!! Сеанс прерван ошибкой: {failure}")
            print("    Записанное до этого момента сохранено и будет выгружено.")

        if not args.no_export:
            _fill_and_report(storage, subject, folder)
    return 1 if failure is not None else 0


def _fill_and_report(storage: Storage, subject: Subject, folder: R.SubjectFolder) -> None:
    print()
    for result in journal.fill_subject(storage, subject, folder):
        if not result.ok:
            print(f"  {result.path.name}: {result.error}")
            continue
        print(f"  Заполнено: {result.path.name}  ({result.group_name}, "
              f"{result.students} чел., дат {len(result.dates)})")
        if result.missing:
            print("    Не нашлись в списке — связь с картой разорвана:")
            for name in result.missing:
                print(f"      {name}")


# ---------------------------------------------------------------------- whois


def cmd_whois(args) -> int:
    view = ConsoleView(sound=False)
    try:
        reader = make_reader(
            args.mode, port=args.port, on_status=view.status,
            mock_lines=DEMO_LINES, stop_after=args.seconds,
            should_stop=make_stop_watcher(),
        )
    except ReaderUnavailable as exc:
        print(exc)
        return 1

    with Storage(args.db) as storage:
        view.banner("Чья карта")
        view.status(f"Прикладывайте карту. Отметки не записываются. {STOP_HINT}.")
        print()
        try:
            for scan in reader.scans():
                student = storage.find_student(scan.code)
                if student:
                    print(f"  {scan.code.canonical}  →  {student.full_name} "
                          f"({student.group_name})")
                else:
                    print(f"  {scan.code.canonical}  →  КАРТА НЕ ПРИВЯЗАНА")
        except KeyboardInterrupt:
            print()
    return 0


# ------------------------------------------------------------------------ меню


MENU = [
    ("1", "Отмечать студентов", "scan"),
    ("2", "Привязать карты к студентам", "enroll"),
    ("3", "Кто пришёл сегодня", "report"),
    ("4", "Опоздавшие — показывают конспект", "late"),
    ("5", "Чья это карта", "whois"),
    ("6", "Предметы и группы", "subjects"),
    ("7", "Кому какая карта принадлежит", "students-list"),
    ("8", "Заполнить файлы групп", "export"),
    ("9", "Обновить базу из файлов групп", "import"),
    ("10", "Проверить оборудование", "doctor"),
]

_MENU_ARGV = {"students-list": ["students", "list"], "import": ["import", "--choose"]}


_EXIT_WORDS = ("0", "q", "й", "выход", "exit", "quit")


def cmd_menu(args) -> int:
    """Меню работает циклом: после действия возвращаемся сюда, а не выходим.

    Раньше программа завершалась после каждой команды, и ради второго
    действия приходилось запускать её заново.
    """
    while True:
        print()
        print("  Учёт посещаемости по RFID-картам")
        print("  " + "─" * 44)
        for key, title, _ in MENU:
            print(f"   {key:>2}. {title}")
        print("    0. Выход        (или Ctrl+C)")
        print()

        try:
            choice = _ask("  Что делаем? ").lower()
        except Interrupted as exc:
            print(exc)
            return 1
        except KeyboardInterrupt:
            print("\nДо свидания.")
            return 0

        if choice in _EXIT_WORDS or not choice:
            print("До свидания.")
            return 0

        action = next((a for key, _, a in MENU if key == choice), None)
        if action is None:
            print("  Нет такого пункта.")
            continue

        # Общие флаги надо протащить: иначе выбранный каталог потеряется.
        argv = ["--db", str(args.db), "--tables", str(args.tables)]
        argv += _MENU_ARGV.get(action, [action])
        try:
            main(argv)
        except KeyboardInterrupt:
            print()

        try:
            again = _ask("\n  Enter — в меню, 0 — выход: ").lower()
        except (Interrupted, KeyboardInterrupt):
            print("\nДо свидания.")
            return 0
        if again in _EXIT_WORDS:
            print("До свидания.")
            return 0


# -------------------------------------------------------------- диагностика


def cmd_ports(_args) -> int:
    ports = available_ports()
    if not ports:
        print("COM-портов в системе нет.")
        return 1
    print(f"Найдено портов: {len(ports)}")
    for port in ports:
        print(f"  {port}{'   <-- считыватель' if port.is_reader else ''}")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    print("Проверка системы")
    print("─" * 60)

    port = find_reader_port()
    if port:
        print(f"[ OK ] считыватель найден: {port}")
    else:
        ok = False
        print("[ !! ] считыватель не найден")
        print(no_port_explanation())

    for module, package in (("serial", "pyserial"), ("openpyxl", "openpyxl")):
        try:
            __import__(module)
            print(f"[ OK ] {package} установлен")
        except ImportError:
            ok = False
            print(f"[ !! ] нет {package}:  py -m pip install -r requirements.txt")

    folders = R.discover_subjects(args.tables)
    if folders:
        print(f"[ OK ] предметов (папок в {args.tables}): {len(folders)}")
        for folder in folders:
            print(f"         {folder.name} — {folder.group_count} групп(ы)")
    else:
        print(f"[ -- ] в {args.tables} нет папок предметов со списками")

    misplaced = R.misplaced_rosters(args.tables)
    if misplaced:
        print(f"[ ?? ] {len(misplaced)} файл(ов) лежат в самой {args.tables}, "
              "мимо папок предметов:")
        for path in misplaced:
            print(f"         {path.name}")


    db_path = Path(args.db)
    if db_path.exists():
        with Storage(db_path) as storage:
            print(f"[ OK ] база: {db_path}  карт привязано: {storage.count_cards()}")
            unknown = storage.unknown_cards()
            if unknown:
                print(f"[ ?? ] непривязанных карт: {len(unknown)} — см. rfid unknown list")
    else:
        print(f"[ -- ] базы пока нет, будет создана: {db_path}")

    print("─" * 60)
    print("Готово к работе." if ok else "Есть проблемы, см. выше.")
    return 0 if ok else 1


# ------------------------------------------------------------------- предметы


def _report_misplaced(tables_dir: Path) -> None:
    misplaced = R.misplaced_rosters(tables_dir)
    if not misplaced:
        return
    print(f"\nЛежат в самой {tables_dir}, мимо папок предметов, и не используются:")
    for path in misplaced:
        print(f"   {path.name}")


def cmd_subjects(args) -> int:
    folders = R.discover_subjects(args.tables)
    if not folders:
        print(f"В {args.tables} нет ни одной папки предмета со списками групп.")
        print("Создайте папку с названием предмета и положите туда списки.")
        _report_misplaced(args.tables)
        return 0

    for folder in folders:
        print(f"\n{folder.name}")
        for path in folder.rosters:
            try:
                parsed = R.read_roster(path)
                print(f"   {parsed.group_name:<18} {len(parsed.students):>3} чел.   {path.name}")
            except R.RosterError as exc:
                print(f"   {path.name}: {exc}")

    _report_misplaced(args.tables)
    return 0


# ------------------------------------------------------------------ студенты


def cmd_students(args) -> int:
    with Storage(args.db) as storage:
        if args.students_action == "list":
            students = storage.list_students(with_card=True)
            if not students:
                print("Ни одна карта ещё не привязана.")
                return 0
            print(f"{'Код карты':<12} {'ФИО':<38} Группа")
            print("─" * 70)
            for student in students:
                print(f"{student.card_code:<12} {student.full_name:<38} {student.group_name}")
            print(f"\nВсего: {len(students)}")
            return 0

        if args.students_action == "remove":
            try:
                code = parse_manual(args.code)
            except CardCodeError as exc:
                raise SystemExit(str(exc))
            print("Привязка снята." if storage.remove_student(code) else "Такой карты нет.")
            return 0
    return 1


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


# --------------------------------------------------------------------- отчёты


def cmd_report(args) -> int:
    day = _parse_date(args.date)
    try:
        folder = choose_subject(args, args.tables)
    except Interrupted as exc:
        if str(exc):
            print(exc)
        return 1

    with Storage(args.db) as storage:
        subject = storage.find_subject(folder.name)
        if subject is None:
            print(f"По предмету «{folder.name}» отметок ещё не было.")
            return 0

        rows = storage.day_rows(day, subject, args.group)
        print()
        print(f"{folder.name} — {day.strftime(DATE_INPUT)}")
        print("─" * 70)
        print(f"{'ФИО':<38} {'Группа':<16} {'Время':<8} Статус")
        for row in rows:
            time_text = row.at.strftime("%H:%M") if row.present else "—"
            status = "отметился" if row.present else "НЕ отметился"
            print(f"{row.student.full_name:<38} {row.student.group_name:<16} "
                  f"{time_text:<8} {status}")

        print("─" * 70)
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
        if not days:
            print(f"По предмету «{folder.name}» отметок ещё не было.")
            return 0
        day = _parse_date(args.date) if args.date else _choose_day(days)

        # Только именные приходы — ровно те, что попадают в файлы групп,
        # чтобы список совпадал с жёлтой подсветкой в таблице.
        arrivals = {row.student: row.at for row in storage.day_rows(day, subject)
                    if row.present}
        late = R.late_arrivals(arrivals)

        minutes = int(R.LATE_AFTER.total_seconds() // 60)
        print()
        print(f"{folder.name} — {day.strftime(DATE_INPUT)}")
        print(f"Опоздали ({minutes} мин и больше от начала пары) — показывают конспект")
        print("─" * 70)
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
        print("─" * 70)
        print(f"Всего: {len(late)}")
    return 0


def _choose_day(days: list[date]) -> date:
    """Выбрать день из тех, когда были отметки. Enter — последний."""
    recent = sorted(days, reverse=True)[:10]
    print()
    print("  День занятия:")
    for index, day in enumerate(recent, start=1):
        print(f"    {index}. {day.strftime(DATE_INPUT)}")
    print("    0. назад")
    choice = _ask("\n  Выберите номер (Enter — последний): ")
    if not choice:
        return recent[0]
    if choice in ("0", "q"):
        raise Cancelled
    if choice.isdigit() and 1 <= int(choice) <= len(recent):
        return recent[int(choice) - 1]
    raise Interrupted("  Нет такого пункта.")


def cmd_reset(args) -> int:
    """Очистить базу. Показывает, что удаляет, и спрашивает подтверждение."""
    with Storage(args.db) as storage:
        marks = storage.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
        subjects = len(storage.list_subjects())
        cards = storage.count_cards()

        what = args.reset_what
        plan = []
        if what in ("marks", "all"):
            plan.append(f"отметок: {marks}")
            plan.append(f"предметов в базе: {subjects}")
        if what in ("cards", "all"):
            plan.append(f"привязок карт: {cards}")

        print("Будет удалено:")
        for line in plan:
            print(f"   {line}")
        print("\nФайлы групп в tables/ не трогаются.")

        if not args.yes:
            if not _confirm("\nПродолжить?"):
                print("Отменено.")
                return 0

        if what in ("marks", "all"):
            # Предметы уходят вместе с отметками: в базе они нужны только
            # как якорь для журнала, а сам список предметов — это папки.
            storage.conn.execute("DELETE FROM attendance")
            storage.conn.execute("DELETE FROM subjects")
        if what == "all":
            storage.conn.execute("DELETE FROM students")
        elif what == "cards":
            # Люди остаются: за ними могут числиться отметки из таблиц.
            storage.unbind_all_cards()

        print("\nГотово. Сейчас в базе:")
        print(f"   отметок: {storage.conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]}")
        print(f"   предметов: {len(storage.list_subjects())}")
        print(f"   привязок карт: {storage.count_cards()}")
    return 0


def cmd_export(args) -> int:
    """Проставить отметки во всех списках групп."""
    with Storage(args.db) as storage:
        filled = journal.fill_all(storage, args.tables)
    if not filled:
        print("Заполнять нечего: нет предметов с отметками.")
        return 0
    return 1 if _print_fill(filled) else 0


def _print_fill(filled: dict[str, list[journal.FillResult]]) -> int:
    """Показать итог записи в файлы. Возвращает число файлов с ошибкой."""
    problems = 0
    for subject_name, results in filled.items():
        print(f"\n{subject_name}")
        for result in results:
            if result.ok:
                print(f"   {result.path.name}  ({result.group_name}, "
                      f"дат {len(result.dates)})")
                for name in result.missing:
                    print(f"      не найден в списке: {name}")
            else:
                problems += 1
                print(f"   {result.path.name}: {result.error}")
    return problems


def cmd_import(args) -> int:
    """Обновить базу из файлов групп.

    Обычно — добавить недостающее, главнее база. --sync — полная
    синхронизация, главнее таблицы: сперва пробный прогон и список того,
    что будет удалено, потом подтверждение, и только тогда запись.
    """
    if args.subject:
        folders = [_subject_by_name(args.subject, args.tables)]
    else:
        folders = R.discover_subjects(args.tables)
    if not folders:
        print(_nothing_found(args.tables))
        return 1

    sync = args.sync or (args.choose and _choose_import_mode())

    with Storage(args.db) as storage:
        if not sync:
            results = {f.name: journal.import_subject(storage, f) for f in folders}
            problems = _print_import(results)
            added = sum(r.added for rs in results.values() for r in rs)
            conflicts = sum(r.conflicts for rs in results.values() for r in rs)
            print(f"\nДобавлено в базу: {added}.")
            if conflicts:
                print(f"Время расходится с базой: {conflicts} — оставлено как в базе. "
                      "Взять из таблиц: полная синхронизация.")
            return 1 if problems else 0

        plan = {f.name: journal.import_subject(storage, f, sync=True, dry_run=True)
                for f in folders}
        print("\nПолная синхронизация — главнее таблицы. Что изменится в базе:")
        _print_import(plan)
        removals = [(r.group_name, rm) for rs in plan.values() for r in rs for rm in r.removed]
        if removals:
            print("\nБудут УДАЛЕНЫ отметки, против которых в таблице прочерк или пусто:")
            for group, rm in removals:
                print(f"   {rm.day.strftime('%d.%m')} {rm.at.strftime('%H:%M')}  "
                      f"{rm.full_name}  ({group})")
        if not args.yes:
            if not _confirm("\nВыполнить синхронизацию?"):
                print("Отменено, база не изменилась.")
                return 0

        problems = 0
        for folder in folders:
            imported, filled = journal.sync_subject(storage, folder)
            problems += sum(1 for r in imported if not r.ok)
            problems += _print_fill({folder.name: filled})
    print("\nГотово: база и файлы групп совпадают." if not problems
          else "\nГотово, но не со всеми файлами — см. выше.")
    return 1 if problems else 0


def _choose_import_mode() -> bool:
    """Спросить режим обновления. True — полная синхронизация."""
    print()
    print("  Как обновить базу:")
    print("    1. Добавить недостающее — главнее база")
    print("       берутся отметки из таблиц, которых в базе нет;")
    print("       если время расходится, остаётся как в базе")
    print("    2. Полная синхронизация — главнее таблицы")
    print("       время берётся из таблиц, отметки против прочерков удаляются,")
    print("       затем файлы переписываются из базы; перед удалением спросит")
    print("    0. назад")
    choice = _ask("\n  Выберите номер: ")
    if choice in ("0", "", "q"):
        raise Cancelled
    if choice not in ("1", "2"):
        raise Interrupted("  Нет такого пункта.")
    return choice == "2"


def _print_import(results: dict[str, list[journal.ImportResult]]) -> int:
    """Показать итог импорта по файлам. Возвращает число файлов с ошибкой."""
    problems = 0
    for subject_name, items in results.items():
        print(f"\n{subject_name}")
        for result in items:
            if not result.ok:
                problems += 1
                print(f"   {result.path.name}: {result.error}")
                continue
            parts = [f"новых {result.added}"]
            if result.updated:
                parts.append(f"время из таблицы {result.updated}")
            if result.conflicts:
                parts.append(f"расходится, оставлено из базы {result.conflicts}")
            if result.removed:
                parts.append(f"удалить {len(result.removed)}")
            parts.append(f"без изменений {result.same}")
            days = ", ".join(d.strftime("%d.%m") for d in result.dates) or "дат нет"
            print(f"   {result.path.name}  ({result.group_name}): "
                  f"{', '.join(parts)}  [{days}]")
            if result.newer:
                print(f"      сделаны позже сохранения файла, не трогаются: {result.newer}")
            for where, value in result.skipped:
                print(f"      не понял ячейку «{value}» ({where}) — оставлена как есть")
    return problems


# --------------------------------------------------------------------- разбор


def _add_reader_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("auto", "serial", "keyboard", "mock"), default="auto")
    parser.add_argument("--port", help="COM-порт, если автопоиск не сработал")
    parser.add_argument("--seconds", type=float, help="остановиться через N секунд")
    parser.add_argument("--no-sound", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfid", description="Учёт посещаемости по RFID-картам IronLogic."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"файл базы (умолч. {DEFAULT_DB})")
    parser.add_argument("--tables", type=Path, default=TABLES_DIR,
                        help=f"папка с предметами (умолч. {TABLES_DIR})")
    parser.set_defaults(func=cmd_menu)
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="отмечать студентов")
    _add_reader_flags(scan)
    scan.add_argument("--subject", help="имя папки-предмета; иначе будет предложен выбор")
    scan.add_argument("--debounce", type=float, default=pipeline.DEFAULT_DEBOUNCE)
    scan.add_argument("--no-export", action="store_true",
                      help="не трогать файлы групп при выходе")
    scan.set_defaults(func=cmd_scan)

    enroll = subparsers.add_parser("enroll", help="привязать карты к студентам из списка")
    _add_reader_flags(enroll)
    enroll.add_argument("--subject", help="имя папки-предмета")
    enroll.set_defaults(func=cmd_enroll)

    whois = subparsers.add_parser("whois", help="приложить карту и увидеть, чья она")
    _add_reader_flags(whois)
    whois.set_defaults(func=cmd_whois)

    subjects = subparsers.add_parser("subjects", help="предметы и группы")
    subjects.set_defaults(func=cmd_subjects)

    students = subparsers.add_parser("students", help="кому какая карта принадлежит")
    students_sub = students.add_subparsers(dest="students_action", required=True)
    students.set_defaults(func=cmd_students)
    students_sub.add_parser("list", help="показать привязки")
    s_remove = students_sub.add_parser("remove", help="снять привязку карты")
    s_remove.add_argument("code")

    unknown = subparsers.add_parser("unknown", help="непривязанные карты")
    unknown.set_defaults(func=cmd_unknown)

    report = subparsers.add_parser("report", help="кто пришёл, кого нет")
    report.add_argument("--date", help="ДД.ММ.ГГГГ, по умолчанию сегодня")
    report.add_argument("--group", help="только эта группа")
    report.add_argument("--subject", help="имя папки-предмета")
    report.set_defaults(func=cmd_report)

    late = subparsers.add_parser("late", help="опоздавшие — показывают конспект")
    late.add_argument("--date", help="ДД.ММ.ГГГГ; иначе будет предложен выбор")
    late.add_argument("--subject", help="имя папки-предмета")
    late.set_defaults(func=cmd_late)

    export_cmd = subparsers.add_parser("export", help="проставить отметки в файлах групп")
    export_cmd.set_defaults(func=cmd_export)

    import_cmd = subparsers.add_parser(
        "import", help="обновить базу из файлов групп (по умолчанию главнее база)"
    )
    import_cmd.add_argument("--subject", help="только эта папка-предмет")
    import_cmd.add_argument(
        "--sync", action="store_true",
        help="полная синхронизация: главнее таблицы, затем файлы переписываются из базы",
    )
    import_cmd.add_argument("--yes", action="store_true", help="без подтверждения")
    # Для меню: спросить режим, а не брать обычный молча.
    import_cmd.add_argument("--choose", action="store_true", help=argparse.SUPPRESS)
    import_cmd.set_defaults(func=cmd_import)

    reset = subparsers.add_parser("reset", help="очистить базу (файлы групп не трогает)")
    reset.add_argument("reset_what", choices=("marks", "cards", "all"),
                       help="marks — отметки и предметы, cards — привязки карт, all — всё")
    reset.add_argument("--yes", action="store_true", help="без подтверждения")
    reset.set_defaults(func=cmd_reset)

    ports = subparsers.add_parser("ports", help="список COM-портов")
    ports.set_defaults(func=cmd_ports)

    doctor = subparsers.add_parser("doctor", help="проверить железо, зависимости и базу")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Interrupted as exc:
        if str(exc):
            print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано.")
        return 130
