"""Снимок индивидуального плана и проверяемый предпросмотр.

`mo-cabinet workload` даёт документ bsu.workload/1 из формы ШТ.
`snapshot_document(raw)` нормализует сырой ответ кабинета (или его
локальную копию) в bsu.snapshot/1 — чтение живого кабинета этим не
доказывается.
`plan_diff_document(workload, snapshot)` строит bsu.diff/1: операции
add/edit/noop/preserve/conflict по каждой строке и сверку четырёх
итогов независимо. Никаких записей в кабинет здесь нет.
"""

from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from typing import Any

from .schema import STATUS_BLOCKED, STATUS_OK, envelope, issue, status_of
from .workload import _ANNUAL_LEAF, _SEM_LEAF, _frac

_SEM_OF_ZONE = {"autumn": 0, "spring": 1}
_EDITABLE_STATUS = (0, 3)  # StatusPlan: заполнение / отклонён

# канонический годовой вид → категория кабинета (tbl-20)
_ANNUAL_CATEGORY = {
    "supervision_edu_practice": "Руководство учебной практикой",
    "supervision_prod_practice": "Руководство производственной практикой",
    "supervision_coursework": "Руководство курсовыми работами",
    "supervision_diploma": "Руководство дипломными работами",
    "supervision_master": "Руководство магистрантами",
    "supervision_phd": "Руководство аспирантами",
    "review": "Рецензирование квалификационных работ",
    "final_attestation": "Итоговая аттестация",
    "other": "Иные виды работ",
}

# порядок полей формы как в list_ss кабинета
_SEMESTER_PAYLOAD = (
    "Curs", "FormEducation", "CountLine", "CountGroups", "CountGroupsSub",
    "CountStudent_lecture", "CountStudent_practical", "CountStudent_laboratory",
    "CountStudentAlien",
    "Clock_lecture_a", "Clock_lecture_b", "Clock_practical_a", "Clock_practical_b",
    "Clock_laboratory_a", "Clock_laboratory_b", "Clock_ucr_a", "Clock_ucr_b",
    "Collocvium", "Help_now", "Help_personal", "Help_group",
    "Check_control", "Exam", "Credit", "Description",
)

_HOUR_PAYLOAD = {
    "Clock_lecture_a": "lectures_audi", "Clock_lecture_b": "lectures_dot",
    "Clock_practical_a": "practice_audi", "Clock_practical_b": "practice_dot",
    "Clock_laboratory_a": "labs_audi", "Clock_laboratory_b": "labs_dot",
    "Clock_ucr_a": "usr_audi", "Clock_ucr_b": "usr_dot",
    "Help_now": "consult_current", "Help_group": "consult_before_exam",
    "Exam": "exam", "Credit": "zachet",
}

_IDENT_PAYLOAD = {
    "CountLine": "streams", "CountGroups": "groups", "CountGroupsSub": "subgroups",
    "CountStudent_lecture": "students_lecture",
    "CountStudent_practical": "students_practical",
    "CountStudent_laboratory": "students_laboratory",
    "CountStudentAlien": "students_foreign",
}

# Часовые поля, которые клиент кабинета складывает в итог строки
# (`td.to-sum` в PersonalPlan-ur-semestr.js). Численность, курс, форма
# обучения и описание сюда не входят.
_SEMESTER_TOTAL_FIELDS = (
    "Clock_lecture_a", "Clock_lecture_b", "Clock_practical_a", "Clock_practical_b",
    "Clock_laboratory_a", "Clock_laboratory_b", "Clock_ucr_a", "Clock_ucr_b",
    "Collocvium", "Help_now", "Help_personal", "Help_group",
    "Check_control", "Exam", "Credit",
)


def _fmt(v: Fraction) -> str:
    if v == 0:
        return "0"
    if v.denominator == 1:
        return str(v.numerator)
    return f"{float(v):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _cell_num(obj: dict | None) -> Fraction | None:
    """Число из ячейки workload/1; unknown/отсутствие → None (не ноль)."""
    if obj is None or obj["state"] == "unknown":
        return None
    if obj["state"] == "blank" or obj["value"] is None:
        return Fraction(0)
    return _frac(obj["value"])


def _cell_hours(obj: dict | None) -> str:
    """Отформатированное значение для payload; пусто → ''."""
    n = _cell_num(obj)
    if n is None:
        raise KeyError("unknown")
    return "" if (obj is None or obj["state"] == "blank") else _fmt(n)


# ----------------------------------------------------------------------
# ожидаемые строки из workload/1
# ----------------------------------------------------------------------

def expected_rows(wdoc: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(semester_ops, annual_ops, issues) из документа workload/1.

    semester_ops: {identity, semestr, expectedPayload, sources, cells}
    annual_ops:   {identity, category, value, sources}
    """
    issues: list[dict] = []
    sem_ops: list[dict] = []
    annual: dict[str, dict] = {}
    data = wdoc["data"]
    year = data.get("year")

    for row in data["rows"]:
        ident_cells = row["identity"]

        def _i(field: str, ic: dict = ident_cells) -> Any:
            c = ic.get(field)
            return c["value"] if c and c["state"] == "value" else None

        identity = {
            "year": year,
            "faculty": _i("faculty"),
            "specialty": _i("specialty"),
            "discipline": _i("discipline"),
            "course": _i("course"),
            "studyForm": _i("study_form"),
        }

        for zone, semestr in _SEM_OF_ZONE.items():
            cells = row[zone]
            leaf = [cells[k] for k in sorted(_SEM_LEAF) if k in cells]
            assigned = any(
                (c["state"] == "value" and _cell_num(c) != 0)
                or c["state"] == "unknown"
                for c in leaf
            )
            if not assigned:
                continue
            payload: dict[str, Any] = {}
            sources: dict[str, str] = {}
            bad = False
            for payload_field, src_field in _HOUR_PAYLOAD.items():
                cell = cells.get(src_field)
                try:
                    payload[payload_field] = _cell_hours(cell)
                except KeyError:
                    bad = True
                    payload[payload_field] = None
                if cell and cell.get("cell"):
                    sources[payload_field] = cell["cell"]
            check_cells = [cells[k] for k in ("check_kr", "check_essays", "check_other")
                           if k in cells]
            if any(c["state"] == "unknown" for c in check_cells):
                bad = True
                payload["Check_control"] = None
            elif not any(c["state"] == "value" for c in check_cells):
                payload["Check_control"] = ""  # все пустые — работы нет, не ноль
            else:
                payload["Check_control"] = _fmt(
                    sum((_cell_num(c) or Fraction(0) for c in check_cells), Fraction(0)))
            sources["Check_control"] = ";".join(
                c["cell"] for c in check_cells if c.get("cell"))
            for payload_field, src_field in _IDENT_PAYLOAD.items():
                cell = ident_cells.get(src_field)
                if cell and cell["state"] == "value":
                    payload[payload_field] = cell["value"]
                    sources[payload_field] = cell["cell"]
                elif cell and cell["state"] == "unknown":
                    bad = True
                    payload[payload_field] = None
                else:
                    payload[payload_field] = ""
            form = _i("study_form")
            payload["Curs"] = _i("course") if _i("course") is not None else ""
            payload["FormEducation"] = (
                "0" if str(form or "").strip().startswith("оч")
                else "1" if form is not None else "")
            payload["Collocvium"] = "0"
            payload["Help_personal"] = "0"
            payload["Description"] = ""
            op = {
                "identity": {**identity, "semester": semestr},
                "semestr": semestr,
                "expectedPayload": payload,
                "sources": sources,
            }
            if bad:
                op["unresolved"] = True
                issues.append(issue(
                    "value-unknown", source=str(row["sourceRow"]),
                    field=f"{zone}.*",
                    next="ячейка-формула без кэша: пересчитать книгу или вписать значение"))
            sem_ops.append(op)

        for k in sorted(_ANNUAL_LEAF):
            cell = row["annual"].get(k)
            if cell is None:
                continue
            if cell["state"] == "unknown":
                issues.append(issue(
                    "value-unknown", source=cell["cell"], field=f"annual.{k}",
                    next="ячейка-формула без кэша"))
                bucket = annual.setdefault(k, {
                    "identity": {"year": year, "category": _ANNUAL_CATEGORY.get(k, k)},
                    "field": k, "value": Fraction(0), "sources": {}, "unknown": False,
                })
                bucket["unknown"] = True
                bucket["sources"][f"{row['sourceRow']}:{k}"] = cell["cell"]
                continue
            n = _cell_num(cell)
            if not n:
                continue
            bucket = annual.setdefault(k, {
                "identity": {"year": year, "category": _ANNUAL_CATEGORY.get(k, k)},
                "field": k, "value": Fraction(0), "sources": {}, "unknown": False,
            })
            bucket["value"] += n
            bucket["sources"][f"{row['sourceRow']}:{k}"] = cell["cell"]

    annual_ops = [
        {**b, "value": _render_num(b["value"])}
        for b in (annual[k] for k in sorted(annual))
    ]
    return sem_ops, annual_ops, issues


def _render_num(v: Fraction) -> int | float | str:
    if v.denominator == 1:
        return v.numerator
    f = float(v)
    return f if Fraction(f) == v else str(v)


def _norm_hours(cell: dict | None) -> Fraction | None:
    """Часы нормализованной ячейки снимка.

    Пустое поле кабинета в часовой колонке — ноль (client-side `int('')`
    в журнале кабинета), а `unknown`/отсутствующее число — None: его нельзя
    молча сложить как ноль.
    """
    if cell is None or cell["state"] == "blank":
        return Fraction(0)
    if cell["state"] == "unknown" or cell["value"] is None:
        return None
    return _frac(cell["value"])


def _snapshot_semester_hours(normalized: dict) -> tuple[Fraction | None, list[str]]:
    """Сумма часов строки снимка; unknown в любом слагаемом делает сумму unknown."""
    total = Fraction(0)
    unknown: list[str] = []
    for field in _SEMESTER_TOTAL_FIELDS:
        n = _norm_hours(normalized.get(field))
        if n is None:
            unknown.append(field)
        else:
            total += n
    return (None if unknown else total), unknown


def _snapshot_annual_hours(row: dict) -> Fraction | None:
    """Плановое Value годовой строки; ValueResult в итог плана не входит."""
    return _norm_hours(row.get("value"))


# ----------------------------------------------------------------------
# snapshot/1
# ----------------------------------------------------------------------

def _norm_field(raw: Any) -> dict:
    """{raw, state, value}: строка '18,6'/'18.6' → Fraction; '' → blank."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {"raw": raw, "state": "blank", "value": None}
    try:
        v = _frac(raw)
    except (ValueError, ZeroDivisionError):
        return {"raw": raw, "state": "unknown", "value": None}
    return {"raw": raw, "state": "value", "value": _render_num(v)}


def snapshot_document(raw: dict) -> dict:
    """bsu.snapshot/1 из сырого чтения кабинета или его локальной копии.

    raw: {owner, year, statusPlan, loaded, loadedSections?,
          rows:[{key,semester,identity,fields}],
          annual:[{key,category,value,valueResult}], totals:{...}}.
    `loaded: false` означает «ответа/таблицы не было» — это не пустой план.
    `loadedSections` перечисляет реально прочитанные разделы
    (autumn/spring/annual); неполный набор — тоже «не прочитано».
    """
    issues: list[dict] = []
    loaded = raw.get("loaded", True)
    status_plan = raw.get("statusPlan")
    editable = status_plan in _EDITABLE_STATUS
    required = {"autumn", "spring", "annual"}
    sections = raw.get("loadedSections")
    partial = sections is not None and set(sections) != required
    if not loaded:
        state = "unloaded"
        issues.append(issue(
            "snapshot-unloaded", expected="загруженная таблица ИП",
            actual=raw.get("error"),
            next="снимок не прочитан: повторить чтение, не считать план пустым"))
    elif partial:
        state = "unloaded"
        issues.append(issue(
            "snapshot-partial", expected=sorted(required), actual=sections,
            next="прочитаны не все разделы ИП — снимок недостоверен"))
    elif not editable:
        state = "closed"
    elif not raw.get("rows"):
        # скелет годовых видов в tbl-20 не считается заполненным планом
        state = "empty"
    else:
        state = "populated"
    if state == "closed":
        issues.append(issue(
            "snapshot-readonly", field="StatusPlan", actual=status_plan,
            expected=f"один из {list(_EDITABLE_STATUS)}",
            next="план на проверке/утверждён — только чтение, подготовка остановлена"))

    rows = []
    for r in raw.get("rows") or []:
        identity = {k: r.get("identity", {}).get(k) for k in
                    ("faculty", "specialty", "discipline", "course", "studyForm")}
        course = str(identity.get("course") or "").strip()
        if course.isdigit():  # DOM отдаёт текст — курсу нужен int как в книге
            identity["course"] = int(course)
        rows.append({
            "key": r.get("key"),
            "semester": r.get("semester"),
            "identity": identity,
            "rawFields": r.get("fields") or {},
            "normalizedFields": {k: _norm_field(v) for k, v in (r.get("fields") or {}).items()},
        })
    annual = [{
        "key": a.get("key"),
        "category": a.get("category"),
        "value": _norm_field(a.get("value")),
        "valueResult": _norm_field(a.get("valueResult")),
    } for a in raw.get("annual") or []]
    totals = {k: _norm_field((raw.get("totals") or {}).get(k))
              for k in ("autumn", "spring", "annual", "grand")}

    data = {
        "owner": raw.get("owner"),
        "year": raw.get("year"),
        "statusPlan": status_plan,
        "editable": editable,
        "state": state,
        "sections": raw.get("loadedSections") or raw.get(
            "sections", ["autumn", "spring", "annual"]),
        "rows": rows,
        "annual": annual,
        "totals": totals,
        "scopeHash": _sha({"owner": raw.get("owner"), "year": raw.get("year"),
                           "rows": [r["identity"] for r in rows]}),
    }
    return envelope("bsu.snapshot/1", status_of(issues), data, issues)


# ----------------------------------------------------------------------
# diff/1
# ----------------------------------------------------------------------

_YEAR_SEP = re.compile(r"[\s\-–—/]+")


def _norm_year(v: object) -> str:
    """Однозначные записи года к канону «2026-2027»; прочее — как есть."""
    return _YEAR_SEP.sub("-", str(v or "").strip())


def _looks_abbreviated(name: str, full: str) -> bool:
    """Сокращённая форма ФИО: инициалы или меньше слов, чем в полном."""
    n, f = _n(name), _n(full)
    if "." in n:
        return True
    toks, ftoks = n.split(), f.split()
    return len(toks) < len(ftoks) or any(len(t) <= 1 for t in toks)


def _scope_issues(wdoc: dict, sdata: dict) -> list[dict]:
    """Сверка владельца и года снимка с книгой. Только однозначное
    представление считается совпадением; сокращение — не подтверждение."""
    out: list[dict] = []
    teacher = wdoc.get("data", {}).get("teacher")
    owner = sdata.get("owner")
    if not _n(owner or "") or not teacher:
        out.append(issue(
            "owner-unknown", expected="владелец книги и снимка",
            actual=owner or teacher,
            next="установить владельца снимка до сверки"))
    elif _n(owner) != _n(teacher):
        code = ("owner-ambiguous" if _looks_abbreviated(owner, teacher)
                else "owner-mismatch")
        out.append(issue(
            code, expected=teacher, actual=owner,
            next="снимок от другого владельца — подготовка остановлена"
            if code == "owner-mismatch"
            else "сокращённое ФИО без подтверждённого сопоставления"))
    wyear = _norm_year(wdoc.get("data", {}).get("year"))
    syear = _norm_year(sdata.get("year"))
    if not syear or not wyear:
        out.append(issue(
            "year-unknown", expected="учебный год книги и снимка",
            actual=sdata.get("year"),
            next="установить учебный год до сверки"))
    elif wyear != syear:
        out.append(issue(
            "year-mismatch", expected=wyear, actual=sdata.get("year"),
            next="снимок другого учебного года — подготовка остановлена"))
    return out


def _match_key(identity: dict, semester: int) -> tuple:
    return (
        _n(identity.get("faculty")), _n(identity.get("specialty")),
        _n(identity.get("discipline")),
        identity.get("course"), _n(identity.get("studyForm")), semester,
    )


def _field_diff(payload: dict, normalized: dict) -> dict[str, dict]:
    """Поля, где ожидание ≠ снимок: {field: {before, after}}."""
    diff: dict[str, dict] = {}
    for field in _SEMESTER_PAYLOAD:
        expected = payload.get(field, "")
        cell = normalized.get(field)
        before_raw = cell["raw"] if cell else None
        if cell is None or cell["state"] == "blank":
            before_num: Fraction | None = None
        elif cell["state"] == "unknown":
            diff[field] = {"before": before_raw, "after": expected, "state": "unknown"}
            continue
        else:
            before_num = _frac(cell["value"]) if cell["value"] is not None else None
        if expected in ("", None):
            exp_num = None
        else:
            try:
                exp_num = _frac(expected)
            except (ValueError, ZeroDivisionError):
                exp_num = None
        if exp_num is None and before_num is None:
            if (expected or "") == (str(before_raw or "")):
                continue
            diff[field] = {"before": before_raw, "after": expected}
            continue
        if before_num is None or exp_num is None or before_num != exp_num:
            diff[field] = {
                "before": _render_num(before_num) if before_num is not None else before_raw,
                "after": expected,
            }
    return diff


def plan_diff_document(wdoc: dict, sdoc: dict) -> dict:
    """bsu.diff/1: предпросмотр workload/1 против snapshot/1, без записи."""
    issues: list[dict] = []
    data = {
        "scope": {"teacher": wdoc.get("data", {}).get("teacher"),
                  "year": wdoc.get("data", {}).get("year")},
        "sourceSha": wdoc.get("data", {}).get("sourceSha"),
        "beforeHash": None,
        "operations": [],
        "totals": [],
        "blocked": False,
    }
    if wdoc.get("status") != STATUS_OK:
        issues.append(issue(
            "workload-blocked", expected="книга с сошедшимися итогами",
            actual=wdoc.get("status"),
            next="пока итоги книги не проверены, ничего не подготавливать"))
        return envelope("bsu.diff/1", STATUS_BLOCKED, data, issues)

    sdata = sdoc.get("data", {})
    state = sdata.get("state")
    # хэш привязан к этому же снимку целиком: владелец, год, статус и поля
    data["beforeHash"] = _sha({
        "owner": sdata.get("owner"), "year": sdata.get("year"),
        "statusPlan": sdata.get("statusPlan"),
        "rows": sdata.get("rows"), "annual": sdata.get("annual"),
        "totals": sdata.get("totals")})
    blocked = False
    if state == "unloaded":
        issues.append(issue(
            "snapshot-unloaded", next="нет чтения плана — дифф недостоверен"))
        return envelope("bsu.diff/1", STATUS_BLOCKED, data, issues)
    if state == "closed":
        issues.append(issue(
            "plan-readonly", field="StatusPlan", actual=sdata.get("statusPlan"),
            next="план не редактируется — предпросмотр запрещён"))
        blocked = True

    # Ожидаемые значения читаются даже при policy-блоке области: это не
    # создаёт операций, но не прячет value-unknown за owner/year/readonly.
    sem_ops, annual_ops, exp_issues = expected_rows(wdoc)

    # владелец и учебный год — до расчёта операций: сомнение = стоп
    id_issues = _scope_issues(wdoc, sdata)
    if id_issues:
        issues.extend(id_issues)
        issues.extend(exp_issues)
        return envelope("bsu.diff/1", STATUS_BLOCKED, data, issues)

    snap_rows = sdata.get("rows") or []
    key_index: dict[tuple, list[dict]] = {}
    for r in snap_rows:
        key_index.setdefault(
            _match_key(r["identity"], r.get("semester")), []).append(r)

    ops: list[dict] = []
    used_keys: set[int] = set()
    issues.extend(exp_issues)
    if exp_issues:
        blocked = True

    preserved: dict[str, Fraction] = {
        "autumn": Fraction(0), "spring": Fraction(0), "annual": Fraction(0),
    }
    preserved_unknown: dict[str, list[str]] = {
        "autumn": [], "spring": [], "annual": [],
    }
    preserved_sources: dict[str, list[str]] = {
        "autumn": [], "spring": [], "annual": [],
    }
    unresolved_total_rows: dict[str, list[str]] = {
        "autumn": [], "spring": [], "annual": [],
    }

    def _mark_row_conflict(rows: list[dict], semester: int | None) -> None:
        kind = "autumn" if semester == 0 else "spring" if semester == 1 else None
        if kind:
            unresolved_total_rows[kind].extend(str(r.get("key")) for r in rows)

    for exp in sem_ops:
        key = _match_key(exp["identity"], exp["semestr"])
        matches = key_index.get(key, [])
        base = {"identity": exp["identity"], "key": None,
                "expectedPayload": exp["expectedPayload"], "sources": exp["sources"]}
        if exp.get("unresolved"):
            if matches:
                used_keys.update(id(m) for m in matches)
                if len(matches) > 1:
                    _mark_row_conflict(matches, exp["semestr"])
            ops.append({**base, "op": "conflict",
                        "key": matches[0]["key"] if len(matches) == 1 else None,
                        "before": ([m["key"] for m in matches] if len(matches) > 1 else {}),
                        "reason": "value-unknown"})
            blocked = True
            continue
        if len(matches) > 1:
            used_keys.update(id(m) for m in matches)
            _mark_row_conflict(matches, exp["semestr"])
            ops.append({**base, "op": "conflict",
                        "before": [m["key"] for m in matches],
                        "reason": "indistinguishable-rows"})
            issues.append(issue(
                "rows-conflict", field=str(key),
                actual=[m["key"] for m in matches],
                next="несколько строк кабинета не различить по ключу — проверить вручную"))
            blocked = True
            continue
        if not matches:
            ops.append({**base, "op": "add", "before": {},
                        "after": exp["expectedPayload"]})
            continue
        cur = matches[0]
        used_keys.add(id(cur))
        fields = _field_diff(exp["expectedPayload"], cur["normalizedFields"])
        op = "noop" if not fields else "edit"
        ops.append({**base, "op": op, "key": cur["key"],
                    "before": {f: cur["rawFields"].get(f) for f in fields},
                    "after": {f: exp["expectedPayload"][f] for f in fields},
                    "changed": sorted(fields)})

    # годовые: ключ — год + категория; существующий вид редактируется
    snap_annual = sdata.get("annual") or []
    ann_index: dict[str, list[dict]] = {}
    for a in snap_annual:
        ann_index.setdefault(_n(a.get("category")), []).append(a)
    for exp in annual_ops:
        cat = exp["identity"]["category"]
        matches = ann_index.get(_n(cat), [])
        expected_value = None if exp.get("unknown") else _fmt(_frac(exp["value"]))
        base = {"identity": exp["identity"], "key": None,
                "expectedPayload": {"Value": expected_value},
                "sources": exp["sources"], "section": "annual"}
        if exp.get("unknown"):
            if matches:
                used_keys.update(id(m) for m in matches)
                if len(matches) > 1:
                    unresolved_total_rows["annual"].extend(str(m.get("key")) for m in matches)
            ops.append({**base, "op": "conflict",
                        "key": matches[0]["key"] if len(matches) == 1 else None,
                        "before": [m["key"] for m in matches] if matches else {},
                        "reason": "value-unknown"})
            blocked = True
            continue
        if len(matches) > 1:
            used_keys.update(id(m) for m in matches)
            unresolved_total_rows["annual"].extend(str(m.get("key")) for m in matches)
            ops.append({**base, "op": "conflict",
                        "before": [m["key"] for m in matches],
                        "reason": "indistinguishable-rows"})
            issues.append(issue(
                "rows-conflict", field=cat, actual=[m["key"] for m in matches],
                next="несколько годовых строк одного вида — проверить вручную"))
            blocked = True
            continue
        if not matches:
            ops.append({**base, "op": "conflict", "before": {}, "after": exp["value"],
                        "reason": "category-missing"})
            issues.append(issue(
                "annual-missing", field=cat, expected="существующий вид работ",
                next="годовая категория не найдена в плане — не создавать автоматически"))
            blocked = True
            continue
        cur = matches[0]
        before = cur["value"]
        after = exp["value"]
        same = (before["state"] == "value" and before["value"] is not None
                and _frac(before["value"]) == _frac(after))
        ops.append({**base, "op": "noop" if same else "edit", "key": cur["key"],
                    "before": before["raw"] if not same else after,
                    "after": {"Value": _fmt(_frac(after))},
                    "changed": [] if same else ["Value"]})
        used_keys.add(id(cur))

    # строки снимка вне запроса — сохранить, не трогать. Их часы остаются
    # в итогах кабинета после записи книги, поэтому общий итог нельзя
    # сравнивать с одним лишь computed книги.
    for r in snap_rows:
        if id(r) not in used_keys:
            semester = r.get("semester")
            kind = "autumn" if semester == 0 else "spring" if semester == 1 else None
            if kind:
                hours, unknown_fields = _snapshot_semester_hours(r["normalizedFields"])
                ref = f"row:{r.get('key')}"
                preserved_sources[kind].append(ref)
                if hours is None:
                    preserved_unknown[kind].extend(
                        f"{ref}.{field}" for field in unknown_fields)
                else:
                    preserved[kind] += hours
            ops.append({"op": "preserve", "key": r["key"],
                        "identity": {**r["identity"], "semester": r["semester"]},
                        "before": r["rawFields"], "after": r["rawFields"],
                        "expectedPayload": {}, "sources": {}})
    for a in snap_annual:
        if id(a) not in used_keys:
            hours = _snapshot_annual_hours(a)
            ref = f"annual:{a.get('key')}"
            preserved_sources["annual"].append(ref)
            if hours is None:
                preserved_unknown["annual"].append(f"{ref}.Value")
            else:
                preserved["annual"] += hours
            ops.append({"op": "preserve", "key": a["key"], "section": "annual",
                        "identity": {"year": sdata.get("year"),
                                     "category": a["category"]},
                        "before": a["value"]["raw"], "after": a["value"]["raw"],
                        "expectedPayload": {}, "sources": {}})

    # четыре итога независимо: цель после записи = книга + сохранённые
    # строки вне запроса. Unknown в любом слагаемом остаётся unknown и не
    # превращается в ноль; явный ноль при этом сохраняется как ноль.
    controls = {c["kind"]: c for c in wdoc["data"]["controls"]}
    snap_totals = sdata.get("totals") or {}
    outside: dict[str, Fraction | None] = {}
    for kind in ("autumn", "spring", "annual"):
        outside[kind] = None if preserved_unknown[kind] else preserved[kind]
    outside["grand"] = (
        None if any(outside[k] is None for k in ("autumn", "spring", "annual"))
        else sum((outside[k] for k in ("autumn", "spring", "annual")), Fraction(0))
    )
    for kind in ("autumn", "spring", "annual", "grand"):
        book = controls.get(kind, {})
        before = snap_totals.get(kind) or {}
        computed = book.get("computed")
        before_v = before.get("value")
        computed_n = _frac(computed) if computed is not None else None
        unresolved = (
            any(unresolved_total_rows[k] for k in ("autumn", "spring", "annual"))
            if kind == "grand" else bool(unresolved_total_rows.get(kind))
        )
        target_n = (
            None if computed_n is None or outside[kind] is None or unresolved
            else computed_n + outside[kind]
        )
        target = _render_num(target_n) if target_n is not None else None
        match = "unknown"
        if target_n is not None and before.get("state") == "value" and before_v is not None:
            match = "ok" if target_n == _frac(before_v) else "mismatch"
        elif target_n is not None and before.get("state") == "blank":
            match = "blank"
        data["totals"].append({
            "kind": kind, "source": book.get("bookCells") or book.get("contributors"),
            "computed": computed,
            "outside": _render_num(outside[kind]) if outside[kind] is not None else None,
            "outsideSources": preserved_sources[kind] if kind != "grand" else (
                preserved_sources["autumn"] + preserved_sources["spring"]
                + preserved_sources["annual"]),
            "before": before.get("raw"),
            "after": target, "match": match,
        })
        if match == "mismatch":
            issues.append(issue(
                "plan-total-mismatch", source=";".join(book.get("bookCells") or []),
                field=kind, expected=target, actual=before_v,
                next="итог кабинета не равен книге с учётом сохраняемых строк — "
                     "ручная сверка до записи"))
            blocked = True
        elif match == "unknown" and target_n is None:
            if kind == "grand":
                actual_unknown = []
                for part in ("autumn", "spring", "annual"):
                    actual_unknown += preserved_unknown[part]
                    actual_unknown += unresolved_total_rows[part]
            else:
                actual_unknown = ((preserved_unknown.get(kind) or [])
                                  + unresolved_total_rows.get(kind, []))
            if actual_unknown:
                issues.append(issue(
                    "plan-total-unverified", field=kind,
                    actual=actual_unknown,
                    next="неизвестные часы сохраняемых/конфликтных строк — итог не сверен"))
                blocked = True

    if any(c.get("match") == "mismatch" for c in wdoc["data"]["controls"]):
        blocked = True  # полная форма не сходится — даже частично не готовить
        issues.append(issue(
            "workload-total-mismatch", next="книга не сошлась — запись даже частями запрещена"))

    data["operations"] = ops
    data["blocked"] = blocked
    return envelope("bsu.diff/1", STATUS_BLOCKED if blocked else STATUS_OK,
                    data, issues)
