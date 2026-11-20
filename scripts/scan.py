#!/usr/bin/env python3
"""Поиск и сверка по выгрузке Tilda: значения развёрнуты в обычный текст. Вер. 1 (20.11.2026)

Папка выгрузки: DIR/<slug>/rec*.json, page-settings.json, head.html (результат tilda_blocks.py pull-all).
Офлайн, MCP не трогает.

    scan.py ctx DIR REGEX [--context 60]     найти с контекстом (до замены: где живёт значение)
    scan.py check DIR REPLACEMENTS.json      после замены: остатки старого, наличие нового, валидность JSON-LD

Зачем разворачивать: список аккордеона лежит в поле как JSON-строка с \\u04xx, grep по сырому
файлу такие места пропускает.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ZERO_TEXT_KEYS = ("text", "alt", "title", "descr")
LD_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def walk(root: Path):
    """Выдаёт (slug, kind, record, locator, text). locator годится как основа операции задания."""
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = d.name
        for f in sorted(d.glob("rec*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            rec = int(f.stem[3:])
            if "content" in data and "recordid" in data:  # обычный блок
                for k, v in data["content"].items():
                    if not isinstance(v, str):
                        continue
                    if v.lstrip().startswith("[{"):  # список аккордеона: JSON внутри поля
                        try:
                            items = json.loads(v)
                        except json.JSONDecodeError:
                            items = None
                        if items is not None:
                            for i, item in enumerate(items, 1):
                                for kk, vv in item.items():
                                    if isinstance(vv, str):
                                        yield slug, "json", rec, dict(op="field_json", record=rec, field=k, index=i, key=kk), vv
                            continue
                    yield slug, "field", rec, dict(op="field", record=rec, field=k), v
            else:  # Zero-блок: элементы лежат по ключам верхнего уровня
                for ek, el in data.items():
                    if isinstance(el, dict):
                        for k, v in el.items():
                            if isinstance(v, str) and k in ZERO_TEXT_KEYS:
                                yield slug, "zero", rec, dict(op="zero", record=rec, elem=ek, field=k), v
        ps = d / "page-settings.json"
        if ps.exists():
            data = json.loads(ps.read_text(encoding="utf-8"))
            root_ps = data.get("page", data)
            for k in ("title", "descr"):
                if isinstance(root_ps.get(k), str):
                    yield slug, "ps", 0, dict(op="page_settings", field=k), root_ps[k]
        h = d / "head.html"
        if h.exists():
            yield slug, "head", 0, dict(op="head"), h.read_text(encoding="utf-8")


def cmd_ctx(args) -> int:
    pat = re.compile(args.regex, re.I)
    n = 0
    for slug, kind, rec, loc, text in walk(Path(args.dir)):
        for m in pat.finditer(text):
            s = max(0, m.start() - args.context)
            where = loc.get("elem", loc.get("index", ""))
            print(f"{slug} {kind} {rec} {where} | {text[s:m.end() + args.context]!r}")
            n += 1
    print(f"найдено: {n}")
    return 0


def cmd_check(args) -> int:
    cfg = json.loads(Path(args.replacements).read_text(encoding="utf-8"))
    pairs = [tuple(p) for p in cfg.get("pairs", [])]
    for sp in cfg.get("specific", []):
        pairs += [tuple(p) for p in sp["pairs"]]
    residue, found_new, bad_ld = [], 0, []
    for slug, kind, rec, loc, text in walk(Path(args.dir)):
        for old, new in pairs:
            found_new += text.count(new)
            if old in text.replace(new, ""):  # старое внутри нового не считаем остатком
                residue.append((slug, kind, rec, loc.get("elem", loc.get("index", "")), old))
        if kind == "head":
            for block in LD_RE.findall(text):
                try:
                    json.loads(block)
                except json.JSONDecodeError as exc:
                    bad_ld.append((slug, str(exc)))
    for r in residue:
        print("ОСТАТОК", *r)
    for b in bad_ld:
        print("JSON-LD НЕ РАЗБИРАЕТСЯ", *b)
    print(f"остатков: {len(residue)}, битых JSON-LD: {len(bad_ld)}, вхождений нового: {found_new}")
    return 1 if residue or bad_ld else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ctx")
    s.add_argument("dir")
    s.add_argument("regex")
    s.add_argument("--context", type=int, default=60)
    s.set_defaults(func=cmd_ctx)
    s = sub.add_parser("check")
    s.add_argument("dir")
    s.add_argument("replacements")
    s.set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
