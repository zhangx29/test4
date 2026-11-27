# Output template: XVA daily summary

Use this structure exactly. Replace everything in angle brackets. Omit the Data warning block when all checks passed.

```markdown
**Data warning:** <source> <failed checks / not landed>: <note>. Figures that depend on it are provisional.

**XVA on <date>: total <total>k, <up/down> <change>k on the day, driven mainly by <driver 1> (<value>k) and <driver 2> (<value>k).**

| Measure | <previous date> | <date> | Change |
| --- | ---: | ---: | ---: |
| <one row per component the tool returns, e.g. CVA, CVACO, DVA, FVA> | | | |
| Total XVA | | | |

Source: <provenance.source> · as of <date> · run <run_ids> · CAD thousands

**P&L explain (desk)**: actual P&L <actual_pl>k

| Driver group | P&L (k) |
| --- | ---: |
| <one row per group the tool returns, in its order> | |
| Unexplained | |

Largest drivers: <driver> (<value>k), <driver> (<value>k).

<If unexplained_flag: "Unexplained of <x>k breaches the threshold (<threshold>); treat the explain with caution.">

**Top movers**

| Counterparty | Change (k) | Main driver |
| --- | ---: | --- |
| <name> | | <driver> (<value>k) |

Source: <provenance line>
```

Rules: amounts in CAD thousands with one decimal; sort movers by absolute change; at most five movers.
