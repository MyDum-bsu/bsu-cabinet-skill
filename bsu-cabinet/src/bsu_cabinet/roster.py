"""Парсеры списков групп: xlsx-ведомости (фпми_3к_Nгр.xlsx) и yaml-ростеры.

xlsx — выгрузка кафедры: заголовок «Список студентов…», колонки
№ | ФИО | Кафедра | Курс | Группа | Подгруппа.
yaml — ростер реестра data/{год}/{поток}/*.yaml: students[].{id,name,subgroup}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import yaml


@dataclass(frozen=True)
class Student:
    name: str
    subgroup: str  # 'a'/'b' (yaml) или '1А'/'1Б' (xlsx)
    student_id: str | None = None


@dataclass(frozen=True)
class GroupRoster:
    group: str
    faculty: str
    course: int | None
    specialty: str
    students: tuple[Student, ...]


def parse_group_xlsx(path: str | Path) -> GroupRoster:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))

    head = next((_str(r[0]) for r in rows[:5] if r and r[0] and "группа" in str(r[0])), "")
    m = re.search(r"ФПМИ|факультет\s+(\S+)", head)
    group_m = re.search(r"группа\s+(\d+)", head)
    course_m = re.search(r"курс\s+(\d+)", head)
    spec_m = re.search(r"группа\s+\d+,?\s*([А-ЯA-Z][^,]*)", head)

    students: list[Student] = []
    for r in rows:
        if r and isinstance(r[0], (int, float)) and r[1]:
            students.append(Student(name=str(r[1]).strip(), subgroup=str(r[5] or "").strip()))
    return GroupRoster(
        group=group_m.group(1) if group_m else "",
        faculty="ФПМИ" if m else "",
        course=int(course_m.group(1)) if course_m else None,
        specialty=spec_m.group(1).strip() if spec_m else "",
        students=tuple(students),
    )


def parse_stream_yaml(path: str | Path) -> tuple[Student, ...]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return tuple(
        Student(name=s["name"], subgroup=str(s.get("subgroup", "")), student_id=s.get("id"))
        for s in data.get("students", [])
    )


def _str(v: object) -> str:
    return "" if v is None else str(v).strip()
