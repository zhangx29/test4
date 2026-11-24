# New or changed tool: pull request checklist

- [ ] Output model has `provenance` (source, as_of, run_ids, unit, sign_convention)
- [ ] Inputs are typed and bounded; no SQL, paths or URLs accepted
- [ ] Query is parameterised; group/column expressions come from a fixed dictionary
- [ ] Registered with `READ_ONLY` annotations; no side effects
- [ ] In `tools.py`, routed through `_guarded`, listed in `TOOL_ROLES` with the right roles
- [ ] Every argument has a description; short command added to `COMMANDS` in `cli.py`
- [ ] Executive access decided and tested
- [ ] Free text from source systems excluded, or truncated and labelled untrusted
- [ ] Result size capped (`top_n` ≤ 200) and aggregated on the server
- [ ] Tests: tie-out, row cap, unknown date, role access, audit line
- [ ] Three or more golden questions added, one of them a refusal
- [ ] Skills that should use the tool updated; dictionary updated for new terms
- [ ] `pytest` and `validate_customizations.py` pass
- [ ] Model risk informed if the tool changes what numbers users see
