"""Синтетический конструктор формы ШТ (xlsx) для тестов.

Строит книгу openpyxl с многострочной шапкой: строка блоков
(осенний/весенний семестр, работы в течение года), строка видов,
строка подвидов, нумерующая строка, данные, строка «Итого».
Никаких рабочих XLSX и реальных ФИО — все значения вымышленные.
"""

from __future__ import annotations

import re
import zipfile
from fractions import Fraction
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

SHEET = "ШТ"

IDENT_FIELDS = [
    ("num", "№"),
    ("teacher", "ФИО преподавателя"),
    ("position", "Должность"),
    ("rate", "Ставка"),
    ("discipline", "Дисциплина"),
    ("faculty", "Факультет"),
    ("course", "Курс"),
    ("study_form", "Форма обучения"),
    ("specialty", "Специальность"),
    ("streams", "Потоки"),
    ("groups", "Группы"),
    ("subgroups", "Подгруппы"),
    ("students_lecture", "Студентов на лекциях"),
    ("students_practical", "Студентов на практических"),
    ("students_laboratory", "Студентов на лабораторных"),
    ("students_foreign", "В том числе иностранных"),
]

# (field, вид-строка, подвид-строка); подряд идущие одинаковые виды merge'ятся
SEM_SUBFIELDS = [
    ("lectures_audi", "Лекции", "аудиторные"),
    ("lectures_dot", "Лекции", "ДОТ"),
    ("practice_audi", "Практические занятия", "аудиторные"),
    ("practice_dot", "Практические занятия", "ДОТ"),
    ("labs_audi", "Лабораторные занятия", "аудиторные"),
    ("labs_dot", "Лабораторные занятия", "ДОТ"),
    ("usr_audi", "УСР", "аудиторные"),
    ("usr_dot", "УСР", "ДОТ"),
    ("consult_current", "Консультации", "текущие"),
    ("consult_before_exam", "Консультации", "перед экзаменом"),
    ("check_kr", "Проверка письменных работ", "контрольные работы"),
    ("check_essays", "Проверка письменных работ", "рефераты"),
    ("check_other", "Проверка письменных работ", "прочие"),
    ("exam", "Экзамен (дифференцированный зачёт)", None),
    ("zachet", "Зачёт", None),
    ("exam_review", "Просмотр экзаменационных работ", None),
    ("semester_total", "Всего за семестр", None),
]

ANNUAL_FIELDS = [
    ("supervision_edu_practice", "Руководство учебной практикой"),
    ("supervision_prod_practice", "Руководство производственной практикой"),
    ("supervision_coursework", "Руководство курсовыми работами и проектами"),
    ("supervision_diploma", "Руководство дипломными работами"),
    ("supervision_master", "Руководство магистрантами"),
    ("supervision_phd", "Руководство аспирантами"),
    ("review", "Рецензирование квалификационных работ"),
    ("final_attestation", "Итоговая аттестация"),
    ("other", "Иные виды работ"),
    ("year_total", "Всего за год"),
]

ANNUAL_LEAFS = [k for k, _ in ANNUAL_FIELDS if k != "year_total"]
SEM_LEAFS = [k for k, _, _ in SEM_SUBFIELDS if k != "semester_total"]

# листовые подписи годового блока «Руководство» (реальная шапка: родитель
# «Руководство» над 6 supervision-статьями, листья в строке ниже)
ANNUAL_LEAF_TITLES = {
    "supervision_edu_practice": "учебной практикой",
    "supervision_prod_practice": "производственной и преддипломной практикой",
    "supervision_coursework": "курсовыми работами, проектами",
    "supervision_diploma": "дипломными работами",
    "supervision_master": "магистрантами",
    "supervision_phd": "аспирантами",
}
N_SUPERVISION = len(ANNUAL_LEAF_TITLES)

N_IDENT = len(IDENT_FIELDS)
N_SEM = len(SEM_SUBFIELDS)
N_ANNUAL = len(ANNUAL_FIELDS)


class Formula(str):
    """Ячейка-формула: строка '=...' без кэша результата (data_only → None)."""


TEACHER = "Иванова Анна Сергеевна"

# Полная вымышленная нагрузка: два семестровых назначения осени, два весны,
# годовые работы, консультация без аудиторных занятий и чисто годовая строка.
CANON_ROWS: list[dict] = [
    {  # [0 групп, 3 подгруппы] — явный 0, не пусто
        "identity": {
            "teacher": TEACHER, "position": "доцент", "rate": 1,
            "discipline": "Методы оптимизации", "faculty": "ФПМИ", "course": 3,
            "study_form": "очная", "specialty": "ИН", "streams": 1, "groups": 0,
            "subgroups": 3, "students_lecture": 90, "students_practical": 45,
            "students_laboratory": 45, "students_foreign": 2,
        },
        "autumn": {"lectures_audi": 36, "practice_audi": 18, "usr_audi": 16,
                   "check_kr": 10, "check_essays": 4, "zachet": 18.6,
                   "semester_total": 102.6},
        "annual": {"supervision_coursework": 8, "year_total": 110.6},
    },
    {  # [3 группы, 0 подгрупп], только весна + годовые
        "identity": {
            "discipline": "Дискретная математика", "faculty": "ФПМИ", "course": 2,
            "study_form": "очная", "specialty": "ИН", "streams": 0, "groups": 3,
            "subgroups": 0, "students_practical": 60,
        },
        "spring": {"practice_audi": 24, "labs_audi": 12, "consult_before_exam": 6,
                   "exam": 20, "semester_total": 62},
        "annual": {"supervision_diploma": 20, "final_attestation": 12,
                   "year_total": 94},
    },
    {  # [2, 2]; в осени — одна текущая консультация, весной — перед экзаменом
        "identity": {
            "discipline": "Численные методы", "faculty": "ФПМИ", "course": 2,
            "study_form": "заочная", "specialty": "ПИ", "groups": 2, "subgroups": 2,
        },
        "autumn": {"consult_current": 3, "semester_total": 3},
        "spring": {"consult_before_exam": 7, "semester_total": 7},
        "annual": {"year_total": 10},
    },
    {  # чисто годовая работа — семестровой строки в кабинете не требует
        "identity": {
            "discipline": "Дипломная работа", "faculty": "ФПМИ", "course": 4,
            "study_form": "очная", "specialty": "ИН",
        },
        "annual": {"supervision_diploma": 30, "review": 15, "year_total": 45},
    },
]


def zone_fields(zone: str, extra_annual: list[str] | None = None) -> list[str]:
    if zone == "identity":
        return [k for k, _ in IDENT_FIELDS]
    if zone in ("autumn", "spring"):
        return [k for k, _, _ in SEM_SUBFIELDS]
    if zone == "annual":
        keys = [k for k, _ in ANNUAL_FIELDS]
        for title in extra_annual or []:
            keys.insert(-1, title)  # перед «Всего за год»
        return keys
    raise KeyError(zone)


def block_cols(zone: str, shift_cols: int = 0, extra_annual: list[str] | None = None) -> tuple[int, int]:
    """Диапазон колонок зоны (включительно) в макете."""
    b = 1 + shift_cols
    n_ann = len(zone_fields("annual", extra_annual))
    if zone == "identity":
        return b, b + N_IDENT - 1
    if zone == "autumn":
        return b + N_IDENT, b + N_IDENT + N_SEM - 1
    if zone == "spring":
        return b + N_IDENT + N_SEM, b + N_IDENT + 2 * N_SEM - 1
    if zone == "annual":
        s = b + N_IDENT + 2 * N_SEM
        return s, s + n_ann - 1
    raise KeyError(zone)


def col_index(zone: str, field: str, shift_cols: int = 0, extra_annual: list[str] | None = None) -> int:
    c0, _ = block_cols(zone, shift_cols, extra_annual)
    return c0 + zone_fields(zone, extra_annual).index(field)


def cell_ref(zone: str, field: str, row: int, shift_cols: int = 0, sheet: str = SHEET,
             extra_annual: list[str] | None = None) -> str:
    col = col_index(zone, field, shift_cols, extra_annual)
    return f"'{sheet}'!{get_column_letter(col)}{row}"


def _num(v) -> Fraction:
    if isinstance(v, Fraction):
        return v
    if isinstance(v, (int, float)):
        return Fraction(str(v)).limit_denominator(10**6)
    return Fraction(0)


def correct_total_row(rows: list[dict]) -> dict:
    """Контрольные значения для строки «Итого», пересчитанные по данным."""
    def s(row: dict, zone: str, key: str) -> Fraction:
        v = (row.get(zone) or {}).get(key)
        return _num(v) if isinstance(v, (int, float, Fraction)) else Fraction(0)

    def row_year(r: dict) -> Fraction:
        if isinstance((r.get("annual") or {}).get("year_total"), (int, float, Fraction)):
            return s(r, "annual", "year_total")
        return (
            s(r, "autumn", "semester_total") + s(r, "spring", "semester_total")
            + sum((_num(v) for k, v in (r.get("annual") or {}).items() if k in ANNUAL_LEAFS), Fraction(0))
        )

    out: dict[str, Fraction] = {
        "autumn_semester_total": sum((s(r, "autumn", "semester_total") for r in rows), Fraction(0)),
        "spring_semester_total": sum((s(r, "spring", "semester_total") for r in rows), Fraction(0)),
        "year_total": sum((row_year(r) for r in rows), Fraction(0)),
    }
    for k in ANNUAL_LEAFS:
        out[k] = sum((s(r, "annual", k) for r in rows), Fraction(0))
    return out


def build_sht(
    path: Path,
    *,
    shift_rows: int = 0,
    shift_cols: int = 0,
    cover: bool = False,
    rows: list[dict] | None = None,
    total_row: dict | None = "auto",
    year: str | None = "2026-2027",
    extra_annual: dict[str, dict[int, float]] | None = None,
    sheet_name: str = SHEET,
    annual_style: str = "superheader",
) -> Path:
    """Записать синтетическую книгу ШТ в path.

    rows — [{"identity": {f: v}, "autumn": {f: v}, "spring": {...}, "annual": {...}}];
    пропущенный ключ = пустая ячейка (blank), явный 0 — число.
    total_row — "auto" (корректные итоги), dict из correct_total_row()
    (возможно, подпорченный) или None (строки «Итого» нет).
    extra_annual — {заголовок: {index строки данных: значение}} — неизвестные
    статьи внутри годового блока.
    annual_style: "superheader" — общая шапка «…в течение года» над годовыми;
    "rukovodstvo" — реальная форма: шапки годового блока нет, первые 6
    статей под общим merge «Руководство», листья строкой ниже, дальше
    отдельные годовые статьи до «Всего за год».
    """
    rows = rows or []
    extra = list((extra_annual or {}).keys())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    hr, mr, lr = 9 + shift_rows, 10 + shift_rows, 11 + shift_rows
    nr, d0 = 12 + shift_rows, 13 + shift_rows
    b = 1 + shift_cols

    if year is not None:
        ws.cell(hr - 6, b, f"Штатная нагрузка на {year} учебный год")

    for i, (_, title) in enumerate(IDENT_FIELDS):
        c = b + i
        ws.merge_cells(start_row=hr, start_column=c, end_row=lr, end_column=c)
        ws.cell(hr, c, title)

    block_titles = [("autumn", "Осенний семестр"), ("spring", "Весенний семестр")]
    if annual_style == "superheader":
        block_titles.append(("annual", "Учебная работа в течение года"))
    for zone, text in block_titles:
        c0, c1 = block_cols(zone, shift_cols, extra)
        ws.merge_cells(start_row=hr, start_column=c0, end_row=hr, end_column=c1)
        ws.cell(hr, c0, text)

    for zone in ("autumn", "spring"):
        c0, _ = block_cols(zone, shift_cols)
        col = c0
        i = 0
        while i < N_SEM:
            mid = SEM_SUBFIELDS[i][1]
            width = 1
            while i + width < N_SEM and SEM_SUBFIELDS[i + width][1] == mid:
                width += 1
            ws.merge_cells(start_row=mr, start_column=col, end_row=mr, end_column=col + width - 1)
            ws.cell(mr, col, mid)
            if width == 1:
                ws.merge_cells(start_row=mr, start_column=col, end_row=lr, end_column=col)
            else:
                for j in range(width):
                    leaf = SEM_SUBFIELDS[i + j][2]
                    if leaf is not None:
                        ws.cell(lr, col + j, leaf)
            col += width
            i += width

    a0, _ = block_cols("annual", shift_cols, extra)
    if annual_style == "rukovodstvo":
        # «Руководство» в строке блоков (на одном ряду с «Осенний/Весенний»),
        # листья — две строки ниже, отдельные статьи и «Всего за год» на всю высоту
        ws.merge_cells(start_row=hr, start_column=a0, end_row=hr,
                       end_column=a0 + N_SUPERVISION - 1)
        ws.cell(hr, a0, "Руководство")
        for i, (key, _) in enumerate(ANNUAL_FIELDS[:N_SUPERVISION]):
            ws.merge_cells(start_row=mr, start_column=a0 + i,
                           end_row=lr, end_column=a0 + i)
            ws.cell(mr, a0 + i, ANNUAL_LEAF_TITLES[key])
        for i, (_key, title) in enumerate(ANNUAL_FIELDS[N_SUPERVISION:-1],
                                          start=N_SUPERVISION):
            ws.merge_cells(start_row=mr, start_column=a0 + i,
                           end_row=lr, end_column=a0 + i)
            ws.cell(mr, a0 + i, title)
    else:
        for i, (_key, title) in enumerate(ANNUAL_FIELDS[:-1]):
            ws.merge_cells(start_row=mr, start_column=a0 + i, end_row=lr, end_column=a0 + i)
            ws.cell(mr, a0 + i, title)
    for j, title in enumerate(extra):
        c = a0 + len(ANNUAL_FIELDS) - 1 + j
        ws.merge_cells(start_row=mr, start_column=c, end_row=lr, end_column=c)
        ws.cell(mr, c, title)
    c_last = a0 + len(zone_fields("annual", extra)) - 1
    ws.merge_cells(
        start_row=hr if annual_style == "rukovodstvo" else mr,
        start_column=c_last, end_row=lr, end_column=c_last)
    ws.cell(hr if annual_style == "rukovodstvo" else mr, c_last,
            ANNUAL_FIELDS[-1][1])

    for i in range(c_last):
        ws.cell(nr, b + i, i + 1)

    def _put(r: int, c: int, v) -> None:
        ws.cell(r, c, float(v) if isinstance(v, Fraction) else v)

    for ri, rspec in enumerate(rows):
        r = d0 + ri
        for zone in ("identity", "autumn", "spring", "annual"):
            for k, v in (rspec.get(zone) or {}).items():
                _put(r, col_index(zone, k, shift_cols, extra), v)
        for title, values in (extra_annual or {}).items():
            if ri in values:
                _put(r, col_index("annual", title, shift_cols, extra), values[ri])

    if total_row is not None:
        tr = d0 + len(rows)
        ws.cell(tr, b, "Итого")
        total = correct_total_row(rows) if total_row == "auto" else dict(total_row)
        for k, v in total.items():
            v = float(v) if isinstance(v, Fraction) else v
            if k == "autumn_semester_total":
                ws.cell(tr, col_index("autumn", "semester_total", shift_cols), v)
            elif k == "spring_semester_total":
                ws.cell(tr, col_index("spring", "semester_total", shift_cols), v)
            elif k in ANNUAL_LEAFS + ["year_total"]:
                ws.cell(tr, col_index("annual", k, shift_cols, extra), v)
            else:
                raise KeyError(f"total_row key {k!r}")

    if cover:
        cv = wb.create_sheet("Обложка", 0)
        cv.cell(1, 1, "Обложка приказа о штатной нагрузке")
        cv.cell(3, 1, "Дисциплинами владеет кафедра, подписал декан")
        cv.cell(5, 1, "Утверждено приказом № 17 от 01.09")

    wb.save(path)
    return path


def inject_formula_cache(path: Path, cell: str, value: float | int) -> None:
    """Zip-хирургия: добавить <v> к формуле <f> в ячейке (кэш результата)."""
    path = Path(path)
    tmp = path.with_suffix(".tmp.xlsx")
    cell_re = re.compile(rf'(<c r="{cell}"[^>]*>)(<f>.*?</f>)(?:<v\s*/>)?(</c>)')
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        target = None
        for item in zin.namelist():
            data = zin.read(item)
            if item.startswith("xl/worksheets/") and item.endswith(".xml"):
                text = data.decode("utf-8")
                if f'r="{cell}"' in text and "<f>" in text:
                    new = cell_re.sub(rf"\1\2<v>{value}</v>\3", text, count=1)
                    if new != text:
                        data, target = new.encode("utf-8"), item
            zout.writestr(item, data)
    if target is None:
        tmp.unlink()
        raise AssertionError(f"formula cell {cell} not found in {path}")
    tmp.replace(path)
