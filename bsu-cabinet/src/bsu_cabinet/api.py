"""Прямые вызовы API кабинета через SendModel в контексте страницы.

Все действия — POST JSON (см. docs/bsu-cabinet/cabinet-map.md). SendModel
существует на любой странице кабинета после логина.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page


def send_model(page: Page, action: str, model: dict[str, Any]) -> Any:
    """SendModel(action, model) → распарсенный ответ сервера.

    Возвращает res как в callback сайта (dict/list/str). 'NotAuthorize'
    пробрасывается как PermissionError — значит, сессия умерла, нужен relogin.
    """
    res = page.evaluate(
        """([action, model]) => new Promise((resolve) => {
            $.ajax({
                url: '/' + action, type: 'POST',
                data: JSON.stringify(model), dataType: 'json',
                contentType: 'application/json', cache: false,
                success: resolve,
                error: (xhr) => resolve({__error__: xhr.status + ' ' + xhr.responseText.slice(0, 500)}),
            });
        })""",
        [action, model],
    )
    if res == "NotAuthorize":
        raise PermissionError("Сессия истекла — нужен повторный вход")
    if isinstance(res, dict) and "__error__" in res:
        raise RuntimeError(f"{action}: {res['__error__']}")
    return res


def select_faculty(page: Page, faculty_id: str) -> dict[str, str]:
    """Опции специальностей и дисциплин факультета (HTML-строки <option>)."""
    res = send_model(page, "Teacher/PersonalPlanSelectFaculty", {"Faculty": faculty_id})
    return {"spec_html": res.get("Title", ""), "disp_html": res.get("obj", "")}


def option_id(options_html: str, needle: str) -> tuple[str, str]:
    """(value, text) первой опции, чей текст содержит needle (без учёта регистра)."""
    import re

    for m in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', options_html, re.S):
        value, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if needle.lower() in text.lower():
            return value, text
    raise LookupError(f"Опция «{needle}» не найдена")


def plan_add_row(page: Page, faculty: str, spec: str, disp: str, semestr: int,
                 faculty_text: str, spec_text: str, disp_text: str) -> Any:
    """PersonalPlanMainAdd → ответ сервера (res.obj.Key — id новой строки)."""
    return send_model(
        page,
        "Teacher/PersonalPlanMainAdd",
        {
            "Faculty": faculty,
            "Disp": disp,
            "Spec": spec,
            "Semestr": semestr,
            "FacultyText": faculty_text,
            "DispText": disp_text,
            "SpecText": spec_text,
        },
    )


def plan_save_row(page: Page, key: str, fields: dict[str, Any]) -> Any:
    """PersonalPlanMainSave — fields по списку list_ss из cabinet-map.md."""
    return send_model(page, "Teacher/PersonalPlanMainSave", {"Key": key, **fields})


def plan_save_year_row(page: Page, key: str, value: float | str) -> Any:
    """Быстрое обновление годового вида работ (tbl-20): только Value."""
    return send_model(page, "Teacher/PersonalPlanMainSaveAll_Plan", {"Key": key, "Value": value})


def card_save(page: Page, key: str, status: int, items: list[dict[str, Any]]) -> Any:
    """CardOneSave — сохранить таблицу status целиком для ключа Key."""
    return send_model(page, "Teacher/CardOneSave", {"Key": key, "Status": status, "Items": items})


def card_keys(page: Page) -> list[dict[str, str]]:
    """Ключи строк ИП в дереве карточки (.main-tree div[vl])."""
    return page.evaluate(
        """() => [...document.querySelectorAll('.main-tree div[vl]')]
            .map(d => ({vl: d.getAttribute('vl'), text: d.innerText.trim()}))"""
    )


# DOM-контракт ИП по site-js кабинета:
#   семестры  tbl-0/tbl-10 tbody tr[key], поля — ячейки td.<имя> текстом;
#             td[0]=дисциплина, td[1]=факультет, td[4]=специальность
#   годовые   tbl-20 tbody tr[vl], .Value / .ValueResult текстом,
#             категория — первая td
#   итоги     .res-a .sum-itog (план) у tbl-0/tbl-10, .itog-a .sum-itog у tbl-20,
#             сводный — .res_4 .sum-itog (HTML из PersonalPlanTable4 res.Title)
#   статус    глобальная window.StatusPlan; ФИО/год — только подтверждённые
#             элементы, иначе null (нормализатор заблокирует сам)
_COLLECT_DOM_JS = """() => {
  const q = (sel, root) => (root || document).querySelector(sel);
  const qa = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const txt = (el) => (el ? (el.textContent || '').trim() : null);
  const SEM_FIELDS = ['Curs', 'CountLine', 'CountGroups', 'CountGroupsSub',
    'CountStudent_lecture', 'CountStudent_practical', 'CountStudent_laboratory',
    'CountStudentAlien', 'Clock_lecture_a', 'Clock_lecture_b',
    'Clock_practical_a', 'Clock_practical_b', 'Clock_laboratory_a',
    'Clock_laboratory_b', 'Clock_ucr_a', 'Clock_ucr_b', 'Collocvium',
    'Help_now', 'Help_personal', 'Help_group', 'Check_control', 'Exam',
    'Credit', 'Description'];
  const semRows = (id, semester) => {
    const t = q('table#' + id);
    if (!t) return null;
    return qa('tbody tr[key]', t).map(tr => {
      const tds = qa('td', tr);
      const fields = {};
      SEM_FIELDS.forEach(f => { fields[f] = txt(q('.' + f, tr)); });
      // FormEducation: код в атрибуте vl ячейки, текст — «очная»/«заочная»
      const fe = q('.FormEducation', tr);
      fields.FormEducation = fe ? (fe.getAttribute('vl') ?? fields.FormEducation) : null;
      return {
        key: tr.getAttribute('key'), semester,
        identity: {
          discipline: txt(tds[0]), faculty: txt(tds[1]), specialty: txt(tds[4]),
          course: txt(q('.Curs', tr)), studyForm: txt(q('.FormEducation', tr)),
        },
        fields,
      };
    });
  };
  const annual = () => {
    const t = q('table#tbl-20');
    if (!t) return null;
    return qa('tbody tr[vl]', t).map(tr => ({
      key: tr.getAttribute('vl'),
      category: txt(qa('td', tr)[0]),
      value: txt(q('.Value', tr)),
      valueResult: txt(q('.ValueResult', tr)),
    }));
  };
  const sumItog = (sel) => { const e = q(sel); return e ? txt(e) : null; };
  const loadedSections = [];
  if (q('table#tbl-0')) loadedSections.push('autumn');
  if (q('table#tbl-10')) loadedSections.push('spring');
  if (q('table#tbl-20')) loadedSections.push('annual');
  const st = window.StatusPlan;
  const head = txt(q('h1')) || document.title || '';
  const ym = head.match(/20\\d\\d\\s*[-–—/]\\s*20\\d\\d/);
  return {
    // одна таблица ≠ снимок: нужны все три раздела
    loaded: loadedSections.length === 3,
    loadedSections,
    statusPlan: (typeof st === 'number' && isFinite(st)) ? st : null,
    owner: txt(q('[data-owner], .teacher-fio, #TeacherFio')),
    year: ym ? ym[0].replace(/\\s+/g, '') : null,
    sem0: semRows('tbl-0', 0),
    sem10: semRows('tbl-10', 1),
    annual: annual(),
    totals: {
      autumn: sumItog('#tbl-0 .res-a .sum-itog'),
      spring: sumItog('#tbl-10 .res-a .sum-itog'),
      annual: sumItog('#tbl-20 .itog-a .sum-itog'),
      grand: sumItog('.res_4 .sum-itog'),
    },
  };
}"""


def collect_plan_raw(page: Page) -> dict[str, Any]:
    """Только чтение: сырой снимок ИП для `bsu-browser snapshot plan`.

    PersonalPlanTable4 отдаёт res.Title — HTML сводки; он сохраняется как
    evidence, но поля итогов из него не угадываются: итоги читаются из
    DOM (.sum-itog). Нераспознанная/неполная структура → loaded=False,
    это «не прочитано», а не «план пуст».
    """
    out: dict[str, Any] = {"loaded": False}
    try:
        summary = send_model(page, "Teacher/PersonalPlanTable4/0", {})
        out["summaryHtml"] = summary.get("Title") if isinstance(summary, dict) else None
        dom = page.evaluate(_COLLECT_DOM_JS)
    except Exception as exc:  # нет страницы/сессии — снимок недостоверен
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out.update({
        "loaded": bool(dom.get("loaded")),
        "loadedSections": dom.get("loadedSections"),
        "owner": dom.get("owner"),
        "year": dom.get("year"),
        "statusPlan": dom.get("statusPlan"),
        "rows": [r for rows in (dom.get("sem0"), dom.get("sem10"))
                 for r in (rows or [])],
        "annual": dom.get("annual") or [],
        "totals": dom.get("totals") or {},
    })
    return out


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
