"""Сквозные проверки на синтетике: форма ШТ и ведомость группы.
Реальных xlsx и ростеров в тестах нет — только сгенерированные файлы."""

from fractions import Fraction
from pathlib import Path

from fixtures.roster import build_group_xlsx
from fixtures.sht import CANON_ROWS, build_sht

from bsu_cabinet.workload import workload_document


def test_parse_synthetic_workbook(tmp_path: Path):
    p = build_sht(tmp_path / "sht.xlsx", rows=CANON_ROWS)
    doc = workload_document(p)
    assert doc["status"] == "ok", doc["issues"]

    data = doc["data"]
    assert data["teacher"] == "Примерова Анна Сергеевна"
    assert len(data["rows"]) == 4

    mo = data["rows"][0]
    assert mo["identity"]["discipline"]["value"] == "Методы оптимизации"
    assert mo["identity"]["specialty"]["value"] == "ИН"
    assert mo["identity"]["course"]["value"] == 3
    assert mo["autumn"]["lectures_audi"]["value"] == 36
    assert mo["autumn"]["zachet"]["value"] == "93/5"  # 18,6 точной дробью
    assert mo["autumn"]["semester_total"]["value"] == "513/5"
    assert mo["annual"]["year_total"]["value"] == "553/5"

    assert data["rows"][1]["identity"]["discipline"]["value"] == "Дискретная математика"

    totals = {c["kind"]: Fraction(str(c["computed"])) for c in data["controls"]}
    assert totals["autumn"] == Fraction(528, 5)
    assert totals["grand"] == Fraction(1298, 5)


def test_parse_group_xlsx(tmp_path: Path):
    from bsu_cabinet.roster import parse_group_xlsx

    p = build_group_xlsx(tmp_path / "group.xlsx")
    roster = parse_group_xlsx(p)
    assert roster.group == "1"
    assert roster.course == 3
    assert len(roster.students) == 3
    assert roster.students[0].name == "Студентова Алина Примеровна"
    assert roster.students[0].subgroup == "1А"
