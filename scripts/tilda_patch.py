#!/usr/bin/env python3
"""Точечные правки страницы Tilda по файлу-заданию. Вер. 1 (20.11.2026)

Задание (JSON):
    {"projectid": 123, "pageid": 456, "ops": [ ... ]}

Операции:
  {"op": "field", "record": ID, "tab": "content", "field": "title", "subs": [["старое","новое"]], "count": 1}
      обычный блок: edit_record -> замена подстрок в поле -> update_record (only_field).
  {"op": "zero", "record": ID, "elem": "ключ элемента", "field": "text", "subs": [...]}
      Zero-блок: get_zero_block -> правка поля элемента -> update_zero_block целиком.
  {"op": "field_json", "record": ID, "field": "list", "index": 6, "key": "li_descr", "subs": [...]}
      поле-строка с JSON-массивом (список аккордеона): правит ключ одного элемента (index с 1).
  {"op": "page_settings", "field": "descr", "subs": [...]}
      настройки страницы (title, descr и др.).
  {"op": "head", "old": "фрагмент", "new": "замена", "count": 1}
      page-level head: get_head_code -> замена -> update_page_head_code.

Каждая подстрока обязана встретиться ровно "count" раз (по умолчанию 1), иначе ошибка и стоп.
Перед записью исходное значение поля кладётся в --backup.
Без --apply ничего не пишется (dry-run). Токен: переменная TILDA_MCP_TOKEN.
Публикации нет: страницы публикует владелец.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tilda_blocks import call_tool, load_token  # noqa: E402


def apply_subs(text: str, subs, count: int, label: str) -> str:
    for old, new in subs:
        found = text.count(old)
        if found != count:
            raise SystemExit(f"[{label}] подстрока встречается {found} раз, ожидалось {count}: {old[:90]!r}")
        text = text.replace(old, new)
    return text


def _read_field(op, pageid, projectid, token, label):
    args = {"projectid": projectid, "pageid": pageid, "recordid": op["record"],
            "tab": op.get("tab", "content")}
    data = call_tool("edit_record", args, token)
    values = data.get("record") if isinstance(data, dict) else None
    if not isinstance(values, dict) or op["field"] not in values:
        raise SystemExit(f"[{label}] нет поля {op['field']} в блоке {op['record']}")
    return args, values[op["field"]]


def _write_backup(backup: Path, name: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)
    (backup / name).write_text(text, encoding="utf-8")


def run_op(op: dict, pageid: int, projectid: int, token: str, apply: bool, backup: Path) -> str:
    kind = op["op"]
    label = op.get("name") or kind
    count = op.get("count", 1)

    if kind == "field":
        args, old = _read_field(op, pageid, projectid, token, label)
        new = apply_subs(old, op["subs"], count, label)
        if apply:
            _write_backup(backup, f"before-field-{op['record']}-{op['field']}.json", {op["field"]: old})
            call_tool("update_record", {**args, "fields": {op["field"]: new},
                                        "only_field": op["field"]}, token)
        return f"{label}: {op['field']} {old[:60]!r} -> {new[:60]!r}"

    if kind == "zero":
        args = {"projectid": projectid, "pageid": pageid, "recordid": op["record"]}
        code = call_tool("get_zero_block", args, token)
        elem = code.get(str(op["elem"]))
        if not isinstance(elem, dict):
            raise SystemExit(f"[{label}] нет элемента {op['elem']} в Zero {op['record']}")
        field = op["field"]
        if field not in elem:
            raise SystemExit(f"[{label}] нет поля {field} у элемента {op['elem']}")
        original = copy.deepcopy(code)
        elem[field] = apply_subs(elem[field], op["subs"], count, label)
        if apply:
            _write_backup(backup, f"before-zero-{op['record']}.json", original)
            call_tool("update_zero_block", {**args, "code": code}, token)
        return f"{label}: zero {op['record']} / элемент {op['elem']} | {field} -> {elem[field][:60]!r}"

    if kind == "field_json":
        args, old_raw = _read_field(op, pageid, projectid, token, label)
        items = json.loads(old_raw)
        idx = int(op["index"]) - 1
        if not 0 <= idx < len(items):
            raise SystemExit(f"[{label}] индекс {op['index']} вне списка (элементов {len(items)})")
        old = items[idx][op["key"]]
        new = apply_subs(old, op["subs"], count, label)
        items[idx][op["key"]] = new
        new_raw = json.dumps(items, ensure_ascii=True, separators=(",", ":"))
        if apply:
            _write_backup(backup, f"before-field_json-{op['record']}-{op['field']}.json", {op["field"]: old_raw})
            call_tool("update_record", {**args, "fields": {op["field"]: new_raw},
                                        "only_field": op["field"]}, token)
        return f"{label}: {op['field']}[{op['index']}].{op['key']} {old[:60]!r} -> {new[:60]!r}"

    if kind == "page_settings":
        args = {"projectid": projectid, "pageid": pageid}
        ps = call_tool("get_page_settings", args, token)
        root = ps.get("page") if isinstance(ps, dict) and isinstance(ps.get("page"), dict) else ps
        field = op["field"]
        if field not in root:
            raise SystemExit(f"[{label}] нет поля {field} в настройках страницы")
        old = root[field]
        new = apply_subs(old, op["subs"], count, label)
        if apply:
            _write_backup(backup, f"before-page_settings-{field}.json", {field: old})
            call_tool("update_page_settings",
                      {**args, "date": root.get("date") or ps.get("date"), field: new}, token)
        return f"{label}: {field} {old[:70]!r} -> {new[:70]!r}"

    if kind == "head":
        args = {"projectid": projectid, "pageid": pageid}
        data = call_tool("get_head_code", args, token)
        page = data.get("page") if isinstance(data, dict) else None
        if not isinstance(page, dict) or "headcode" not in page:
            raise SystemExit(f"[{label}] head страницы не получен: {json.dumps(data, ensure_ascii=False)[:200]}")
        hc = page["headcode"]
        new = apply_subs(hc, [[op["old"], op["new"]]], count, label)
        if apply:
            _write_backup(backup, "before-head-headcode.txt", hc)
            call_tool("update_page_head_code", {**args, "headcode": new}, token)
        return f"{label}: head {len(hc)}б -> {len(new)}б"

    raise SystemExit(f"неизвестная операция: {kind}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Правки страницы Tilda по заданию (по умолчанию dry-run)")
    p.add_argument("task", help="файл-задание JSON")
    p.add_argument("--apply", action="store_true", help="записать (без флага только показать)")
    p.add_argument("--backup", default=None, help="папка для снимков «до»")
    args = p.parse_args(argv)

    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    pageid, projectid = int(task["pageid"]), int(task["projectid"])
    backup = Path(args.backup) if args.backup else Path(args.task).parent / "backup"
    backup.mkdir(parents=True, exist_ok=True)

    token = load_token()
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: страница {pageid}, проект {projectid}, операций {len(task['ops'])}")
    for op in task["ops"]:
        print("  " + run_op(op, pageid, projectid, token, args.apply, backup))
    if not args.apply:
        print("ничего не записано (нужен --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
