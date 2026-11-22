"""Синтетические книги расписания и публичный индекс для тестов schedule.

Два формата по наблюдениям release-spec.md §«Публичная папка»:

- формат A (курс 2 / семестр 3): год/курс/семестр в C8, строка потоков 11,
  группы 15, подгруппы А/Б 16, дни — объединённые блоки в колонке A
  (A21:A44), базовое время в B, противоположные метки чётности —
  горизонтальные объединённые подписи «чётная/нечётная неделя» над
  диапазонами групповых колонок;
- формат B (курс 3 / семестр 5): обозначения профилей в строке 15,
  группы 16, подгруппы 17, дни A22:A45, базовое время B, альтернативное
  время отдельной колонкой C; меток чётности в книге нет — фаза только
  из текста ячейки.

Никаких реальных XLSX и ФИО — все значения вымышленные.
"""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

# ---------------------------------------------------------------------------
# Книга формата A: полосы чётности над групповыми колонками
# ---------------------------------------------------------------------------

# Колонки сетки формата A: (col, band, group, subgroup)
A_COLS = [
    (3, "even", 1, "А"),   # C
    (4, "even", 1, "Б"),   # D
    (5, "even", 2, None),  # E
    (6, "odd", 1, "А"),    # F
    (7, "odd", 1, "Б"),    # G
    (8, "odd", 2, None),   # H
    (9, None, 3, None),    # I — вне полос чётности
]


def build_book_a(
    path: Path,
    *,
    year: str = "2026-2027",
    course: int = 2,
    semester: int = 3,
    scope_cell: str | None = None,
    times: list[str] | None = None,
    rows_per_day: int = 4,
    n_days: int = 6,
    slots: dict | None = None,
    col_overrides: dict[int, tuple[int | str, str | None]] | None = None,
) -> Path:
    """Книга формата A.

    slots — {(day_index, row_in_day, col): текст ячейки назначения}.
    День i занимает строки 21 + i*rows_per_day .. +rows_per_day-1.
    col_overrides — {col: (group, subgroup)} переопределение шапки колонки.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = year
    ws["C8"] = scope_cell or (
        f"Расписание занятий студентов {course} курса "
        f"в {semester} семестре {year} учебного года"
    )
    # строка 11 — потоки (объединение над всеми групповыми колонками)
    ws.merge_cells(start_row=11, start_column=3, end_row=11, end_column=9)
    ws.cell(11, 3, "Поток ИН")
    # строка 15 — группы, 16 — подгруппы
    cols_spec = {c: (g, s) for c, _b, g, s in A_COLS}
    cols_spec.update(col_overrides or {})
    for col, (group, sub) in cols_spec.items():
        if sub is None:
            ws.merge_cells(start_row=15, start_column=col, end_row=16, end_column=col)
            ws.cell(15, col, group)
        else:
            ws.cell(15, col, group)
            ws.cell(16, col, sub)
    # строка 20 — полосы чётности над диапазонами колонок
    even_cols = [c for c, b, _g, _s in A_COLS if b == "even"]
    odd_cols = [c for c, b, _g, _s in A_COLS if b == "odd"]
    ws.merge_cells(start_row=20, start_column=min(even_cols),
                   end_row=20, end_column=max(even_cols))
    ws.cell(20, min(even_cols), "чётная неделя")
    ws.merge_cells(start_row=20, start_column=min(odd_cols),
                   end_row=20, end_column=max(odd_cols))
    ws.cell(20, min(odd_cols), "нечётная неделя")

    times = times or ["8.00-9.35", "9.45-11.20", "11.40-13.15", "13.45-15.20"]
    for d in range(n_days):
        r0 = 21 + d * rows_per_day
        ws.merge_cells(start_row=r0, start_column=1,
                       end_row=r0 + rows_per_day - 1, end_column=1)
        ws.cell(r0, 1, WEEKDAYS[d])
        for i in range(rows_per_day):
            ws.cell(r0 + i, 2, times[i % len(times)])
    for (d, i, col), text in (slots or {}).items():
        ws.cell(21 + d * rows_per_day + i, col, text)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Книга формата B: профили, отдельная колонка альтернативного времени
# ---------------------------------------------------------------------------

# (col, profile, group, subgroup)
B_COLS = [
    (4, "ИН-21", 3, "А"),   # D
    (5, "ИН-21", 3, "Б"),   # E
    (6, "ИН-21", 4, None),  # F
    (7, "ПИ-30", 1, None),  # G
]


def build_book_b(
    path: Path,
    *,
    year: str = "2026-2027",
    course: int = 3,
    semester: int = 5,
    scope_cell: str | None = None,
    times: list[str] | None = None,
    alt_times: dict[int, str] | None = None,
    rows_per_day: int = 4,
    n_days: int = 6,
    slots: dict | None = None,
) -> Path:
    """Книга формата B. Дни — строки 22+i*rows_per_day; alt_times — {row: текст}."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = year
    ws["C8"] = scope_cell or (
        f"Расписание {course} курса, {semester} семестр, {year}"
    )
    profiles: dict[str, list[int]] = {}
    for col, prof, _g, _s in B_COLS:
        profiles.setdefault(prof, []).append(col)
    for prof, cols in profiles.items():
        ws.merge_cells(start_row=15, start_column=min(cols),
                       end_row=15, end_column=max(cols))
        ws.cell(15, min(cols), f"Профиль {prof}")
    for col, _p, group, sub in B_COLS:
        if sub is None:
            ws.merge_cells(start_row=16, start_column=col, end_row=17, end_column=col)
            ws.cell(16, col, group)
        else:
            ws.cell(16, col, group)
            ws.cell(17, col, sub)

    times = times or ["8.00-9.35", "9.45-11.20", "11.40-13.15", "13.45-15.20"]
    for d in range(n_days):
        r0 = 22 + d * rows_per_day
        ws.merge_cells(start_row=r0, start_column=1,
                       end_row=r0 + rows_per_day - 1, end_column=1)
        ws.cell(r0, 1, WEEKDAYS[d])
        for i in range(rows_per_day):
            ws.cell(r0 + i, 2, times[i % len(times)])
    for r, text in (alt_times or {}).items():
        ws.cell(r, 3, text)
    for (d, i, col), text in (slots or {}).items():
        ws.cell(22 + d * rows_per_day + i, col, text)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Публичная папка (Drive-подобная разметка) и подставной транспорт
# ---------------------------------------------------------------------------

FOLDER_ID = "1VBpxem-8KE3CBgEG5l0AI9PGAuojAl_P"
FOLDER_URL = f"https://drive.google.com/drive/folders/{FOLDER_ID}"


def drive_folder_cards_html(entries: list[dict], *, duplicate_nodes: bool = True) -> str:
    """Обезличенная разметка фактической структуры: карточки файла — div
    с атрибутами data-id и data-tooltip (имя файла + подпись о типе).
    Каждая карточка встречается в разметке дважды (список + плитка)."""
    nodes = []
    for e in entries:
        node = (
            f'<div class="card" role="listitem" data-id="{e["id"]}" '
            f'data-tooltip="{e["name"]} — Файл Microsoft Excel" '
            f'data-target="drive"></div>'
        )
        nodes.append(node)
        if duplicate_nodes:
            nodes.append(node.replace('class="card"', 'class="tile"'))
    return (
        '<html><head><title>Папка расписаний — Google Drive</title></head>'
        '<body><div id="drive_main_page"><div role="list" aria-label="files">'
        + "".join(nodes)
        + "</div></div></body></html>"
    )


def drive_folder_html(entries: list[dict]) -> str:
    """Синтетическая разметка публичной папки: AF_initDataCallback-массивы.

    entries — [{"id", "name", "modified"}].
    """
    rows = "".join(
        f'["{e["id"]}",["{FOLDER_URL}"],"{e["name"]}",'
        f'"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",'
        f'"{e.get("modified", "")}"],\n'
        for e in entries
    )
    return (
        "<html><head><title>Папка расписаний</title></head><body>"
        "<script>AF_initDataCallback({key: 'ds:0', data: [null, [\n"
        f"{rows}"
        "]]});</script></body></html>"
    )


def make_fetch(routes: dict[str, bytes | str]):
    """Подставной транспорт fetch(url) -> bytes; KeyError на неизвестный URL."""
    def fetch(url: str) -> bytes:
        if url not in routes:
            raise KeyError(f"no route: {url}")
        v = routes[url]
        return v.encode("utf-8") if isinstance(v, str) else v
    return fetch


def download_url(book_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={book_id}&export=download&confirm=t"
    )


def book_entry(book_id: str, name: str, modified: str = "2026-11-20") -> dict:
    return {"id": book_id, "name": name, "modified": modified}


def exceptions_json(path: Path, items: list[dict]) -> Path:
    path.write_text(json.dumps({"exceptions": items}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def column_letter(col: int) -> str:
    return get_column_letter(col)
