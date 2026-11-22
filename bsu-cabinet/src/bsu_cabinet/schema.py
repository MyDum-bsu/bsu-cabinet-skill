"""Общая оболочка документов `bsu.<вид>/1` для CLI bsu-cabinet.

На stdout — один документ JSON `{schema, status, data, issues}`;
диагностика — на stderr. Код выхода: 0 — результат проверен (включая
пустой diff), 2 — нужно исправить ввод/выбор до записи, 3 — частично
применено или исход неизвестен.
"""

from __future__ import annotations

import json
from typing import Any

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_PARTIAL = "partial"
STATUS_UNKNOWN = "unknown"

EXIT = {STATUS_OK: 0, STATUS_BLOCKED: 2, STATUS_PARTIAL: 3, STATUS_UNKNOWN: 3}


def issue(
    code: str,
    source: str | None = None,
    field: str | None = None,
    expected: Any = None,
    actual: Any = None,
    next: str | None = None,
) -> dict[str, Any]:
    """Одна проблема разбора/сверки: {code,source,field,expected,actual,next}."""
    return {
        "code": code,
        "source": source,
        "field": field,
        "expected": expected,
        "actual": actual,
        "next": next,
    }


def envelope(schema: str, status: str, data: Any, issues: list[dict] | None = None) -> dict[str, Any]:
    return {"schema": schema, "status": status, "data": data, "issues": issues or []}


def status_of(issues: list[dict], default: str = STATUS_OK) -> str:
    """blocked, если есть хоть одна проблема (все issues — блокирующие)."""
    return STATUS_BLOCKED if issues else default


def exit_code(doc: dict) -> int:
    return EXIT.get(doc.get("status", ""), 3)


def dumps(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2)
