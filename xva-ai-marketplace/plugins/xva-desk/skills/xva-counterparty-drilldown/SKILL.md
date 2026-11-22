---
name: xva-counterparty-drilldown
description: One-counterparty XVA profile - reference data, CVA/DVA/FVA time series and trend, sensitivities by asset class including credit CS01, and P&L drivers over the period. Use when the user names a counterparty and asks how its XVA looks, why it moved, or what it is exposed to.
argument-hint: Counterparty name or ID, optional period (default last 20 business days)
---

# Counterparty drill-down

1. **Resolve the name** with `search_counterparties`. If more than one match, list them and ask which one. Stop until the user answers.
2. **Dates.** Call `get_data_status` to get the end date (latest clean date unless the user gave one). Default start: 20 business days earlier; if unsure, use `month_start_reference`.
3. **Time series.** Call `get_counterparty_xva_timeseries` for the ID and dates. Report the level at the start and end, the high and low of `total` from the returned points, and the change.
4. **Sensitivities.** Call `get_xva_sensitivities` with `counterparty_id` for: CR `cs01` (group_by `bucket`), IR `delta` (group_by `bucket`), and FX `delta` (group_by `risk_factor`). Add EQ or CO `delta` only if those calls return rows.
5. **Drivers.** Call `get_pnl_attribution` with `level: "counterparty"`, `key` = ID, for the same period.
6. **Write it up:** header line (name, rating, sector, book), then three short tables (levels, top sensitivities with units from `provenance.unit`, drivers), each followed by its provenance line.

Do not speculate about the counterparty's credit quality or news. Rating and sector come only from `search_counterparties`.
