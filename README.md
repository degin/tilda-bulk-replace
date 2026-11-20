# tilda-bulk-replace

A skill (Hermes / Claude format) for replacing one value on dozens of Tilda pages through MCP: pull, task generator, dry-run, write, leftover check. Python 3, stdlib only.

Run on a project of 18 pages: 138 replacements, 0 write errors, a repeated pull with no leftovers.

## Install

Copy the folder into your agent's skills directory (or clone the repository there) as `tilda-bulk-replace`. Pass the Tilda MCP token in the `TILDA_MCP_TOKEN` environment variable; set the project with `--project` or `TILDA_PROJECT_ID`.

## Contents

- `SKILL.md` — workflow and pitfalls (English); `SKILL.ru.md` — the same in Russian.
- `scripts/` — `tilda_blocks.py`, `tilda_patch.py`, `gen.py`, `scan.py`.
- `examples/` — replacement table, page list, a sample task.

The scripts never publish: writing to Tilda does not publish pages, the site owner does.

License: MIT.

---

# tilda-bulk-replace (RU)

Скилл (формат Hermes / Claude) для массовой замены одного значения на десятках страниц Tilda через MCP: выгрузка, генератор заданий, dry-run, запись, сверка остатков. Python 3, только stdlib.

Обкатан на проекте из 18 страниц: 138 замен, 0 ошибок записи, повторная выгрузка без остатков. Установка и состав те же, что выше; описание на русском: `SKILL.ru.md`. Скрипты ничего не публикуют, страницы публикует владелец сайта.
