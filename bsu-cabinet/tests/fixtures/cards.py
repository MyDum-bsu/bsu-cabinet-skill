"""Синтетические документы для предпросмотра карточек (card-diff):

- ``slots_doc`` — bsu.slots/1 с уже вычисленными датами (как отдаёт
  ``schedule-find`` после развёртки и исключений);
- ``card_raw`` — сырое чтение таблицы карточки (то, что нормализует
  ``card_snapshot_document``); поля — как в ``CardOneSave.Items``;
- ``topics_raw``/``topics_unordered`` — формализованный тематический план;
- ``plan_raw`` — сырой снимок ИП для ``snapshot_document``.

Все значения вымышленные; живого кабинета и реальных документов нет.
"""

from __future__ import annotations

OWNER = "Иванова Анна Сергеевна"
TEACHER = "Иванова А.С."
YEAR = "2026-2027"


# ---------------------------------------------------------------------------
# bsu.slots/1
# ---------------------------------------------------------------------------

def instance(date: str, start: str = "08:00", end: str = "09:35",
             hours: object = 2, ordinal: int | None = None,
             exception: dict | None = None) -> dict:
    inst: dict = {"date": date, "localStart": start, "localEnd": end,
                  "source": "'Р'!B21", "hours": hours}
    if ordinal is not None:
        inst["ordinal"] = ordinal
    if exception:
        inst["exception"] = exception
        if exception.get("action") == "move":
            inst["originalTime"] = {"localStart": start, "localEnd": end}
    return inst


def slot(cell: str, *, discipline: str = "Дискретная математика",
         kind: str | None = "laboratory", group: str = "1",
         subgroup: str | None = "А", stream: str | None = "Поток ИН",
         weekday: str = "monday", start: str = "08:00", end: str = "09:35",
         phase: str = "every", instances: tuple | list = (),
         hours: object = 2, assignment: str = TEACHER) -> dict:
    return {
        "cell": cell, "assignment": assignment, "discipline": discipline,
        "kind": kind,
        "groups": [{"group": group, "subgroup": subgroup, "stream": stream}],
        "weekday": weekday, "localStart": start, "localEnd": end,
        "phase": phase, "bounds": {"from": None, "to": None, "source": {}},
        "altTime": None, "sourceRanges": [cell],
        "ambiguities": [], "instances": list(instances), "hours": hours,
    }


def slots_doc(slots: list, *, teacher: str = TEACHER, year: str = YEAR,
              course: int = 2, semester: int = 3,
              period: tuple = ("2026-09-01", "2026-12-31"),
              status: str = "ok", issues: tuple | list = ()) -> dict:
    return {
        "schema": "bsu.slots/1", "status": status,
        "data": {
            "bookId": "ID-A", "sha": "f" * 64,
            "scope": {"year": year, "course": course, "semester": semester,
                      "cell": "C8"},
            "teacher": teacher,
            "period": {"from": period[0], "to": period[1]},
            "slots": list(slots), "conflicts": [],
        },
        "issues": list(issues),
    }


# ---------------------------------------------------------------------------
# сырая таблица карточки (вход card_snapshot_document)
# ---------------------------------------------------------------------------

def card_item(dt: str, *, time: str = "08:00-09:35", hours: object = "2",
              ready: object = False, group: str = "1", note: str = "",
              title: str = "Тема", dl: object = False, **extra) -> dict:
    return {"title": title, "group": group, "groupCount": "1", "dt": dt,
            "time": time, "hours": hours, "ready": ready, "dl": dl,
            "note": note, **extra}


def card_raw(key: str = "101", status: int = 1, items: list | None = (),
             *, loaded: bool = True, owner: str = OWNER,
             year: str = YEAR) -> dict:
    """items=None — структура не распознана (таблица не прочитана)."""
    out = {"key": key, "status": status, "owner": owner, "year": year,
           "loaded": loaded}
    if items is not None:
        out["items"] = list(items)
    if not loaded:
        out["error"] = "нет ответа CardOne"
    return out


# ---------------------------------------------------------------------------
# тематический план (вход topics_document)
# ---------------------------------------------------------------------------

def lesson(kind: str, ordinal: int, topic: str,
           ref: str = "программа, раздел 1") -> dict:
    return {"kind": kind, "ordinal": ordinal, "topic": topic,
            "sourceRef": ref}


def topics_raw(lessons: list, *, discipline: str = "Дискретная математика",
               edition: str = "программа 2026") -> dict:
    return {"discipline": discipline, "edition": edition,
            "lessons": list(lessons)}


def topics_unordered(topics: list, *,
                     discipline: str = "Дискретная математика") -> dict:
    """Программа с голым перечнем тем — распределения по занятиям нет."""
    return {"discipline": discipline, "edition": "программа 2026",
            "topics": list(topics)}


# ---------------------------------------------------------------------------
# сырой снимок ИП (вход snapshot_document)
# ---------------------------------------------------------------------------

def plan_row(key: str = "101", *, semester: int = 0,
             discipline: str = "Дискретная математика", course: int = 2,
             fields: dict | None = None) -> dict:
    return {
        "key": key, "semester": semester,
        "identity": {"faculty": "ФПМИ", "specialty": "ИН",
                     "discipline": discipline, "course": course,
                     "studyForm": "очная"},
        "fields": dict(fields or {}),
    }


def plan_raw(rows: list, *, owner: str = OWNER, year: str = YEAR,
             status_plan: int = 0) -> dict:
    return {"owner": owner, "year": year, "statusPlan": status_plan,
            "loaded": True, "rows": list(rows), "annual": [], "totals": {}}
