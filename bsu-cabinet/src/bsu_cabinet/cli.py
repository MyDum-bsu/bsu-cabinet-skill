"""mo-cabinet — CLI автоматизации кабинета БГУ.

Команды:
  workload <xlsx> [--year Г]   — разобрать форму ШТ → документ bsu.workload/1
  snapshot <raw.json>          — нормализовать сырой снимок ИП → bsu.snapshot/1
  plan-diff <workload.json> <snapshot.json> — дифф ИП → документ bsu.diff/1
  parse-roster <xlsx>          — разобрать ведомость группы
  card-plan <spec.yaml>        — развернуть card-spec в список операций (dry-run)
  schedule-index --folder URL | --file dump.html
                               — индекс публичной папки расписаний → bsu.index/1
  schedule-find <index.json> --year Y --semester N --course N --teacher Ф
      [--book file.xlsx | --book-id ID | --url URL]
      [--from D --to D] [--even-week-start D] [--odd-week-start D]
      [--exceptions file.json] — личные слоты и даты → bsu.slots/1
  snapshot-card <raw.json>     — нормализовать сырую таблицу карточки → bsu.card-snapshot/1
  topics <topics.json>         — тематический план → bsu.topics/1
  card-diff <slots.json> --cards <file|-> --plan <file|->
      [--topics topics.json] [--facts facts.json] [--as-of YYYY-MM-DD]
                               — предпросмотр карточек (read-only) → bsu.diff/1
  login [--sms adb --adb-serial SERIAL] — вход через bsu-browser, SMS вручную по умолчанию
  dump                         — после входа: дамп структуры кабинета (меню, формы)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path


def _require_args(rest: list[str], count: int, usage: str) -> None:
    """Единая понятная ошибка CLI вместо IndexError на неполной команде."""
    if len(rest) < count:
        print(f"Недостаточно аргументов. Использование: {usage}", file=sys.stderr)
        sys.exit(2)


def _json_default(o: object) -> object:
    if isinstance(o, Fraction):
        return float(o) if o.denominator != 1 else o.numerator
    return str(o)


def cmd_workload(path: str, year: str | None = None) -> None:
    from .schema import dumps, exit_code
    from .workload import workload_document

    doc = workload_document(path, year=year)
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_snapshot(raw_path: str) -> None:
    from .plan import snapshot_document
    from .schema import dumps, exit_code

    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    doc = snapshot_document(raw)
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_plan_diff(workload_path: str, snapshot_path: str) -> None:
    from .plan import plan_diff_document
    from .schema import dumps, exit_code

    doc = plan_diff_document(
        json.loads(Path(workload_path).read_text(encoding="utf-8")),
        json.loads(Path(snapshot_path).read_text(encoding="utf-8")),
    )
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_parse_roster(path: str) -> None:
    from .roster import parse_group_xlsx

    r = parse_group_xlsx(path)
    print(json.dumps(asdict(r), ensure_ascii=False, indent=2, default=_json_default))


def cmd_card_plan(path: str) -> None:
    from .cardspec import load_card_spec

    spec = load_card_spec(path)
    print(f"Дисциплина {spec.discipline_code}, {spec.academic_year}, семестр {spec.semester}")
    print(f"Операций: {len(spec.fills)}")
    for f in spec.fills:
        print(f"  {f.group_id} пара {f.lesson_n:>2} {f.date} {f.time} ауд.{f.room} — {f.topic}")


def cmd_schedule_index(rest: list[str]) -> None:
    from .schedule import fetch_index_document, index_document
    from .schema import dumps, exit_code

    if "--folder" in rest:
        url = rest[rest.index("--folder") + 1]
        doc = fetch_index_document(url)
    elif "--file" in rest:
        p = Path(rest[rest.index("--file") + 1])
        doc = index_document(
            p.read_bytes(), source=str(p),
            observed_at=datetime.fromtimestamp(p.stat().st_mtime)
            .isoformat(timespec="seconds"))
    else:
        print("schedule-index: нужен --folder URL или --file dump.html",
              file=sys.stderr)
        sys.exit(2)
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_schedule_find(rest: list[str]) -> None:
    import argparse

    from .schedule import schedule_find_document
    from .schema import dumps, exit_code

    ap = argparse.ArgumentParser(prog="mo-cabinet schedule-find")
    ap.add_argument("index")
    ap.add_argument("--year", required=True)
    ap.add_argument("--semester", required=True, type=int)
    ap.add_argument("--course", required=True, type=int)
    ap.add_argument("--teacher", required=True)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--book")
    src.add_argument("--book-id")
    src.add_argument("--url")
    ap.add_argument("--from", dest="period_from")
    ap.add_argument("--to", dest="period_to")
    ap.add_argument("--even-week-start")
    ap.add_argument("--odd-week-start")
    ap.add_argument("--exceptions")
    ns = ap.parse_args(rest)

    year = re.sub(r"\s+", "", ns.year).replace("/", "-").replace("–", "-") \
        .replace("—", "-")
    exc = None
    if ns.exceptions:
        exc = json.loads(Path(ns.exceptions).read_text(encoding="utf-8"))
    book_bytes = Path(ns.book).read_bytes() if ns.book else None
    doc = schedule_find_document(
        json.loads(Path(ns.index).read_text(encoding="utf-8")),
        year=year, semester=ns.semester, course=ns.course,
        teacher=ns.teacher,
        book_bytes=book_bytes, book_id=ns.book_id, url=ns.url,
        period_from=ns.period_from, period_to=ns.period_to,
        even_week_start=ns.even_week_start,
        odd_week_start=ns.odd_week_start, exceptions=exc)
    print(dumps(doc))
    sys.exit(exit_code(doc))


def _load_json_arg(value: str) -> dict:
    """JSON из файла; '-' — stdin."""
    if value == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(value).read_text(encoding="utf-8"))


def cmd_card_snapshot(path: str) -> None:
    from .carddiff import card_snapshot_document
    from .schema import dumps, exit_code

    doc = card_snapshot_document(_load_json_arg(path))
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_topics(path: str) -> None:
    from .carddiff import topics_document
    from .schema import dumps, exit_code

    doc = topics_document(_load_json_arg(path))
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_card_diff(rest: list[str]) -> None:
    import argparse
    from datetime import date

    from .carddiff import (
        card_diff_document,
        card_snapshot_document,
        topics_document,
    )
    from .plan import snapshot_document
    from .schema import dumps, exit_code

    ap = argparse.ArgumentParser(prog="mo-cabinet card-diff")
    ap.add_argument("slots")
    ap.add_argument("--cards", required=True,
                    help="bsu.card-snapshot/1 или сырая таблица (файл|'-')")
    ap.add_argument("--plan", required=True,
                    help="bsu.snapshot/1 или сырой снимок ИП (файл|'-')")
    ap.add_argument("--topics", help="bsu.topics/1 или сырой план тем")
    ap.add_argument("--facts", help="bsu.facts/1")
    ap.add_argument("--name-map",
                    help="JSON-файл подтверждённых соответствий ФИО")
    ap.add_argument("--as-of", dest="as_of",
                    help="зафиксированная дата Europe/Minsk; без флага — сегодня")
    ns = ap.parse_args(rest)

    cards = _load_json_arg(ns.cards)
    if cards.get("schema") != "bsu.card-snapshot/1":
        cards = card_snapshot_document(cards)
    plan = _load_json_arg(ns.plan)
    if plan.get("schema") != "bsu.snapshot/1":
        plan = snapshot_document(plan)
    topics = None
    if ns.topics:
        topics = _load_json_arg(ns.topics)
        if topics.get("schema") != "bsu.topics/1":
            topics = topics_document(topics)
    facts = _load_json_arg(ns.facts) if ns.facts else None
    if ns.as_of:
        as_of = date.fromisoformat(ns.as_of)
    else:
        from zoneinfo import ZoneInfo

        as_of = datetime.now(ZoneInfo("Europe/Minsk")).date()

    doc = card_diff_document(
        _load_json_arg(ns.slots), cards, plan,
        topics_doc=topics, facts_doc=facts,
        name_map=_load_json_arg(ns.name_map) if ns.name_map else None,
        as_of=as_of)
    print(dumps(doc))
    sys.exit(exit_code(doc))


def cmd_dump() -> None:
    from .browser import BASE_URL, launch, shot

    pw, ctx, page = launch()
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)
        shot(page, "dump-main")
        print(page.url)
        print(page.title())
        print(page.locator("body").inner_text()[:4000])
    finally:
        ctx.close()
        pw.stop()


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if args else 2)
    cmd, *rest = args
    if cmd == "workload":
        year = None
        if "--year" in rest:
            i = rest.index("--year")
            if i + 1 >= len(rest):
                print("workload: --year требует значение", file=sys.stderr)
                sys.exit(2)
            year = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        _require_args(rest, 1, "mo-cabinet workload <xlsx> [--year Г]")
        cmd_workload(rest[0], year=year)
    elif cmd == "snapshot":
        _require_args(rest, 1, "mo-cabinet snapshot <raw.json>")
        cmd_snapshot(rest[0])
    elif cmd == "plan-diff":
        _require_args(rest, 2, "mo-cabinet plan-diff <workload.json> <snapshot.json>")
        cmd_plan_diff(rest[0], rest[1])
    elif cmd == "parse-roster":
        _require_args(rest, 1, "mo-cabinet parse-roster <xlsx>")
        cmd_parse_roster(rest[0])
    elif cmd == "card-plan":
        _require_args(rest, 1, "mo-cabinet card-plan <spec.yaml>")
        cmd_card_plan(rest[0])
    elif cmd == "schedule-index":
        cmd_schedule_index(rest)
    elif cmd == "schedule-find":
        cmd_schedule_find(rest)
    elif cmd == "snapshot-card":
        _require_args(rest, 1, "mo-cabinet snapshot-card <raw.json>")
        cmd_card_snapshot(rest[0])
    elif cmd == "topics":
        _require_args(rest, 1, "mo-cabinet topics <topics.json>")
        cmd_topics(rest[0])
    elif cmd == "card-diff":
        cmd_card_diff(rest)
    elif cmd == "login":
        from .browser_cli import login_command

        login_command(rest)
    elif cmd == "dump":
        cmd_dump()
    else:
        print(f"Неизвестная команда: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
