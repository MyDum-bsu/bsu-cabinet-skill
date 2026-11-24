"""Сырые снимки ИП для тестов diff — локальные подставные ответы,
не доказательство чтения живого кабинета."""

from __future__ import annotations

import copy

OWNER = "Примерова Анна Сергеевна"
YEAR = "2026-2027"

# категории годовых работ, как их показывает tbl-20
ANNUAL_ROWS = [
    {"key": "a1", "category": "Руководство курсовыми работами", "value": "8"},
    {"key": "a2", "category": "Руководство дипломными работами", "value": "50"},
    {"key": "a3", "category": "Итоговая аттестация", "value": "12"},
    {"key": "a4", "category": "Рецензирование квалификационных работ", "value": "15"},
    {"key": "a5", "category": "Иные виды работ", "value": ""},
]

# семестровые строки, совпадающие с CANON_ROWS (no-op при точной книге)
POPULATED_ROWS = [
    {
        "key": "101", "semester": 0,
        "identity": {"faculty": "ФПМИ", "specialty": "ИН",
                     "discipline": "Методы оптимизации", "course": 3,
                     "studyForm": "очная"},
        "fields": {
            "Curs": "3", "FormEducation": "0", "CountLine": "1",
            "CountGroups": "0", "CountGroupsSub": "3",
            "CountStudent_lecture": "90", "CountStudent_practical": "45",
            "CountStudent_laboratory": "45", "CountStudentAlien": "2",
            "Clock_lecture_a": "36", "Clock_practical_a": "18",
            "Clock_ucr_a": "16", "Check_control": "14", "Credit": "18,6",
            "Collocvium": "0", "Help_personal": "0",
        },
    },
    {
        "key": "102", "semester": 1,
        "identity": {"faculty": "ФПМИ", "specialty": "ИН",
                     "discipline": "Дискретная математика", "course": 2,
                     "studyForm": "очная"},
        "fields": {
            "Curs": "2", "FormEducation": "0", "CountLine": "0",
            "CountGroups": "3", "CountGroupsSub": "0",
            "CountStudent_practical": "60",
            "Clock_practical_a": "24", "Clock_laboratory_a": "12",
            "Help_group": "6", "Exam": "20",
            "Collocvium": "0", "Help_personal": "0",
        },
    },
    {
        "key": "103", "semester": 0,
        "identity": {"faculty": "ФПМИ", "specialty": "ПИ",
                     "discipline": "Численные методы", "course": 2,
                     "studyForm": "заочная"},
        "fields": {
            "Curs": "2", "FormEducation": "1", "CountGroups": "2",
            "CountGroupsSub": "2", "Help_now": "3",
            "Collocvium": "0", "Help_personal": "0",
        },
    },
    {
        "key": "104", "semester": 1,
        "identity": {"faculty": "ФПМИ", "specialty": "ПИ",
                     "discipline": "Численные методы", "course": 2,
                     "studyForm": "заочная"},
        "fields": {
            "Curs": "2", "FormEducation": "1", "CountGroups": "2",
            "CountGroupsSub": "2", "Help_group": "7",
            "Collocvium": "0", "Help_personal": "0",
        },
    },
    # строка кабинета вне запроса — должна остаться preserve
    {
        "key": "900", "semester": 0,
        "identity": {"faculty": "ФПМИ", "specialty": "ИН",
                     "discipline": "Теория вероятностей", "course": 2,
                     "studyForm": "очная"},
        "fields": {"Curs": "2", "Clock_lecture_a": "40"},
    },
]

POPULATED_TOTALS = {"autumn": "145,6", "spring": "69", "annual": "85", "grand": "299,6"}


def raw_empty(status_plan: int = 0) -> dict:
    """Загруженный пустой план: годовые виды есть, часов нет."""
    return {
        "owner": OWNER, "year": YEAR, "statusPlan": status_plan,
        "loaded": True,
        "rows": [],
        "annual": [{**a, "value": "", "valueResult": ""} for a in ANNUAL_ROWS],
        "totals": {k: "" for k in ("autumn", "spring", "annual", "grand")},
    }


def raw_populated(status_plan: int = 0) -> dict:
    return {
        "owner": OWNER, "year": YEAR, "statusPlan": status_plan,
        "loaded": True, "rows": copy.deepcopy(POPULATED_ROWS),
        "annual": copy.deepcopy(ANNUAL_ROWS),
        "totals": dict(POPULATED_TOTALS),
    }


def raw_closed() -> dict:
    return raw_populated(status_plan=1)


def raw_unloaded() -> dict:
    return {"owner": OWNER, "year": YEAR, "loaded": False,
            "error": "нет ответа PersonalPlanTable4"}
