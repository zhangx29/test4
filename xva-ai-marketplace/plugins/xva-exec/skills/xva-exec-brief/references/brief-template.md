# Brief template

```markdown
**XVA brief, <date>** <"(provisional: <source> failed checks)" if any check failed>

- **Headline:** total XVA <level>k, <+/-><day change>k on the day, <+/-><MTD>k month-to-date.
- **Day:** driven by <driver> (<value>k) and <driver> (<value>k).
- **Month-to-date:** driven by <driver> (<value>k); <note model change or new trades if material>.
- **Where:** <book> carries <share>% of total XVA.
- **Risk:** a 1bp rise in rates changes XVA by <IR delta total>k; a 1bp widening in counterparty spreads by <CS01 total>k.

| | CVA | DVA | FVA | Total |
| --- | ---: | ---: | ---: | ---: |
| <book 1> | | | | |
| <book 2> | | | | |
| Desk | | | | |

Source: <provenance line> · CAD thousands
```

The share in "Where" may be computed from the book and desk totals returned by the tools; show it as a whole percentage.
