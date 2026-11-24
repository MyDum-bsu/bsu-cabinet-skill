"""collect_plan_raw: используемый JS исполняется в Node против
обезличенного DOM реального контракта (tr[key] + td.<класс>,
tr[vl] + .Value/.ValueResult, .sum-itog, window.StatusPlan).
Mock-объектов результата нет — выполняется тот же исходник."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fixtures.sht import CANON_ROWS, build_sht

from bsu_cabinet.api import _COLLECT_DOM_JS
from bsu_cabinet.plan import plan_diff_document, snapshot_document
from bsu_cabinet.workload import workload_document

node = pytest.mark.skipif(not shutil.which("node"), reason="нет node")

# минимальный DOM для Node: тег/attrs/дети + querySelector(All)
# с поддержкой tag, #id, .class, [attr], потомков и запятых.
DOM_JS = r"""
class El {
  constructor(tag, attrs, text) {
    this.tagName = tag.toUpperCase();
    this.attrs = attrs || {};
    this._text = text || '';
    this.children = [];
  }
  add(child) { this.children.push(child); return child; }
  get textContent() {
    return this._text + this.children.map(c => c.textContent).join('');
  }
  get innerText() { return this.textContent; }
  getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; }
  querySelectorAll(sel) {
    const out = [];
    for (const g of sel.split(',')) {
      const parts = g.trim().split(/\s+/).filter(Boolean).map(simple);
      find(this, parts, 0, out);
    }
    return [...new Set(out)];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function simple(s) {
  const m = { tag: null, id: null, cls: null, attr: null };
  let r = s;
  const a = r.match(/\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/);
  if (a) { m.attr = [a[1], a[2] === undefined ? null : a[2]]; r = r.replace(a[0], ''); }
  const i = r.indexOf('#');
  if (i >= 0) { m.id = r.slice(i + 1); r = r.slice(0, i); }
  const ci = r.indexOf('.');
  if (ci >= 0) { m.cls = r.slice(ci + 1); r = r.slice(0, ci); }
  if (r) m.tag = r.toUpperCase();
  return m;
}
function match(el, m) {
  if (m.tag && el.tagName !== m.tag) return false;
  if (m.id !== null && el.attrs.id !== m.id) return false;
  if (m.cls !== null && !(el.attrs.class || '').split(/\s+/).includes(m.cls)) return false;
  if (m.attr) {
    const v = el.attrs[m.attr[0]];
    if (v === undefined) return false;
    if (m.attr[1] !== null && String(v) !== m.attr[1]) return false;
  }
  return true;
}
function find(root, parts, i, out) {
  for (const d of root.children) {
    if (match(d, parts[i])) {
      if (i === parts.length - 1) out.push(d); else find(d, parts, i + 1, out);
    }
    find(d, parts, i, out);
  }
}
const el = (tag, attrs, text) => new El(tag, attrs, text);
const td = (cls, text) => el('td', cls ? { class: cls } : {}, text);

function table(id, rows, totalsRow) {
  const t = el('table', { id });
  const body = t.add(el('tbody'));
  for (const r of rows) body.add(r);
  if (totalsRow) body.add(totalsRow);
  return t;
}
function semRow(key, cells) {
  const tr = el('tr', { key });
  for (const c of cells) tr.add(c);
  return tr;
}
function semFixture() {
  return [  // контракт Add-строки: дисциплина, факультет, 2 пустых, специальность
    semRow('101', [
      td(null, 'Методы оптимизации'), td(null, 'ФПМИ'), td(), td(), td(null, 'ИН'),
      td('Curs', '3'), el('td', { class: 'FormEducation', vl: '0' }, 'очная'),
      td('CountLine', '1'), td('CountGroups', '0'), td('CountGroupsSub', '3'),
      td('CountStudent_lecture', '90'), td('CountStudent_practical', '45'),
      td('CountStudent_laboratory', '45'), td('CountStudentAlien', '2'),
      td('Clock_lecture_a', '36'), td('Clock_practical_a', '18'),
      td('Clock_ucr_a', '16'), td('Check_control', '14'),
      td('Credit', '18,6'), td('Collocvium', '0'), td('Help_personal', '0'),
    ]),
    semRow('900', [
      td(null, 'Теория вероятностей'), td(null, 'ФПМИ'), td(), td(), td(null, 'ИН'),
      td('Curs', '2'), td('Clock_lecture_a', '40'),
    ]),
  ];
}
function annualRows() {
  const mk = (vl, cat, v, vr) => {
    const tr = el('tr', { vl });
    tr.add(td(null, cat));
    tr.add(td('Value', v));
    tr.add(td('ValueResult', vr));
    return tr;
  };
  return [
    mk('a1', 'Руководство курсовыми работами', '8', ''),
    mk('a2', 'Руководство дипломными работами', '50', ''),
    mk('a3', 'Итоговая аттестация', '12', ''),
    mk('a4', 'Рецензирование квалификационных работ', '15', ''),
    mk('a5', 'Иные виды работ', '', ''),
  ];
}
function itog(cls, val) {
  const tr = el('tr', { class: cls });
  tr.add(td());
  tr.add(td('sum-itog', val));
  return tr;
}

function buildDocument(withTables) {
  const doc = new El('document');
  doc.title = 'Индивидуальный план';
  doc.add(el('h1', {}, 'Индивидуальный план преподавателя на 2026-2027 учебный год'));
  doc.add(el('span', { class: 'teacher-fio' }, 'Примерова Анна Сергеевна'));
  if (withTables.includes('tbl-0'))
    doc.add(table('tbl-0', semFixture(), itog('res-a', '145,6')));
  if (withTables.includes('tbl-10')) {
    const t = table('tbl-10', [], itog('res-a', '69'));
    const tr = el('tr', { key: '102' });
    for (const c of [
      td(null, 'Дискретная математика'), td(null, 'ФПМИ'), td(), td(), td(null, 'ИН'),
      td('Curs', '2'), el('td', { class: 'FormEducation', vl: '0' }, 'очная'),
      td('CountLine', '0'), td('CountGroups', '3'), td('CountGroupsSub', '0'),
      td('CountStudent_practical', '60'),
      td('Clock_practical_a', '24'), td('Clock_laboratory_a', '12'),
      td('Help_group', '6'), td('Exam', '20'),
      td('Collocvium', '0'), td('Help_personal', '0'),
    ]) tr.add(c);
    t.querySelector('tbody').add(tr);
    const tr2 = el('tr', { key: '104' });
    for (const c of [
      td(null, 'Численные методы'), td(null, 'ФПМИ'), td(), td(), td(null, 'ПИ'),
      td('Curs', '2'), el('td', { class: 'FormEducation', vl: '1' }, 'заочная'),
      td('CountGroups', '2'), td('CountGroupsSub', '2'),
      td('Help_group', '7'), td('Collocvium', '0'), td('Help_personal', '0'),
    ]) tr2.add(c);
    t.querySelector('tbody').add(tr2);
    doc.add(t);
  }
  if (withTables.includes('tbl-20'))
    doc.add(table('tbl-20', annualRows(), itog('itog-a', '85')));
  if (withTables.includes('tbl-0') && withTables.includes('tbl-10')
      && withTables.includes('tbl-20')) {
    const tr103 = el('tr', { key: '103' });
    for (const c of [
      td(null, 'Численные методы'), td(null, 'ФПМИ'), td(), td(), td(null, 'ПИ'),
      td('Curs', '2'), el('td', { class: 'FormEducation', vl: '1' }, 'заочная'),
      td('CountGroups', '2'), td('CountGroupsSub', '2'),
      td('Help_now', '3'), td('Collocvium', '0'), td('Help_personal', '0'),
    ]) tr103.add(c);
    doc.querySelector('table#tbl-0 tbody').add(tr103);
    const r4 = el('div', { class: 'res_4' });
    r4.add(el('span', { class: 'sum-itog' }, '299,6'));
    doc.add(r4);
  }
  return doc;
}
"""


def _run_collect(tmp_path: Path, tables: list[str]) -> dict:
    """Исполнить _COLLECT_DOM_JS в Node на синтетическом DOM."""
    runner = tmp_path / "run.js"
    js_file = tmp_path / "collect.js"
    js_file.write_text(_COLLECT_DOM_JS, encoding="utf-8")
    runner.write_text(
        DOM_JS
        + f"""
globalThis.window = {{ StatusPlan: 0 }};
globalThis.document = buildDocument({json.dumps(tables)});
const src = require('fs').readFileSync({json.dumps(str(js_file))}, 'utf-8');
const out = eval('(' + src + ')')();
process.stdout.write(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    res = subprocess.run(["node", str(runner)], check=False, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"collect js упал: {res.stderr}"
    return json.loads(res.stdout)


@node
def test_collect_dom_executes_used_js(tmp_path: Path):
    """Используемый evaluate-JS читает реальный контракт DOM без ошибок."""
    dom = _run_collect(tmp_path, ["tbl-0", "tbl-10", "tbl-20"])
    assert dom["loaded"] is True
    assert dom["loadedSections"] == ["autumn", "spring", "annual"]
    assert dom["statusPlan"] == 0
    assert dom["owner"] == "Примерова Анна Сергеевна"
    assert dom["year"] == "2026-2027"
    autumn = {r["key"]: r for r in dom["sem0"]}
    assert autumn["101"]["fields"]["Clock_lecture_a"] == "36"
    assert autumn["101"]["identity"]["discipline"] == "Методы оптимизации"
    assert autumn["101"]["identity"]["faculty"] == "ФПМИ"
    assert autumn["101"]["identity"]["specialty"] == "ИН"
    assert autumn["101"]["identity"]["course"] == "3"
    assert autumn["101"]["identity"]["studyForm"] == "очная"
    assert {a["category"]: a["value"] for a in dom["annual"]}[
        "Руководство дипломными работами"] == "50"
    assert dom["totals"]["autumn"] == "145,6"
    assert dom["totals"]["annual"] == "85"
    assert dom["totals"]["grand"] == "299,6"


@node
def test_collect_dom_partial_not_loaded(tmp_path: Path):
    """Одна таблица — не снимок: частичное чтение → loaded=false."""
    dom = _run_collect(tmp_path, ["tbl-0"])
    assert dom["loaded"] is False
    assert dom["loadedSections"] == ["autumn"]


@node
def test_collect_dom_full_path_to_diff(tmp_path: Path, ):
    """DOM → raw → snapshot/1 → diff/1 без подмены результатов."""
    dom = _run_collect(tmp_path, ["tbl-0", "tbl-10", "tbl-20"])
    raw = {
        "owner": dom["owner"], "year": dom["year"],
        "statusPlan": dom["statusPlan"], "loaded": dom["loaded"],
        "loadedSections": dom["loadedSections"],
        "rows": dom["sem0"] + dom["sem10"],
        "annual": dom["annual"], "totals": dom["totals"],
    }
    snap = snapshot_document(raw)
    assert snap["data"]["state"] == "populated"
    p = build_sht(tmp_path / "s.xlsx", rows=CANON_ROWS)
    doc = plan_diff_document(workload_document(p), snap)
    ops = {o["op"] for o in doc["data"]["operations"]}
    assert doc["status"] == "ok", doc["issues"]
    assert ops <= {"noop", "preserve"}


def test_collect_plan_raw_summary_failure_does_not_discard_dom(monkeypatch):
    """Сводная вкладка — evidence: её HTTP/JS-сбой не превращает полностью
    прочитанные три раздела ИП в «нет снимка»."""
    from bsu_cabinet import api

    dom = {
        "loaded": True, "loadedSections": ["autumn", "spring", "annual"],
        "owner": "Примерова Анна Сергеевна", "year": "2026-2027",
        "statusPlan": 0, "sem0": [], "sem10": [], "annual": [],
        "totals": {"autumn": "0", "spring": "0", "annual": "0", "grand": "0"},
    }

    class Page:
        def evaluate(self, script, *args):
            assert script == api._COLLECT_DOM_JS
            return dom

    def broken_summary(page, action, model):
        raise RuntimeError("summary unavailable")

    monkeypatch.setattr(api, "send_model", broken_summary)
    raw = api.collect_plan_raw(Page())
    assert raw["loaded"] is True
    assert raw["loadedSections"] == ["autumn", "spring", "annual"]
    assert raw["summaryHtml"] is None
    assert raw["summaryError"] == "RuntimeError"
    assert "error" not in raw


def test_collect_plan_raw_dom_failure_stays_unloaded(monkeypatch):
    """Без основной области DOM снимок остаётся unloaded, даже если сводка прочитана."""
    from bsu_cabinet import api

    class Page:
        def evaluate(self, script, *args):
            raise RuntimeError("page gone")

    monkeypatch.setattr(api, "send_model", lambda *a: {"Title": "<html/>"})
    raw = api.collect_plan_raw(Page())
    assert raw["loaded"] is False
    assert raw["summaryHtml"] == "<html/>"
    assert "error" in raw
