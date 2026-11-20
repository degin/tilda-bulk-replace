---
name: tilda-bulk-replace
description: Use when one fact must change on 5+ Tilda pages via MCP. Pull, generate tasks, dry-run, write, verify.
---

# Bulk value replacement across Tilda pages via MCP

(Русская версия: [SKILL.ru.md](SKILL.ru.md))

A client sends a new version of the facts (a dimension, an area, a seat count), and the old value lives on 15–20 pages: in Zero Blocks, regular blocks, accordion lists (JSON inside a field), the page `description` and JSON-LD. This skill replaces the value everywhere and proves that nothing is left behind. For one or two pages a plain `update_record` is enough.

Scripts: Python 3, stdlib only. Token: environment variable `TILDA_MCP_TOKEN`. Project: `--project` or `TILDA_PROJECT_ID`. The scripts read and write data outside the model context: a Zero Block weighs up to 58,000 characters, and through the agent's MCP tools it would be paid for again on every step.

## Workflow

Paths are relative to the skill folder; `$W` is the working folder of your project that holds snapshots and tasks.

1. **Page list.** `python scripts/tilda_blocks.py pages --project ID`, then write `pages.json` as `{"slug": pageid}` (see `examples/pages.json`).
2. **"Before" snapshots.** `python scripts/tilda_blocks.py pull-all pages.json --project ID --out $W/before`. It pulls blocks, page settings and `head.html`; a rerun skips pages that are already pulled. Commit the snapshots to git: this is your restore point.
3. **Find where the value lives.** `python scripts/scan.py ctx $W/before "100×60"` prints the page, block kind and context. The search runs over unfolded text, because an accordion list sits in the raw file as `\u04xx` escapes and grep over raw files misses it.
4. **Replacement table** `replacements.json` (see `examples/replacements.json`). Rules:
   - use long "old → new" pairs with context ("Main 105×68" → "Main 102×68"), not bare numbers: another field next to it may hold the same number;
   - word stems are safe where inflection is kept (`многофункциональн` → `универсальн`);
   - Tilda stores a non-breaking space as `#nbsp;`: check the `#nbsp;` variant of every pair;
   - a pair that lies entirely inside another pair of the same field is dropped automatically;
   - anything true for one block only goes into `specific`.
5. **Tasks.** `python scripts/gen.py $W/before replacements.json pages.json $W/tasks --project ID` creates `tasks/<slug>.json` (format: `examples/task-page_a.json`). Check how many operations each page got.
6. **Dry-run on every page.** `for f in $W/tasks/*.json; do python scripts/tilda_patch.py $f --backup $W/bak/$(basename $f .json); done`. The script fails if a pair occurs a number of times other than `count`. A dry-run failure means Tilda has drifted from your snapshot: pull again and regenerate the tasks.
7. **Write.** The same loop with `--apply`. The field is saved to `--backup` before every write. Start with one page, then the rest.
8. **"After" check.** Pull the same pages into `$W/after` (step 2) and run `python scripts/scan.py check $W/after replacements.json`. Old values must not be found, new ones must; every `<script type="application/ld+json">` is parsed with `json.loads`. Exit code 1 means leftovers or broken JSON-LD.
9. **The site owner publishes.** Writing to Tilda does not publish pages, and this skill never publishes.

## Task operations

`field` (regular block), `zero` (a Zero Block element), `field_json` (a key of one list item inside a JSON field, `index` starts at 1), `page_settings`, `head`. Every substring must occur exactly `count` times (default 1). Details and fields: the header of `scripts/tilda_patch.py`.

## Pitfalls

- **A new accordion question:** `field_json` edits existing items only. Add a new one in a separate call: read the field, append an item with a new `lid` and `ls`, write the array back as `json.dumps(items, ensure_ascii=True, separators=(',', ':'))`.
- **Keep the visible FAQ and JSON-LD identical:** markup follows content. A new JSON-LD question is inserted by a separate `head` edit anchored at the end of the last answer; `scan.py check` validates the result.
- **`head`:** every operation re-reads the head and writes it whole, so use one pair per operation, strictly sequentially.
- **No parallel writes to one block:** every operation reads the block and writes it whole, so the second write would overwrite the first.
- **Long text in a Zero Block:** check `heightmode` first. Text in Auto Layout is `hug` and the group grows by itself; without Auto Layout, heights and the button under the list have to be moved by hand on every breakpoint.
- **Empty Zero Block** (`tplid 396` without a layout) is treated by the server as a regular block: `pull` handles this itself.
- **Tilda normalizes HTML when a Zero Block is written:** `<br>` becomes `<br />`, `timestamp` turns from a number into a string, and `changed` is updated in page settings. Check pairs that contain `<br>` against the `<br />` form in the "after" snapshot, and treat these differences as normal when comparing "before" and "after" snapshots byte by byte.
- **Rate limit:** the server answers 429; the client waits and retries. Do not raise `--jobs` above 2–3.
- **Rollback:** "before" values are in `--backup` (`before-*` files) and in the `before/` snapshots.
  - A regular block field or an accordion list: `python scripts/tilda_blocks.py set PAGE REC FIELD --from-backup $W/bak/<slug>/before-field-REC-FIELD.json --project ID` (for an accordion the file is `before-field_json-REC-FIELD.json`).
  - A Zero Block, `page_settings` and `head` cannot be rolled back with one command. Reverse the replacement table (swap "old" and "new"), generate tasks with `gen.py` from the "after" snapshot and run them through `tilda_patch.py` in the same order (dry-run, then `--apply`). Verified on live Tilda: the text comes back, and the only differences left are Tilda's own normalization (see the pitfall above).

## Verified

The recipe was run on a project of 18 pages: 138 replacements (`field`, `zero`, `field_json`, `page_settings`, `head`) and 16 list insertions, 0 write errors, and the repeated pull showed no leftovers. Dry-run and write of all replacements took one call of up to 10 minutes each. The scripts were also tested on a live Tilda test page: write, check, rollback.
