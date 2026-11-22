---
name: xva-pnl-explain
description: Explain XVA P&L for a day or a period (day-on-day, week, month-to-date) by driver - IR, FX, EQ, CO and credit-spread moves, time decay, new trades, amendments, terminations, model or parameter changes, and unexplained - at desk, book or counterparty level. Use for "why did CVA move", "P&L explain", "MTD XVA P&L".
argument-hint: Period and scope, e.g. "yesterday, desk" or "MTD, book XVA-CORP" or "last week, Northwind"
---

# XVA P&L explain

1. **Resolve the period.** Call `get_data_status` for the end date. For "yesterday" or "day", `start` = `previous_business_date`. For month-to-date, `start` = `month_start_reference`. For other periods, ask if unclear. `start` is exclusive, `end` inclusive.
2. **Resolve the scope.** Desk (no key), book (key = book name), or counterparty (resolve with `search_counterparties`, key = ID).
3. **Explain.** Call `get_pnl_attribution` for the period and scope.
4. **Check reliability.** Call `get_definition` with `thresholds`. If `unexplained_flag` is true or `reconciliation_gap` is not zero, say so in the first sentence.
5. **Find concentration (desk or book scope only).** Call `get_xva_movers` for the same dates at counterparty level, `top_n: 5`, and name the counterparties that account for most of the change.
6. **Write it up**: one-sentence answer, then the driver table, then movers. Use the phrasing patterns in [narrative examples](references/narrative-examples.md).

Never attribute a move to a market event the tools did not return. "Credit spreads" means the `market_CR` driver, nothing more specific, until `get_market_moves` exists.
