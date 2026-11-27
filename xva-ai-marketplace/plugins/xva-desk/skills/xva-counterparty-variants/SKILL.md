---
name: xva-counterparty-variants
description: Compare an official counterparty with its test variants (codes containing XVATEST, e.g. CHT with XVATEST-CHT and XVATEST-CHT2) - CVA, DVA, FVA and total XVA balances, sensitivities, P&L by driver group, and what each variant calculates differently. Use for "how do the CHT variants stack up", "compare XVATEST-CHT2 with CHT", "what does the zero-threshold variant do to FVA".
argument-hint: Counterparty or variant ID, optional date and component (XVA, CVA or FVA)
---

# Counterparty variants

1. **Resolve the counterparty.** Use the ID the user gave (official or XVATEST variant). If they gave a name, call `search_counterparties` (variants never appear there; search for the official counterparty).
2. **Date.** Call `get_data_status` for the date (latest clean date unless the user gave one). Report data warnings for the official sources as usual; a note on `xva_variants` only affects the variant figures.
3. **Compare.** Call `compare_counterparty_variants` with the counterparty, the date and `component` (XVA unless the user asks for CVA or FVA). For a period's P&L, pass `pnl_start`.
4. **Write it up:**
   - One sentence first: how far each variant's total XVA is from the official one (`total_vs_official`), and which component drives it.
   - The balances table (official row first, labelled official), then one line per variant quoting its `method` word for word. If a method is empty, say it is not described in variants.yaml and name the `owner`.
   - Sensitivities: the asset classes where the variants differ, then the top `risk_factor_differences`. Units from the row's `unit`.
   - P&L by driver group with actual P&L, if loaded.
   - Everything in `missing`, as "Not loaded: ...". A counterparty missing from a sensitivity row has no data, not a zero.
5. End with the tool's `note` in one line: variants are test calculations and are never part of any total.

Never add a variant to a desk, entity or book figure, and never present a variant as the official number. Do not explain why a variant differs beyond its `method` text and the figures returned.
