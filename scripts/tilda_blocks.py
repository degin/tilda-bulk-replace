#!/usr/bin/env python3
"""Выгрузка страниц Tilda через MCP в файлы и точечная запись одного поля. Вер. 1 (20.11.2026)

Только stdlib. Токен берётся из переменной окружения TILDA_MCP_TOKEN.
Проект: --project или переменная TILDA_PROJECT_ID.

Команды:
    pages                      список страниц проекта (--grep фильтр)
    ls PAGE                    блоки страницы
    pull PAGE --out DIR        страница в файлы: rec*.json, page-settings.json, head.html, MANIFEST.json
    pull-all PAGES.json --out DIR   все страницы из {"slug": pageid}, по папке на страницу
    fields PAGE REC            имена полей блока
    get PAGE REC FIELD         значение поля
    set PAGE REC FIELD (--value | --from-file | --from-backup | --stdin) [--dry-run]

Публикации здесь нет: записанное в Tilda публикует владелец сайта.
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
MIN_INTERVAL = 0.35  # сервер отвечает 429 при частых запросах

_throttle_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    with _throttle_lock:
        delta = time.time() - _last_call[0]
        if delta < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - delta)
        _last_call[0] = time.time()


# ---------------------------------------------------------------- MCP-клиент

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
    if raw.lstrip().startswith(("event:", "data:")):  # ответ SSE-потоком
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    data = json.loads(raw)
    if "error" in data:
        raise SystemExit(f"MCP error: {json.dumps(data['error'], ensure_ascii=False)[:600]}")
    return data


def call_tool(name: str, args: dict, token: str) -> dict:
    """Вызвать инструмент Tilda MCP и вернуть распарсенный JSON-ответ."""
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
        raise SystemExit("Задайте переменную окружения TILDA_MCP_TOKEN")
    return token


# ------------------------------------------------------------------- утилиты

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


# ------------------------------------------------------------------ команды

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
    print(f"{'pageid':>12}  {'адрес':<40} заголовок")
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
    print(f"всего страниц в проекте: {len(pages)}, показано: {shown}")


def cmd_ls(args, token: str) -> None:
    data = call_tool("get_page_records", page_args(args), token)
    records = block_list(data)
    if not records:
        print(json.dumps(data, ensure_ascii=False)[:1500])
        return
    print(f"страница {args.page}, блоков {len(records)}")
    print(f"{'#':>3}  {'recordid':>12}  {'tplid':>5}  заголовок")
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
        raise SystemExit(f"страница {page}: пустой список блоков")

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
                # пустой Zero без раскладки сервер считает не-Zero: читаем как обычный блок
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
    return f"{out}: блоков {len(items)}, {total / 1024:.0f} КБ, head {len(hc)} б"


def cmd_pull(args, token: str) -> None:
    print("выгружено в " + pull_page(args.project, args.page, Path(args.out).resolve(), token, args.jobs))


def cmd_pull_all(args, token: str) -> None:
    pages = json.loads(Path(args.pages).read_text(encoding="utf-8"))
    root = Path(args.out).resolve()
    for slug, pid in pages.items():
        folder = root / slug
        if (folder / "MANIFEST.json").exists():  # уже выгружена: повторный запуск продолжает
            print(f"{slug}: уже есть, пропуск")
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
    print(f"блок {args.record}, вкладка {args.tab}, полей {len(names)}")
    for name in names:
        raw = values.get(name, "")
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        print(f"  {name:<18} {str((schema.get(name) or {}).get('type', '')):<10} "
              f"{len(text):>6}б  {short(text, 50)}")


def cmd_get(args, token: str) -> None:
    values, schema = read_tab(args, token)
    if args.field not in values:
        print(f"нет поля {args.field}; доступные: {', '.join(list(values) or list(schema))}",
              file=sys.stderr)
        raise SystemExit(2)
    value = values[args.field]
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def cmd_set(args, token: str) -> None:
    if args.from_backup:  # файл «before-…json» от tilda_patch.py: {"поле": значение}
        backup = json.loads(Path(args.from_backup).read_text(encoding="utf-8"))
        if args.field not in backup:
            raise SystemExit(f"в {args.from_backup} нет поля {args.field}: {', '.join(backup)}")
        value = backup[args.field]
    elif args.from_file:
        value = Path(args.from_file).read_text(encoding="utf-8")
    elif args.stdin:
        value = sys.stdin.read()
    elif args.value is not None:
        value = args.value
    else:
        raise SystemExit("нужно --value, --from-file, --from-backup или --stdin")
    payload = {**page_args(args), "recordid": args.record, "tab": args.tab,
               "fields": {args.field: value}, "only_field": args.field}
    if args.dry_run:
        print("dry-run:", json.dumps(dict(payload, fields={args.field: short(value, 200)}),
                                     ensure_ascii=False))
        return
    result = call_tool("update_record", payload, token)
    print(f"записано {args.field} в блок {args.record}: "
          f"{result.get('_text') or json.dumps(result, ensure_ascii=False)[:200]}")


# --------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    env_project = os.environ.get("TILDA_PROJECT_ID")
    common.add_argument("--project", type=int, default=int(env_project) if env_project else None,
                        help="id проекта Tilda (или переменная TILDA_PROJECT_ID)")

    p = argparse.ArgumentParser(description="Блоки Tilda через MCP: выгрузка в файлы и запись поля")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pages", parents=[common], help="список страниц проекта")
    s.add_argument("--grep")
    s.set_defaults(func=cmd_pages)

    s = sub.add_parser("ls", parents=[common], help="список блоков страницы")
    s.add_argument("page", type=int)
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("pull", parents=[common], help="выгрузить страницу в файлы")
    s.add_argument("page", type=int)
    s.add_argument("--out", required=True)
    s.add_argument("--jobs", type=int, default=2)
    s.set_defaults(func=cmd_pull)

    s = sub.add_parser("pull-all", parents=[common], help="выгрузить все страницы из pages.json")
    s.add_argument("pages", help='JSON {"slug": pageid}')
    s.add_argument("--out", required=True)
    s.add_argument("--jobs", type=int, default=2)
    s.set_defaults(func=cmd_pull_all)

    for name, fn, hlp in (("fields", cmd_fields, "имена полей блока"),
                          ("get", cmd_get, "значение одного поля"),
                          ("set", cmd_set, "записать одно поле блока")):
        s = sub.add_parser(name, parents=[common], help=hlp)
        s.add_argument("page", type=int)
        s.add_argument("record", type=int)
        if name != "fields":
            s.add_argument("field")
        s.add_argument("--tab", choices=["content", "settings"], default="content")
        if name == "set":
            s.add_argument("--value")
            s.add_argument("--from-file")
            s.add_argument("--from-backup", help="снимок before-…json от tilda_patch.py (откат поля)")
            s.add_argument("--stdin", action="store_true")
            s.add_argument("--dry-run", action="store_true")
        s.set_defaults(func=fn)

    args = p.parse_args(argv)
    if args.project is None:
        raise SystemExit("Укажите --project или переменную TILDA_PROJECT_ID")
    args.func(args, load_token())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
