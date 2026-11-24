"""schedule-index / schedule-find: публичный индекс папки, выбор редакции,
разбор двух форматов книги расписания, личные слоты и вычисленные даты.
Только синтетика и подставной транспорт — сети, кабинета и реальных
книг/списков в тестах нет."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from fixtures.schedule import (
    FOLDER_ID,
    FOLDER_URL,
    book_entry,
    build_book_a,
    build_book_b,
    download_url,
    drive_folder_cards_html,
    drive_folder_html,
    exceptions_json,
    make_fetch,
)

import bsu_cabinet.schedule as schedule_mod
from bsu_cabinet.schedule import (
    fetch_index_document,
    index_document,
    schedule_find_document,
)

TEACHER = "Примерова А.С."

INDEX_NAME_A = "2 курс, 3 семестр, 2026-2027.xlsx"
INDEX_NAME_B = "3 курс, 5 семестр, 2026-2027.xlsx"


def _codes(doc: dict) -> list[str]:
    return [i["code"] for i in doc["issues"]]


@pytest.fixture()
def book_a(tmp_path: Path) -> Path:
    return build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.), ауд.212\nПримерова А.С.",   # Пн, 1А, чёт
        (0, 1, 6): "Методы оптимизации, лек., поток ИН\nПримерова А.С., ауд.305 (нечётн.)",  # Пн нечёт
        (1, 0, 9): "Численные методы (пр.) с 15.09\nПримерова А.С., ауд.101 еженедельно",    # Вт, гр.3
        (2, 2, 4): "Информатика (лаб.) по 20.11\nПосторонняя И.И.",            # Ср, чужой
    })


def _index_doc(book_a_path: Path, *, name: str = INDEX_NAME_A, book_id: str = "ID-A") -> dict:
    html = drive_folder_html([book_entry(book_id, name)])
    return index_document(html.encode(), source=FOLDER_URL, observed_at="2026-11-21T10:00:00")


def _find(index_doc: dict, book: Path, **kw) -> dict:
    return schedule_find_document(
        index_doc, year="2026-2027", semester=3, course=2,
        teacher=kw.pop("teacher", TEACHER),
        book_bytes=book.read_bytes(), **kw)


# ---------------------------------------------------------------------------
# Индекс
# ---------------------------------------------------------------------------

def test_index_lists_books_with_hints():
    html = drive_folder_html([
        book_entry("ID-A", INDEX_NAME_A),
        book_entry("ID-B", INDEX_NAME_B, modified="2026-11-19"),
    ])
    doc = index_document(html.encode(), source=FOLDER_URL,
                         observed_at="2026-11-21T10:00:00")
    assert doc["schema"] == "bsu.index/1"
    assert doc["status"] == "ok", doc["issues"]
    data = doc["data"]
    assert data["folderId"] == FOLDER_ID
    assert data["observedAt"] == "2026-11-21T10:00:00"
    assert [b["id"] for b in data["books"]] == ["ID-A", "ID-B"]
    a = data["books"][0]
    assert a["name"] == INDEX_NAME_A
    assert a["courseHint"] == 2 and a["semesterHint"] == 3
    assert a["yearHint"] == "2026-2027"
    assert a["modifiedHint"] == "2026-11-20"


def test_index_via_fake_transport():
    html = drive_folder_html([book_entry("ID-A", INDEX_NAME_A)])
    doc = fetch_index_document(FOLDER_URL, fetch=make_fetch({FOLDER_URL: html}))
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["folderId"] == FOLDER_ID


def test_index_changed_markup_blocked():
    doc = index_document(b"<html><body>nothing here</body></html>",
                         source=FOLDER_URL, observed_at="t")
    assert doc["status"] == "blocked"
    assert "markup-unrecognized" in _codes(doc)


def test_index_no_xlsx_blocked():
    html = drive_folder_html([book_entry("ID-X", "readme.txt")])
    doc = index_document(html.encode(), source=FOLDER_URL, observed_at="t")
    assert doc["status"] == "blocked"


def test_index_card_markup_actual_structure():
    """Фактическая структура папки: карточки data-id + data-tooltip;
    двойные UI-элементы объединяются по ID."""
    html = drive_folder_cards_html([
        book_entry("1Bi5nCoVbFyV7i-0ELHBzgozbzlAdzBfP", INDEX_NAME_A),
        book_entry("1xP_gY1Gl8AdXXZORgKaO-8z1ZdXKG3FW", INDEX_NAME_B),
    ])
    doc = index_document(html.encode(), source=FOLDER_URL,
                         observed_at="2026-11-21T10:00:00")
    assert doc["status"] == "ok", doc["issues"]
    books = doc["data"]["books"]
    assert [b["id"] for b in books] == [
        "1Bi5nCoVbFyV7i-0ELHBzgozbzlAdzBfP",
        "1xP_gY1Gl8AdXXZORgKaO-8z1ZdXKG3FW"]
    assert books[0]["name"] == INDEX_NAME_A
    assert books[0]["courseHint"] == 2 and books[0]["semesterHint"] == 3
    assert books[1]["yearHint"] == "2026-2027"


def test_index_half_loaded_not_empty_folder():
    """Страница без карточек файлов — блок, а не «пустая папка»."""
    html = ('<html><body><div id="drive_main_page">'
            '<div class="loading">Загрузка…</div></div></body></html>')
    doc = index_document(html.encode(), source=FOLDER_URL, observed_at="t")
    assert doc["status"] == "blocked"
    assert "books" not in doc["data"] or doc["data"]["books"] == []


# ---------------------------------------------------------------------------
# Предохранители транспорта и распаковки
# ---------------------------------------------------------------------------

def test_fetch_rejects_non_https_before_transport():
    """file:///... и прочие не-HTTPS/не-Drive адреса — отказ ДО вызова транспорта."""
    fetch = Mock()
    for bad in ("file:///synthetic-not-read.html",
                "http://drive.google.com/drive/folders/x",
                "https://evil.example.com/drive/folders/x"):
        doc = fetch_index_document(bad, fetch=fetch)
        assert doc["status"] == "blocked", bad
        assert "url-not-allowed" in _codes(doc), bad
    fetch.assert_not_called()


def test_fetch_rejects_non_drive_book_url():
    fetch = Mock()
    doc = schedule_find_document(
        index_document(drive_folder_cards_html(
            [book_entry("ID-A", INDEX_NAME_A)]).encode(),
            source=FOLDER_URL, observed_at="t"),
        year="2026-2027", semester=3, course=2, teacher=TEACHER,
        url="https://evil.example.com/book.xlsx", fetch=fetch)
    assert doc["status"] == "blocked"
    assert "url-not-allowed" in _codes(doc)
    fetch.assert_not_called()


def test_book_unpacked_size_limit(book_a: Path, monkeypatch):
    """Лимит распакованного объёма проверяется до openpyxl."""
    monkeypatch.setattr(schedule_mod, "MAX_UNPACKED_BYTES", 1024)
    doc = schedule_find_document(
        _index_doc(book_a), year="2026-2027", semester=3, course=2,
        teacher=TEACHER, book_bytes=book_a.read_bytes())
    assert doc["status"] == "blocked"
    assert "book-too-large" in _codes(doc)


def test_book_unpacked_within_limit(book_a: Path, monkeypatch):
    monkeypatch.setattr(schedule_mod, "MAX_UNPACKED_BYTES", 10 * 1024 * 1024)
    doc = _find(_index_doc(book_a), book_a)
    assert doc["status"] == "ok", doc["issues"]


def test_index_fragment_over_limit_blocked(monkeypatch):
    """Локальный HTML-фрагмент больше лимита индекса: blocked с пустым
    списком кандидатов, до разбора записей."""
    full = drive_folder_html([
        book_entry("ID-old", INDEX_NAME_A, modified="2026-10-01"),
        book_entry("ID-new", INDEX_NAME_A, modified="2026-11-20"),
    ]).encode()
    fragment = full[:full.index(b"ID-new")]  # одна запись внутри, вторая редакция — за границей
    monkeypatch.setattr(schedule_mod, "MAX_INDEX_BYTES",
                        len(fragment) - 1, raising=False)
    doc = index_document(fragment, source=FOLDER_URL, observed_at="t")
    assert doc["status"] == "blocked"
    assert "index-too-large" in _codes(doc)
    assert doc["data"]["books"] == []


def test_index_fragment_over_limit_via_fetch_blocked(monkeypatch):
    """Та же граница для сетевого ответа подставного транспорта."""
    full = drive_folder_html([book_entry("ID-A", INDEX_NAME_A)]).encode()
    monkeypatch.setattr(schedule_mod, "MAX_INDEX_BYTES",
                        len(full) - 1, raising=False)
    doc = fetch_index_document(FOLDER_URL, fetch=make_fetch({FOLDER_URL: full}))
    assert doc["status"] == "blocked"
    assert "index-too-large" in _codes(doc)
    assert doc["data"]["books"] == []


def test_index_exactly_at_limit_parses(monkeypatch):
    """Полный ответ ровно в лимите по-прежнему разбирается."""
    full = drive_folder_html([book_entry("ID-A", INDEX_NAME_A)]).encode()
    monkeypatch.setattr(schedule_mod, "MAX_INDEX_BYTES",
                        len(full), raising=False)
    doc = index_document(full, source=FOLDER_URL, observed_at="t")
    assert doc["status"] == "ok", doc["issues"]
    assert [b["id"] for b in doc["data"]["books"]] == ["ID-A"]


def test_truncated_index_does_not_pick_edition(book_a: Path, monkeypatch):
    """Усечённый индекс с одной подходящей книгой (другая редакция дальше
    по списку): расписание по фрагменту не выбирается."""
    full = drive_folder_html([
        book_entry("ID-old", INDEX_NAME_A, modified="2026-10-01"),
        book_entry("ID-new", INDEX_NAME_A, modified="2026-11-20"),
    ]).encode()
    fragment = full[:full.index(b"ID-new")]
    monkeypatch.setattr(schedule_mod, "MAX_INDEX_BYTES",
                        len(fragment) - 1, raising=False)
    idx = index_document(fragment, source=FOLDER_URL, observed_at="t")
    fetch = Mock(wraps=make_fetch({
        download_url("ID-old"): book_a.read_bytes(),
        download_url("ID-new"): book_a.read_bytes(),
    }))
    doc = schedule_find_document(
        idx, year="2026-2027", semester=3, course=2, teacher=TEACHER,
        fetch=fetch)
    assert doc["status"] == "blocked"
    assert doc["data"]["bookId"] is None
    assert doc["data"]["slots"] == []
    fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Выбор редакции
# ---------------------------------------------------------------------------

def test_edition_ambiguous_blocked(book_a: Path):
    """Две книги того же года/курса/семестра — кандидаты, а не «последняя по времени»."""
    html = drive_folder_html([
        book_entry("ID-old", INDEX_NAME_A, modified="2026-10-01"),
        book_entry("ID-new", INDEX_NAME_A, modified="2026-11-20"),
    ])
    doc = _find(index_document(html.encode(), source=FOLDER_URL, observed_at="t"), book_a)
    assert doc["status"] == "blocked"
    assert "edition-ambiguous" in _codes(doc)
    cand = next(i for i in doc["issues"] if i["code"] == "edition-ambiguous")
    assert {c["id"] for c in cand["actual"]} == {"ID-old", "ID-new"}


def test_edition_not_found(book_a: Path):
    html = drive_folder_html([book_entry("ID-B", INDEX_NAME_B)])
    doc = _find(index_document(html.encode(), source=FOLDER_URL, observed_at="t"), book_a)
    assert doc["status"] == "blocked"
    assert "edition-not-found" in _codes(doc)


def test_scope_mismatch_blocks(book_a: Path):
    """Книга другого семестра: C8 проверяется независимо от имени файла."""
    doc = schedule_find_document(
        _index_doc(book_a), year="2026-2027", semester=5, course=2,
        teacher=TEACHER, book_bytes=book_a.read_bytes(), book_id="ID-A")
    assert doc["status"] == "blocked"
    assert "scope-mismatch" in _codes(doc)


def test_same_id_changed_bytes_detectable(book_a: Path, tmp_path: Path):
    """Тот же ID, другое содержимое → другой sha и другой разбор."""
    doc1 = _find(_index_doc(book_a), book_a)
    changed = build_book_a(tmp_path / "a2.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.), ауд.212\nПримерова А.С.",
        (3, 0, 5): "Алгебра (лек.) еженедельно\nПримерова А.С.",  # новый слот Чт
    })
    doc2 = _find(_index_doc(changed), changed)
    assert doc1["status"] == doc2["status"] == "ok", doc2["issues"]
    assert doc1["data"]["sha"] == hashlib.sha256(book_a.read_bytes()).hexdigest()
    assert doc2["data"]["sha"] == hashlib.sha256(changed.read_bytes()).hexdigest()
    assert doc1["data"]["sha"] != doc2["data"]["sha"]
    d1 = {(s["weekday"], s["cell"], s["discipline"]) for s in doc1["data"]["slots"]}
    d2 = {(s["weekday"], s["cell"], s["discipline"]) for s in doc2["data"]["slots"]}
    assert d1 != d2


def test_book_via_transport_download(book_a: Path):
    html = drive_folder_html([book_entry("ID-A", INDEX_NAME_A)])
    fetch = make_fetch({FOLDER_URL: html, download_url("ID-A"): book_a.read_bytes()})
    idx = fetch_index_document(FOLDER_URL, fetch=fetch)
    doc = schedule_find_document(
        idx, year="2026-2027", semester=3, course=2, teacher=TEACHER, fetch=fetch)
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["bookId"] == "ID-A"
    assert doc["data"]["sha"] == hashlib.sha256(book_a.read_bytes()).hexdigest()


def test_not_xlsx_blocked():
    doc = schedule_find_document(
        index_document(drive_folder_html([book_entry("ID-A", INDEX_NAME_A)]).encode(),
                       source=FOLDER_URL, observed_at="t"),
        year="2026-2027", semester=3, course=2, teacher=TEACHER,
        book_bytes=b"<html>error page</html>")
    assert doc["status"] == "blocked"
    assert "book-format" in _codes(doc)


# ---------------------------------------------------------------------------
# Разбор формата A
# ---------------------------------------------------------------------------

def test_format_a_slots(book_a: Path):
    doc = _find(_index_doc(book_a), book_a)
    assert doc["status"] == "ok", doc["issues"]
    d = doc["data"]
    assert d["scope"] == {"year": "2026-2027", "course": 2, "semester": 3, "cell": "C8"}
    assert d["bookId"] == "ID-A"
    slots = d["slots"]
    assert len(slots) == 3  # три назначения Примеровой, чужая Посторонняя не входит

    lab = next(s for s in slots if s["discipline"] == "Дискретная математика")
    assert lab["assignment"] == "Примерова А.С."
    assert lab["kind"] == "laboratory"
    assert lab["groups"] == [{"group": "1", "subgroup": "А", "stream": "Поток ИН"}]
    assert lab["weekday"] == "monday"
    assert lab["localStart"] == "08:00" and lab["localEnd"] == "09:35"
    assert lab["phase"] == "even"  # полоса «чётная неделя» над колонкой C
    assert any("A21" in s for s in lab["sourceRanges"])  # диапазон дня
    assert any("C21" in s for s in lab["sourceRanges"])

    lec = next(s for s in slots if s["discipline"] == "Методы оптимизации")
    assert lec["kind"] == "lecture"
    assert lec["phase"] == "odd"   # метка «(нечётн.)» в ячейке согласована с полосой

    pr = next(s for s in slots if s["discipline"] == "Численные методы")
    assert pr["kind"] == "practice"
    assert pr["phase"] == "every"  # явная «еженедельно» вне полос чётности
    assert pr["bounds"]["from"] == "2026-09-15"  # «с 15.09», год по семестру
    assert pr["bounds"]["to"] is None
    assert pr["groups"] == [{"group": "3", "subgroup": None, "stream": "Поток ИН"}]


def test_teacher_not_found_lists_candidates(book_a: Path):
    doc = _find(_index_doc(book_a), book_a, teacher="Несуществующий")
    assert doc["status"] == "blocked"
    issue = next(i for i in doc["issues"] if i["code"] == "teacher-not-found")
    assert "Примерова А.С." in issue["actual"]
    assert "Посторонняя И.И." in issue["actual"]


def test_teacher_alternate_spelling(book_a: Path):
    """Подтверждённое иное написание имени принимается как аргумент."""
    doc = _find(_index_doc(book_a), book_a, teacher="Примерова")
    assert doc["status"] == "ok", doc["issues"]
    assert len(doc["data"]["slots"]) == 3


def test_teacher_ambiguous(tmp_path: Path):
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",
        (0, 1, 4): "Алгебра (лек.)\nПримерова Б.В.",
    })
    doc = _find(_index_doc(book), book, teacher="Примерова")
    assert doc["status"] == "blocked"
    assert "teacher-ambiguous" in _codes(doc)


def test_unknown_kind_flagged_not_guessed(tmp_path: Path):
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Иностранный язык\nПримерова А.С., ауд.101",
    })
    doc = _find(_index_doc(book), book)
    assert doc["status"] == "ok", doc["issues"]
    s = doc["data"]["slots"][0]
    assert s["kind"] is None
    assert any(a["code"] == "kind-unknown" for a in s["ambiguities"])


def test_different_subgroups_and_times(tmp_path: Path):
    """Подгруппы и время разных назначений не смешиваются."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",  # Пн 08:00, 1А
        (0, 1, 4): "Дискретная математика (лаб.)\nПримерова А.С.",  # Пн 09:45, 1Б
    })
    doc = _find(_index_doc(book), book)
    assert doc["status"] == "ok", doc["issues"]
    slots = sorted(doc["data"]["slots"], key=lambda s: s["localStart"])
    assert slots[0]["localStart"] == "08:00" and slots[0]["groups"][0]["subgroup"] == "А"
    assert slots[1]["localStart"] == "09:45" and slots[1]["groups"][0]["subgroup"] == "Б"


def test_conflict_separate(tmp_path: Path):
    """Два неразличимых назначения (день/время/фаза/группа/подгруппа у одного
    преподавателя, разные дисциплины) — в conflicts, не молчаливый выбор."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",      # чёт, гр.1 А
        (0, 0, 9): "Алгебра (пр.)\nПримерова А.С. (четн.)",             # та же 1А, чёт
    }, col_overrides={9: (1, "А")})
    doc = _find(_index_doc(book), book)
    assert doc["status"] == "ok", doc["issues"]
    assert doc["data"]["conflicts"], doc["data"]
    kinds = {tuple(sorted(c["kind"] for c in doc["data"]["conflicts"][0]["candidates"]))}
    assert kinds == {("laboratory", "practice")}


def test_empty_neighbor_not_filled(tmp_path: Path):
    """Пустая ячейка не наследует назначение соседней."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",  # только 1А
    })
    doc = _find(_index_doc(book), book)
    assert len(doc["data"]["slots"]) == 1


# ---------------------------------------------------------------------------
# Разбор формата B
# ---------------------------------------------------------------------------

@pytest.fixture()
def book_b(tmp_path: Path) -> Path:
    return build_book_b(tmp_path / "b.xlsx", slots={
        (0, 0, 4): "Методы оптимизации (лек.)\nПримерова А.С., ауд.305 (четн.)",
        (0, 1, 5): "Анализ данных (пр.)\nПримерова А.С., в 15.00 с 12.10",
        (1, 2, 6): "Информатика\nПосторонняя А.А., ауд.101",
    })


def _index_b(book_b_path: Path) -> dict:
    html = drive_folder_html([book_entry("ID-B", INDEX_NAME_B)])
    return index_document(html.encode(), source=FOLDER_URL, observed_at="t")


def test_format_b_slots(book_b: Path):
    doc = schedule_find_document(
        _index_b(book_b), year="2026-2027", semester=5, course=3,
        teacher=TEACHER, book_bytes=book_b.read_bytes(), book_id="ID-B")
    assert doc["status"] == "ok", doc["issues"]
    slots = doc["data"]["slots"]
    assert len(slots) == 2
    lec = next(s for s in slots if s["kind"] == "lecture")
    assert lec["phase"] == "even"
    assert lec["groups"] == [{"group": "3", "subgroup": "А", "stream": "Профиль ИН-21"}]
    pr = next(s for s in slots if s["kind"] == "practice")
    assert pr["altTime"]["localStart"] == "15:00"
    assert pr["bounds"]["from"] == "2026-10-12"
    assert pr["phase"] == "unknown"  # метки нет — не еженедельно по умолчанию
    assert any(a["code"] == "phase-unknown" for a in pr["ambiguities"])


def test_format_b_alt_time_column(tmp_path: Path):
    """Альтернативное время из отдельной колонки C."""
    book = build_book_b(tmp_path / "b.xlsx",
                        alt_times={23: "в 17.00"},
                        slots={(0, 1, 4): "Анализ (пр.)\nПримерова А.С."})
    doc = schedule_find_document(
        _index_b(book), year="2026-2027", semester=5, course=3,
        teacher=TEACHER, book_bytes=book.read_bytes())
    assert doc["status"] == "ok", doc["issues"]
    s = doc["data"]["slots"][0]
    assert s["localStart"] == "09:45"
    assert s["altTime"]["localStart"] == "17:00"
    assert "C23" in s["altTime"]["source"]


# ---------------------------------------------------------------------------
# Вычисленные даты
# ---------------------------------------------------------------------------

def test_dates_every_with_bounds(tmp_path: Path):
    """Еженедельный слот с «с 15.09»: развёртка внутри границ, не раньше."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.) с 15.09\nПримерова А.С. еженедельно",  # Вторник
    })
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-10-31")
    assert doc["status"] == "ok", doc["issues"]
    s = doc["data"]["slots"][0]
    dates = [i["date"] for i in s["instances"]]
    # вторники с 15.09 по 31.10; ранние даты до «с …» не разворачиваются
    assert dates[0] == "2026-09-15"
    assert "2026-09-08" not in dates and "2026-09-01" not in dates
    assert dates == ["2026-09-15", "2026-09-22", "2026-09-29",
                     "2026-10-06", "2026-10-13", "2026-10-20", "2026-10-27"]
    assert all(i["localStart"] == "08:00" for i in s["instances"])
    assert all(i["source"] for i in s["instances"])


def test_dates_parity_needs_anchor(tmp_path: Path):
    """Чётная фаза без календарного якоря → блок, даты не угадываются."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",  # Пн, чёт
    })
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-10-31")
    assert doc["status"] == "blocked"
    assert "phase-anchor-missing" in _codes(doc)
    assert doc["data"]["slots"][0]["instances"] == []


def test_dates_parity_with_anchor(tmp_path: Path):
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",        # Пн чёт
        (0, 1, 6): "Алгебра (лек.)\nПримерова А.С.",                     # Пн нечёт
    })
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30",
                even_week_start="2026-09-07")  # понедельник чётной недели
    assert doc["status"] == "ok", doc["issues"]
    ev = next(s for s in doc["data"]["slots"] if s["phase"] == "even")
    od = next(s for s in doc["data"]["slots"] if s["phase"] == "odd")
    assert [i["date"] for i in ev["instances"]] == ["2026-09-07", "2026-09-21"]
    assert [i["date"] for i in od["instances"]] == ["2026-09-14", "2026-09-28"]


def test_early_dates_no_history_not_blocked(tmp_path: Path):
    """Ранние даты без прежней редакции и истории — расчёт по действующей книге."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",  # без «с …»
    })
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30")
    assert doc["status"] == "ok", doc["issues"]
    dates = [i["date"] for i in doc["data"]["slots"][0]["instances"]]
    assert dates[0] == "2026-09-01"  # вторник
    assert len(dates) == 5


def test_known_move_and_cancel_once(tmp_path: Path):
    """Известные перенос и отмена применяются по одному разу."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",  # Вторники
    })
    exc = {"exceptions": [
        {"date": "2026-09-08", "action": "move", "newDate": "2026-09-10",
         "newTime": "15:00", "source": "объявление кафедры №5"},
        {"date": "2026-09-22", "action": "cancel", "source": "объявление кафедры №7"},
    ]}
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30",
                exceptions=exc)
    assert doc["status"] == "ok", doc["issues"]
    inst = doc["data"]["slots"][0]["instances"]
    dates = [i["date"] for i in inst]
    assert "2026-09-08" not in dates      # исходный перенесён
    assert "2026-09-22" not in dates      # отменён
    moved = next(i for i in inst if i["date"] == "2026-09-10")
    assert moved["localStart"] == "15:00"
    assert moved["exception"]["action"] == "move"
    assert moved["exception"]["originalDate"] == "2026-09-08"
    assert moved["exception"]["source"] == "объявление кафедры №5"
    assert len([i for i in inst if i["date"] == "2026-09-10"]) == 1  # ровно один раз


def test_exception_unmatched_blocks(tmp_path: Path):
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",
    })
    exc = {"exceptions": [{"date": "2026-12-25", "action": "cancel", "source": "x"}]}
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30", exceptions=exc)
    assert doc["status"] == "blocked"
    assert "exception-unmatched" in _codes(doc)


def test_exceptions_file_cli_format(tmp_path: Path):
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",
    })
    exc_path = exceptions_json(tmp_path / "exc.json", [
        {"date": "2026-09-15", "action": "cancel", "source": "приказ"}])
    import json
    exc = json.loads(exc_path.read_text(encoding="utf-8"))
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30", exceptions=exc)
    assert doc["status"] == "ok", doc["issues"]
    assert "2026-09-15" not in [i["date"] for i in doc["data"]["slots"][0]["instances"]]


def test_source_refs_to_cells(book_a: Path):
    """Источники — до адреса ячейки/диапазона, не только текст."""
    doc = _find(_index_doc(book_a), book_a)
    for s in doc["data"]["slots"]:
        assert s["sourceRanges"]
        assert all("!" in r for r in s["sourceRanges"])


def test_instances_carry_stable_ordinal(tmp_path: Path):
    """Порядковый номер занятия серии не сдвигается при переносе/отмене."""
    book = build_book_a(tmp_path / "a.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",
    })
    exc = {"exceptions": [
        {"date": "2026-09-08", "action": "move", "newDate": "2026-09-10",
         "source": "объявление кафедры №5"},
        {"date": "2026-09-22", "action": "cancel", "source": "приказ"},
    ]}
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30",
                exceptions=exc)
    inst = doc["data"]["slots"][0]["instances"]
    ords = {i["date"]: i["ordinal"] for i in inst}
    assert ords["2026-09-01"] == 1
    assert ords["2026-09-10"] == 2   # перенесённый вторник — тот же №2
    assert ords["2026-09-15"] == 3
    assert ords["2026-09-29"] == 5   # №4 отменён — без сдвига нумерации
    moved = next(i for i in inst if i["date"] == "2026-09-10")
    assert moved["originalTime"] == {"localStart": "08:00", "localEnd": "09:35"}


# ---------------------------------------------------------------------------
# Полировочные edge-cases: полосы XLSX, область сетки, пределы серий
# ---------------------------------------------------------------------------

def test_unmerged_parity_stripes_merged_group_header_and_blank_rows(tmp_path: Path):
    """Striped-полоса без merge не смещает колонки и не становится потоком;
    горизонтально объединённая группа и пустые строки шапки сохраняют адреса."""
    import openpyxl

    book = build_book_a(tmp_path / "striped.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",  # Пн, 1А
    })
    wb = openpyxl.load_workbook(book)
    ws = wb.active
    # группа 1 реально объединена над подгруппами А/Б, а не повторена текстом
    ws.merge_cells(start_row=15, start_column=3, end_row=15, end_column=4)
    # полосы чётности без merge: подпись повторяется в каждой колонке;
    # строки 17–19 уже пустые, как в выгрузке с разрежённой шапкой
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row == 20:
            ws.unmerge_cells(str(rng))
    for c in (3, 4, 5):
        ws.cell(20, c, "чётная неделя")
    for c in (6, 7, 8):
        ws.cell(20, c, "нечётная неделя")
    wb.save(book)

    doc = _find(_index_doc(book), book)
    assert doc["status"] == "ok", doc["issues"]
    slot = doc["data"]["slots"][0]
    assert slot["groups"] == [{"group": "1", "subgroup": "А", "stream": "Поток ИН"}]
    assert slot["phase"] == "even"
    assert not any(a["code"] == "phase-unknown" for a in slot["ambiguities"])


def test_formatting_only_empty_tail_not_scanned(tmp_path: Path, monkeypatch):
    """Пустой formatting-only хвост XLSX не обходится построчно и не входит
    в область сетки; сами диапазоны дней при этом не обрезаются."""
    import openpyxl
    from openpyxl.styles import PatternFill

    book = build_book_a(tmp_path / "tail.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",
    })
    wb = openpyxl.load_workbook(book)
    ws = wb.active
    ws.cell(2000, 1).fill = PatternFill(fill_type="solid", fgColor="EEEEEE")
    ws.cell(2000, 3, "Легенда вне сетки\nПримерова А.С.")
    wb.save(book)

    calls: list[int] = []
    original = schedule_mod._day_of_row

    def spy(spans, row):
        calls.append(row)
        return original(spans, row)

    monkeypatch.setattr(schedule_mod, "_day_of_row", spy)
    doc = _find(_index_doc(book), book)
    assert doc["status"] == "ok", doc["issues"]
    assert len(doc["data"]["slots"]) == 1
    assert calls  # область не обрезана до нуля
    assert max(calls) < 2000
    assert all("Легенда" not in (s["discipline"] or "") for s in doc["data"]["slots"])


def test_giant_period_blocked_before_series_generation(tmp_path: Path):
    """Гигантский диапазон дат не разворачивается в тысячи ICS-подобных повторов."""
    book = build_book_a(tmp_path / "huge.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",
    })
    doc = _find(_index_doc(book), book,
                period_from="0001-01-01", period_to="9999-12-31")
    assert doc["status"] == "blocked"
    assert "period-too-large" in _codes(doc)
    assert doc["data"]["slots"][0]["instances"] == []


def test_invalid_or_reversed_period_is_structured_block(tmp_path: Path):
    """Невалидная/перевёрнутая граница — issue, а не traceback или пустая серия."""
    book = build_book_a(tmp_path / "bad-period.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",
    })
    for start, end in (("2026-10-31", "2026-09-01"), ("not-a-date", "2026-09-01")):
        doc = _find(_index_doc(book), book, period_from=start, period_to=end)
        assert doc["status"] == "blocked", (start, end)
        assert "period-invalid" in _codes(doc), (start, end)
        assert doc["data"]["slots"][0]["instances"] == []


def test_series_instance_limit_is_enforced(tmp_path: Path, monkeypatch):
    """Потолок экземпляров — отдельный предохранитель даже внутри допустимого периода."""
    monkeypatch.setattr(schedule_mod, "MAX_SERIES_INSTANCES", 2)
    book = build_book_a(tmp_path / "cap.xlsx", slots={
        (1, 0, 9): "Численные методы (пр.)\nПримерова А.С. еженедельно",
    })
    doc = _find(_index_doc(book), book,
                period_from="2026-09-01", period_to="2026-09-30")
    assert doc["status"] == "blocked"
    assert "series-too-large" in _codes(doc)
    assert doc["data"]["slots"][0]["instances"] == []


def test_day_span_detection_ignores_formatting_only_tail(tmp_path: Path):
    """Выбор области дней тоже ограничен значениями: formatting-only строки
    не попадают даже в первичный поиск weekday-меток."""
    import openpyxl
    from openpyxl.styles import PatternFill

    book = build_book_a(tmp_path / "tail-scan.xlsx", slots={
        (0, 0, 3): "Дискретная математика (лаб.)\nПримерова А.С.",
    })
    wb = openpyxl.load_workbook(book)
    ws = wb.active
    ws.cell(5000, 1).fill = PatternFill(fill_type="solid", fgColor="EEEEEE")
    wb.save(book)

    import openpyxl as op
    check_ws = op.load_workbook(book).active
    spans = schedule_mod._find_day_spans(check_ws, schedule_mod._merged_map(check_ws))[1]
    assert spans
    assert max(s["r1"] for s in spans) < 5000
    assert schedule_mod._value_row_end(check_ws) < 5000
