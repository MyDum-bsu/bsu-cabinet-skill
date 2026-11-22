"""Card-spec: развёртка yaml-спеки карточек в записи заполнения.

Формат спеки — как в bsu-agent/cards/mo-in-2026.yaml: header (факультет,
дисциплина, курс, семестр), groups[] (id, расписание, число студентов),
program[] (№ пары, коды тем, тема, даты по дням недели).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class LessonCardFill:
    """Одна запись заполнения карточки занятия."""

    group_id: str
    lesson_n: int
    date: str  # ISO
    time: str
    room: str
    weekday: str
    topic: str
    codes: str
    students: int
    status: int = 0  # таблица CardOne: 0 лекции, 1 лаб, 2 УСР, 6 проверка, 7 экзамен


@dataclass(frozen=True)
class CardSpec:
    discipline_code: str
    teacher: str
    faculty: str
    specialty: str
    course: int
    semester: int
    academic_year: str
    fills: tuple[LessonCardFill, ...]


def load_card_spec(path: str | Path) -> CardSpec:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    h = data["header"]
    fills: list[LessonCardFill] = []
    for g in data["groups"]:
        wd = g["schedule"]["weekday"]  # 'Wed' / 'Thu'
        for section, status in (("program", 0), ("labs", 1), ("ucr", 2)):
            for p in data.get(section, []):
                date = p["dates"].get(wd)
                if not date:
                    continue
                fills.append(
                    LessonCardFill(
                        group_id=g["id"],
                        lesson_n=p["n"],
                        date=str(date),
                        time=g["schedule"]["time"],
                        room=g["schedule"]["room"],
                        weekday=wd,
                        topic=p["topic"],
                        codes=p.get("code", ""),
                        students=g["students"],
                        status=status,
                    )
                )
    return CardSpec(
        discipline_code=str(h.get("discipline_code", "")),
        teacher=str(h.get("teacher", "")),
        faculty=h["faculty"],
        specialty=h["specialty"],
        course=h["course"],
        semester=h["semester"],
        academic_year=h["academic_year"],
        fills=tuple(sorted(fills, key=lambda f: (f.group_id, f.lesson_n))),
    )
