"""Расписание: публичный индекс папки, выбор редакции книги, личные слоты
и вычисленные даты. Документы bsu.index/1 и bsu.slots/1.

Архитектурно разделены получение и разбор: сетевую часть выполняет только
инъецируемый ``fetch(url) -> bytes`` (в тестах — подставной транспорт),
всё остальное — чистый разбор байтов/HTML/XLSX. Разбор опирается на
семантику (объединённые подписи дней, строки групп/подгрупп, метки
чётности, текст назначения), а не на фиксированные адреса; объединения
переносятся только внутри их диапазона. Вид, фаза, даты и преподаватель
не угадываются: без явного источника — ambiguity/issue, не значение.

Контракт — docs/bsu-cabinet/release-spec.md, очередь 5; словарь —
bsu-cabinet/GLOSSARY.md.
"""

from __future__ import annotations

import hashlib
import io
import re
import urllib.request
import zipfile
from datetime import date, datetime, timedelta

import openpyxl
from openpyxl.utils import get_column_letter

from .schema import envelope, issue, status_of
from .workload import _YEAR_RE, _eff, _merged_map, _norm

MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_BOOK_BYTES = 25 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
# Учебный семестр исчисляется месяцами; два полных года — уже вне
# поддерживаемого сценария. Эти пределы защищают развёртку повторяющихся
# слотов от гигантских диапазонов и чрезмерного вывода.
MAX_PERIOD_DAYS = 731
MAX_SERIES_INSTANCES = 128


class SeriesLimitError(ValueError):
    """Превышен безопасный объём развёртки повторяющегося слота."""


DRIVE_DOWNLOAD = (
    "https://drive.usercontent.google.com/download"
    "?id={id}&export=download&confirm=t"
)

_WEEKDAYS = {
    "понедельник": "monday", "пн": "monday",
    "вторник": "tuesday", "вт": "tuesday",
    "среда": "wednesday", "ср": "wednesday",
    "четверг": "thursday", "чт": "thursday",
    "пятница": "friday", "пт": "friday",
    "суббота": "saturday", "сб": "saturday",
    "воскресенье": "sunday", "вс": "sunday",
}
_WEEKDAY_NUM = {name: i for i, name in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday",
     "saturday", "sunday"])}

_KIND_MARKS = [
    ("laboratory", re.compile(r"лабораторн|лаб\.?\b", re.IGNORECASE)),
    ("lecture", re.compile(r"лекци|лек\.?\b", re.IGNORECASE)),
    ("practice", re.compile(r"практич|практ\.?\b|семинар|\bпр\.?\b", re.IGNORECASE)),
    ("usr", re.compile(r"\bуср\b|самостоятельн", re.IGNORECASE)),
    ("consult", re.compile(r"консульт", re.IGNORECASE)),
]

_TEACHER_RE = re.compile(
    r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s+([А-ЯЁ])\.\s*([А-ЯЁ])\.")
_TEACHER_REQ_RE = re.compile(
    r"^\s*([А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z][а-яёa-z]+)?)"
    r"(?:\s+([А-ЯЁA-Z])\.\s*([А-ЯЁA-Z])\.?)?\s*$")

_TIME_RE = re.compile(
    r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
_TIME_AT_RE = re.compile(r"в\s*(\d{1,2})[.:](\d{2})")
_BOUND_FROM_RE = re.compile(r"(?<![\d.])с\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")
_BOUND_TO_RE = re.compile(r"(?<![\d.])по\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")
_ODD_RE = re.compile(r"не\s*ч[её]тн", re.IGNORECASE)
_EVEN_RE = re.compile(r"ч[её]тн", re.IGNORECASE)
_EVERY_RE = re.compile(r"еженедельн|кажд\w*\s+недел", re.IGNORECASE)

_FOLDER_ID_RE = re.compile(r"/folders/([-\w]+)|[?&]id=([-\w]+)")
_ENTRY_RE = re.compile(
    r'\[?"([-A-Za-z0-9_]{3,})",\["[^"]*"\],"([^"]+)","([^"]*)","([^"]*)"')
_CARD_TAG_RE = re.compile(r"<[^>]+data-id=\"([^\"]+)\"[^>]*>")
_TOOLTIP_RE = re.compile(r'data-tooltip="([^"]*)"')
_XLSX_NAME_RE = re.compile(r"[^\"—<>]*?\.xlsx?", re.IGNORECASE)

_ALLOWED_HOSTS = {"drive.google.com", "drive.usercontent.google.com"}
_COURSE_RE = re.compile(r"(\d)\s*курс|курс\s*(\d)", re.IGNORECASE)
_SEMESTER_RE = re.compile(r"(\d)\s*семестр|семестр\s*(\d)", re.IGNORECASE)


def _allowed_url(url: str) -> bool:
    """Разрешённые адреса транспорта: только HTTPS публичного источника/Drive.
    Локальные данные — через --file/--book, не через fetch."""
    from urllib.parse import urlsplit
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    return p.scheme == "https" and (p.hostname or "") in _ALLOWED_HOSTS


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Проверка адреса назначения при каждом переходе."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise ValueError(f"url-not-allowed: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(url: str) -> bytes:
    if not _allowed_url(url):
        raise ValueError(f"url-not-allowed: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "mo-cabinet"})
    opener = urllib.request.build_opener(_GuardedRedirect)
    with opener.open(req, timeout=30) as resp:
        return resp.read(MAX_BOOK_BYTES + 1)


# ---------------------------------------------------------------------------
# Индекс публичной папки
# ---------------------------------------------------------------------------

def _hint_course(name: str) -> int | None:
    m = _COURSE_RE.search(name)
    return int(m.group(1) or m.group(2)) if m else None


def _hint_semester(name: str) -> int | None:
    m = _SEMESTER_RE.search(name)
    return int(m.group(1) or m.group(2)) if m else None


def _hint_year(name: str) -> str | None:
    m = _YEAR_RE.search(name)
    return re.sub(r"\s+", "", m.group(0)).replace("—", "-").replace("–", "-").replace("/", "-") if m else None


def parse_index_html(blob: bytes) -> tuple[list[dict], bool]:
    """Записи файлов из разметки публичной папки.

    Возвращает (entries, markup_recognized): каждая запись —
    {id,name,mime,modified}; markup_recognized=False, если разметка
    не содержит ни одной распознанной записи (изменившийся формат —
    блокировка, а не «пустая папка»).
    """
    text = blob.decode("utf-8", errors="replace")
    by_id: dict[str, dict] = {}
    for m in _ENTRY_RE.finditer(text):
        fid, name, mime, modified = m.groups()
        by_id.setdefault(fid, {"id": fid, "name": name, "mime": mime,
                               "modified": modified or None})
    # карточки файлов: div data-id + data-tooltip («имя — тип файла»);
    # двойные элементы UI сливаются по ID
    for tag in _CARD_TAG_RE.finditer(text):
        fid = tag.group(1)
        tip = _TOOLTIP_RE.search(tag.group(0))
        name = ""
        if tip:
            nm = _XLSX_NAME_RE.search(tip.group(1))
            name = nm.group(0).strip() if nm else ""
        prev = by_id.get(fid)
        if prev is None or (name and not prev["name"]):
            by_id[fid] = {"id": fid, "name": name or (prev or {}).get("name", ""),
                          "mime": (prev or {}).get("mime"), "modified": None}
    return list(by_id.values()), bool(by_id)


def index_document(blob: bytes, *, source: str, observed_at: str) -> dict:
    """Документ bsu.index/1 из разметки папки (локальные байты)."""
    issues: list[dict] = []
    m = _FOLDER_ID_RE.search(source)
    folder_id = (m.group(1) or m.group(2)) if m else None
    if len(blob) > MAX_INDEX_BYTES:
        issues.append(issue(
            "index-too-large", source=source,
            expected=f"≤{MAX_INDEX_BYTES} байт", actual=len(blob),
            next="индекс усечён — выбор редакции по неполному списку "
                 "запрещён"))
        return envelope("bsu.index/1", status_of(issues), {
            "folderId": folder_id,
            "observedAt": observed_at,
            "source": source,
            "books": [],
        }, issues)
    entries, recognized = parse_index_html(blob)
    books: list[dict] = []
    if not recognized:
        issues.append(issue(
            "markup-unrecognized", source=source,
            expected="разметка публичной папки с записями файлов",
            next="разметка изменилась — квалифицировать формат заново"))
    for e in entries:
        if not e["name"].lower().endswith((".xlsx", ".xls")):
            continue
        books.append({
            "id": e["id"],
            "name": e["name"],
            "modifiedHint": e["modified"],
            "courseHint": _hint_course(e["name"]),
            "semesterHint": _hint_semester(e["name"]),
            "yearHint": _hint_year(e["name"]),
        })
    if recognized and not books:
        issues.append(issue(
            "book-list-empty", source=source,
            expected="хотя бы один файл расписания XLSX",
            next="проверить папку или указать иной источник"))
    return envelope("bsu.index/1", status_of(issues), {
        "folderId": folder_id,
        "observedAt": observed_at,
        "source": source,
        "books": books,
    }, issues)


def fetch_index_document(url: str, *, fetch=None) -> dict:
    """Скачать и разобрать публичную папку → bsu.index/1.

    Неподходящий URL (не HTTPS разрешённого источника/Drive) отклоняется
    ДО вызова транспорта."""
    issues: list[dict] = []
    if not _allowed_url(url):
        issues.append(issue(
            "url-not-allowed", source=url,
            expected="https://drive.google.com/… или Drive download",
            next="локальные данные — через --file/--book, не fetch"))
        return envelope("bsu.index/1", status_of(issues), {
            "folderId": None, "observedAt": None, "source": url, "books": [],
        }, issues)
    fetch = fetch or _default_fetch
    try:
        blob = fetch(url)
    except Exception as exc:  # noqa: BLE001 — транспорт подставной/сетевой
        issues.append(issue(
            "fetch-failed", source=url, actual=type(exc).__name__,
            next="проверить доступность публичной папки"))
        return envelope("bsu.index/1", status_of(issues), {
            "folderId": None, "observedAt": None, "source": url, "books": [],
        }, issues)
    return index_document(blob, source=url,
                          observed_at=datetime.now().isoformat(timespec="seconds"))


# ---------------------------------------------------------------------------
# Выбор редакции
# ---------------------------------------------------------------------------

def _select_edition(books: list[dict], year: str, course: int,
                    semester: int) -> tuple[dict | None, list[dict]]:
    exact = [b for b in books
             if b["yearHint"] == year and b["courseHint"] == course
             and b["semesterHint"] == semester]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, [issue(
            "edition-ambiguous", expected="одна редакция года/курса/семестра",
            actual=[{"id": b["id"], "name": b["name"],
                     "modifiedHint": b["modifiedHint"]} for b in exact],
            next="время изменения не выбирает редакцию — указать --book-id")]
    possible = [b for b in books
                if all(b[k] is None or b[k] == v for k, v in
                       (("yearHint", year), ("courseHint", course),
                        ("semesterHint", semester)))]
    if len(possible) == 1:
        return possible[0], []
    if len(possible) > 1:
        return None, [issue(
            "edition-ambiguous", expected="однозначные признаки редакции",
            actual=[{"id": b["id"], "name": b["name"],
                     "modifiedHint": b["modifiedHint"]} for b in possible],
            next="указать --book-id или иной источник")]
    return None, [issue(
        "edition-not-found",
        expected=f"книга {year}, курс {course}, семестр {semester}",
        actual=[{"id": b["id"], "name": b["name"]} for b in books],
        next="проверить папку или указать --book/--url")]


# ---------------------------------------------------------------------------
# Шапка области (год/курс/семестр)
# ---------------------------------------------------------------------------

def _find_scope(ws) -> tuple[dict | None, list[dict]]:
    """Единственная ячейка области с курсом и семестром; год — из неё
    или из названия листа."""
    issues: list[dict] = []
    cands: list[tuple[str, int, int, str | None]] = []
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 25)):
        for cell in row:
            t = _norm(cell.value)
            if not t:
                continue
            cm, sm = _COURSE_RE.search(t), _SEMESTER_RE.search(t)
            if cm and sm:
                cands.append((cell.coordinate,
                              int(cm.group(1) or cm.group(2)),
                              int(sm.group(1) or sm.group(2)),
                              _hint_year(t)))
    if not cands:
        issues.append(issue(
            "scope-unknown", source=ws.title,
            expected="ячейка с курсом и семестром (напр. C8)",
            next="шапка книги не содержит области — квалифицировать формат"))
        return None, issues
    distinct = {(c, s) for _, c, s, _ in cands}
    if len(distinct) > 1:
        issues.append(issue(
            "scope-ambiguous", source=";".join(c[0] for c in cands),
            expected="одна область год/курс/семестр",
            actual=[{"cell": c, "course": co, "semester": s}
                    for c, co, s, _ in cands],
            next="несколько противоречивых шапок — проверить книгу"))
        return None, issues
    cell, course, semester, year = cands[0]
    if year is None:
        year = _hint_year(ws.title)
    if year is None:
        issues.append(issue(
            "year-unknown", source=f"'{ws.title}'!{cell}",
            expected="учебный год в шапке или названии листа",
            next="добавить год в книгу или указать явно"))
        return None, issues
    return {"year": year, "course": course, "semester": semester,
            "cell": cell}, issues


# ---------------------------------------------------------------------------
# Структура сетки расписания
# ---------------------------------------------------------------------------

def _cellref(ws, r: int, c: int) -> str:
    return f"'{ws.title}'!{get_column_letter(c)}{r}"




def _value_row_end(ws) -> int:
    """Последняя строка с реальным значением, игнорируя formatting-only хвост.

    `ws.max_row` в XLSX раздувается от пустых стилей и бордюров. Внутренний
    индекс openpyxl уже материализован при чтении книги, поэтому фильтр по
    нему не обходит миллионы пустых строк и не обрезает строки с данными.
    """
    rows = []
    for (r, _), cell in getattr(ws, "_cells", {}).items():
        value = cell.value
        if value is not None and (not isinstance(value, str) or value.strip()):
            rows.append(r)
    return max(rows, default=0)


def _find_day_spans(ws, merges) -> tuple[int | None, list[dict]]:
    """Колонка дней и объединённые диапазоны (weekday, r0, r1)."""
    best_col, best_hits = None, 0
    row_end = _value_row_end(ws)
    for c in range(1, min(ws.max_column or 1, 8) + 1):
        hits = sum(1 for r in range(1, row_end + 1)
                   if _norm(ws.cell(r, c).value) in _WEEKDAYS)
        if hits > best_hits:
            best_col, best_hits = c, hits
    if best_col is None or best_hits < 2:
        return None, []
    spans: list[dict] = []
    covered: set[int] = set()
    for mr in sorted(ws.merged_cells.ranges, key=lambda x: x.min_row):
        if mr.min_col <= best_col <= mr.max_col:
            label = _norm(ws.cell(mr.min_row, mr.min_col).value)
            if label in _WEEKDAYS:
                spans.append({"weekday": _WEEKDAYS[label],
                              "r0": mr.min_row, "r1": mr.max_row,
                              "ref": f"'{ws.title}'!{mr.coord}"})
                covered.update(range(mr.min_row, mr.max_row + 1))
    for r in range(1, row_end + 1):
        if r in covered:
            continue
        label = _norm(ws.cell(r, best_col).value)
        if label in _WEEKDAYS:
            spans.append({"weekday": _WEEKDAYS[label], "r0": r, "r1": r,
                          "ref": _cellref(ws, r, best_col)})
    spans.sort(key=lambda s: s["r0"])
    return best_col, spans


def _time_in(text: str) -> tuple[str, str] | None:
    m = _TIME_RE.search(text)
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}", f"{int(m.group(3)):02d}:{m.group(4)}"


def _find_time_cols(ws, day_col: int, r0: int, r1: int) -> tuple[int | None, int | None]:
    """Базовая и альтернативная колонки времени справа от колонки дней."""
    base = alt = None
    for c in range(day_col + 1, day_col + 4):
        hits = sum(
            1 for r in range(r0, r1 + 1)
            if _time_in(_norm(ws.cell(r, c).value) or "")
            or _TIME_AT_RE.search(str(ws.cell(r, c).value or "")))
        if base is None and hits >= 2:
            base = c
        elif base is not None and hits >= 1:
            nonempty = sum(1 for r in range(r0, r1 + 1)
                           if str(ws.cell(r, c).value or "").strip())
            if hits == nonempty:  # колонка целиком про время, не текст назначений
                alt = c
                break
    return base, alt


def _short_label(text: str) -> bool:
    t = text.strip()
    return bool(t) and len(t) <= 4 and (t[0].isdigit() or t.lower() in
                                        {"а", "б", "в", "г", "д"})


def _eff_label(ws, merges, r: int, c: int) -> str:
    """Подпись ячейки: перенос объединения внутри диапазона, пробелы
    нормализованы, регистр сохранён."""
    if (r, c) in merges:
        r0, c0, _, _ = merges[(r, c)]
        v = ws.cell(r0, c0).value
    else:
        v = ws.cell(r, c).value
    return re.sub(r"\s+", " ", str(v).strip()) if v is not None else ""


def _resolve_grid(ws, merges, day_col: int, time_cols: tuple[int | None, int | None],
                  data_start: int) -> dict:
    """Шапка сетки: колонки → {group, subgroup, stream, band, headerRefs}.

    Строки над первой строкой данных: нижняя «короткая» строка —
    подгруппы, над ней — группы; строка с объединёнными метками чётности —
    полоса; остальные текстовые строки — потоки/профили.
    """
    skip = {c for c in (day_col, *time_cols) if c}
    band_rows = list(range(1, data_start))
    parity_row = None
    for r in band_rows:
        parity_hits = 0
        merged_hit = False
        for c in range(1, (ws.max_column or 1) + 1):
            t = _eff(ws, merges, r, c)
            if not (_ODD_RE.search(t) or (_EVEN_RE.search(t) and not _ODD_RE.search(t))):
                continue
            parity_hits += 1
            if (r, c) in merges:
                _, c0, _, c1 = merges[(r, c)]
                merged_hit = merged_hit or c1 > c0
        # Полоса чётности может быть одной объединённой подписью над
        # диапазоном либо повторяться в отдельных колонках без merge.
        # Пустые строки шапки здесь не участвуют и не сдвигают колонки.
        if merged_hit or parity_hits >= 2:
            parity_row = r
            break
    label_rows = []
    for r in band_rows:
        if r == parity_row:
            continue
        hits = sum(1 for c in range(1, (ws.max_column or 1) + 1)
                   if c not in skip and _short_label(_eff(ws, merges, r, c)))
        if hits >= 2:
            label_rows.append(r)
    groups_row = subgroups_row = None
    if len(label_rows) >= 2:
        groups_row, subgroups_row = label_rows[-2], label_rows[-1]
    elif label_rows:
        groups_row = label_rows[0]

    cols: dict[int, dict] = {}
    if groups_row is None:
        return {"cols": cols, "groupsRow": None}
    for c in range(day_col + 1, (ws.max_column or 1) + 1):
        if c in skip:
            continue
        group = _eff_label(ws, merges, groups_row, c)
        if not group:
            continue
        refs = [_cellref(ws, groups_row, c)]
        subgroup = None
        if subgroups_row is not None and subgroups_row != groups_row:
            merged_down = (subgroups_row, c) in merges and \
                merges[(subgroups_row, c)][0] <= groups_row
            if not merged_down:
                subgroup = _eff_label(ws, merges, subgroups_row, c) or None
                refs.append(_cellref(ws, subgroups_row, c))
        stream = None
        for r in band_rows:
            if r in (groups_row, subgroups_row, parity_row):
                continue
            t = _eff_label(ws, merges, r, c)
            if t and not _short_label(t):
                stream = t
                refs.insert(0, _cellref(ws, r, c))
        band = None
        if parity_row is not None:
            t = _eff(ws, merges, parity_row, c)
            # Для merged-полосы `_eff` даёт подпись всем ячейкам диапазона;
            # для раздельной striped-разметки действует только явная подпись
            # самой колонки — пустое соседство не заполняется догадкой.
            if t:
                if _ODD_RE.search(t):
                    band = "odd"
                elif _EVEN_RE.search(t):
                    band = "even"
                if band:
                    refs.append(_cellref(ws, parity_row, c))
        cols[c] = {"group": group, "subgroup": subgroup, "stream": stream,
                   "band": band, "headerRefs": refs}
    return {"cols": cols, "groupsRow": groups_row}


# ---------------------------------------------------------------------------
# Разбор текста назначения
# ---------------------------------------------------------------------------

def _teachers_in(text: str) -> list[dict]:
    """Токены «Фамилия И.И.» в тексте ячейки."""
    return [{"surname": m.group(1), "initials": f"{m.group(2)}.{m.group(3)}.",
             "text": m.group(0)}
            for m in _TEACHER_RE.finditer(text)]


def _teacher_request(value: str) -> dict | None:
    m = _TEACHER_REQ_RE.match(value)
    if not m:
        return None
    initials = f"{m.group(2)}.{m.group(3)}." if m.group(2) else None
    return {"surname": m.group(1), "initials": initials}


def _norm_person(s: str) -> str:
    return _norm(s).replace("ё", "е").replace(" ", "")


def _teacher_matches(req: dict, token: dict) -> bool:
    if _norm_person(req["surname"]) != _norm_person(token["surname"]):
        return False
    if req["initials"] and token["initials"]:
        return _norm_person(req["initials"]) == _norm_person(token["initials"])
    return True


def _kind_of(text: str) -> tuple[str | None, bool]:
    hits = {k for k, rx in _KIND_MARKS if rx.search(text)}
    if len(hits) == 1:
        return next(iter(hits)), False
    return (next(iter(sorted(hits))) if hits else None), len(hits) > 1


def _phase_of_cell(text: str) -> str | None:
    if _ODD_RE.search(text):
        return "odd"
    if _EVEN_RE.search(text):
        return "even"
    if _EVERY_RE.search(text):
        return "every"
    return None


def _bound_year(month: int, scope_year: str) -> int:
    y1 = int(scope_year.split("-")[0])
    return y1 if month >= 9 else y1 + 1


def _bounds_of(text: str, scope_year: str) -> dict:
    out: dict = {"from": None, "to": None, "source": {}}
    for key, rx in (("from", _BOUND_FROM_RE), ("to", _BOUND_TO_RE)):
        m = rx.search(text)
        if not m:
            continue
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = (2000 + int(y) if y and len(y) <= 2 else int(y)) if y \
            else _bound_year(mo, scope_year)
        try:
            out[key] = date(year, mo, d).isoformat()
            out["source"][key] = m.group(0)
        except ValueError:
            pass
    return out


def _alt_time_of(text: str) -> dict | None:
    m = _TIME_AT_RE.search(text)
    if m:
        return {"localStart": f"{int(m.group(1)):02d}:{m.group(2)}",
                "localEnd": None, "source": m.group(0)}
    m = _TIME_RE.search(text)
    if m:
        return {"localStart": f"{int(m.group(1)):02d}:{m.group(2)}",
                "localEnd": f"{int(m.group(3)):02d}:{m.group(4)}",
                "source": m.group(0)}
    return None


def _discipline_of(text: str) -> str | None:
    for line in re.split(r"[\n;]+", text):
        if _TEACHER_RE.search(line):
            continue
        parts = []
        for chunk in re.split(r"[,()]", line):
            t = chunk.strip()
            if not t:
                continue
            if any(rx.search(t) for _, rx in _KIND_MARKS):
                continue
            if re.search(r"ауд\.|ч[её]тн|еженедельн|поток|^[св]\s|по\s|^\d", t,
                         re.IGNORECASE):
                continue
            parts.append(t)
        if parts:
            return " ".join(parts)
    return None


# ---------------------------------------------------------------------------
# Разбор книги → слоты
# ---------------------------------------------------------------------------

def _day_of_row(spans: list[dict], r: int) -> dict | None:
    for s in spans:
        if s["r0"] <= r <= s["r1"]:
            return s
    return None


def parse_schedule_book(blob: bytes, *, scope_req: dict) -> tuple[dict, list[dict]]:
    """XLSX → {scope, teachers, slots, conflicts}; чистый разбор, без сети."""
    issues: list[dict] = []
    data: dict = {"scope": None, "teachers": [], "slots": [], "conflicts": []}
    if not blob[:4].startswith(b"PK\x03\x04"):
        issues.append(issue(
            "book-format", expected="XLSX (zip)", actual=blob[:8].hex(),
            next="файл не является книгой Excel"))
        return data, issues
    if len(blob) > MAX_BOOK_BYTES:
        issues.append(issue(
            "book-format", expected=f"≤{MAX_BOOK_BYTES} байт",
            actual=len(blob), next="книга превышает лимит размера"))
        return data, issues
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            unpacked = sum(i.file_size for i in zf.infolist())
        if unpacked > MAX_UNPACKED_BYTES:
            issues.append(issue(
                "book-too-large", expected=f"≤{MAX_UNPACKED_BYTES} байт "
                "в распакованном виде", actual=unpacked,
                next="книга превышает лимит распаковки — не открываем"))
            return data, issues
    except zipfile.BadZipFile:
        issues.append(issue(
            "book-format", expected="валидный zip-архив XLSX",
            next="файл повреждён или не является книгой"))
        return data, issues
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True,
                                    keep_links=False)
    except Exception as exc:  # noqa: BLE001
        issues.append(issue(
            "book-format", actual=type(exc).__name__,
            next="книга не открывается как XLSX"))
        return data, issues

    # лист: содержит колонку дней; несколько кандидатов — блок
    cands = []
    for ws in wb.worksheets:
        col, spans = _find_day_spans(ws, _merged_map(ws))
        if col is not None and spans:
            cands.append((ws, col, spans))
    if len(cands) != 1:
        issues.append(issue(
            "sheet-not-found" if not cands else "sheet-ambiguous",
            expected="один лист с сеткой расписания",
            actual=[c[0].title for c in cands] or wb.sheetnames,
            next="указать книгу с единственной сеткой"))
        return data, issues
    ws, day_col, spans = cands[0]
    merges = _merged_map(ws)

    scope, scope_issues = _find_scope(ws)
    issues.extend(scope_issues)
    if scope is None:
        return data, issues
    data["scope"] = scope
    for key, req in (("year", scope_req["year"]),
                     ("course", scope_req["course"]),
                     ("semester", scope_req["semester"])):
        if scope[key] != req:
            issues.append(issue(
                "scope-mismatch", source=f"'{ws.title}'!{scope['cell']}",
                field=key, expected=req, actual=scope[key],
                next="редакция книги не совпадает с запрошенной областью"))
            return data, issues

    r0_data = spans[0]["r0"]
    base_col, alt_col = _find_time_cols(ws, day_col, r0_data, spans[-1]["r1"])
    grid = _resolve_grid(ws, merges, day_col, (base_col, alt_col), r0_data)
    if not grid["cols"]:
        issues.append(issue(
            "header-unrecognized", source=ws.title,
            expected="строки групп/подгрупп над сеткой",
            next="шапка групп не распознана — квалифицировать формат"))
        return data, issues

    slots: list[dict] = []
    all_teachers: dict[str, dict] = {}
    seen_cells: set[tuple[int, int]] = set()
    # Разбирается только область, покрытая распознанными диапазонами дней.
    # formatting-only/пустой хвост листа не обходится построчно, а текст за
    # пределами сетки не становится занятием; внутри диапазонов обрезания нет.
    data_end = max(s["r1"] for s in spans)
    for r in range(r0_data, data_end + 1):
        day = _day_of_row(spans, r)
        if day is None:
            continue
        for c, col in sorted(grid["cols"].items()):
            if (r, c) in seen_cells:
                continue
            if (r, c) in merges:
                r0, c0, _, _ = merges[(r, c)]
                if (r0, c0) != (r, c):
                    continue  # верхний левый угол обработается сам
                rr0, cc0, rr1, cc1 = merges[(r, c)]
                for rr in range(rr0, rr1 + 1):
                    for cc in range(cc0, cc1 + 1):
                        seen_cells.add((rr, cc))
            text = str(ws.cell(r, c).value or "").strip()
            if not text:
                continue
            teachers = _teachers_in(text)
            for t in teachers:
                all_teachers[t["text"]] = t
            if not teachers:
                continue
            if _phase_of_cell(text) and not _discipline_of(text):
                continue  # ячейка-метка чётности, не назначение
            cell_ref = _cellref(ws, r, c)
            day_ref = day["ref"]
            srcs = [day_ref]
            rng = merges.get((r, c))
            cell_range = (f"'{ws.title}'!{get_column_letter(c)}{r}"
                          if not rng else
                          f"'{ws.title}'!{get_column_letter(rng[1])}{rng[0]}:"
                          f"{get_column_letter(rng[3])}{rng[2]}")
            srcs.append(cell_range)
            time_val = ws.cell(r, base_col).value if base_col else None
            start_end = _time_in(str(time_val)) if time_val else None
            if base_col:
                srcs.append(_cellref(ws, r, base_col))
            srcs += col["headerRefs"]

            kind, kind_conflict = _kind_of(text)
            ambiguities: list[dict] = []
            if kind is None:
                ambiguities.append({"code": "kind-unknown", "source": cell_ref,
                                    "detail": "вид занятия не обозначен"})
            elif kind_conflict:
                ambiguities.append({"code": "kind-conflict", "source": cell_ref,
                                    "detail": "несколько меток вида"})
            cell_phase = _phase_of_cell(text)
            if cell_phase and col["band"] and cell_phase != col["band"]:
                ambiguities.append({"code": "phase-conflict", "source": cell_ref,
                                    "detail": f"ячейка «{cell_phase}» ≠ полоса «{col['band']}»"})
                phase = "unknown"
            else:
                phase = cell_phase or col["band"] or "unknown"
            if phase == "unknown":
                ambiguities.append({"code": "phase-unknown", "source": cell_ref,
                                    "detail": "нет метки чётности — не еженедельно по умолчанию"})

            bounds = _bounds_of(text, scope["year"])
            alt = _alt_time_of(text)
            if alt_col:
                alt_text = ws.cell(r, alt_col).value
                alt_col_time = _TIME_AT_RE.search(str(alt_text or "")) or \
                    _TIME_RE.search(str(alt_text or ""))
                if alt_col_time and alt is None:
                    g = alt_col_time
                    alt = {"localStart": f"{int(g.group(1)):02d}:{g.group(2)}",
                           "localEnd": None, "source": _cellref(ws, r, alt_col)}
                    if len(g.groups()) >= 4 and g.group(3):
                        alt["localEnd"] = f"{int(g.group(3)):02d}:{g.group(4)}"
            if start_end:
                local_start, local_end = start_end
            else:
                local_start = local_end = None
                ambiguities.append({"code": "time-unknown",
                                    "source": _cellref(ws, r, base_col) if base_col else ws.title,
                                    "detail": "базовое время строки не распознано"})
            slots.append({
                "cell": cell_ref,
                "row": r,
                "assignment": None,  # заполняется при сопоставлении преподавателя
                "teachers": [t["text"] for t in teachers],
                "discipline": _discipline_of(text),
                "kind": kind,
                "groups": [{"group": col["group"], "subgroup": col["subgroup"],
                            "stream": col["stream"]}],
                "weekday": day["weekday"],
                "localStart": local_start,
                "localEnd": local_end,
                "phase": phase,
                "bounds": {"from": bounds["from"], "to": bounds["to"],
                           "source": bounds["source"]},
                "altTime": alt,
                "sourceRanges": srcs,
                "ambiguities": ambiguities,
                "instances": [],
            })
    data["slots"] = slots
    data["teachers"] = sorted(all_teachers)
    return data, issues


# ---------------------------------------------------------------------------
# Вычисленные даты и исключения
# ---------------------------------------------------------------------------

def _isodate(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _unroll(slot: dict, from_d: date, to_d: date,
            anchor: date | None,
            max_instances: int | None = None) -> list[dict]:
    """Даты слота в [from_d, to_d]: день недели, границы, фаза.

    Развёртка ограничена сверху даже при будущем появлении более частых
    правил повторения: серия не может молча раздуть результат до тысяч
    экземпляров.
    """
    if max_instances is None:
        max_instances = MAX_SERIES_INSTANCES
    wd = _WEEKDAY_NUM[slot["weekday"]]
    b_from = _isodate(slot["bounds"].get("from"))
    b_to = _isodate(slot["bounds"].get("to"))
    lo = max(from_d, b_from) if b_from else from_d
    hi = min(to_d, b_to) if b_to else to_d
    out: list[dict] = []
    if hi < lo:
        return out
    phase = slot["phase"]
    if phase in ("even", "odd"):
        if anchor is None:
            return out
        want = 0 if phase == "even" else 1
    else:
        want = None
    d = lo - timedelta(days=(lo.weekday() - wd) % 7)
    while d <= hi:
        if d >= lo:
            if want is not None:
                monday = d - timedelta(days=d.weekday())
                idx = (monday - anchor).days // 7
                if idx % 2 != want:
                    d += timedelta(days=7)
                    continue
            if len(out) >= max_instances:
                raise SeriesLimitError(
                    f"series exceeds {max_instances} instances")
            out.append({"date": d.isoformat(),
                        "localStart": slot["localStart"],
                        "localEnd": slot["localEnd"],
                        "ordinal": len(out) + 1,
                        "source": slot["cell"]})
        d += timedelta(days=7)
    return out


def _exc_matches_slot(exc: dict, slot: dict) -> bool:
    if exc.get("discipline") and _norm(exc["discipline"]) != _norm(
            slot.get("discipline") or ""):
        return False
    if exc.get("group"):
        g = _norm(exc["group"])
        return any(_norm(gr["group"]) == g for gr in slot["groups"])
    return True


def _apply_exceptions(slots: list[dict], exceptions: list[dict],
                      issues: list[dict]) -> None:
    used: set[int] = set()
    for ei, exc in enumerate(exceptions):
        target_date = exc.get("date")
        action = exc.get("action")
        matched: list[tuple[dict, dict]] = []
        for slot in slots:
            if not _exc_matches_slot(exc, slot):
                continue
            for inst in slot["instances"]:
                if inst["date"] == target_date and not inst.get("_consumed"):
                    matched.append((slot, inst))
        if len(matched) > 1:
            issues.append(issue(
                "exception-ambiguous",
                source=exc.get("source"), field="exceptions",
                expected="исключение одного занятия",
                actual={"date": target_date, "matches": len(matched)},
                next="уточнить discipline/group в исключении"))
            continue
        if not matched:
            continue
        used.add(ei)
        slot, inst = matched[0]
        inst["_consumed"] = True
        if action == "cancel":
            slot["instances"].remove(inst)
        elif action == "move":
            slot["instances"].remove(inst)
            moved = {"date": exc["newDate"],
                     "localStart": exc.get("newTime") or inst["localStart"],
                     "localEnd": inst["localEnd"] if not exc.get("newTime") else None,
                     "ordinal": inst.get("ordinal"),
                     "originalTime": {"localStart": inst["localStart"],
                                      "localEnd": inst["localEnd"]},
                     "source": inst["source"],
                     "exception": {"action": "move",
                                   "originalDate": inst["date"],
                                   "source": exc.get("source")}}
            dup = next((i for i in slot["instances"]
                        if i["date"] == moved["date"]
                        and i["localStart"] == moved["localStart"]), None)
            if dup is not None:
                dup["exception"] = moved["exception"]
            else:
                slot["instances"].append(moved)
    for ei, exc in enumerate(exceptions):
        if ei not in used:
            issues.append(issue(
                "exception-unmatched", source=exc.get("source"),
                field="exceptions",
                expected="дата занятия в вычисленном периоде",
                actual={"date": exc.get("date"), "action": exc.get("action")},
                next="исключение ни к чему не применилось — проверить дату"))
    for slot in slots:
        for inst in slot["instances"]:
            inst.pop("_consumed", None)
        slot["instances"].sort(key=lambda i: (i["date"], i["localStart"] or ""))


# ---------------------------------------------------------------------------
# Документ bsu.slots/1
# ---------------------------------------------------------------------------

def schedule_find_document(index_doc: dict, *, year: str, semester: int,
                           course: int, teacher: str,
                           book_bytes: bytes | None = None,
                           book_id: str | None = None,
                           url: str | None = None,
                           fetch=None,
                           period_from: str | None = None,
                           period_to: str | None = None,
                           even_week_start: str | None = None,
                           odd_week_start: str | None = None,
                           exceptions: dict | None = None) -> dict:
    """Личные слоты преподавателя и вычисленные даты → bsu.slots/1."""
    issues: list[dict] = []
    books = ((index_doc or {}).get("data") or {}).get("books") or []
    entry = None
    if book_id:
        entry = next((b for b in books if b["id"] == book_id), None)
        if entry is None:
            issues.append(issue(
                "edition-not-found", expected=f"книга id={book_id}",
                actual=[b["id"] for b in books],
                next="id отсутствует в индексе"))
    elif url is not None:
        entry = None  # явный источник — выбор редакции по индексу не требуется
        if not _allowed_url(url):
            issues.append(issue(
                "url-not-allowed", source=url,
                expected="https-адрес разрешённого источника/Drive",
                next="локальная книга — через --book, не --url"))
            url = None  # транспорт не вызывается
    else:
        entry, sel = _select_edition(books, year, course, semester)
        issues.extend(sel)
    if book_bytes is None and url is None and entry is not None:
        url = DRIVE_DOWNLOAD.format(id=entry["id"])
    if book_bytes is None:
        if url is None:
            return envelope("bsu.slots/1", status_of(issues), {
                "bookId": None, "sha": None, "scope": None, "teacher": teacher,
                "period": {"from": period_from, "to": period_to},
                "slots": [], "conflicts": [],
            }, issues)
        fetch = fetch or _default_fetch
        try:
            book_bytes = fetch(url)
        except Exception as exc:  # noqa: BLE001
            issues.append(issue(
                "fetch-failed", source=url, actual=type(exc).__name__,
                next="книгу не удалось получить"))

    data: dict = {
        "bookId": (entry or {}).get("id"),
        "sha": hashlib.sha256(book_bytes).hexdigest() if book_bytes else None,
        "scope": None,
        "teacher": teacher,
        "period": {"from": period_from, "to": period_to},
        "slots": [], "conflicts": [],
    }
    if book_bytes is None:
        return envelope("bsu.slots/1", status_of(issues), data, issues)

    parsed, parse_issues = parse_schedule_book(
        book_bytes, scope_req={"year": year, "course": course,
                               "semester": semester})
    issues.extend(parse_issues)
    data["scope"] = parsed["scope"]
    if parsed["scope"] is None:
        return envelope("bsu.slots/1", status_of(issues), data, issues)

    req = _teacher_request(teacher)
    if req is None:
        issues.append(issue(
            "teacher-format", expected="«Фамилия» или «Фамилия И.О.»",
            actual=teacher, next="исправить написание имени"))
        return envelope("bsu.slots/1", status_of(issues), data, issues)

    def _tok(text: str) -> dict | None:
        m = _TEACHER_RE.search(text)
        return {"surname": m.group(1),
                "initials": f"{m.group(2)}.{m.group(3)}."} if m else None

    matched_tokens: dict[str, dict] = {}
    for slot in parsed["slots"]:
        hits = [t for t in (_tok(x) for x in slot["teachers"])
                if t and _teacher_matches(req, t)]
        if hits:
            for h in hits:
                matched_tokens[f"{h['surname']} {h['initials']}"] = h
            slot["assignment"] = next(
                t for t in slot["teachers"] if _tok(t) in hits)

    if not matched_tokens:
        issues.append(issue(
            "teacher-not-found", expected=teacher,
            actual=parsed["teachers"],
            next="проверить написание или передать подтверждённое в --teacher"))
    elif len(matched_tokens) > 1:
        issues.append(issue(
            "teacher-ambiguous", expected="один преподаватель",
            actual=sorted(matched_tokens),
            next="уточнить инициалы в --teacher"))

    data["slots"] = [s for s in parsed["slots"] if s["assignment"]]
    for s in data["slots"]:
        s.pop("teachers", None)
        s.pop("row", None)

    # конфликты: один ключ день/время/фаза/группа — разные назначения
    by_key: dict[tuple, list[dict]] = {}
    for s in data["slots"]:
        key = (s["weekday"], s["localStart"], s["phase"],
               tuple((g["group"], g["subgroup"]) for g in s["groups"]))
        by_key.setdefault(key, []).append(s)
    for group in by_key.values():
        distinct = {(s["discipline"], s["kind"]) for s in group}
        if len(group) > 1 and len(distinct) > 1:
            data["conflicts"].append({
                "weekday": group[0]["weekday"],
                "localStart": group[0]["localStart"],
                "phase": group[0]["phase"],
                "groups": group[0]["groups"],
                "candidates": [
                    {"discipline": s["discipline"], "kind": s["kind"],
                     "cell": s["cell"]} for s in group],
            })

    # вычисленные даты: диапазон валидируется до развёртки, чтобы ни
    # ошибка ввода, ни гигантский период не превращались в бесконечную серию
    if (period_from is None) != (period_to is None):
        issues.append(issue(
            "period-incomplete", field="period",
            expected="обе границы --from и --to",
            actual={"from": period_from, "to": period_to},
            next="передать весь диапазон или убрать обе границы"))
    elif period_from and period_to:

        def _period_date(value: str, field: str) -> date | None:
            try:
                return date.fromisoformat(value)
            except (TypeError, ValueError):
                issues.append(issue(
                    "period-invalid", field=field, expected="YYYY-MM-DD",
                    actual=value, next="исправить дату периода"))
                return None

        def _anchor_date(value: str | None, field: str) -> date | None:
            if value is None:
                return None
            try:
                return date.fromisoformat(value)
            except (TypeError, ValueError):
                issues.append(issue(
                    "period-invalid", field=field, expected="YYYY-MM-DD",
                    actual=value, next="исправить календарный якорь"))
                return None

        from_d = _period_date(period_from, "from")
        to_d = _period_date(period_to, "to")
        even_anchor = _anchor_date(even_week_start, "even-week-start")
        odd_anchor = _anchor_date(odd_week_start, "odd-week-start")
        period_ready = from_d is not None and to_d is not None
        if period_ready and from_d > to_d:
            issues.append(issue(
                "period-invalid", field="period", expected="from ≤ to",
                actual={"from": period_from, "to": period_to},
                next="исправить порядок границ периода"))
            period_ready = False
        elif period_ready and (to_d - from_d).days > MAX_PERIOD_DAYS:
            issues.append(issue(
                "period-too-large", field="period",
                expected=f"≤{MAX_PERIOD_DAYS} дней",
                actual=(to_d - from_d).days + 1,
                next="разбить расчёт на учебные периоды — развёртка гигантского "
                     "диапазона запрещена"))
            period_ready = False
        if period_ready:
            if even_anchor is None and odd_anchor is not None:
                even_anchor = odd_anchor - timedelta(days=7)
            anchor_missing = [
                s for s in data["slots"]
                if s["phase"] in ("even", "odd") and even_anchor is None]
            if anchor_missing:
                issues.append(issue(
                    "phase-anchor-missing",
                    source=";".join(s["cell"] for s in anchor_missing),
                    expected="якорь чётной недели (--even-week-start)",
                    next="метки чётности не дают дат без календарного якоря"))
            series_overflow: list[str] = []
            for s in data["slots"]:
                if s["phase"] in ("even", "odd") and even_anchor is None or s["phase"] == "unknown":
                    s["instances"] = []
                else:
                    try:
                        s["instances"] = _unroll(s, from_d, to_d, even_anchor)
                    except SeriesLimitError:
                        s["instances"] = []
                        series_overflow.append(s["cell"])
            if series_overflow:
                issues.append(issue(
                    "series-too-large", source=";".join(series_overflow),
                    expected=f"≤{MAX_SERIES_INSTANCES} экземпляров на слот",
                    next="сузить период или правило повторения — серия не развёрнута"))
            exc_list = (exceptions or {}).get("exceptions") or []
            if exc_list:
                _apply_exceptions(data["slots"], exc_list, issues)

    return envelope("bsu.slots/1", status_of(issues), data, issues)
