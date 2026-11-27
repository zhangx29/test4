---
name: xva-pnl-explain
description: Explain XVA P&L for a day or a period (day-on-day, week, month-to-date) by driver group and driver - counterparty and BMO credit, funding, rates, FX, commodities, carry, new trades, model changes, temporary items and unexplained - for total XVA, CVA or FVA, at desk, legal entity, book or counterparty level. Use for "why did CVA move", "P&L explain", "MTD XVA P&L".
argument-hint: Period and scope, e.g. "yesterday, desk" or "MTD, book XVA-CORP" or "last week, Northwind"
---

# XVA P&L explain

1. **Resolve the period.** Call `get_data_status` for the end date. For "yesterday" or "day", `start` = `previous_business_date`. For month-to-date, `start` = `month_start_reference`. For other periods, ask if unclear. `start` is exclusive, `end` inclusive.
2. **Resolve the scope.** Desk (no key), book (key = book name), or counterparty (resolve with `search_counterparties`, key = ID).
3. **Explain.** Call `get_pnl_attribution` for the period and scope (`component` XVA unless the user asks for CVA or FVA). Lead with `actual_pl` when it is present; it is the P&L system's actual P&L. `xva_change` is the change in official values and can differ in sign convention: show it only if asked, and never subtract one from the other yourself.
4. **Check reliability.** Call `get_definition` with `thresholds`. If `unexplained_flag` is true or the absolute `reconciliation_gap` is more than 1, say so in the first sentence.
5. **Find concentration (desk or book scope only).** Call `get_xva_movers` for the same dates at counterparty level, `top_n: 5`, and name the counterparties that account for most of the change.
6. **Write it up**: one-sentence answer, then a table of `groups` (largest first is fine; keep Unexplained last), then the two or three largest `drivers` inside the largest groups, then movers. Use the phrasing patterns in [narrative examples](references/narrative-examples.md).

Never attribute a move to a market event the tools did not return. Describe a driver by its name and its `get_definition` entry only (for example "USD rates (USD_RATES)"), nothing more specific, until `get_market_moves` exists. If a definition says CONFIRM, don't add detail beyond its words.
