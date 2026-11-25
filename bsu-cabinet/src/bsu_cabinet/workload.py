"""Парсер Excel-нагрузки преподавателя (лист «ШТ») в документ workload/1.

Разбор ведётся по тексту заголовков, а не по фиксированным колонкам:
находится единственный лист с многострочной шапкой, подписи объединённых
ячеек переносятся только внутри их диапазона, семестровые блоки определяются
по подписям «осенний/весенний семестр», годовые — по своим заголовкам.
Каждое значение хранит источник до ячейки: {cell, header, state, value},
где state — value | blank | unknown (формула без сохранённого результата —
не ноль). Четыре контроля (осень, весна, годовые, общий) сравниваются с
книжной строкой «Итого»; несходящийся итог блокирует документ.

Контракт — docs/bsu-cabinet/release-spec.md, очередь 2; правила полей —
docs/bsu-cabinet/individual-plan-guide.md.
"""

from __future__ import annotations

import hashlib
import io
import re
from fractions import Fraction
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from .schema import envelope, issue, status_of

# ---------------------------------------------------------------------------
# Лексика заголовков (нормализованные подстроки; явная таблица синонимов)
# ---------------------------------------------------------------------------

def _norm(v: object) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v).strip().lower())


def _has(elems: tuple[str, ...] | list[str], *subs: str) -> bool:
    """Любой элемент пути заголовка содержит любую из подстрок."""
    return any(sub in e for e in elems for sub in subs)


def _has_not(elems: tuple[str, ...] | list[str], *subs: str) -> bool:
    return not _has(elems, *subs)


_BLOCK_MARKERS = (
    ("autumn", ("осенн",)),
    ("spring", ("весенн",)),
    ("annual", ("в течение года", "за год", "годовая работ", "руководств")),
)

# «руководств» — маркер зоны (родитель «Руководство» над supervision-статьями),
# но в пути заголовка это часть имени поля: вырезать нельзя.
_MARKER_SUBS = tuple(
    m for _, marks in _BLOCK_MARKERS for m in marks if m != "руководств")


_HEADER_TOKENS = (
    "дисциплин", "факультет", "специальност", "форма обучения", "поток",
    "подгрупп", "групп", "лекц", "практ", "лабор", "уср", "самостоятельн",
    "консульт", "проверк", "экзамен", "зачёт", "зачет", "семестр",
    "руководств", "рецензир", "аттестац", "студент", "иностран",
    "преподавател", "должност", "ставк", "аудиторн", "реферат", "итого",
    "всего", "контрольн", "дот", "текущ", "прочие", "иные", "дистанц",
)

# Идентичность строки: (field, kind, matcher)
_IDENT_SIGS: list[tuple[str, str, object]] = [
    ("num", "int", lambda e: _has(e, "№")),
    ("teacher", "str", lambda e: _has(e, "преподавател", "ф.и.о", "фио")),
    ("position", "str", lambda e: _has(e, "должност")),
    ("rate", "hours", lambda e: _has(e, "ставк")),
    ("discipline", "str", lambda e: _has(e, "дисциплин")),
    ("faculty", "str", lambda e: _has(e, "факультет")),
    ("course", "int", lambda e: any(x.strip() == "курс" or "курс обучения" in x for x in e)),
    ("study_form", "str", lambda e: _has(e, "форма")),
    ("specialty", "str", lambda e: _has(e, "специальност")),
    ("streams", "int", lambda e: _has(e, "поток")),
    ("subgroups", "int", lambda e: _has(e, "подгрупп")),
    ("groups", "int", lambda e: _has(e, "групп") and _has_not(e, "подгрупп")),
    ("students_lecture", "int", lambda e: _has(e, "студент") and _has(e, "лекц")),
    ("students_practical", "int", lambda e: _has(e, "студент") and _has(e, "практ", "семинар")),
    ("students_laboratory", "int", lambda e: _has(e, "студент") and _has(e, "лабор")),
    ("students_foreign", "int", lambda e: _has(e, "иностран")),
    ("note", "str", lambda e: _has(e, "примечан")),
]

# Семестровые поля: (field, вид, поле, kind, matcher)
_SEM_SIGS: list[tuple[str, str, str, str, object]] = [
    ("consult_current", "consult", "current", "hours",
     lambda e: _has(e, "консульт") and _has(e, "текущ")),
    ("consult_before_exam", "consult", "before_exam", "hours",
     lambda e: _has(e, "консульт") and _has(e, "экзамен", "группов")),
    ("lectures_audi", "lectures", "audi", "hours",
     lambda e: _has(e, "лекц") and _has(e, "ауд")),
    ("lectures_dot", "lectures", "dot", "hours",
     lambda e: _has(e, "лекц") and _has(e, "дот", "дистанц")),
    ("practice_audi", "practice", "audi", "hours",
     lambda e: _has(e, "практ", "семинар") and _has(e, "ауд")),
    ("practice_dot", "practice", "dot", "hours",
     lambda e: _has(e, "практ", "семинар") and _has(e, "дот", "дистанц")),
    ("labs_audi", "labs", "audi", "hours",
     lambda e: _has(e, "лабор") and _has(e, "ауд")),
    ("labs_dot", "labs", "dot", "hours",
     lambda e: _has(e, "лабор") and _has(e, "дот", "дистанц")),
    ("usr_audi", "usr", "audi", "hours",
     lambda e: _has(e, "уср", "самостоятельн") and _has(e, "ауд")),
    ("usr_dot", "usr", "dot", "hours",
     lambda e: _has(e, "уср", "самостоятельн") and _has(e, "дот", "дистанц")),
    ("check_kr", "check", "kr", "hours",
     lambda e: _has(e, "проверк", "контрольн") and _has(e, "контрольн")),
    ("check_essays", "check", "essays", "hours", lambda e: _has(e, "реферат")),
    ("check_other", "check", "other", "hours",
     lambda e: _has(e, "проверк") and _has(e, "проч", "иные")),
    ("exam", "", "exam", "hours",
     lambda e: _has(e, "экзамен", "дифференцир") and _has_not(e, "консульт", "просмотр")),
    ("exam_review", "", "exam_review", "hours", lambda e: _has(e, "просмотр")),
    ("zachet", "", "zachet", "hours",
     lambda e: _has(e, "зачёт", "зачет") and _has_not(e, "дифференцир", "консульт")),
    ("semester_total", "", "semester_total", "hours",
     lambda e: _has(e, "всего за семестр", "итого за семестр")),
]

# Годовые поля: (field, kind, matcher)
_ANNUAL_SIGS: list[tuple[str, str, object]] = [
    ("supervision_edu_practice", "hours",
     lambda e: _has(e, "руководств") and _has(e, "практик") and _has(e, "учебн")),
    ("supervision_prod_practice", "hours",
     lambda e: _has(e, "руководств") and _has(e, "практик") and _has(e, "производств", "преддипломн")),
    ("supervision_coursework", "hours",
     lambda e: _has(e, "руководств") and _has(e, "курсов")),
    ("supervision_diploma", "hours",
     lambda e: _has(e, "руководств") and _has(e, "диплом")),
    ("supervision_master", "hours", lambda e: _has(e, "магистр")),
    ("supervision_phd", "hours", lambda e: _has(e, "аспирант")),
    ("review", "hours", lambda e: _has(e, "рецензир")),
    ("final_attestation", "hours", lambda e: _has(e, "итоговая аттестац", "гэк", "госэкзамен")),
    ("other", "hours", lambda e: _has(e, "иные", "прочие") and _has_not(e, "проверк")),
    ("year_total", "hours", lambda e: _has(e, "всего за год", "итого за год", "запланировано")),
]

_COUNT_FIELDS = {
    k for k, kind, _ in _IDENT_SIGS if kind == "int"
}
_HOUR_SEM = {k for k, _, _, kind, _ in _SEM_SIGS if kind == "hours"}
_HOUR_ANNUAL = {k for k, kind, _ in _ANNUAL_SIGS if kind == "hours"}
_SEM_LEAF = _HOUR_SEM - {"semester_total"}
_ANNUAL_LEAF = _HOUR_ANNUAL - {"year_total"}

_YEAR_RE = re.compile(r"20\d\d\s*[-–—/]\s*20\d\d")

# ---------------------------------------------------------------------------
# Значение ячейки с источником
# ---------------------------------------------------------------------------


def _frac(v: object) -> Fraction:
    if isinstance(v, Fraction):
        return v
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        return Fraction(str(v)).limit_denominator(1_000_000)
    s = str(v).strip().replace(",", ".")
    return Fraction(s) if s else Fraction(0)


def _render(v: object, kind: str) -> object:
    """Сериализация значения: часы — точная рациональная строка, численность — int."""
    if kind == "int":
        return int(v)  # type: ignore[arg-type]
    f = _frac(v)
    return f.numerator if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _cell(sheet: str, row: int, col: int, header: list[str], state: str,
          value: object = None, raw: str | None = None) -> dict:
    out = {
        "cell": f"'{sheet}'!{get_column_letter(col)}{row}",
        "header": header,
        "state": state,
        "value": value,
    }
    if raw is not None:
        out["raw"] = raw
    return out


def _read_cell(ws_d, ws_f, row: int, col: int, kind: str,
               header: list[str], issues: list[dict], sheet: str) -> dict:
    """Прочитать ячейку с учётом формулы и кэша; вернуть Cell-объект."""
    dval = ws_d.cell(row, col).value
    fcell = ws_f.cell(row, col)
    is_formula = fcell.data_type == "f"
    if is_formula and dval is None:
        return _cell(sheet, row, col, header, "unknown", raw=str(fcell.value))
    if not is_formula and (dval is None or (isinstance(dval, str) and not dval.strip())):
        return _cell(sheet, row, col, header, "blank")
    if kind == "str":
        text = re.sub(r"\s+", " ", str(dval).strip()) if dval is not None else None
        return _cell(sheet, row, col, header, "value", text)
    try:
        if kind == "int":
            f = _frac(dval)
            if f.denominator != 1 or f < 0:
                raise ValueError("ожидалось неотрицательное целое")
            rendered = f.numerator
        else:
            rendered = _render(dval, "hours")
        out = _cell(sheet, row, col, header, "value", rendered)
        if is_formula:
            out["formula"] = str(fcell.value)
        return out
    except (ValueError, ZeroDivisionError):
        issues.append(issue(
            "bad-value", source=f"'{sheet}'!{get_column_letter(col)}{row}",
            field=".".join(header), expected="число", actual=str(dval),
            next="исправить ячейку в книге",
        ))
        return _cell(sheet, row, col, header, "unknown", raw=str(dval))


# ---------------------------------------------------------------------------
# Поиск листа и шапки
# ---------------------------------------------------------------------------


def _row_token_hits(ws, r: int) -> int:
    hits = 0
    for c in range(1, (ws.max_column or 1) + 1):
        t = _norm(ws.cell(r, c).value)
        if t and any(tok in t for tok in _HEADER_TOKENS):
            hits += 1
    return hits


def _find_header(ws) -> int | None:
    """Строка начала шапки: содержит «дисциплин» и ≥4 токенов в окне 6 строк."""
    max_row = ws.max_row or 0
    for r in range(1, max_row + 1):
        for c in range(1, (ws.max_column or 1) + 1):
            if "дисциплин" in _norm(ws.cell(r, c).value):
                score = sum(_row_token_hits(ws, rr) for rr in range(r, min(r + 6, max_row + 1)))
                if score >= 4:
                    return r
    return None


def _merged_map(ws) -> dict[tuple[int, int], tuple[int, int, int, int]]:
    """(row,col) → (r0,c0,r1,c1) объединения, содержащего ячейку."""
    out: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for mr in ws.merged_cells.ranges:
        for rr in range(mr.min_row, mr.max_row + 1):
            for cc in range(mr.min_col, mr.max_col + 1):
                out[(rr, cc)] = (mr.min_row, mr.min_col, mr.max_row, mr.max_col)
    return out


def _eff(ws, merges, r: int, c: int) -> str:
    """Подпись ячейки с переносом объединения только внутри его диапазона."""
    if (r, c) in merges:
        r0, c0, _, _ = merges[(r, c)]
        return _norm(ws.cell(r0, c0).value)
    return _norm(ws.cell(r, c).value)


def _is_numbering_row(ws, r: int) -> bool:
    """Нумерующая строка шапки: много целых и ни одной непустой строки."""
    ints, texts = 0, 0
    for c in range(1, (ws.max_column or 1) + 1):
        v = ws.cell(r, c).value
        if isinstance(v, int):
            ints += 1
        elif isinstance(v, str) and v.strip():
            texts += 1
    return ints >= 3 and texts == 0


def _is_blank_row(ws, r: int) -> bool:
    return all(
        ws.cell(r, c).value in (None, "")
        for c in range(1, (ws.max_column or 1) + 1)
    )


# ---------------------------------------------------------------------------
# Главный разбор
# ---------------------------------------------------------------------------


def parse_sht(path: str | Path, year: str | None = None) -> tuple[dict, list[dict]]:
    """Разобрать книгу ШТ → (data, issues) документа workload/1.

    year — явный подтверждённый учебный год (CLI --year): нужен, когда
    в книге года нет; конфликт с годом книги — ошибка, не перезапись.
    """
    path = Path(path)
    issues: list[dict] = []
    # один захват: sha, значения и формулы — из одной редакции файла
    blob = path.read_bytes()
    wb_d = openpyxl.load_workbook(io.BytesIO(blob), data_only=True)
    wb_f = openpyxl.load_workbook(io.BytesIO(blob), data_only=False)

    # единственный лист с шапкой ШТ
    cands = [ws.title for ws in wb_d.worksheets if _find_header(ws) is not None]
    data: dict = {
        "sourceSha": hashlib.sha256(blob).hexdigest(),
        "teacher": None,
        "year": None,
        "yearSource": None,
        "sheet": None,
        "rows": [],
        "controls": [],
    }
    if len(cands) != 1:
        issues.append(issue(
            "sheet-not-found" if not cands else "sheet-ambiguous",
            expected="один лист с шапкой ШТ", actual=cands or wb_d.sheetnames,
            next="указать книгу с единственным листом нагрузки",
        ))
        return data, issues
    sheet = cands[0]
    data["sheet"] = sheet
    ws_d, ws_f = wb_d[sheet], wb_f[sheet]
    merges = _merged_map(ws_d)
    hdr = _find_header(ws_d)
    assert hdr is not None

    # учебный год из области над шапкой
    for r in range(1, hdr):
        for c in range(1, (ws_d.max_column or 1) + 1):
            m = _YEAR_RE.search(str(ws_d.cell(r, c).value or ""))
            if m:
                data["year"] = re.sub(r"\s+", "", m.group(0))
                break
        if data["year"]:
            break
    if data["year"]:
        data["yearSource"] = "header"
    want_year = re.sub(r"\s*[-–—/]+\s*", "-", str(year).strip()) if year else None
    if want_year and data["year"] and want_year != data["year"]:
        issues.append(issue(
            "year-mismatch", expected=data["year"], actual=want_year,
            next="явный год не совпадает с книгой — сверить источник"))
    elif want_year and not data["year"]:
        if _YEAR_RE.fullmatch(want_year):
            data["year"] = want_year
            data["yearSource"] = "parameter"
        else:
            issues.append(issue(
                "year-unknown", expected="год формата 2026-2027", actual=year,
                next="передать учебный год явно (--year 2026-2027)"))
    if not data["year"] and not any(
            i["code"] == "year-unknown" for i in issues):
        issues.append(issue("year-unknown", source=sheet,
                            next="добавить учебный год в шапку книги или указать явно"))

    # конец шапки: заголовочные, нумерующие и пустые строки
    band_end = hdr
    for r in range(hdr + 1, (ws_d.max_row or hdr) + 1):
        if _row_token_hits(ws_d, r) >= 2 or _is_numbering_row(ws_d, r) or _is_blank_row(ws_d, r):
            band_end = r
        else:
            break
    data_start = band_end + 1

    # путь заголовка каждой колонки (объединения — внутри диапазона).
    # Подпись блока — это объединённая ячейка шире одной колонки с текстом
    # маркера («осенний семестр», «работа в течение года»): в путь поля она
    # не входит. Одноколоночная ячейка вроде «Всего за год» — поле, не блок.
    max_col = ws_d.max_column or 1
    header_paths: dict[int, tuple[str, ...]] = {}
    for c in range(1, max_col + 1):
        elems: list[str] = []
        for r in range(hdr, band_end + 1):
            t = _eff(ws_d, merges, r, c)
            if not t or t.isdigit():
                continue
            width = 1
            if (r, c) in merges:
                _, mc0, _, mc1 = merges[(r, c)]
                width = mc1 - mc0 + 1
            if width > 1 and any(m in t for m in _MARKER_SUBS):
                continue  # подпись блока, а не поля
            if not elems or elems[-1] != t:
                elems.append(t)
        header_paths[c] = tuple(elems)

    # пролёты блоков по маркерам в шапке
    spans: dict[str, list[tuple[int, int]]] = {"autumn": [], "spring": [], "annual": []}
    for r in range(hdr, band_end + 1):
        for c in range(1, max_col + 1):
            t = _eff(ws_d, merges, r, c)
            if not t:
                continue
            c0 = c1 = c
            if (r, c) in merges:
                _, mc0, mr1, mc1 = merges[(r, c)]
                if mr1 >= r and mr1 <= band_end:  # только merge внутри шапки по вертикали допустим
                    c0, c1 = mc0, mc1
            for block, marks in _BLOCK_MARKERS:
                if any(m in t for m in marks):
                    spans[block].append((c0, c1))

    sem_blocks_found = bool(spans["autumn"] or spans["spring"])
    if sem_blocks_found and not (spans["autumn"] and spans["spring"]):
        issues.append(issue(
            "block-ambiguous", source=sheet, expected="обе подписи семестров",
            actual={"autumn": spans["autumn"], "spring": spans["spring"]},
            next="проверить строку блоков шапки",
        ))

    # зоны колонок
    zones: dict[int, str] = {}  # col -> identity|autumn|spring|annual|gap
    if sem_blocks_found:
        span_list = sorted(
            (a, b, block) for block, ss in spans.items() for a, b in ss)
        first_block = span_list[0][0]
        for c in range(1, max_col + 1):
            if c < first_block:
                zones[c] = "identity"
                continue
            covering = [bl for a, b, bl in span_list if a <= c <= b]
            if covering:
                zones[c] = covering[0]
                continue
            left = [s for s in span_list if s[1] < c]
            right = [s for s in span_list if s[0] > c]
            # разрыв строго между пролётами — продолжение левого блока
            # (годовые статьи между «Руководство» и «Всего за год»);
            # колонки правее последнего пролёта — вне блоков
            zones[c] = left[-1][2] if left and right else "gap"
    else:
        # нет маркеров блоков: зоны по сигнатурам полей
        for c in range(1, max_col + 1):
            e = header_paths[c]
            if any(m(e) for _, _, m in _IDENT_SIGS):
                zones[c] = "identity"
            elif any(m(e) for _, _, m in _ANNUAL_SIGS):
                zones[c] = "annual"
            elif any(m(e) for _, _, _, _, m in _SEM_SIGS):
                zones[c] = "sem"  # разведём ниже по порядку
            else:
                zones[c] = "gap"
        # порядок повторов семестровых полей: 1-й — осень, 2-й — весна
        seen: dict[str, int] = {}
        for c in range(1, max_col + 1):
            if zones[c] != "sem":
                continue
            e = header_paths[c]
            field = next((k for k, _, _, _, m in _SEM_SIGS if m(e)), None)
            seen[field] = seen.get(field, 0) + 1
            zones[c] = "autumn" if seen[field] == 1 else "spring"
            if seen[field] > 2:
                issues.append(issue("column-ambiguous", source=sheet, field=field,
                                    actual=f"колонка {get_column_letter(c)}",
                                    next="проверить подписи семестровых колонок"))

    # разрешение колонок в поля
    colmap: dict[int, tuple[str, str, str]] = {}  # col -> (zone, field, kind)
    used: set[tuple[str, str]] = set()
    for c in range(1, max_col + 1):
        e = header_paths[c]
        zone = zones.get(c, "gap")
        hit = None
        if zone == "identity":
            hit = next(((k, kind) for k, kind, m in _IDENT_SIGS if m(e)), None)
        elif zone in ("autumn", "spring"):
            hit = next(((k, kind) for k, _, _, kind, m in _SEM_SIGS if m(e)), None)
        elif zone == "annual":
            hit = next(((k, kind) for k, kind, m in _ANNUAL_SIGS if m(e)), None)
        if hit:
            field, kind = hit
            if (zone, field) in used:
                issues.append(issue(
                    "column-ambiguous", source=sheet, field=f"{zone}.{field}",
                    actual=f"колонка {get_column_letter(c)}",
                    next="одна подпись — одна колонка",
                ))
                continue
            used.add((zone, field))
            colmap[c] = (zone, field, kind)

    # строки данных и «Итого»
    total_row_idx = None
    data_rows: list[int] = []
    for r in range(data_start, (ws_d.max_row or data_start - 1) + 1):
        label = " ".join(
            _norm(ws_d.cell(r, c).value)
            for c in range(1, min(max_col, 4) + 1)
        )
        if re.search(r"\bитого\b", label):
            total_row_idx = r
            break
        if any(ws_d.cell(r, c).value not in (None, "") for c in colmap):
            data_rows.append(r)
    if total_row_idx is None:
        issues.append(issue(
            "control-missing", source=sheet, expected="строка «Итого»",
            next="полная форма ШТ обязана содержать контрольные итоги",
        ))

    def _header(zone: str, field: str) -> list[str]:
        if zone == "identity":
            return ["identity", "", field]
        if zone == "annual":
            return ["annual", "", field]
        grp = next((g for k, g, _, _, _ in _SEM_SIGS if k == field), "")
        sub = next((s for k, _, s, _, _ in _SEM_SIGS if k == field), "")
        return [zone, grp, sub or field]

    rows: list[dict] = []
    teachers: list[str] = []
    for r in data_rows:
        row: dict = {"sourceRow": r, "identity": {}, "autumn": {}, "spring": {}, "annual": {}}
        for c, (zone, field, kind) in sorted(colmap.items()):
            cell_obj = _read_cell(ws_d, ws_f, r, c, kind, _header(zone, field), issues, sheet)
            row[zone][field] = cell_obj
            if field == "teacher" and cell_obj["state"] == "value":
                teachers.append(cell_obj["value"])
        rows.append(row)
    # учитель: прямое чтение колонки; повторы — одна и та же строка
    distinct_teachers = list(dict.fromkeys(t for t in teachers if t))
    if len(distinct_teachers) == 1:
        data["teacher"] = distinct_teachers[0]
    elif len(distinct_teachers) > 1:
        issues.append(issue(
            "multiple-teachers", source=sheet, expected="один преподаватель",
            actual=distinct_teachers, next="выбрать книгу одного преподавателя",
        ))

    # carry-forward учителя: значение и его источник — ячейка, где записан
    last_teacher = last_teacher_cell = None
    for row in rows:
        t = row["identity"].get("teacher")
        if t and t["state"] == "value":
            last_teacher, last_teacher_cell = t["value"], t["cell"]
        elif last_teacher is not None:
            row["identity"]["teacher"] = {
                "cell": last_teacher_cell, "header": ["identity", "", "teacher"],
                "state": "value", "value": last_teacher, "carried": True,
            }
        else:
            row["identity"]["teacher"] = {"cell": None, "header": ["identity", "", "teacher"],
                                          "state": "blank", "value": None}

    # ------------------------------------------------------------------
    # контроли
    # ------------------------------------------------------------------

    def _cellnum(obj: dict) -> Fraction | None:
        """Число ячейки: None, если состояние не позволяет считать сумму."""
        if obj["state"] == "unknown":
            return None
        if obj["state"] == "blank" or obj["value"] is None:
            return Fraction(0)
        return _frac(obj["value"])

    def _total_cell(col: int, *, blank_is_zero: bool = False) -> tuple[Fraction | None, str | None]:
        if total_row_idx is None:
            return None, None
        ref = f"'{sheet}'!{get_column_letter(col)}{total_row_idx}"
        v = ws_d.cell(total_row_idx, col).value
        if ws_f.cell(total_row_idx, col).data_type == "f" and (
                v is None or (isinstance(v, str) and not v.strip())):
            # Формула без сохранённого кэша не прочитана. Даже для годовой
            # листовой статьи, где обычная пустая ячейка означает ноль,
            # формула — это не пусто и не ноль.
            return None, ref
        if v is None or (isinstance(v, str) and not v.strip()):
            # Пустая листовая статья годового блока означает отсутствие
            # работы, но пустая контрольная ячейка семестра/года — это
            # непрочитанный контроль, а не доказанный ноль.
            return (Fraction(0) if blank_is_zero else None), ref
        try:
            return _frac(v), ref
        except (ValueError, ZeroDivisionError):
            issues.append(issue("bad-value", source=ref, expected="число",
                                actual=str(v), next="исправить строку «Итого»"))
            return None, ref

    controls: list[dict] = []
    cols_of = {f: c for c, (z, f, _) in colmap.items() if z == "annual"}
    sem_cols = {
        z: c for c, (z, f, _) in colmap.items()
        if f == "semester_total" and z in ("autumn", "spring")
    }

    per_kind: dict[str, tuple[Fraction | None, list[str]]] = {}
    for sem in ("autumn", "spring"):
        contribs, computed, unknown = [], Fraction(0), False
        for row in rows:
            obj = row[sem].get("semester_total")
            if obj is None:
                continue
            contribs.append(obj["cell"])
            n = _cellnum(obj)
            if n is None:
                unknown = True
            else:
                computed += n
        per_kind[sem] = (None if unknown else computed, contribs)

    ann_contribs, ann_computed, ann_unknown = [], Fraction(0), False
    for row in rows:
        for k in _ANNUAL_LEAF:
            obj = row["annual"].get(k)
            if obj is None:
                continue
            ann_contribs.append(obj["cell"])
            n = _cellnum(obj)
            if n is None:
                ann_unknown = True
            else:
                ann_computed += n
    per_kind["annual"] = (None if ann_unknown else ann_computed, ann_contribs)

    a_c, s_c, an_c = per_kind["autumn"][0], per_kind["spring"][0], per_kind["annual"][0]
    grand_computed = None if any(v is None for v in (a_c, s_c, an_c)) else a_c + s_c + an_c
    per_kind["grand"] = (
        grand_computed,
        per_kind["autumn"][1] + per_kind["spring"][1] + ann_contribs,
    )

    book: dict[str, tuple[Fraction | None, list[str]]] = {
        "autumn": (None, []), "spring": (None, []),
        "annual": (None, []), "grand": (None, []),
    }
    if total_row_idx is not None:
        for sem in ("autumn", "spring"):
            if sem in sem_cols:
                v, ref = _total_cell(sem_cols[sem])
                book[sem] = (v, [ref] if ref else [])
        ann_book, ann_refs, ann_bad = Fraction(0), [], False
        for k in _ANNUAL_LEAF:
            if k not in cols_of:
                continue
            v, ref = _total_cell(cols_of[k], blank_is_zero=True)
            if ref:
                ann_refs.append(ref)
            if v is None:
                ann_bad = True
            else:
                ann_book += v
        book["annual"] = (None if ann_bad else ann_book, ann_refs)
        if "year_total" in cols_of:
            v, ref = _total_cell(cols_of["year_total"])
            book["grand"] = (v, [ref] if ref else [])

    for kind in ("autumn", "spring", "annual", "grand"):
        computed, contribs = per_kind[kind]
        book_v, book_refs = book[kind]
        if book_v is None or computed is None:
            match = "unknown"
        else:
            match = "ok" if computed == book_v else "mismatch"
        controls.append({
            "kind": kind,
            "computed": _render(computed, "hours") if computed is not None else None,
            "book": _render(book_v, "hours") if book_v is not None else None,
            "bookCells": book_refs,
            "contributors": contribs,
            "match": match,
        })
        if match == "mismatch":
            issues.append(issue(
                "total-mismatch", source=";".join(book_refs), field=kind,
                expected=str(book_v), actual=str(computed),
                next="сверить книгу: итог не сходится, подготовка заблокирована",
            ))
        elif match == "unknown" and total_row_idx is not None:
            issues.append(issue(
                "total-unverified", source=";".join(book_refs) or sheet, field=kind,
                next="контрольное значение книги или вклады не прочитаны",
            ))

    # внутренние проверки строк: сумма листьев против контрольной ячейки
    for row in rows:
        for sem in ("autumn", "spring"):
            objs = [row[sem][k] for k in _SEM_LEAF if k in row[sem]]
            total_obj = row[sem].get("semester_total")
            if total_obj is None:
                continue
            if any(o["state"] == "unknown" for o in objs) or total_obj["state"] == "unknown":
                continue
            leaf = sum((_cellnum(o) or Fraction(0) for o in objs), Fraction(0))
            tval = _cellnum(total_obj)
            if tval is not None and leaf != tval:
                issues.append(issue(
                    "row-total-mismatch",
                    source=";".join(o["cell"] for o in objs if o["cell"]) + ";" + str(total_obj["cell"]),
                    field=f"{sem}.semester_total",
                    expected=str(tval), actual=str(leaf),
                    next="листья строки не сходятся с «Всего за семестр»",
                ))
        # «всего за год» = осень + весна + годовые
        yt = row["annual"].get("year_total")
        if yt and yt["state"] == "value":
            parts = [row["autumn"].get("semester_total"), row["spring"].get("semester_total")]
            parts += [row["annual"][k] for k in _ANNUAL_LEAF if k in row["annual"]]
            if all(p is not None and p["state"] != "unknown" for p in parts):
                expect = sum((_cellnum(p) or Fraction(0) for p in parts), Fraction(0))
                got = _cellnum(yt)
                if got is not None and expect != got:
                    issues.append(issue(
                        "row-total-mismatch", source=str(yt["cell"]), field="annual.year_total",
                        expected=str(got), actual=str(expect),
                        next="строка не сходится с «Всего за год»",
                    ))

    # неизвестные колонки с ненулевыми данными внутри блоков и между ними
    block_cols_ = [c for c, z in zones.items() if z in ("autumn", "spring", "annual")]
    first_b, last_b = (min(block_cols_), max(block_cols_)) if block_cols_ else (0, 0)
    for c in range(1, max_col + 1):
        if c in colmap or zones.get(c) == "identity":
            continue
        in_zone = zones.get(c) in ("autumn", "spring", "annual")
        between = first_b < c < last_b
        if not (in_zone or between):
            continue
        for r in data_rows + ([total_row_idx] if total_row_idx else []):
            v = ws_d.cell(r, c).value
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            try:
                nonzero = _frac(v) != 0
            except (ValueError, ZeroDivisionError):
                nonzero = False  # нечисловой текст
            # внутри блока — любое значение; между блоками — только ненулевое число
            if not (nonzero or (in_zone and not isinstance(v, (int, float)))):
                continue
            issues.append(issue(
                "unknown-column", source=f"'{sheet}'!{get_column_letter(c)}{r}",
                field=".".join(header_paths[c]) or get_column_letter(c),
                expected="известный заголовок", actual=str(v),
                next="незнакомая ненулевая статья — сопоставить заголовок вручную",
            ))
            break

    data["rows"] = rows
    data["controls"] = controls
    return data, issues


def workload_document(path: str | Path, year: str | None = None) -> dict:
    """Документ bsu.workload/1 для `mo-cabinet workload [--year]`."""
    data, issues = parse_sht(path, year=year)
    return envelope("bsu.workload/1", status_of(issues), data, issues)
