#!/usr/bin/env python3
"""Targeted edits of a Tilda page from a task file. Ver. 1 (2026-11-20)

Task (JSON):
    {"projectid": 123, "pageid": 456, "ops": [ ... ]}

Operations:
  {"op": "field", "record": ID, "tab": "content", "field": "title", "subs": [["old","new"]], "count": 1}
      regular block: edit_record -> substring replacement in the field -> update_record (only_field).
  {"op": "zero", "record": ID, "elem": "element key", "field": "text", "subs": [...]}
      Zero Block: get_zero_block -> edit a field of one element -> update_zero_block as a whole.
  {"op": "field_json", "record": ID, "field": "list", "index": 6, "key": "li_descr", "subs": [...]}
      a string field holding a JSON array (accordion list): edits one key of one item (index starts at 1).
  {"op": "page_settings", "field": "descr", "subs": [...]}
      page settings (title, descr, etc.).
  {"op": "head", "old": "fragment", "new": "replacement", "count": 1}
      page-level head: get_head_code -> replacement -> update_page_head_code.

Every substring must occur exactly "count" times (default 1), otherwise the run stops with an error.
Before writing, the original value is saved into --backup.
Without --apply nothing is written (dry-run). Token: the TILDA_MCP_TOKEN environment variable.
This script never publishes: pages are published by the site owner.
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
            raise SystemExit(f"[{label}] substring occurs {found} times, expected {count}: {old[:90]!r}")
        text = text.replace(old, new)
    return text


def _read_field(op, pageid, projectid, token, label):
    args = {"projectid": projectid, "pageid": pageid, "recordid": op["record"],
            "tab": op.get("tab", "content")}
    data = call_tool("edit_record", args, token)
    values = data.get("record") if isinstance(data, dict) else None
    if not isinstance(values, dict) or op["field"] not in values:
        raise SystemExit(f"[{label}] no field {op['field']} in block {op['record']}")
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
            raise SystemExit(f"[{label}] no element {op['elem']} in Zero block {op['record']}")
        field = op["field"]
        if field not in elem:
            raise SystemExit(f"[{label}] no field {field} in element {op['elem']}")
        original = copy.deepcopy(code)
        elem[field] = apply_subs(elem[field], op["subs"], count, label)
        if apply:
            _write_backup(backup, f"before-zero-{op['record']}.json", original)
            call_tool("update_zero_block", {**args, "code": code}, token)
        return f"{label}: zero {op['record']} / element {op['elem']} | {field} -> {elem[field][:60]!r}"

    if kind == "field_json":
        args, old_raw = _read_field(op, pageid, projectid, token, label)
        items = json.loads(old_raw)
        idx = int(op["index"]) - 1
        if not 0 <= idx < len(items):
            raise SystemExit(f"[{label}] index {op['index']} is outside the list ({len(items)} items)")
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
            raise SystemExit(f"[{label}] no field {field} in page settings")
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
            raise SystemExit(f"[{label}] page head not received: {json.dumps(data, ensure_ascii=False)[:200]}")
        hc = page["headcode"]
        new = apply_subs(hc, [[op["old"], op["new"]]], count, label)
        if apply:
            _write_backup(backup, "before-head-headcode.txt", hc)
            call_tool("update_page_head_code", {**args, "headcode": new}, token)
        return f"{label}: head {len(hc)}b -> {len(new)}b"

    raise SystemExit(f"unknown operation: {kind}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Edit a Tilda page from a task file (dry-run by default)")
    p.add_argument("task", help="task JSON file")
    p.add_argument("--apply", action="store_true", help="write (without the flag only show the changes)")
    p.add_argument("--backup", default=None, help="folder for the \"before\" snapshots")
    args = p.parse_args(argv)

    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    pageid, projectid = int(task["pageid"]), int(task["projectid"])
    backup = Path(args.backup) if args.backup else Path(args.task).parent / "backup"
    backup.mkdir(parents=True, exist_ok=True)

    token = load_token()
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: page {pageid}, project {projectid}, operations {len(task['ops'])}")
    for op in task["ops"]:
        print("  " + run_op(op, pageid, projectid, token, args.apply, backup))
    if not args.apply:
        print("nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
