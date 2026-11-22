#!/usr/bin/env bash
# Run every golden question through Copilot CLI in non-interactive mode, then score the answers.
# Run from a machine where the plugin is installed and the staging xva-data server is reachable.
# Check agent names with `copilot` then `/agent`, and tool-permission syntax with `copilot help permissions`.
set -euo pipefail

QUESTIONS="${1:-evals/golden_questions.yaml}"
OUT="${2:-evals/out}"
mkdir -p "$OUT"

python - "$QUESTIONS" <<'PY' | while IFS=$'\t' read -r id agent question; do
import sys, yaml
for q in yaml.safe_load(open(sys.argv[1], encoding="utf-8")):
    print(f"{q['id']}\t{q['agent']}\t{q['question']}")
PY
  echo "== $id ($agent)"
  copilot -p "$question" --agent "$agent" -s \
    --allow-tool 'xva-data' \
    --share "$OUT/$id.session.md" > "$OUT/$id.md" || echo "run failed for $id"
done

python evals/score_answer.py "$QUESTIONS" "$OUT"
