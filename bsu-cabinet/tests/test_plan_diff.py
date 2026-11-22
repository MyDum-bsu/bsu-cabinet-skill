"""Проверяемый предпросмотр: workload/1 + snapshot/1 → diff/1.

Снимки — локальные подставные структуры; они не доказывают чтение
живого кабинета, только проверку логики сверки.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bsu_cabinet.plan import plan_diff_document, snapshot_document
from bsu_cabinet.workload import workload_document
from fixtures.plan import (
    raw_closed,
    raw_empty,
    raw_populated,
    raw_unloaded,
)
from fixtures.sht import CANON_ROWS, build_sht


@pytest.fixture()
def wdoc(tmp_path: Path) -> dict:
    p = build_sht(tmp_path / "sht.xlsx", rows=CANON_ROWS)
    doc = workload_document(p)
    assert doc["status"] == "ok", doc["issues"]
    return doc


def _ops(doc: dict) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for op in doc["data"]["operations"]:
        by.setdefault(op["op"], []).append(op)
    return by


def _codes(doc: dict) -> list[str]:
    return [i["code"] for i in doc["issues"]]


def test_snapshot_states():
    assert snapshot_document(raw_empty())["data"]["state"] == "empty"
    assert snapshot_document(raw_populated())["data"]["state"] == "populated"
    assert snapshot_document(raw_unloaded())["data"]["state"] == "unloaded"
    closed = snapshot_document(raw_closed())
    assert closed["data"]["state"] == "closed"
    assert "snapshot-readonly" in _codes(closed)
    # загруженный, но пустой по строкам и годовым — «пусто», а не «не прочитано»
    bare = {"owner": "Иванова Анна Сергеевна", "year": "2026-2027",
            "statusPlan": 0, "loaded": True}
    assert snapshot_document(bare)["data"]["state"] == "empty"


def test_empty_plan_adds_only_assigned_semesters(wdoc: dict):
    doc = plan_diff_document(wdoc, snapshot_document(raw_empty()))
    ops = _ops(doc)
    adds = ops.get("add", [])
    # осень ×2 (лекции и одна текущая консультация), весна ×2 — годовая-одна строки нет
    semesters = sorted((o["identity"]["semester"], o["identity"]["discipline"]) for o in adds)
    assert len(adds) == 4
    assert (0, "Численные методы") in semesters  # только консультация — тоже основание
    assert not any(o["identity"]["discipline"] == "Дипломная работа" for o in adds)
    # годовые виды существуют в плане — редактируются, не создаются
    edits = [o for o in ops.get("edit", []) if o.get("section") == "annual"]
    assert {o["identity"]["category"] for o in edits} == {
        "Руководство курсовыми работами", "Руководство дипломными работами",
        "Итоговая аттестация", "Рецензирование квалификационных работ",
    }
    assert _ops(doc).get("preserve")  # «Иные виды работ» сохранены


def test_populated_plan_is_noop(wdoc: dict):
    doc = plan_diff_document(wdoc, snapshot_document(raw_populated()))
    assert doc["status"] == "ok", doc["issues"]
    ops = _ops(doc)
    assert len(ops["noop"]) == 8  # 4 семестровые + 4 годовые
    assert [o["key"] for o in ops["preserve"]] == ["900", "a5"]
    # итоги сверены независимо
    assert {t["kind"]: t["match"] for t in doc["data"]["totals"]} == {
        "autumn": "ok", "spring": "ok", "annual": "ok", "grand": "ok"}


def test_edit_reports_before_after_and_source(wdoc: dict):
    raw = raw_populated()
    raw["rows"][0]["fields"]["Clock_lecture_a"] = "30"
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    edit = next(o for o in doc["data"]["operations"] if o["op"] == "edit"
                and o["key"] == "101")
    assert edit["before"] == {"Clock_lecture_a": "30"}
    assert edit["after"] == {"Clock_lecture_a": "36"}
    assert edit["sources"]["Clock_lecture_a"].startswith("'ШТ'!")


def test_groups_and_subgroups_are_independent(wdoc: dict):
    raw = raw_populated()
    # кабинет перепутал: вместо [0,3] стоит [3,0]
    raw["rows"][0]["fields"]["CountGroups"] = "3"
    raw["rows"][0]["fields"]["CountGroupsSub"] = "0"
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    edit = next(o for o in doc["data"]["operations"]
                if o["key"] == "101" and o["op"] == "edit")
    assert edit["before"] == {"CountGroups": "3", "CountGroupsSub": "0"}
    assert edit["after"] == {"CountGroups": 0, "CountGroupsSub": 3}
    # [2,2] заочной строки остаётся noop
    assert any(o["key"] == "103" and o["op"] == "noop"
               for o in doc["data"]["operations"])


def test_closed_and_unloaded_block(wdoc: dict):
    closed = plan_diff_document(wdoc, snapshot_document(raw_closed()))
    assert closed["status"] == "blocked"
    assert "plan-readonly" in _codes(closed)
    unloaded = plan_diff_document(wdoc, snapshot_document(raw_unloaded()))
    assert unloaded["status"] == "blocked"
    assert "snapshot-unloaded" in _codes(unloaded)


def test_workload_blocked_blocks_diff(tmp_path: Path):
    from fixtures.sht import correct_total_row

    total = correct_total_row(CANON_ROWS)
    total["year_total"] += 1
    p = build_sht(tmp_path / "bad.xlsx", rows=CANON_ROWS, total_row=total)
    wbad = workload_document(p)
    assert wbad["status"] == "blocked"
    doc = plan_diff_document(wbad, snapshot_document(raw_empty()))
    assert doc["status"] == "blocked"
    assert "workload-blocked" in _codes(doc)
    # несходящаяся книга не превращается в план-ноль
    assert not doc["data"]["operations"]


def test_duplicate_rows_conflict(wdoc: dict):
    raw = raw_populated()
    raw["rows"].append({**raw["rows"][0], "key": "101B"})
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    conflict = next(o for o in doc["data"]["operations"] if o["op"] == "conflict")
    assert conflict["before"] == ["101", "101B"]


def test_repeated_diff_is_deterministic(wdoc: dict):
    snap = snapshot_document(raw_populated())
    d1 = plan_diff_document(wdoc, snap)
    d2 = plan_diff_document(wdoc, snap)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert all(o["op"] != "edit" or o["changed"] for o in d2["data"]["operations"])
    # второй прогон не придумывает различий: всё noop/preserve
    assert {o["op"] for o in d2["data"]["operations"]} <= {"noop", "preserve"}


def test_empty_plan_without_annual_categories_conflicts(wdoc: dict):
    raw = {"owner": "Иванова Анна Сергеевна", "year": "2026-2027",
           "statusPlan": 0, "loaded": True,
           "rows": [], "annual": [], "totals": {}}
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    assert "annual-missing" in _codes(doc)
    # семестровые add при этом в отчёте есть — но весь документ блокирован
    assert any(o["op"] == "add" for o in doc["data"]["operations"])


def test_owner_mismatch_blocks_without_operations(wdoc: dict):
    """Другой владелец снимка при той же книге — блокировка до операций."""
    raw = raw_populated()
    raw["owner"] = "Петров Пётр Петрович"
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    assert "owner-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_owner_abbreviated_is_ambiguous(wdoc: dict):
    """Сокращённое ФИО не считается тем же аккаунтом без подтверждения."""
    raw = raw_populated()
    raw["owner"] = "Иванова А. С."
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    assert "owner-ambiguous" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_owner_unknown_blocks(wdoc: dict):
    raw = raw_populated()
    raw["owner"] = None
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    assert "owner-unknown" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_year_mismatch_blocks_without_operations(wdoc: dict):
    raw = raw_populated()
    raw["year"] = "2027-2028"
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    assert "year-mismatch" in _codes(doc)
    assert doc["data"]["operations"] == []


def test_year_unambiguous_separator_normalized(wdoc: dict):
    """Однозначное представление года нормализуется: '2026/2027' = '2026-2027'."""
    raw = raw_populated()
    raw["year"] = "2026/2027"
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "ok", doc["issues"]


def test_partial_sections_is_unloaded(wdoc: dict):
    """Одна таблица ≠ все разделы прочитаны: частичный снимок — unloaded."""
    raw = raw_populated()
    raw["loadedSections"] = ["autumn"]
    snap = snapshot_document(raw)
    assert snap["data"]["state"] == "unloaded"
    assert "snapshot-partial" in _codes(snap)
    doc = plan_diff_document(wdoc, snap)
    assert doc["status"] == "blocked"
    assert doc["data"]["operations"] == []


def test_before_hash_binds_owner_year_status(wdoc: dict):
    """beforeHash меняется при смене owner/year/statusPlan/полей снимка."""
    hashes = []
    for mutate in (
        lambda r: None,
        lambda r: r.update(owner="Петров Пётр Петрович"),
        lambda r: r.update(year="2027-2028"),
        lambda r: r.update(statusPlan=1),
        lambda r: r["rows"][0]["fields"].update(Clock_lecture_a="30"),
    ):
        raw = raw_populated()
        mutate(raw)
        doc = plan_diff_document(wdoc, snapshot_document(raw))
        hashes.append(doc["data"]["beforeHash"])
    assert len(set(hashes)) == 5
    assert all(isinstance(h, str) and len(h) == 64 for h in hashes)


def test_totals_mismatch_reported(wdoc: dict):
    raw = raw_populated()
    raw["totals"]["autumn"] = "100"
    doc = plan_diff_document(wdoc, snapshot_document(raw))
    assert doc["status"] == "blocked"
    assert "plan-total-mismatch" in _codes(doc)
    t = next(t for t in doc["data"]["totals"] if t["kind"] == "autumn")
    assert t["computed"] == "528/5" and t["before"] == "100"
