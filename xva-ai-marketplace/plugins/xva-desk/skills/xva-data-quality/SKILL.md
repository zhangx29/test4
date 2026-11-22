---
name: xva-data-quality
description: Check whether XVA inputs landed and passed checks for a date (XVA system, MFL spreadsheet, stored procedures), explain any failure in plain words, and say who to contact. Use when data looks missing or stale, before month-end sign-off, or when another skill reports a data warning.
argument-hint: Optional business date (YYYY-MM-DD)
---

# XVA data quality

1. Call `get_data_status` for the date (or none for the latest).
2. For each source, report: landed or missing, checks passed or failed, run ID, load time, and the note.
3. Translate each failure into what it means for the user, for example: "MFL totals do not tie to the control cell, so CVA figures that come from the MFL run are provisional."
4. Name the owner from [data owners](references/data-owners.md) and the action to request.
5. Give `latest_clean_date` so the user knows the most recent fully reliable date.

Do not retry tools to "fix" data, and do not present numbers for a failed source as final.
