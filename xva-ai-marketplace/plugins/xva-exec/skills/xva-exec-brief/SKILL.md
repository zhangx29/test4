---
name: xva-exec-brief
description: One-page XVA brief for senior management - headline, top-of-house CVA, DVA and FVA, day and month-to-date P&L with the main drivers, book split and largest sensitivities, at desk and book level only. Use for "XVA brief", "XVA update", "how did XVA do".
argument-hint: Optional date (YYYY-MM-DD); defaults to the latest clean date
---

# XVA executive brief

1. `get_data_status` (date if given). Keep `previous_business_date` and `month_start_reference`.
2. `get_xva_summary` with `level: "desk"` and again with `level: "book"`.
3. `get_pnl_attribution` at desk level for the day (`start` = `previous_business_date`) and for month-to-date (`start` = `month_start_reference`).
4. `get_xva_sensitivities` grouped by `risk_factor` for IR `delta` and CR `cs01` (desk level, `top_n: 3`).
5. Write the brief with [the brief template](references/brief-template.md): at most five bullets and one table.

Never request counterparty-level data. If the executive asks about a named client, say that name-level detail sits with the XVA desk and offer the book view.
