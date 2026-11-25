"""Драйвер массового заполнения: план операций → dry-run/исполнение → отчёт.

План — последовательность FillOp. dry_run печатает операции и выходит;
исполнение требует живой сессии (login()) и снимает скриншот каждого шага.
Финальный submit в кабинете — ТОЛЬКО после подтверждения владельца: операции
не жмут «Сохранить/Отправить», пока маппинг кнопки не зафиксирован в
docs/bsu-cabinet/cabinet-map.md.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from .browser import shot


@dataclass(frozen=True)
class FillOp:
    """Одна операция заполнения формы."""

    kind: str  # lesson_card | individual_plan
    target: str  # группа/дисциплина/дата — человекочитаемый идентификатор
    fields: dict[str, Any]


@dataclass
class FillReport:
    done: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = True


def fill_lesson_card(page: Page, op: FillOp) -> None:
    """Заполнить карточку занятия. Карта полей — docs/bsu-cabinet/cabinet-map.md."""
    raise NotImplementedError("карта полей кабинета не снята — нужен вход и дамп")


def fill_individual_plan(page: Page, op: FillOp) -> None:
    """Заполнить строку индивидуального плана."""
    raise NotImplementedError("карта полей кабинета не снята — нужен вход и дамп")


_FILLERS = {"lesson_card": fill_lesson_card, "individual_plan": fill_individual_plan}


def run(page: Page | None, ops: list[FillOp], report_path: Path, dry_run: bool = True) -> FillReport:
    rep = FillReport(dry_run=dry_run)
    if dry_run:
        rep.done = [f"{o.kind}:{o.target}" for o in ops]
    else:
        assert page is not None, "для исполнения нужна страница с живой сессией"
        for o in ops:
            key = f"{o.kind}:{o.target}"
            try:
                _FILLERS[o.kind](page, o)
                shot(page, f"{int(time.time())}-{o.kind}")
                rep.done.append(key)
            except Exception as e:  # noqa: BLE001 — фиксируем и идём дальше
                rep.failed.append((key, str(e)))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "dry_run": rep.dry_run,
                "done": rep.done,
                "failed": [{"op": k, "error": e} for k, e in rep.failed],
                "ops": [asdict(o) for o in ops],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return rep
