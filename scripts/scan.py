#!/usr/bin/env python3
"""Search and verify a Tilda pull: values are unfolded into plain text. Ver. 1 (2026-11-20)

Pull folder: DIR/<slug>/rec*.json, page-settings.json, head.html (the result of tilda_blocks.py pull-all).
Offline, does not touch MCP.

    scan.py ctx DIR REGEX [--context 60]     find with context (before a replacement: where the value lives)
    scan.py check DIR REPLACEMENTS.json      after a replacement: leftovers of the old value, presence of the new, JSON-LD validity

Why unfold: an accordion list sits in a field as a JSON string with \\u04xx escapes, so grep over the raw file
misses such places.
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
    """Yield (slug, kind, record, locator, text). The locator is usable as the base of a task operation."""
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = d.name
        for f in sorted(d.glob("rec*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            rec = int(f.stem[3:])
            if "content" in data and "recordid" in data:  # regular block
                for k, v in data["content"].items():
                    if not isinstance(v, str):
                        continue
                    if v.lstrip().startswith("[{"):  # accordion list: JSON inside the field
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
            else:  # Zero Block: elements sit under top-level keys
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
    print(f"found: {n}")
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
            if old in text.replace(new, ""):  # an old value inside a new one is not a leftover
                residue.append((slug, kind, rec, loc.get("elem", loc.get("index", "")), old))
        if kind == "head":
            for block in LD_RE.findall(text):
                try:
                    json.loads(block)
                except json.JSONDecodeError as exc:
                    bad_ld.append((slug, str(exc)))
    for r in residue:
        print("LEFTOVER", *r)
    for b in bad_ld:
        print("JSON-LD DOES NOT PARSE", *b)
    print(f"leftovers: {len(residue)}, broken JSON-LD: {len(bad_ld)}, occurrences of the new value: {found_new}")
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
