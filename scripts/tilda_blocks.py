#!/usr/bin/env python3
"""Pull Tilda pages through MCP into files and write a single field. Ver. 1 (2026-11-20)

stdlib only. The token comes from the TILDA_MCP_TOKEN environment variable.
Project: --project or the TILDA_PROJECT_ID environment variable.

Commands:
    pages                      list the project's pages (--grep filter)
    ls PAGE                    list a page's blocks
    pull PAGE --out DIR        page to files: rec*.json, page-settings.json, head.html, MANIFEST.json
    pull-all PAGES.json --out DIR   every page from {"slug": pageid}, one folder per page
    fields PAGE REC            field names of a block
    get PAGE REC FIELD         value of a field
    set PAGE REC FIELD (--value | --from-file | --from-backup | --stdin) [--dry-run]

This script never publishes: whatever is written to Tilda is published by the site owner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MCP_URL = os.environ.get("TILDA_MCP_URL", "https://tilda.ru/api/mcp/")
PROTOCOL = "2025-06-18"
ZERO_TPLID = 396
MIN_INTERVAL = 0.35  # the server answers 429 on frequent requests

_throttle_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    with _throttle_lock:
        delta = time.time() - _last_call[0]
        if delta < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - delta)
        _last_call[0] = time.time()


# ---------------------------------------------------------------- MCP client

def _post(payload: dict, token: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = ""
    for attempt in range(6):
        _throttle()
        req = urllib.request.Request(
            MCP_URL, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            last = f"MCP HTTP {exc.code}: {detail[:600]}"
            if exc.code == 429 and attempt < 5:
                wait = 2.0
                try:
                    err = json.loads(detail).get("error", {}).get("data", {})
                    if err.get("retry_at"):
                        wait = max(1.0, min(60.0, float(err["retry_at"]) - time.time()))
                except Exception:
                    pass
                time.sleep(wait)
                continue
            raise SystemExit(last) from None
    else:
        raise SystemExit(last)
    if raw.lstrip().startswith(("event:", "data:")):  # SSE stream response
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    data = json.loads(raw)
    if "error" in data:
        raise SystemExit(f"MCP error: {json.dumps(data['error'], ensure_ascii=False)[:600]}")
    return data


def call_tool(name: str, args: dict, token: str) -> dict:
    """Call a Tilda MCP tool and return the parsed JSON reply."""
    rpc = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": args}}, token)
    result = rpc.get("result", {})
    chunks = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    text = "\n".join(chunks).strip()
    if result.get("isError"):
        raise SystemExit(f"{name}: {text[:800]}")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_text": text}


def load_token() -> str:
    token = os.environ.get("TILDA_MCP_TOKEN")
    if not token:
        raise SystemExit("Set the TILDA_MCP_TOKEN environment variable")
    return token


# ------------------------------------------------------------------- helpers

def short(value, limit: int = 60) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    value = str(value).replace("\n", " ").replace("\r", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def save_json(path: Path, data) -> dict:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    raw = text.encode("utf-8")
    return {"file": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def page_args(args) -> dict:
    return {"projectid": args.project, "pageid": args.page}


def block_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("records") or data.get("blocks") or []
    return []


# ----------------------------------------------------------------- commands

def cmd_pages(args, token: str) -> None:
    data = call_tool("get_pages", {"projectid": args.project}, token)
    if isinstance(data, list):
        pages = data
    elif isinstance(data, dict):
        pages = data.get("pages") or data.get("result") or data.get("items") or []
    else:
        pages = []
    if not pages:
        print(json.dumps(data, ensure_ascii=False)[:1500])
        return
    needle = (args.grep or "").lower()
    print(f"{'pageid':>12}  {'url':<40} title")
    shown = 0
    for p in pages:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or p.get("pageid")
        url = p.get("url") or p.get("alias") or p.get("filename") or ""
        title = p.get("title") or p.get("name") or ""
        if needle and needle not in f"{url} {title}".lower():
            continue
        print(f"{str(pid):>12}  {short(url, 40):<40} {short(title, 60)}")
        shown += 1
    print(f"pages in project: {len(pages)}, shown: {shown}")


def cmd_ls(args, token: str) -> None:
    data = call_tool("get_page_records", page_args(args), token)
    records = block_list(data)
    if not records:
        print(json.dumps(data, ensure_ascii=False)[:1500])
        return
    print(f"page {args.page}, blocks {len(records)}")
    print(f"{'#':>3}  {'recordid':>12}  {'tplid':>5}  title")
    for i, rec in enumerate(records, 1):
        rid = rec.get("recordid") or rec.get("id")
        tpl = rec.get("tplid") or rec.get("type")
        title = rec.get("title") or rec.get("name") or rec.get("descr") or ""
        print(f"{i:>3}  {rid:>12}  {tpl:>5}  {short(title, 70)}")


def pull_page(project: int, page: int, out: Path, token: str, jobs: int = 2) -> str:
    out.mkdir(parents=True, exist_ok=True)
    pa = {"projectid": project, "pageid": page}
    items = block_list(call_tool("get_page_records", pa, token))
    if not items:
        raise SystemExit(f"page {page}: empty block list")

    manifest = {"projectid": project, "pageid": page,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "order": [], "records": {}}
    manifest["page_settings"] = save_json(out / "page-settings.json",
                                          call_tool("get_page_settings", pa, token))
    head = call_tool("get_head_code", pa, token)
    hc = (head.get("page") or {}).get("headcode", "") if isinstance(head, dict) else ""
    (out / "head.html").write_text(hc, encoding="utf-8")

    def pull_one(rec):
        rid = rec.get("recordid") or rec.get("id")
        tpl = int(rec.get("tplid") or rec.get("type") or 0)
        if tpl == ZERO_TPLID:
            try:
                data = call_tool("get_zero_block", {**pa, "recordid": rid}, token)
            except SystemExit as exc:
                # an empty Zero block without a layout is treated as a non-Zero block: read it as a regular one
                if "ZeroBlock" not in str(exc):
                    raise
            else:
                meta = save_json(out / f"rec{rid}.json", data)
                meta.update({"kind": "zero", "tplid": tpl})
                return rid, meta
        content = call_tool("edit_record", {**pa, "recordid": rid, "tab": "content"}, token)
        meta = save_json(out / f"rec{rid}.json", {
            "recordid": rid, "tplid": tpl,
            "content": content.get("record", content) if isinstance(content, dict) else content,
        })
        meta.update({"kind": "record", "tplid": tpl})
        return rid, meta

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for rid, meta in pool.map(pull_one, items):
            manifest["order"].append(rid)
            manifest["records"][str(rid)] = meta
    save_json(out / "MANIFEST.json", manifest)
    total = sum(m["bytes"] for m in manifest["records"].values())
    return f"{out}: blocks {len(items)}, {total / 1024:.0f} KB, head {len(hc)} bytes"


def cmd_pull(args, token: str) -> None:
    print("pulled to " + pull_page(args.project, args.page, Path(args.out).resolve(), token, args.jobs))


def cmd_pull_all(args, token: str) -> None:
    pages = json.loads(Path(args.pages).read_text(encoding="utf-8"))
    root = Path(args.out).resolve()
    for slug, pid in pages.items():
        folder = root / slug
        if (folder / "MANIFEST.json").exists():  # already pulled: a rerun resumes
            print(f"{slug}: already pulled, skipped")
            continue
        print(f"{slug}: {pull_page(args.project, int(pid), folder, token, args.jobs)}", flush=True)


def read_tab(args, token: str) -> tuple[dict, dict]:
    data = call_tool("edit_record", {**page_args(args), "recordid": args.record,
                                     "tab": args.tab}, token)
    values = data.get("record") if isinstance(data, dict) else None
    schema = data.get("fields") if isinstance(data, dict) else None
    return (values if isinstance(values, dict) else {},
            schema if isinstance(schema, dict) else {})


def cmd_fields(args, token: str) -> None:
    values, schema = read_tab(args, token)
    names = list(schema) or list(values)
    print(f"block {args.record}, tab {args.tab}, fields {len(names)}")
    for name in names:
        raw = values.get(name, "")
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        print(f"  {name:<18} {str((schema.get(name) or {}).get('type', '')):<10} "
              f"{len(text):>6}b  {short(text, 50)}")


def cmd_get(args, token: str) -> None:
    values, schema = read_tab(args, token)
    if args.field not in values:
        print(f"no field {args.field}; available: {', '.join(list(values) or list(schema))}",
              file=sys.stderr)
        raise SystemExit(2)
    value = values[args.field]
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def cmd_set(args, token: str) -> None:
    if args.from_backup:  # a before-*.json file from tilda_patch.py: {"field": value}
        backup = json.loads(Path(args.from_backup).read_text(encoding="utf-8"))
        if args.field not in backup:
            raise SystemExit(f"no field {args.field} in {args.from_backup}: {', '.join(backup)}")
        value = backup[args.field]
    elif args.from_file:
        value = Path(args.from_file).read_text(encoding="utf-8")
    elif args.stdin:
        value = sys.stdin.read()
    elif args.value is not None:
        value = args.value
    else:
        raise SystemExit("one of --value, --from-file, --from-backup or --stdin is required")
    payload = {**page_args(args), "recordid": args.record, "tab": args.tab,
               "fields": {args.field: value}, "only_field": args.field}
    if args.dry_run:
        print("dry-run:", json.dumps(dict(payload, fields={args.field: short(value, 200)}),
                                     ensure_ascii=False))
        return
    result = call_tool("update_record", payload, token)
    print(f"written {args.field} to block {args.record}: "
          f"{result.get('_text') or json.dumps(result, ensure_ascii=False)[:200]}")


# --------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    env_project = os.environ.get("TILDA_PROJECT_ID")
    common.add_argument("--project", type=int, default=int(env_project) if env_project else None,
                        help="Tilda project id (or the TILDA_PROJECT_ID environment variable)")

    p = argparse.ArgumentParser(description="Tilda blocks through MCP: pull to files and write a single field")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pages", parents=[common], help="list the project's pages")
    s.add_argument("--grep", help="filter by url or title")
    s.set_defaults(func=cmd_pages)

    s = sub.add_parser("ls", parents=[common], help="list a page's blocks")
    s.add_argument("page", type=int)
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("pull", parents=[common], help="pull a page to files")
    s.add_argument("page", type=int)
    s.add_argument("--out", required=True)
    s.add_argument("--jobs", type=int, default=2)
    s.set_defaults(func=cmd_pull)

    s = sub.add_parser("pull-all", parents=[common], help="pull every page listed in pages.json")
    s.add_argument("pages", help='JSON {"slug": pageid}')
    s.add_argument("--out", required=True)
    s.add_argument("--jobs", type=int, default=2)
    s.set_defaults(func=cmd_pull_all)

    for name, fn, hlp in (("fields", cmd_fields, "field names of a block"),
                          ("get", cmd_get, "value of a single field"),
                          ("set", cmd_set, "write a single block field")):
        s = sub.add_parser(name, parents=[common], help=hlp)
        s.add_argument("page", type=int)
        s.add_argument("record", type=int)
        if name != "fields":
            s.add_argument("field")
        s.add_argument("--tab", choices=["content", "settings"], default="content")
        if name == "set":
            s.add_argument("--value")
            s.add_argument("--from-file")
            s.add_argument("--from-backup", help="a before-*.json snapshot from tilda_patch.py (field rollback)")
            s.add_argument("--stdin", action="store_true")
            s.add_argument("--dry-run", action="store_true")
        s.set_defaults(func=fn)

    args = p.parse_args(argv)
    if args.project is None:
        raise SystemExit("Specify --project or the TILDA_PROJECT_ID environment variable")
    args.func(args, load_token())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
