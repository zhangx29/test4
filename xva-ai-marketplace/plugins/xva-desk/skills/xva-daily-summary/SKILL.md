---
name: xva-daily-summary
description: Produce the morning XVA summary for a business date - data status, top-of-house CVA, DVA and FVA with day-on-day change, P&L attribution by driver, and the top counterparty movers with their main driver. Use for "daily XVA summary", "morning report", "what happened in XVA yesterday".
argument-hint: Optional business date (YYYY-MM-DD); defaults to the latest clean date
---

# XVA daily summary

Follow these steps in order. Every number must come from a tool result.

1. **Data status.** Call `get_data_status` (with the date if the user gave one). Note `previous_business_date`. If `all_passed` is false, start the report with the Data warning block from the template.
2. **Levels.** Call `get_xva_summary` with `level: "desk"` for the date.
3. **Desk P&L explain.** Call `get_pnl_attribution` with `start` = `previous_business_date`, `end` = the date, `level: "desk"`.
4. **Movers.** Call `get_xva_movers` with the same two dates, `level: "counterparty"`, `top_n: 5`.
5. **Thresholds.** Call `get_definition` with `thresholds`.
6. **Drill into big movers.** For each mover whose absolute `change` exceeds `mover_abs_cad`, call `get_pnl_attribution` with `level: "counterparty"` and `key` = its ID; keep the one or two largest drivers.
7. **Write the report** using [the output template](references/output-template.md). Flag the explain if `unexplained_flag` is true or `reconciliation_gap` is not zero.

Do not add commentary on causes beyond the drivers the tools return. If the user wants market context (which rates or spreads moved), say it arrives in phase 3 with `get_market_moves`.
