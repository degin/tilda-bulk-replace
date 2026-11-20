#!/usr/bin/env python3
"""Генератор заданий для tilda_patch.py из таблицы замен. Вер. 1 (20.11.2026)

    gen.py BEFORE_DIR REPLACEMENTS.json PAGES.json OUT_DIR --project ID

BEFORE_DIR    выгрузка «до» (tilda_blocks.py pull-all)
REPLACEMENTS  {"pairs": [["было","стало"], ...],
               "specific": [{"page": "slug", "record": 123, "elem": "1", "pairs": [["было","стало"]]}]}
              pairs действуют везде, specific только в одном блоке (elem для Zero, можно опустить)
PAGES         {"slug": pageid}
OUT_DIR       по одному файлу-заданию на страницу: OUT_DIR/<slug>.json

Для каждого поля берутся пары, которые в нём есть. Пара, целиком лежащая внутри другой пары
того же поля, отбрасывается. Пары группируются по числу вхождений (count).
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
            if kind == "head":  # head читается заново на каждую операцию: по паре на операцию
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
            raise SystemExit(f"страницы {slug} нет в {args.pages}")
        (out / f"{slug}.json").write_text(
            json.dumps(dict(projectid=args.project, pageid=int(page_ids[slug]), ops=ops),
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{slug}: операций {len(ops)}")
    print(f"страниц {len(tasks)}, операций {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
