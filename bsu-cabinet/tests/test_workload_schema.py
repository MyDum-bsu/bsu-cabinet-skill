"""Разбор формы ШТ в bsu.workload/1: шапка по тексту, источник до ячейки,
состояния value/blank/unknown, четыре контроля. Только синтетика."""

from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path

import openpyxl
import pytest
from fixtures.sht import (
    CANON_ROWS,
    TEACHER,
    Formula,
    build_sht,
    cell_ref,
    correct_total_row,
    inject_formula_cache,
)

from bsu_cabinet.workload import workload_document


@pytest.fixture()
def canon(tmp_path: Path) -> Path:
    return build_sht(tmp_path / "sht.xlsx", rows=CANON_ROWS)


def _codes(doc: dict) -> list[str]:
    return [i["code"] for i in doc["issues"]]


def test_full_workbook_ok(canon: Path):
    doc = workload_document(canon)
    assert doc["schema"] == "bsu.workload/1"
    assert doc["status"] == "ok", doc["issues"]
    data = doc["data"]
    assert data["sheet"] == "ШТ"
    assert data["teacher"] == "Примерова Анна Сергеевна"
    assert data["year"] == "2026-2027"
    assert len(data["sourceSha"]) == 64
    assert len(data["rows"]) == 4
    assert {c["kind"] for c in data["controls"]} == {"autumn", "spring", "annual", "grand"}
    assert all(c["match"] == "ok" for c in data["controls"])


def test_cell_provenance_to_address(canon: Path):
    doc = workload_document(canon)
    r0 = doc["data"]["rows"][0]
    cell = r0["autumn"]["lectures_audi"]
    assert cell["cell"] == "'ШТ'!Q13"
    assert cell["header"] == ["autumn", "lectures", "audi"]
    assert cell["state"] == "value" and cell["value"] == 36
    # дробные часы — точная рациональная строка, не float
    assert r0["autumn"]["zachet"]["value"] == "93/5"
    # контроль содержит адреса вкладов, а не только суммы
    grand = next(c for c in doc["data"]["controls"] if c["kind"] == "grand")
    assert "'ШТ'!AG13" in grand["contributors"]
    assert grand["bookCells"] == ["'ШТ'!BH17"]


def test_cover_sheet_skipped(canon: Path):
    doc = workload_document(canon)
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["sheet"] == "ШТ"


def test_shifted_header_layout(tmp_path: Path):
    """Шапка сдвинута на 2 строки и 1 колонку — разбор идёт по тексту."""
    base = workload_document(build_sht(tmp_path / "a.xlsx", rows=CANON_ROWS))
    moved = workload_document(
        build_sht(tmp_path / "b.xlsx", rows=CANON_ROWS, shift_rows=2, shift_cols=1)
    )
    assert moved["status"] == "ok", moved["issues"]
    b0, m0 = base["data"]["rows"][0], moved["data"]["rows"][0]
    assert m0["autumn"]["lectures_audi"]["value"] == b0["autumn"]["lectures_audi"]["value"]
    assert m0["autumn"]["lectures_audi"]["cell"] == "'ШТ'!R15"
    assert b0["autumn"]["lectures_audi"]["cell"] == "'ШТ'!Q13"
    # идентичности совпадают
    for r_b, r_m in zip(base["data"]["rows"], moved["data"]["rows"], strict=True):
        for k in ("discipline", "groups", "subgroups"):
            assert r_m["identity"][k]["value"] == r_b["identity"][k]["value"]


def test_states_value_blank_unknown(canon: Path):
    doc = workload_document(canon)
    r0, r1, r3 = (doc["data"]["rows"][i] for i in (0, 1, 3))
    # явный 0 ≠ пусто
    assert r0["identity"]["groups"] == {
        "cell": "'ШТ'!K13", "header": ["identity", "", "groups"],
        "state": "value", "value": 0,
    }
    # пустая ячейка — blank, не ноль
    assert r3["identity"]["groups"]["state"] == "blank"
    assert r3["identity"]["groups"]["value"] is None
    # пустая колонка в данных — blank
    assert r1["autumn"]["lectures_audi"]["state"] == "blank"


def test_formula_without_cache_is_unknown_not_zero(tmp_path: Path):
    rows = [dict(CANON_ROWS[0])]
    rows[0] = {**rows[0], "spring": dict(rows[0]["autumn"]), "autumn": {
        **rows[0]["autumn"],
        "consult_current": Formula("=Q13/2"),
        "semester_total": Formula("=SUM(Q13:AF13)"),
    }, "annual": {"year_total": 120}}
    p = build_sht(tmp_path / "f.xlsx", rows=rows)
    doc = workload_document(p)
    r0 = doc["data"]["rows"][0]
    assert r0["autumn"]["consult_current"]["state"] == "unknown"
    assert r0["autumn"]["consult_current"]["value"] is None
    assert r0["autumn"]["consult_current"]["raw"].startswith("=")
    # итог строки тоже формула без кэша → unknown
    assert r0["autumn"]["semester_total"]["state"] == "unknown"
    # это блокировка, а не подстановка нуля
    assert doc["status"] == "blocked"
    assert "total-unverified" in _codes(doc)


def test_formula_with_cache_is_value(tmp_path: Path):
    rows = [{**CANON_ROWS[0], "annual": dict(CANON_ROWS[0]["annual"])}]
    rows[0]["autumn"] = {**rows[0]["autumn"], "consult_current": Formula("=Q13/2")}
    p = build_sht(tmp_path / "fc.xlsx", rows=rows)
    inject_formula_cache(p, cell_ref("autumn", "consult_current", 13).split("!")[1], 18)
    doc = workload_document(p)
    cell = doc["data"]["rows"][0]["autumn"]["consult_current"]
    assert cell["state"] == "value" and cell["value"] == 18
    assert cell["formula"].startswith("=")


def test_totals_mismatch_blocks_but_keeps_values(tmp_path: Path):
    total = correct_total_row(CANON_ROWS)
    total["year_total"] = total["year_total"] + 1  # книга не сходится
    p = build_sht(tmp_path / "bad.xlsx", rows=CANON_ROWS, total_row=total)
    doc = workload_document(p)
    assert doc["status"] == "blocked"
    assert "total-mismatch" in _codes(doc)
    grand = next(c for c in doc["data"]["controls"] if c["kind"] == "grand")
    assert grand["match"] == "mismatch"
    assert grand["computed"] == "1298/5" and grand["book"] == "1303/5"
    # план не обнуляется: значения строк остаются как в книге
    assert doc["data"]["rows"][0]["autumn"]["lectures_audi"]["value"] == 36


def test_row_total_cell_mismatch(tmp_path: Path):
    rows = [dict(CANON_ROWS[0])]
    rows[0]["autumn"] = {**rows[0]["autumn"], "semester_total": 999}
    total = correct_total_row(rows)  # итого берёт «кривую» ячейку — книга сходится
    p = build_sht(tmp_path / "rt.xlsx", rows=rows, total_row=total)
    doc = workload_document(p)
    assert "row-total-mismatch" in _codes(doc)
    assert doc["status"] == "blocked"


def test_unknown_annual_article_blocks(tmp_path: Path):
    p = build_sht(
        tmp_path / "u.xlsx", rows=CANON_ROWS,
        extra_annual={"Рейтинговая работа": {0: 5}},
    )
    doc = workload_document(p)
    assert doc["status"] == "blocked"
    assert "unknown-column" in _codes(doc)
    src = next(i["source"] for i in doc["issues"] if i["code"] == "unknown-column")
    assert src.startswith("'ШТ'!")


def test_missing_total_row_blocks(tmp_path: Path):
    p = build_sht(tmp_path / "nt.xlsx", rows=CANON_ROWS, total_row=None)
    doc = workload_document(p)
    assert doc["status"] == "blocked"
    assert "control-missing" in _codes(doc)


def test_spring_and_annual_not_mixed(canon: Path):
    rows = workload_document(canon)["data"]["rows"]
    r1 = rows[1]  # весенние часы + годовые работы, без осени
    assert r1["autumn"]["lectures_audi"]["state"] == "blank"
    assert r1["spring"]["practice_audi"]["value"] == 24
    assert r1["annual"]["supervision_diploma"]["value"] == 20
    # годовые не попали в семестры
    ann = next(c for c in workload_document(canon)["data"]["controls"] if c["kind"] == "annual")
    assert ann["computed"] == 85


def test_teacher_carry_forward_and_multi(canon: Path, tmp_path: Path):
    data = workload_document(canon)["data"]
    assert data["rows"][2]["identity"]["teacher"]["value"] == "Примерова Анна Сергеевна"
    rows = [dict(CANON_ROWS[0]), dict(CANON_ROWS[1])]
    rows[1]["identity"] = {**rows[1]["identity"], "teacher": "Посторонний Пётр Примерович"}
    p = build_sht(tmp_path / "m.xlsx", rows=rows)
    doc = workload_document(p)
    assert "multiple-teachers" in _codes(doc)
    assert doc["status"] == "blocked"


def test_real_annual_header_layout(tmp_path: Path):
    """Реальная шапка: «Руководство» merge над supervision-статьями,
    отдельные годовые статьи до «Всего за год», общей шапки года нет."""
    p = build_sht(tmp_path / "real.xlsx", rows=CANON_ROWS,
                  year=None, annual_style="rukovodstvo")
    doc = workload_document(p, year="2026-2027")
    assert doc["status"] == "ok", doc["issues"]
    data = doc["data"]
    assert data["year"] == "2026-2027"
    rows = data["rows"]
    assert rows[0]["annual"]["supervision_coursework"]["value"] == 8
    assert rows[1]["annual"]["supervision_diploma"]["value"] == 20
    assert rows[1]["annual"]["final_attestation"]["value"] == 12
    assert rows[3]["annual"]["review"]["value"] == 15
    # ненулевые весенние и годовые значения прочитаны, итоги сходятся
    assert rows[1]["spring"]["exam"]["value"] == 20
    assert all(c["match"] == "ok" for c in data["controls"])
    assert {c["kind"]: c["computed"] for c in data["controls"]}["annual"] == 85


def test_book_without_year_needs_explicit_year(tmp_path: Path):
    """Книга без года: без параметра — блокировка, не угадывание."""
    p = build_sht(tmp_path / "ny.xlsx", rows=CANON_ROWS,
                  year=None, annual_style="rukovodstvo")
    doc = workload_document(p)
    assert doc["status"] == "blocked"
    assert "year-unknown" in _codes(doc)


def test_year_parameter_conflict_with_book(tmp_path: Path):
    """Явный год, противоречащий книге, блокирует, а не перекрывает."""
    p = build_sht(tmp_path / "cy.xlsx", rows=CANON_ROWS, year="2026-2027")
    doc = workload_document(p, year="2027-2028")
    assert doc["status"] == "blocked"
    assert "year-mismatch" in _codes(doc)


def test_source_sha_is_sha256_of_book_bytes(canon: Path):
    doc = workload_document(canon)
    assert doc["data"]["sourceSha"] == hashlib.sha256(canon.read_bytes()).hexdigest()


def test_snapshot_is_single_revision_sha_values_formulas(tmp_path: Path, monkeypatch):
    """Подмена файла между захватом bytes и загрузкой книг (TOCTOU):
    документ обязан описывать одну редакцию целиком — sha, значения, формулы.
    На базе sha отвечает книге A, а данные и формулы — подменённой книге B."""
    consult_ref = cell_ref("autumn", "consult_current", 13).split("!")[1]

    def _row(teacher: str, formula: str) -> dict:
        r = copy.deepcopy(CANON_ROWS[0])
        r["identity"]["teacher"] = teacher
        r["autumn"]["consult_current"] = Formula(formula)
        return r

    a = build_sht(tmp_path / "sht.xlsx", rows=[_row(TEACHER, "=Q13/2")])
    inject_formula_cache(a, consult_ref, 18)
    sha_a = hashlib.sha256(a.read_bytes()).hexdigest()

    b = build_sht(tmp_path / "sht-b.xlsx",
                  rows=[_row("Посторонняя Вера Примеровна", "=Q13/4")])
    inject_formula_cache(b, consult_ref, 18)
    blob_b = b.read_bytes()

    real_load = openpyxl.load_workbook
    swapped = False

    def _swap_then_load(filename, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            a.write_bytes(blob_b)  # подмена после первого захвата bytes
        return real_load(filename, *args, **kwargs)

    monkeypatch.setattr(openpyxl, "load_workbook", _swap_then_load)

    doc = workload_document(a)
    data = doc["data"]
    assert data["sourceSha"] == sha_a
    assert data["teacher"] == TEACHER
    assert data["rows"][0]["autumn"]["consult_current"]["formula"] == "=Q13/2"


def test_unreadable_workbook_refused(tmp_path: Path):
    """Нечитаемый ввод — явный отказ исключением, не документ с мусором."""
    p = tmp_path / "junk.xlsx"
    p.write_bytes(b"plain text, definitely not xlsx")
    with pytest.raises(zipfile.BadZipFile):
        workload_document(p)


def test_cli_workload_year_flag(tmp_path: Path, capsys, monkeypatch):
    import sys

    from bsu_cabinet import cli

    p = build_sht(tmp_path / "cy2.xlsx", rows=CANON_ROWS,
                  year=None, annual_style="rukovodstvo")
    monkeypatch.setattr(
        sys, "argv",
        ["mo-cabinet", "workload", str(p), "--year", "2026/2027"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["data"]["year"] == "2026-2027"


def test_cli_workload_envelope(canon: Path, capsys, monkeypatch):
    import sys

    from bsu_cabinet import cli

    monkeypatch.setattr(sys, "argv", ["mo-cabinet", "workload", str(canon)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "bsu.workload/1" and doc["status"] == "ok"


def test_blank_grand_control_is_unknown_not_zero(tmp_path: Path):
    """Пустая контрольная ячейка «Всего за год» — непрочитанный контроль,
    а не доказанный ноль; ноль должен быть записан явно."""
    total = correct_total_row(CANON_ROWS)
    del total["year_total"]
    p = build_sht(tmp_path / "blank-grand.xlsx", rows=CANON_ROWS, total_row=total)
    doc = workload_document(p)
    assert doc["status"] == "blocked"
    grand = next(c for c in doc["data"]["controls"] if c["kind"] == "grand")
    assert grand["book"] is None
    assert grand["computed"] == "1298/5"  # computed сам по себе не стирается
    assert grand["match"] == "unknown"
    assert "total-unverified" in _codes(doc)


def test_cli_missing_workload_argument_is_clean_error(capsys, monkeypatch):
    """Неполная команда — понятный usage и exit 2, без traceback/IndexError."""
    import sys

    from bsu_cabinet import cli

    monkeypatch.setattr(sys, "argv", ["mo-cabinet", "workload"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    captured = capsys.readouterr()
    assert "Использование: mo-cabinet workload <xlsx>" in captured.err
    assert "Traceback" not in captured.err


def test_uncached_formula_in_annual_leaf_total_is_unknown_not_zero(tmp_path: Path):
    """Формула без кэша в строке «Итого» annual-leaf не превращается в ноль."""
    total = correct_total_row(CANON_ROWS)
    total["supervision_diploma"] = Formula("=SUM(BB13:BB16)")
    p = build_sht(tmp_path / "uncached-total.xlsx", rows=CANON_ROWS, total_row=total)
    doc = workload_document(p)
    assert doc["status"] == "blocked"
    annual = next(c for c in doc["data"]["controls"] if c["kind"] == "annual")
    assert annual["book"] is None
    assert annual["computed"] == 85
    assert annual["match"] == "unknown"
    assert "total-unverified" in _codes(doc)
