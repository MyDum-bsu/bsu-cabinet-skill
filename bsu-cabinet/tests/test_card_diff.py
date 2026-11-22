"""Предпросмотр карточек: slots/1 + снимок карточки + снимок ИП + темы → diff/1.

Один read-only вертикальный срез (Board #143): пилот — лабораторные и
практика. Снимки и документы — локальная синтетика; записи в кабинет
в этом срезе нет и не может быть: модуль не знает транспорта.
"""

from __future__ import annotations

import json
from datetime import date

from bsu_cabinet.carddiff import (
    card_diff_document,
    card_snapshot_document,
    topics_document,
)
from bsu_cabinet.plan import snapshot_document
from fixtures import cards as fx

AS_OF = date(2026, 9, 23)  # зафиксированная дата Europe/Minsk для прогона


def _codes(doc: dict) -> list[str]:
    return [i["code"] for i in doc["issues"]]


def _ops(doc: dict) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for op in doc["data"]["operations"]:
        by.setdefault(op["op"], []).append(op)
    return by


def _lab_row(limit: str = "18", key: str = "101", **kw) -> dict:
    fields = {"Clock_laboratory_a": limit}
    fields.update(kw.pop("fields", {}))
    return fx.plan_row(key, fields=fields, **kw)


def _plan(rows: list, **kw) -> dict:
    doc = snapshot_document(fx.plan_raw(rows, **kw))
    assert doc["status"] == "ok", doc["issues"]
    return doc


def _snap(items: list, **kw) -> dict:
    return card_snapshot_document(fx.card_raw(items=items, **kw))


# ---------------------------------------------------------------------------
# Снимок таблицы карточки
# ---------------------------------------------------------------------------

def test_card_snapshot_normalizes_items():
    doc = _snap([fx.card_item("02-09-2026", title="Тема 1", ready=True,
                              group="1", note="1а")])
    assert doc["schema"] == "bsu.card-snapshot/1"
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["state"] == "populated"
    item = doc["data"]["items"][0]
    assert item["normalized"]["date"] == "2026-09-02"
    assert item["normalized"]["ready"] is True
    assert item["normalized"]["group"] == "1"
    assert item["normalized"]["subgroup"] == "а"
    assert item["raw"]["dt"] == "02-09-2026"


def test_card_snapshot_empty_table_is_empty_not_unloaded():
    doc = _snap([])
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["state"] == "empty"
    assert doc["data"]["items"] == []


def test_card_snapshot_unloaded_is_blocked_not_empty():
    doc = _snap([], loaded=False)
    assert doc["status"] == "blocked"
    assert "card-table-unloaded" in _codes(doc)
    assert doc["data"]["state"] == "unloaded"
    assert doc["data"]["items"] == []


def test_card_snapshot_unrecognized_table_is_blocked_not_empty():
    """Таблица не прочитана (items не получены) — блокировка, не «пусто»."""
    doc = card_snapshot_document(fx.card_raw(items=None))
    assert doc["status"] == "blocked"
    assert "card-table-unrecognized" in _codes(doc)
    assert doc["data"]["items"] == []


# ---------------------------------------------------------------------------
# Базовый срез: новые карточки на весь период
# ---------------------------------------------------------------------------

def _lab_slot(instances: list, **kw) -> dict:
    return fx.slot("'Расписание'!C21", kind="laboratory", instances=instances, **kw)


def test_diff_adds_cards_for_whole_period_with_ready_rule():
    """Вчера → выполнено, сегодня и будущее — нет; будущие тоже предлагаются."""
    slot = _lab_slot([
        fx.instance("2026-09-22", ordinal=1),   # вчера
        fx.instance("2026-09-23", ordinal=2),   # сегодня
        fx.instance("2026-09-24", ordinal=3),   # завтра
        fx.instance("2026-10-01", ordinal=4),   # будущее
    ])
    slots = fx.slots_doc([slot])
    topics = topics_document(fx.topics_raw([
        fx.lesson("laboratory", n, f"Тема {n}") for n in range(1, 5)]))
    doc = card_diff_document(
        slots, _snap([]), _plan([_lab_row()]), topics_doc=topics, as_of=AS_OF)
    assert doc["schema"] == "bsu.diff/1"
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["asOfDate"] == "2026-09-23"
    assert doc["data"]["timezone"] == "Europe/Minsk"
    ops = _ops(doc)["add"]
    assert [o["after"]["dt"] for o in ops] == [
        "2026-09-22", "2026-09-23", "2026-09-24", "2026-10-01"]
    assert [o["after"]["ready"] for o in ops] == [True, False, False, False]
    assert [o["after"]["title"] for o in ops] == [
        "Тема 1", "Тема 2", "Тема 3", "Тема 4"]
    first = ops[0]
    assert first["key"] == "101" and first["status"] == 1
    assert first["after"]["group"] == "1"
    assert first["after"]["note"] == "1а"  # подгруппа — в примечание
    assert first["after"]["time"] == "08:00-09:35"
    assert first["after"]["hours"] == 2
    assert first["after"]["dl"] is False  # аудиторные часы → очные
    assert first["sources"]["slot"] == "'Расписание'!C21"
    assert first["sources"]["topic"]


def test_diff_deterministic_repeat_no_second_run_writes():
    """Повторный прогон на том же снимке — те же операции; совпавшая
    карточка — noop, а не повторная запись."""
    items = [fx.card_item("22-09-2026", ready=True, title="Тема 1",
                          note="1а")]
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    slots = fx.slots_doc([slot])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    plan = _plan([_lab_row()])
    snap = _snap(items)
    d1 = card_diff_document(slots, snap, plan, topics_doc=topics, as_of=AS_OF)
    d2 = card_diff_document(slots, snap, plan, topics_doc=topics, as_of=AS_OF)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    ops = _ops(d1)
    assert len(ops["noop"]) == 1
    assert not ops.get("add")  # совпавшая карточка — без повторной операции


# ---------------------------------------------------------------------------
# Скоуп: преподаватель, год, семестр, строка ИП, категория
# ---------------------------------------------------------------------------

def test_teacher_mismatch_blocks_without_ops():
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    doc = card_diff_document(
        fx.slots_doc([slot], teacher="Петрова И.И."),
        _snap([]), _plan([_lab_row()]), as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "teacher-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_same_surname_other_initials_is_mismatch():
    """«Иванова Б.П.» ≠ «Иванова Анна Сергеевна»: одна фамилия — не человек."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    doc = card_diff_document(
        fx.slots_doc([slot], teacher="Иванова Б.П."),
        _snap([]), _plan([_lab_row()]), as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "teacher-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_agreed_abbreviation_and_explicit_map_agree():
    """«Иванова А.С.» ≡ «Иванова Анна Сергеевна» — согласованное сокращение;
    прочая пара — только по явному подтверждённому сопоставлению."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    slots = fx.slots_doc([slot])
    topics = topics_document(fx.topics_raw(
        [fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap([]), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "ok", doc["issues"]

    # явное подтверждённое сопоставление тоже допустимо
    mapped = card_diff_document(
        fx.slots_doc([slot], teacher="Иванова Б.П."), _snap([]),
        _plan([_lab_row()]), topics_doc=topics,
        name_map={"Иванова Б.П.": "Иванова Анна Сергеевна"}, as_of=AS_OF)
    assert "teacher-mismatch" not in _codes(mapped)

    # голая фамилия без инициалов — не доказательство
    bare = card_diff_document(
        fx.slots_doc([slot], teacher="Иванова"), _snap([]),
        _plan([_lab_row()]), topics_doc=topics, as_of=AS_OF)
    assert "teacher-mismatch" in _codes(bare)


def test_card_owner_and_year_checked_too():
    """owner/year снимка карточки сверяются со слотами и ИП, а не молчат."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    slots = fx.slots_doc([slot])
    doc = card_diff_document(slots, _snap([], owner="Петрова И.И."),
                             _plan([_lab_row()]), as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "teacher-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []
    doc2 = card_diff_document(slots, _snap([], year="2027-2028"),
                              _plan([_lab_row()]), as_of=AS_OF)
    assert "year-mismatch" in _codes(doc2)
    assert doc2["data"]["operations"] == []


def test_slot_assignment_must_be_confirmed_teacher():
    """Фактическое назначение слота — другой человек: блок, не add."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)],
                     assignment="Иванова Б.П.")
    doc = card_diff_document(fx.slots_doc([slot]), _snap([]),
                             _plan([_lab_row()]), as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "slot-assignment-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_topics_discipline_must_match_plan_row():
    """Темплан другой дисциплины — blocked, темы по чужой программе не идут."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    topics = topics_document(fx.topics_raw(
        [fx.lesson("laboratory", 1, "Тема 1")], discipline="Алгебра"))
    doc = card_diff_document(fx.slots_doc([slot]), _snap([]),
                             _plan([_lab_row()]), topics_doc=topics,
                             as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "topics-discipline-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_scope_hash_covers_owner_year_and_whole_raw_table():
    """scopeHash реагирует на owner/year/key/status и любое сырое поле."""
    base = fx.card_raw(items=[fx.card_item("22-09-2026", title="Тема 1")])
    h = card_snapshot_document(base)["data"]["scopeHash"]
    assert h
    for mut in (
        lambda r: r.update(owner="Петрова И.И."),
        lambda r: r.update(year="2027-2028"),
        lambda r: r.update(key="102"),
        lambda r: r.update(status=2),
        lambda r: r.update(extraField={"keep": "me"}),
        lambda r: r["items"][0].update(groupCount="2"),
    ):
        raw = json.loads(json.dumps(base))
        mut(raw)
        assert card_snapshot_document(raw)["data"]["scopeHash"] != h


def test_snapshot_requires_explicit_loaded_marker():
    """Нет явного признака полного чтения — blocked, не «пусто»/«ok»."""
    raw = fx.card_raw(items=[fx.card_item("22-09-2026")])
    del raw["loaded"]
    doc = card_snapshot_document(raw)
    assert doc["status"] == "blocked"
    assert "card-table-unloaded" in _codes(doc)
    assert doc["data"]["state"] == "unloaded"
    # и применимого диффа из такого снимка нет
    slots = fx.slots_doc([_lab_slot([fx.instance("2026-09-22", ordinal=1)])])
    d = card_diff_document(slots, doc, _plan([_lab_row()]), as_of=AS_OF)
    assert d["status"] == "blocked"
    assert d["data"]["operations"] == []


def test_year_and_semester_not_mixed():
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)])
    slots = fx.slots_doc([slot])
    doc = card_diff_document(slots, _snap([]),
                             _plan([_lab_row()], year="2027-2028"), as_of=AS_OF)
    assert "year-mismatch" in _codes(doc)
    # строка ИП весеннего семестра против осеннего расписания
    doc2 = card_diff_document(
        slots, _snap([]), _plan([_lab_row(semester=1)]), as_of=AS_OF)
    assert "semester-mismatch" in _codes(doc2)
    # ключ карточки, которого нет в ИП
    doc3 = card_diff_document(
        slots, _snap([], key="999"), _plan([_lab_row()]), as_of=AS_OF)
    assert "card-key-unknown" in _codes(doc3)


def test_category_outside_pilot_blocked_not_extended():
    doc = card_diff_document(
        fx.slots_doc([fx.slot("'Р'!C21", kind="lecture",
                              instances=[fx.instance("2026-09-22", ordinal=1)])]),
        _snap([], status=0),  # таблица лекций
        _plan([fx.plan_row("101", fields={"Clock_lecture_a": "36"})]),
        as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "category-not-pilot" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_slots_of_other_kinds_and_rows_skipped_not_applied():
    """Лекция и слот чужой строки ИП — в skipped, операций по ним нет."""
    slots = fx.slots_doc([
        fx.slot("'Р'!C21", kind="lecture", discipline="Дискретная математика",
                instances=[fx.instance("2026-09-22", ordinal=1)]),
        fx.slot("'Р'!F21", kind="laboratory", discipline="Алгебра",
                instances=[fx.instance("2026-09-22", ordinal=1)]),
        _lab_slot([fx.instance("2026-09-22", ordinal=1)]),
    ])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap([]), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "ok", doc["issues"]
    assert {s["reason"] for s in doc["data"]["skipped"]} == {
        "outside-card-kind", "other-plan-row"}
    adds = _ops(doc)["add"]
    assert len(adds) == 1
    assert adds[0]["identity"]["discipline"] == "Дискретная математика"


def test_unknown_kind_flagged_not_applied(tmp_path):
    slots = fx.slots_doc([fx.slot("'Р'!C21", kind=None,
                                  instances=[fx.instance("2026-09-22")])])
    doc = card_diff_document(slots, _snap([]), _plan([_lab_row()]), as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "kind-unknown" in _codes(doc)
    assert doc["data"]["operations"] == []


# ---------------------------------------------------------------------------
# Группы и подгруппы не смешиваются
# ---------------------------------------------------------------------------

def test_groups_and_subgroups_are_independent():
    """1А и 1Б — разные записи; существующая карточка 1А не закрывает 1Б."""
    items = [fx.card_item("22-09-2026", ready=True, title="Тема 1",
                          group="1", note="1а")]
    slots = fx.slots_doc([
        fx.slot("'Р'!C21", group="1", subgroup="А",
                instances=[fx.instance("2026-09-22", ordinal=1)]),
        fx.slot("'Р'!D21", group="1", subgroup="Б", weekday="tuesday",
                instances=[fx.instance("2026-09-22", ordinal=1)]),
    ])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "ok", doc["issues"]
    ops = _ops(doc)
    assert len(ops["noop"]) == 1   # 1А совпала
    adds = ops["add"]
    assert len(adds) == 1          # 1Б — новая запись, не noop чужой подгруппы
    assert adds[0]["after"]["group"] == "1"
    assert adds[0]["after"]["note"] == "1б"
    assert adds[0]["identity"]["subgroup"] == "Б"


# ---------------------------------------------------------------------------
# Лимит часов: прежние карточки + новые вместе против ИП
# ---------------------------------------------------------------------------

def test_hours_limit_counts_existing_and_new_together():
    """ИП даёт 6 ч лабораторных; прежние 4 ч + новые 4 ч → превышение."""
    items = [
        fx.card_item("15-09-2026", ready=True, title="Тема 1", note="1а"),
        fx.card_item("22-09-2026", ready=True, title="Тема 2", note="1а"),
    ]
    slots = fx.slots_doc([_lab_slot([
        fx.instance("2026-09-15", ordinal=1),
        fx.instance("2026-09-22", ordinal=2),
        fx.instance("2026-09-29", ordinal=3),
        fx.instance("2026-10-06", ordinal=4),
    ])])
    topics = topics_document(fx.topics_raw(
        [fx.lesson("laboratory", n, f"Тема {n}") for n in range(1, 5)]))
    doc = card_diff_document(
        slots, _snap(items), _plan([_lab_row(limit="6")]),
        topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "hours-limit-exceeded" in _codes(doc)
    h = doc["data"]["hours"][0]
    assert h["limit"] == 6 and h["before"] == 4 and h["after"] == 8
    assert h["match"] == "exceeded"


def test_hours_within_limit_ok():
    slots = fx.slots_doc([_lab_slot([
        fx.instance("2026-09-22", ordinal=1),
        fx.instance("2026-09-29", ordinal=2),
    ])])
    topics = topics_document(fx.topics_raw(
        [fx.lesson("laboratory", n, f"Тема {n}") for n in range(1, 3)]))
    doc = card_diff_document(
        slots, _snap([]), _plan([_lab_row(limit="4")]),
        topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["hours"][0]["match"] == "ok"


# ---------------------------------------------------------------------------
# Перенос и отмена: через живую цепочку schedule → card-diff
# ---------------------------------------------------------------------------

def test_move_edits_instance_not_duplicate_and_not_twice(tmp_path):
    """Известный перенос меняет экземпляр: существующая карточка на старую
    дату правится, а не дублируется; повторный прогон — noop."""
    from bsu_cabinet.schedule import index_document, schedule_find_document
    from fixtures.schedule import (
        FOLDER_URL,
        book_entry,
        build_book_a,
        drive_folder_html,
    )

    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Дискретная математика (лаб.)\nИванова А.С. еженедельно",
    }, col_overrides={9: (1, "А")})  # гр.1А во вторнике вне полос чётности
    idx = index_document(
        drive_folder_html([book_entry("ID-A", "2 курс, 3 семестр, 2026-2027.xlsx")]).encode(),
        source=FOLDER_URL, observed_at="t")
    exc = {"exceptions": [
        {"date": "2026-09-08", "action": "move", "newDate": "2026-09-10",
         "source": "объявление кафедры №5"},
        {"date": "2026-09-22", "action": "cancel", "source": "приказ"},
    ]}
    slots = schedule_find_document(
        idx, year="2026-2027", semester=3, course=2,
        teacher=fx.TEACHER, book_bytes=book.read_bytes(),
        period_from="2026-09-01", period_to="2026-09-30", exceptions=exc)
    assert slots["status"] == "ok", slots["issues"]
    slot_cell = slots["data"]["slots"][0]["cell"]

    def _facts(dates: list[str]) -> dict:
        return {"schema": "bsu.facts/1", "status": "ok",
                "data": {"events": [
                    {"assignmentRef": slot_cell, "date": d, "hours": 2,
                     "evidenceRef": f"лист учёта {d}"} for d in dates]}}

    topics = topics_document(fx.topics_raw(
        [fx.lesson("laboratory", n, f"Тема {n}") for n in range(1, 6)]))
    plan = _plan([_lab_row()])
    # на старую дату перенесённой пары карточка уже существует
    items = [fx.card_item("08-09-2026", ready=True, title="Тема 2", note="1а")]
    snap = _snap(items)

    doc = card_diff_document(slots, snap, plan, topics_doc=topics,
                             facts_doc=_facts(
                                 ["2026-09-01", "2026-09-08", "2026-09-15",
                                  "2026-09-29"]), as_of=AS_OF)
    assert doc["status"] == "ok", doc["issues"]
    ops = _ops(doc)
    edit = ops["edit"]
    assert len(edit) == 1
    assert edit[0]["changed"] == ["dt"]
    assert edit[0]["before"] == {"dt": "2026-09-08"}
    assert edit[0]["after"] == {"dt": "2026-09-10"}
    assert edit[0]["exception"]["action"] == "move"
    assert edit[0]["matchedOriginal"] is True
    adds = {o["after"]["dt"] for o in ops["add"]}
    assert adds == {"2026-09-01", "2026-09-15", "2026-09-29"}
    assert "2026-09-10" not in adds       # перенос — правка, не дубль
    assert "2026-09-22" not in adds       # отменённого занятия нет
    assert not ops.get("preserve")        # старая запись подобрана переносом
    by_ord = {o["after"]["dt"]: o["identity"]["instance"]["ordinal"]
              for o in ops["add"]}
    assert by_ord["2026-09-29"] == 5      # нумерация тем не сдвинулась
    assert next(o for o in ops["add"] if o["after"]["dt"] == "2026-09-29")[
        "after"]["title"] == "Тема 5"

    # второй прогон: карточка уже на новой дате — noop, перенос не применяется дважды
    items2 = [fx.card_item("10-09-2026", ready=True, title="Тема 2", note="1а")]
    doc2 = card_diff_document(slots, _snap(items2), plan, topics_doc=topics,
                              facts_doc=_facts(
                                  ["2026-09-01", "2026-09-10", "2026-09-15",
                                   "2026-09-29"]), as_of=AS_OF)
    ops2 = _ops(doc2)
    assert len(ops2["noop"]) == 1
    assert not ops2.get("edit") and not any(
        o["after"]["dt"] == "2026-09-10" for o in ops2["add"])


def test_moved_to_future_not_ready(tmp_path):
    """Перенос на будущую дату: одна карточка, ready=false."""
    items = [fx.card_item("08-09-2026", ready=True, title="Тема 2", note="1а")]
    slots = fx.slots_doc([_lab_slot([
        fx.instance("2026-09-01", ordinal=1),
        fx.instance("2026-09-25", ordinal=2, exception={
            "action": "move", "originalDate": "2026-09-08",
            "source": "объявление"}),
    ])])
    topics = topics_document(fx.topics_raw(
        [fx.lesson("laboratory", n, f"Тема {n}") for n in range(1, 3)]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    ops = _ops(doc)
    edit = ops["edit"]
    assert len(edit) == 1 and edit[0]["exception"]["action"] == "move"
    assert edit[0]["after"]["ready"] is False   # 25.09 > as_of
    assert "2026-09-25" not in {o["after"]["dt"] for o in ops.get("add", [])}


# ---------------------------------------------------------------------------
# Совпадение и конфликты существующих записей
# ---------------------------------------------------------------------------

def test_ready_only_difference_is_edit_not_rewrite():
    """Уже заполненная прошлая пара меняет только отметку."""
    items = [fx.card_item("22-09-2026", ready=False, title="Тема 1", note="1а")]
    slots = fx.slots_doc([_lab_slot([fx.instance("2026-09-22", ordinal=1)])])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    ops = _ops(doc)
    assert len(ops["edit"]) == 1
    assert ops["edit"][0]["changed"] == ["ready"]
    assert ops["edit"][0]["after"] == {"ready": True}


def test_other_fields_differ_is_conflict_not_duplicate():
    """Та же дата/слот, но другая тема — конфликт, а не новая запись."""
    items = [fx.card_item("22-09-2026", ready=True, title="Другая тема", note="1а")]
    slots = fx.slots_doc([_lab_slot([fx.instance("2026-09-22", ordinal=1)])])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "fields-differ" in _codes(doc)
    ops = _ops(doc)
    assert len(ops["conflict"]) == 1
    assert not ops.get("add")


def test_indistinguishable_existing_cards_conflict():
    """Две одинаковых записи в таблице — не перезаписывать молча."""
    items = [fx.card_item("22-09-2026", ready=True, title="Тема 1", note="1а"),
             fx.card_item("22-09-2026", ready=True, title="Тема 1", note="1а")]
    slots = fx.slots_doc([_lab_slot([fx.instance("2026-09-22", ordinal=1)])])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert "rows-conflict" in _codes(doc)
    assert _ops(doc)["conflict"][0]["reason"] == "indistinguishable-cards"


def test_unmatched_existing_rows_preserved_and_anomalies_shown():
    """Чужие записи — preserve; будущая с отметкой — аномалия, не правка."""
    items = [
        fx.card_item("15-09-2026", ready=True, title="Чужая", group="9"),
        fx.card_item("30-09-2026", ready=True, title="Будущая отмечена",
                     group="9"),
    ]
    slots = fx.slots_doc([_lab_slot([fx.instance("2026-09-22", ordinal=1)])])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "ready-future-anomaly" in _codes(doc)
    preserves = _ops(doc)["preserve"]
    assert len(preserves) == 2  # обе чужие записи сохранены, не изменены


def test_unparsed_existing_field_is_diagnosed_not_guessed():
    items = [fx.card_item("скоро", hours="много", ready=True,
                          title="Странная", group="9")]
    slots = fx.slots_doc([_lab_slot([fx.instance("2026-09-22", ordinal=1)])])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap(items), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "field-unknown" in _codes(doc)
    assert "hours-unknown" in _codes(doc) or \
        doc["data"]["hours"][0]["match"] == "unknown"


# ---------------------------------------------------------------------------
# Темы: неупорядоченный перечень — блокировка строк, не дат
# ---------------------------------------------------------------------------

def test_unordered_topics_block_rows_dates_still_computed():
    slots = fx.slots_doc([_lab_slot([
        fx.instance("2026-09-22", ordinal=1),
        fx.instance("2026-09-29", ordinal=2),
    ])])
    topics = topics_document(fx.topics_unordered(["Тема А", "Тема Б"]))
    assert "topics-unordered" in _codes(topics)
    doc = card_diff_document(slots, _snap([]), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "topic-unordered" in _codes(doc)
    conflicts = _ops(doc)["conflict"]
    assert len(conflicts) == 2  # обе строки заблокированы
    assert [c["after"]["dt"] for c in conflicts] == ["2026-09-22", "2026-09-29"]
    assert all(c["after"]["title"] is None for c in conflicts)


def test_missing_lesson_blocks_only_affected_row():
    slots = fx.slots_doc([_lab_slot([
        fx.instance("2026-09-22", ordinal=1),
        fx.instance("2026-09-29", ordinal=7),   # темы №7 нет
    ])])
    topics = topics_document(fx.topics_raw([fx.lesson("laboratory", 1, "Тема 1")]))
    doc = card_diff_document(slots, _snap([]), _plan([_lab_row()]),
                             topics_doc=topics, as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "topic-unknown" in _codes(doc)
    ops = _ops(doc)
    assert len(ops["add"]) == 1 and ops["add"][0]["after"]["dt"] == "2026-09-22"
    assert len(ops["conflict"]) == 1


def test_facts_supply_hours_delivery_and_topic():
    """Факты уточняют событие: часы/ДО/тема из факта с источником."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1, hours=None)],
                     hours=None)
    slot["instances"][0].pop("hours")
    slot.pop("hours")
    facts = {"schema": "bsu.facts/1", "status": "ok", "data": {"events": [
        {"assignmentRef": "'Расписание'!C21", "date": "2026-09-22",
         "hours": 3, "topic": "Фактическая тема", "delivery": "distance",
         "evidenceRef": "журнал группы 1"}]}}
    doc = card_diff_document(fx.slots_doc([slot]), _snap([]),
                             _plan([_lab_row()]), facts_doc=facts, as_of=AS_OF)
    assert doc["status"] == "ok", doc["issues"]
    op = _ops(doc)["add"][0]
    assert op["after"]["hours"] == 3
    assert op["after"]["title"] == "Фактическая тема"
    assert op["after"]["dl"] is True
    assert op["sources"]["fact"] == "журнал группы 1"


def test_no_hours_source_blocks_row_not_guessed():
    """Ни в слоте, ни в фактах часов нет — блокировка, не минуты по часам."""
    slot = _lab_slot([fx.instance("2026-09-22", ordinal=1)], hours=None)
    slot["instances"][0].pop("hours")
    slot.pop("hours")
    doc = card_diff_document(fx.slots_doc([slot]), _snap([]),
                             _plan([_lab_row()]), as_of=AS_OF)
    assert doc["status"] == "blocked"
    assert "hours-unknown" in _codes(doc)
    assert _ops(doc)["conflict"][0]["reason"] == "hours-unknown"


# ---------------------------------------------------------------------------
# CLI: команды на локальных файлах
# ---------------------------------------------------------------------------

def test_cli_card_diff_end_to_end(tmp_path, monkeypatch, capsys):
    """mo-cabinet snapshot-card|topics|card-diff на синтетических файлах."""
    import sys

    from bsu_cabinet import cli

    (tmp_path / "slots.json").write_text(
        json.dumps(fx.slots_doc([_lab_slot([
            fx.instance("2026-09-22", ordinal=1),
            fx.instance("2026-09-29", ordinal=2)])]),
            ensure_ascii=False), encoding="utf-8")
    (tmp_path / "cardraw.json").write_text(
        json.dumps(fx.card_raw(items=[]), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "planraw.json").write_text(
        json.dumps(fx.plan_raw([_lab_row()]), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "topicsraw.json").write_text(
        json.dumps(fx.topics_raw(
            [fx.lesson("laboratory", n, f"Тема {n}") for n in range(1, 3)]),
            ensure_ascii=False), encoding="utf-8")

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["mo-cabinet", *argv])
        try:
            cli.main()
        except SystemExit as e:
            return e.code or 0, capsys.readouterr().out
        return 0, capsys.readouterr().out

    code, out = run("snapshot-card", str(tmp_path / "cardraw.json"))
    assert code == 0
    card_doc = json.loads(out)
    assert card_doc["schema"] == "bsu.card-snapshot/1"

    code, out = run("topics", str(tmp_path / "topicsraw.json"))
    assert code == 0
    topics_doc = json.loads(out)
    assert topics_doc["schema"] == "bsu.topics/1"

    code, out = run("snapshot", str(tmp_path / "planraw.json"))
    assert code == 0
    (tmp_path / "plan.json").write_text(out, encoding="utf-8")

    code, out = run(
        "card-diff", str(tmp_path / "slots.json"),
        "--cards", str(tmp_path / "cardraw.json"),
        "--plan", str(tmp_path / "plan.json"),
        "--topics", str(tmp_path / "topicsraw.json"),
        "--as-of", "2026-09-23")
    assert code == 0, out
    doc = json.loads(out)
    assert doc["schema"] == "bsu.diff/1"
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["asOfDate"] == "2026-09-23"
    assert [o["after"]["ready"] for o in doc["data"]["operations"]
            if o["op"] == "add"] == [True, False]
