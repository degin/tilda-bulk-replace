#!/usr/bin/env python3
"""Task generator for tilda_patch.py from a replacement table. Ver. 1 (2026-11-20)

    gen.py BEFORE_DIR REPLACEMENTS.json PAGES.json OUT_DIR --project ID

BEFORE_DIR    the "before" pull (tilda_blocks.py pull-all)
REPLACEMENTS  {"pairs": [["old","new"], ...],
               "specific": [{"page": "slug", "record": 123, "elem": "1", "pairs": [["old","new"]]}]}
              pairs apply everywhere, specific only in one block (elem is for Zero, may be omitted)
PAGES         {"slug": pageid}
OUT_DIR       one task file per page: OUT_DIR/<slug>.json

For every field, the pairs present in it are taken. A pair that lies entirely inside another pair
of the same field is dropped. Pairs are grouped by number of occurrences (count).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan import walk  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("before")
    p.add_argument("replacements")
    p.add_argument("pages")
    p.add_argument("out")
    p.add_argument("--project", type=int, required=True)
    args = p.parse_args(argv)

    cfg = json.loads(Path(args.replacements).read_text(encoding="utf-8"))
    pairs_all = [tuple(x) for x in cfg.get("pairs", [])]
    specific = {}
    for sp in cfg.get("specific", []):
        key = (sp["page"], int(sp["record"]), str(sp["elem"]) if "elem" in sp else None)
        specific.setdefault(key, []).extend(tuple(x) for x in sp["pairs"])
    page_ids = json.loads(Path(args.pages).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tasks, total = {}, 0
    for slug, kind, rec, loc, text in walk(Path(args.before)):
        pairs = [(o, n) for o, n in pairs_all if o in text]
        for k in ((slug, rec, loc.get("elem")), (slug, rec, None)):
            pairs += specific.get(k, [])
        pairs = [(o, n) for o, n in dict.fromkeys(pairs) if o in text]
        if not pairs:
            continue
        pairs = [(o, n) for o, n in pairs if not any(o != o2 and o in o2 for o2, _ in pairs)]
        groups = {}
        for o, n in pairs:
            groups.setdefault(text.count(o), []).append([o, n])
        for cnt, subs in groups.items():
            if kind == "head":  # the head is re-read on every operation: one pair per operation
                for o, n in subs:
                    tasks.setdefault(slug, []).append(dict(op="head", old=o, new=n, count=cnt))
                    total += 1
                continue
            op = dict(loc, subs=subs)
            if cnt != 1:
                op["count"] = cnt
            tasks.setdefault(slug, []).append(op)
            total += 1

    for slug, ops in tasks.items():
        if slug not in page_ids:
            raise SystemExit(f"page {slug} is not in {args.pages}")
        (out / f"{slug}.json").write_text(
            json.dumps(dict(projectid=args.project, pageid=int(page_ids[slug]), ops=ops),
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{slug}: operations {len(ops)}")
    print(f"pages {len(tasks)}, operations {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
