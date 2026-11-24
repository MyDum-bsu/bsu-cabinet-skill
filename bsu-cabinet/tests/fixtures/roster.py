"""Синтетическая ведомость группы (xlsx) вместо рабочего ростера."""

from __future__ import annotations

from pathlib import Path

import openpyxl


def build_group_xlsx(
    path: Path,
    *,
    header: str = "Список студентов группа 1, ИН, курс 3, ФПМИ",
    students: list[tuple[str, str]] | None = None,
) -> Path:
    """Книга вида: заголовок, затем строки [№, ФИО, кафедра, курс, группа, подгруппа]."""
    students = students if students is not None else [
        ("Студентова Алина Примеровна", "1А"),
        ("Галкин Марк Олегович", "1Б"),
        ("Демидова Софья Игоревна", "1А"),
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(1, 1, header)
    ws.cell(3, 1, "№")
    ws.cell(3, 2, "ФИО")
    ws.cell(3, 3, "Кафедра")
    ws.cell(3, 4, "Курс")
    ws.cell(3, 5, "Группа")
    ws.cell(3, 6, "Подгруппа")
    for i, (name, subgroup) in enumerate(students, 1):
        ws.cell(3 + i, 1, i)
        ws.cell(3 + i, 2, name)
        ws.cell(3 + i, 6, subgroup)
    wb.save(path)
    return path
