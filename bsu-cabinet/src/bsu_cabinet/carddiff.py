"""Предпросмотр карточек занятий: slots/1 + снимок карточки + снимок ИП
+ тематический план → проверяемый diff/1. Один read-only срез.

Входы — документы из локальных файлов:

- ``slots_doc`` — bsu.slots/1 с вычисленными датами (schedule-find
  после развёртки и применённых исключений);
- ``card_snapshot_document(raw)`` → bsu.card-snapshot/1 — существующие
  записи одной таблицы карточки (ключ строки ИП + категория);
- ``snapshot/1`` — структурированный ИП: строка даёт вид и потолок часов;
- ``topics_document(raw)`` → bsu.topics/1 — формализованный тематический
  план (номер/вид/тема/источник).

Пилот — лабораторные (status 1) и практика/семинары (status 4); прочие
виды выводятся в ``skipped``, не применяются. Отметка «Выполнено» — по
явно зафиксированной дате Europe/Minsk (``as_of``): строго раньше —
True, сегодня и будущее — False. Записи здесь нет: модуль не знает
транспорта, результат — только предпросмотр.
"""

from __future__ import annotations

import re
from datetime import date
from fractions import Fraction
from typing import Any

from .plan import _n, _norm_field, _norm_year, _render_num, _sha
from .schema import STATUS_BLOCKED, STATUS_OK, envelope, issue, status_of
from .workload import _frac

# пилот среза: вид слота → (status таблицы карточки, поля лимита ИП)
PILOT = {
    "laboratory": (1, ("Clock_laboratory_a", "Clock_laboratory_b")),
    "practice": (4, ("Clock_practical_a", "Clock_practical_b")),
}

# виды тематического плана — только канонические ключи, без угадывания
_KNOWN_KINDS = {
    "lecture", "laboratory", "practice", "usr", "consult", "check", "exam",
}

_DT_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_SUBGROUP_TAG_RE = re.compile(r"^(\d+)([a-zа-яё])$")

_TRUE = {True, "true", "1", "True"}
_FALSE = {False, "false", "0", "False", "", None}


# ---------------------------------------------------------------------------
# bsu.card-snapshot/1
# ---------------------------------------------------------------------------

def _parse_dt(raw: Any) -> str | None:
    """'dd-mm-yyyy' формы кабинета или ISO → ISO; прочее → None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        pass
    m = _DT_RE.match(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)),
                        int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def _parse_bool(raw: Any) -> bool | None:
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _subgroup_of(group: str, note: str) -> str | None:
    """Подгруппа из примечания вида «1а» при совпадающей группе."""
    m = _SUBGROUP_TAG_RE.match(_n(note).replace(" ", ""))
    if m and m.group(1) == _n(group):
        return m.group(2)
    return None


def _norm_card_item(raw: dict, index: int) -> dict:
    """Одна запись Items: нормализованные поля + исходник без потерь."""
    group = str(raw.get("group") or "").strip()
    note = str(raw.get("note") or "").strip()
    hours_cell = _norm_field(raw.get("hours"))
    norm = {
        "title": str(raw.get("title") or "").strip(),
        "group": group,
        "subgroup": _subgroup_of(group, note),
        "note": note,
        "date": _parse_dt(raw.get("dt")),
        "time": (str(raw.get("time") or "").strip() or None),
        "hours": hours_cell,
        "ready": _parse_bool(raw.get("ready")) if "ready" in raw else None,
        "dl": _parse_bool(raw.get("dl")),
    }
    unknown = [
        f for f, src in (("date", "dt"), ("time", "time"), ("ready", "ready"))
        if norm[f] is None and raw.get(src) is not None or
        (f == "ready" and "ready" not in raw)
    ]
    state = "unknown" if (hours_cell["state"] == "unknown"
                          or unknown) else "ok"
    return {"index": index, "state": state, "raw": raw, "normalized": norm}


def card_snapshot_document(raw: dict) -> dict:
    """bsu.card-snapshot/1 из сырого чтения таблицы карточки.

    ``loaded: false`` — «не прочитано», не «пусто»; отсутствие структуры
    Items — нераспознанная таблица. Оба случая — блокировка, а не пустой
    снимок.
    """
    issues: list[dict] = []
    status_kind = {
        s: k for k, (s, _f) in PILOT.items()
    }.get(raw.get("status"))
    data: dict[str, Any] = {
        "key": raw.get("key"),
        "status": raw.get("status"),
        "kind": status_kind,
        "owner": raw.get("owner"),
        "year": raw.get("year"),
        "state": "unloaded",
        "items": [],
        "scopeHash": None,
    }
    if raw.get("loaded") is not True:
        issues.append(issue(
            "card-table-unloaded", expected="загруженная таблица карточки",
            actual=raw.get("error") or "признак loaded отсутствует/ложен",
            next="снимок карточки не прочитан: повторить чтение, "
                 "не считать таблицу пустой"))
        return envelope("bsu.card-snapshot/1", status_of(issues), data, issues)
    items = raw.get("items")
    if not isinstance(items, list):
        issues.append(issue(
            "card-table-unrecognized",
            expected="распознанная таблица записей карточки (items)",
            actual=type(items).__name__,
            next="структура таблицы не распознана — квалифицировать формат, "
                 "не подставлять пустой снимок"))
        data["state"] = "unrecognized"
        return envelope("bsu.card-snapshot/1", status_of(issues), data, issues)
    data["items"] = [_norm_card_item(r, i) for i, r in enumerate(items)]
    data["state"] = "populated" if items else "empty"
    # хэш — по всей сырой таблице: owner/year/key/status и любые поля,
    # нужные для сохранности старых строк при возможной записи
    data["scopeHash"] = _sha(raw)
    return envelope("bsu.card-snapshot/1", status_of(issues), data, issues)


# ---------------------------------------------------------------------------
# bsu.topics/1
# ---------------------------------------------------------------------------

def topics_document(raw: dict) -> dict:
    """bsu.topics/1 — формализованный тематический план.

    Поддерживаемая форма: ``lessons`` с номером/видом/темой/источником.
    Голый перечень тем (``topics``) не даёт распределения по занятиям —
    блокировка затронутых строк, а не подстановка по порядку.
    """
    issues: list[dict] = []
    data: dict[str, Any] = {
        "discipline": raw.get("discipline"),
        "edition": raw.get("edition"),
        "lessons": [],
    }
    if "lessons" in raw:
        for i, e in enumerate(raw.get("lessons") or []):
            kind = e.get("kind")
            ordinal = e.get("ordinal")
            topic = e.get("topic")
            if kind not in _KNOWN_KINDS:
                issues.append(issue(
                    "kind-unknown", source=f"lessons[{i}]",
                    field="kind", expected=sorted(_KNOWN_KINDS), actual=kind,
                    next="вид занятия не распознан — не присваивать молча"))
                continue
            if not isinstance(ordinal, int) or ordinal < 1:
                issues.append(issue(
                    "topic-ordinal-missing", source=f"lessons[{i}]",
                    field="ordinal", expected="номер занятия ≥ 1", actual=ordinal,
                    next="тема без номера занятия — не распределять по порядку"))
                continue
            if not isinstance(topic, str) or not topic.strip():
                issues.append(issue(
                    "topic-missing", source=f"lessons[{i}]",
                    field="topic", expected="формулировка темы", actual=topic,
                    next="пустая тема — не подставлять"))
                continue
            data["lessons"].append({
                "kind": kind, "ordinal": ordinal, "topic": topic.strip(),
                "sourceRef": e.get("sourceRef"),
            })
        data["lessons"].sort(key=lambda les: (les["kind"], les["ordinal"]))
        if not data["lessons"] and not issues:
            issues.append(issue(
                "topics-empty", expected="хотя бы одна тема вида",
                next="тематический план пуст"))
    elif "topics" in raw:
        issues.append(issue(
            "topics-unordered", source="topics",
            expected="распределение тем по номерам занятий",
            actual=f"перечень из {len(raw.get('topics') or [])} тем",
            next="неизвестный порядок перечня не угадывается — нужен "
                 "тематический план с номерами"))
    else:
        issues.append(issue(
            "topics-unrecognized",
            expected="lessons[] или topics[]", actual=sorted(raw),
            next="формат тематического плана не распознан"))
    return envelope("bsu.topics/1", status_of(issues), data, issues)


# ---------------------------------------------------------------------------
# Сквозные проверки и операции
# ---------------------------------------------------------------------------

def _surname(name: str | None) -> str:
    """Фамилия — первый токен нормализованного ФИО любой формы."""
    return _n(name).split(" ")[0] if _n(name) else ""


def _initials(name: str | None) -> str:
    """Инициалы ФИО любой формы: «А.С.» и «Анна Сергеевна» → «ас»."""
    toks = _n(name).split(" ")
    out = []
    for t in toks[1:]:
        out.append(t.replace(".", "") if "." in t else t[:1])
    return "".join(out)


def _names_agree(a: str | None, b: str | None,
                 name_map: dict | None = None) -> bool:
    """Тот же человек: точное совпадение, однозначное сокращение
    (фамилия + инициалы у обеих форм) или явное name_map."""
    na, nb = _n(a), _n(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if name_map:
        mapped = {_n(k): _n(v) for k, v in name_map.items()}
        if mapped.get(na) == nb or mapped.get(nb) == na:
            return True
    return (_surname(na) == _surname(nb) and bool(_initials(na))
            and _initials(na) == _initials(nb))


def _sem_index(abs_semester: int) -> int:
    """Номер семестра (3, 5) → индекс строки ИП (0 осень, 1 весна)."""
    return (abs_semester - 1) % 2


def _fmt_hours(v: Fraction | None) -> object:
    return _render_num(v) if v is not None else None


def _hours_of(value: Any) -> Fraction | None:
    """Часы из normalized-ячейки или числа; неопределённое → None."""
    if value is None:
        return None
    if isinstance(value, dict):
        if value.get("state") != "value" or value.get("value") is None:
            return None
        value = value["value"]
    try:
        return _frac(value)
    except (ValueError, ZeroDivisionError):
        return None


def _match_key(date_s: str | None, time_s: str | None,
               group: str, subgroup: str | None) -> tuple:
    return (date_s, _n(time_s), _n(group), _n(subgroup))


def _time_of(start: str | None, end: str | None) -> str | None:
    return f"{start}-{end}" if start and end else None


def _blocked_doc(data: dict, issues: list[dict]) -> dict:
    data["blocked"] = True
    return envelope("bsu.diff/1", STATUS_BLOCKED, data, issues)


def card_diff_document(slots_doc: dict, card_doc: dict, plan_doc: dict, *,
                       topics_doc: dict | None = None,
                       facts_doc: dict | None = None,
                       name_map: dict | None = None,
                       as_of: date) -> dict:
    """bsu.diff/1: предпросмотр карточек, без записи.

    Операции по каждому экземпляру: add/edit/noop/conflict; существующие
    записи вне сопоставления — preserve. Любая issue блокирует весь
    документ (issues здесь — только блокирующие).
    """
    issues: list[dict] = []
    sdata = (slots_doc or {}).get("data") or {}
    cdata = (card_doc or {}).get("data") or {}
    pdata = (plan_doc or {}).get("data") or {}
    scope = sdata.get("scope") or {}
    data: dict[str, Any] = {
        "scope": {"teacher": sdata.get("teacher"),
                  "year": scope.get("year"),
                  "course": scope.get("course"),
                  "semester": scope.get("semester"),
                  "cardKey": cdata.get("key"),
                  "cardStatus": cdata.get("status"),
                  "cardKind": cdata.get("kind")},
        "asOfDate": as_of.isoformat(),
        "timezone": "Europe/Minsk",
        "sourceSha": sdata.get("sha"),
        "beforeHash": cdata.get("scopeHash"),
        "operations": [],
        "hours": [],
        "skipped": [],
        "blocked": False,
    }
    if (slots_doc or {}).get("status") != STATUS_OK:
        issues.append(issue(
            "slots-blocked", expected="проверенные слоты",
            actual=(slots_doc or {}).get("status"),
            next="слоты не проверены — карточки не готовить"))
        return _blocked_doc(data, issues)
    if (card_doc or {}).get("status") != STATUS_OK:
        issues.append(issue(
            "card-snapshot-blocked", expected="проверенный снимок карточки",
            actual=(card_doc or {}).get("status"),
            next="снимок карточки недостоверен — предпросмотр остановлен"))
        return _blocked_doc(data, issues)
    if pdata.get("state") == "unloaded" or (plan_doc or {}).get("status") != STATUS_OK:
        issues.append(issue(
            "plan-snapshot-blocked", expected="прочитанный снимок ИП",
            actual=pdata.get("state") or (plan_doc or {}).get("status"),
            next="нет достоверного чтения ИП — лимиты не проверить"))
        return _blocked_doc(data, issues)

    # личность подтверждается во всех трёх источниках, не только фамилией
    persons = {"schedule": sdata.get("teacher"),
               "plan": pdata.get("owner"),
               "card": cdata.get("owner")}
    refs = list(persons.items())
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            sa, va = refs[i]
            sb, vb = refs[j]
            if not _names_agree(va, vb, name_map):
                issues.append(issue(
                    "teacher-mismatch",
                    expected=f"{sb}={vb}", actual=f"{sa}={va}",
                    next="личность в источниках не подтверждена "
                         "(фамилия+инициалы или явное сопоставление) — стоп"))
                return _blocked_doc(data, issues)
    # учебный год — тоже во всех трёх источниках
    years = {"schedule": scope.get("year"),
             "plan": pdata.get("year"),
             "card": cdata.get("year")}
    canon = [_norm_year(y) for y in years.values()]
    if any(y != canon[0] for y in canon):
        issues.append(issue(
            "year-mismatch", expected=canon[0], actual=years,
            next="учебный год источников различается — стоп"))
        return _blocked_doc(data, issues)

    kind = cdata.get("kind")
    if kind not in PILOT:
        issues.append(issue(
            "category-not-pilot", expected=sorted(PILOT),
            actual={"status": cdata.get("status"), "kind": kind},
            next="категория вне пилота лабораторных/практики — не расширять"))
        return _blocked_doc(data, issues)
    card_status, limit_fields = PILOT[kind]

    row = next((r for r in pdata.get("rows") or [] if r.get("key") == cdata.get("key")),
               None)
    if row is None:
        issues.append(issue(
            "card-key-unknown", expected="строка ИП с ключом карточки",
            actual=cdata.get("key"),
            next="карточка от другой строки ИП — не смешивать строки"))
        return _blocked_doc(data, issues)
    ident = row.get("identity") or {}
    if row.get("semester") != _sem_index(scope.get("semester") or 0):
        issues.append(issue(
            "semester-mismatch", expected=f"семестр {scope.get('semester')}",
            actual={"key": row.get("key"), "semester": row.get("semester")},
            next="карточка и расписание от разных семестров — стоп"))
        return _blocked_doc(data, issues)
    if ident.get("course") != scope.get("course"):
        issues.append(issue(
            "course-mismatch", expected=scope.get("course"),
            actual=ident.get("course"),
            next="карточка и расписание от разных курсов — стоп"))
        return _blocked_doc(data, issues)

    # лимит и форма (ауд/ДОТ) из строки ИП
    fields = row.get("normalizedFields") or {}
    cell_a, cell_b = (_hours_of(fields.get(limit_fields[0])),
                      _hours_of(fields.get(limit_fields[1])))
    if (fields.get(limit_fields[0]) or {}).get("state") == "unknown" or \
            (fields.get(limit_fields[1]) or {}).get("state") == "unknown":
        issues.append(issue(
            "limit-unknown", field=".".join(limit_fields),
            next="лимит часов в ИП не прочитан — сверку не гадать"))
        return _blocked_doc(data, issues)
    limit = (cell_a or Fraction(0)) + (cell_b or Fraction(0))
    if limit <= 0:
        issues.append(issue(
            "kind-not-in-plan", field=".".join(limit_fields),
            expected="часы вида в строке ИП", actual=0,
            next="вид не назначен в ИП — карточки по нему не готовить"))
        return _blocked_doc(data, issues)
    if (cell_a or 0) > 0 and (cell_b or 0) > 0:
        issues.append(issue(
            "dl-ambiguous", field=".".join(limit_fields),
            expected="одна форма: аудиторные или ДОТ",
            actual={"audi": str(cell_a), "dot": str(cell_b)},
            next="дистанционность не угадывается — уточнить форму"))
        return _blocked_doc(data, issues)
    dl_default = bool((cell_b or 0) > 0)

    topics_by_ord: dict[int, dict] = {}
    topics_blocked = False
    if topics_doc is not None:
        tdata = topics_doc.get("data") or {}
        # дисциплина тем — из той же строки ИП, иначе темы чужой программы
        if _n(tdata.get("discipline")) != _n(ident.get("discipline")):
            issues.append(issue(
                "topics-discipline-mismatch",
                expected=ident.get("discipline"),
                actual=tdata.get("discipline"),
                next="тематический план другой дисциплины — темы не "
                     "подставлять, подготовка остановлена"))
            return _blocked_doc(data, issues)
        for les in tdata.get("lessons") or []:
            if les["kind"] == kind:
                topics_by_ord[les["ordinal"]] = les
        if topics_doc.get("status") != STATUS_OK:
            topics_blocked = True  # даты считаем, темы не подставляем

    facts_events = ((facts_doc or {}).get("data") or {}).get("events") or []

    items = cdata.get("items") or []
    by_key: dict[tuple, list[dict]] = {}
    for it in items:
        nm = it["normalized"]
        by_key.setdefault(
            _match_key(nm["date"], nm["time"], nm["group"], nm["subgroup"]),
            []).append(it)
    used_items: set[int] = set()
    ops: list[dict] = []
    skipped: list[dict] = []
    add_hours = Fraction(0)

    for s in sdata.get("slots") or []:
        cell = s.get("cell")
        if s.get("kind") is None:
            issues.append(issue(
                "kind-unknown", source=cell, field="kind",
                expected="вид занятия", actual=None,
                next="вид слота не доказан — строки не применять"))
            skipped.append({"cell": cell, "reason": "kind-unknown"})
            continue
        if s["kind"] != kind:
            skipped.append({"cell": cell, "reason": "outside-card-kind"})
            continue
        if _n(s.get("discipline")) != _n(ident.get("discipline")):
            skipped.append({"cell": cell, "reason": "other-plan-row"})
            continue
        if not _names_agree(s.get("assignment"), sdata.get("teacher"),
                            name_map):
            issues.append(issue(
                "slot-assignment-mismatch", source=cell, field="assignment",
                expected=sdata.get("teacher"), actual=s.get("assignment"),
                next="фактическое назначение слота не подтверждено — "
                     "не применять"))
            skipped.append({"cell": cell,
                            "reason": "slot-assignment-mismatch"})
            continue
        groups = s.get("groups") or []
        if len(groups) != 1:
            issues.append(issue(
                "groups-ambiguous", source=cell,
                expected="одна группа/подгруппа назначения",
                actual=groups,
                next="несколько групп в слоте — не размножать молча"))
            skipped.append({"cell": cell, "reason": "groups-ambiguous"})
            continue
        g = groups[0]
        group, subgroup = g.get("group"), g.get("subgroup")
        subtag = f"{group}{_n(subgroup)}" if subgroup else ""
        facts = [e for e in facts_events
                 if (e.get("assignmentRef") or e.get("originalSlot")) == cell]
        for inst in s.get("instances") or []:
            exc = inst.get("exception") or {}
            moved = exc.get("action") == "move"
            original = exc.get("originalDate") or inst["date"]
            fact = next((e for e in facts
                         if e.get("date") in (inst["date"], original)), None)
            unresolved: list[str] = []
            hours = _hours_of((fact or {}).get("hours"))
            if hours is None:
                hours = _hours_of(inst.get("hours")) or _hours_of(s.get("hours"))
            if hours is None:
                unresolved.append("hours-unknown")
            time_s = _time_of(inst.get("localStart"), inst.get("localEnd"))
            if time_s is None:
                unresolved.append("time-unknown")
            les = topics_by_ord.get(inst.get("ordinal"))
            topic = (fact or {}).get("topic") or (les or {}).get("topic")
            if inst.get("ordinal") is None:
                unresolved.append("ordinal-unknown")
            elif topic is None and not topics_blocked:
                unresolved.append("topic-unknown")
            if topics_blocked and topic is None:
                unresolved.append("topic-unordered")
            delivery = (fact or {}).get("delivery")
            dl = {"distance": True, "in-person": False}.get(delivery, dl_default)
            expected = {
                "title": topic, "group": str(group), "groupCount": "1",
                "dt": inst["date"], "time": time_s,
                "hours": _fmt_hours(hours), "ready": inst["date"] < as_of.isoformat(),
                "dl": dl, "note": subtag,
            }
            base = {
                "key": cdata.get("key"), "status": card_status,
                "identity": {
                    "discipline": s.get("discipline"), "kind": kind,
                    "group": group, "subgroup": subgroup,
                    "instance": {"slot": cell, "ordinal": inst.get("ordinal"),
                                 "originalDate": original}},
                "expectedItem": expected,
                "sources": {
                    "slot": cell, "instance": inst.get("source"),
                    "topic": (fact or {}).get("evidenceRef") or (les or {}).get("sourceRef"),
                    "plan": f"ИП key={cdata.get('key')} {limit_fields[0]}/{limit_fields[1]}",
                    **({"exception": exc.get("source")} if exc else {}),
                    **({"fact": fact["evidenceRef"]} if fact and fact.get("evidenceRef") else {}),
                },
            }
            if unresolved:
                ops.append({**base, "op": "conflict", "reason": unresolved[0],
                            "before": {}, "after": expected})
                issues.append(issue(
                    unresolved[0], source=cell,
                    field=unresolved[0].split("-")[0],
                    actual={"date": inst["date"], "ordinal": inst.get("ordinal")},
                    next="неопределённое поле — показать, не подставлять"))
                continue
            cur_key = _match_key(inst["date"], time_s, str(group), _n(subgroup))
            candidates = list(by_key.get(cur_key, []))
            orig_key = None
            if moved:
                ot = inst.get("originalTime") or {}
                orig_time = _time_of(ot.get("localStart"), ot.get("localEnd"))
                orig_key = _match_key(original, orig_time, str(group), _n(subgroup))
                for c in by_key.get(orig_key, []):
                    if id(c) not in {id(x) for x in candidates}:
                        candidates.append(c)
            if len(candidates) > 1:
                used_items.update(id(c) for c in candidates)
                ops.append({**base, "op": "conflict",
                            "reason": "indistinguishable-cards",
                            "before": [c["index"] for c in candidates],
                            "after": expected})
                issues.append(issue(
                    "rows-conflict", source=cell, field="items",
                    actual=[c["index"] for c in candidates],
                    next="несколько записей не различить — не перезаписывать"))
                continue
            if not candidates:
                ops.append({**base, "op": "add", "before": {}, "after": expected})
                if hours is not None:
                    add_hours += hours
                continue
            cur = candidates[0]
            used_items.add(id(cur))
            nm = cur["normalized"]
            diffs: dict[str, dict] = {}
            if _n(nm["title"]) != _n(expected["title"] or ""):
                diffs["title"] = {"before": nm["title"], "after": expected["title"]}
            if nm["date"] != expected["dt"]:
                diffs["dt"] = {"before": nm["date"], "after": expected["dt"]}
            if _n(nm["time"]) != _n(expected["time"]):
                diffs["time"] = {"before": nm["time"], "after": expected["time"]}
            cur_hours = _hours_of(nm["hours"])
            if cur_hours != hours:
                diffs["hours"] = {"before": _fmt_hours(cur_hours),
                                  "after": expected["hours"]}
            if nm["ready"] != expected["ready"]:
                diffs["ready"] = {"before": nm["ready"], "after": expected["ready"]}
            if nm["dl"] != expected["dl"]:
                diffs["dl"] = {"before": nm["dl"], "after": expected["dl"]}
            if _n(nm["note"]) != _n(expected["note"]):
                diffs["note"] = {"before": nm["note"], "after": expected["note"]}
            if _n(nm["group"]) != _n(expected["group"]):
                diffs["group"] = {"before": nm["group"], "after": expected["group"]}
            allowed = {"dt", "time", "ready"} if moved else {"ready"}
            if not diffs:
                ops.append({**base, "op": "noop", "key": cdata.get("key"),
                            "before": cur["raw"], "after": expected,
                            "changed": []})
            elif set(diffs) <= allowed:
                ops.append({**base, "op": "edit",
                            "before": {f: d["before"] for f, d in diffs.items()},
                            "after": {f: d["after"] for f, d in diffs.items()},
                            "changed": sorted(diffs),
                            **({"exception": exc} if moved else {})})
            else:
                ops.append({**base, "op": "conflict", "reason": "fields-differ",
                            "before": {f: d["before"] for f, d in diffs.items()},
                            "after": {f: d["after"] for f, d in diffs.items()},
                            "changed": sorted(diffs)})
                issues.append(issue(
                    "fields-differ", source=cell, field=",".join(sorted(diffs)),
                    next="та же дата/слот, другие поля — конфликт, не дубль"))
            if moved:
                ops[-1]["exception"] = exc
                ops[-1]["matchedOriginal"] = orig_key is not None and any(
                    id(c) == id(cur) for c in by_key.get(orig_key, []))

    # существующие записи вне сопоставления — сохранить, аномалии — показать
    for it in items:
        if id(it) in used_items:
            continue
        ops.append({"op": "preserve", "key": cdata.get("key"),
                    "status": card_status, "itemIndex": it["index"],
                    "identity": {}, "expectedItem": {},
                    "before": it["raw"], "after": it["raw"], "sources": {}})
        nm = it["normalized"]
        if it["state"] == "unknown":
            issues.append(issue(
                "field-unknown", source=f"items[{it['index']}]",
                expected="распознанные дата/время/часы/отметка",
                actual=it["raw"],
                next="поле существующей записи не прочитано — не гадать"))
        elif nm["ready"] and nm["date"] and nm["date"] >= as_of.isoformat():
            issues.append(issue(
                "ready-future-anomaly", source=f"items[{it['index']}]",
                field="ready", expected=False, actual=True,
                next="будущая карточка отмечена выполненной — аномалия, "
                     "не исправлять молча"))

    # лимит часов: прежние записи + новые, вместе против ИП
    item_hours = [_hours_of(it["normalized"]["hours"]) for it in items]
    before_hours = sum((h for h in item_hours if h is not None), Fraction(0))
    if any(h is None for h in item_hours) and items:
        issues.append(issue(
            "hours-unknown", source=cdata.get("key"), field="hours",
            next="часы существующей записи не прочитаны — лимит не проверить"))
    after_hours = before_hours + add_hours
    match = "unknown"
    if not any(i["code"] == "hours-unknown" for i in issues):
        match = "exceeded" if after_hours > limit else "ok"
    data["hours"].append({
        "key": cdata.get("key"), "kind": kind,
        "limit": _render_num(limit),
        "before": _render_num(before_hours),
        "after": _render_num(after_hours),
        "match": match,
    })
    if match == "exceeded":
        issues.append(issue(
            "hours-limit-exceeded", field=".".join(limit_fields),
            expected=str(_render_num(limit)), actual=str(_render_num(after_hours)),
            next="прежние + новые часы превышают лимит ИП — запись запрещена"))

    data["operations"] = sorted(
        ops, key=lambda o: (str(o.get("key")), o.get("status") or 0,
                            (o.get("expectedItem") or {}).get("dt") or "",
                            (o.get("expectedItem") or {}).get("group") or "",
                            str(o.get("itemIndex", ""))))
    data["skipped"] = skipped
    counts: dict[str, int] = {}
    for o in ops:
        counts[o["op"]] = counts.get(o["op"], 0) + 1
    data["summary"] = counts
    data["blocked"] = bool(issues)
    return envelope("bsu.diff/1", STATUS_BLOCKED if issues else STATUS_OK,
                    data, issues)
