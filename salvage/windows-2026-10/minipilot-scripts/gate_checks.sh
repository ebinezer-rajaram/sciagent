#!/usr/bin/env bash
# Overnight: wait until run 2 has 21 baseline cells and run 1 has 12 llm-score
# cells, then (with >= 4 GB headroom) run the read-only gate checks from v2.
# Outputs go to .cache/gate/. Nothing is launched beyond these checks.
set -u
cd /c/dev/products/sciagent
G=.cache/gate; mkdir -p $G
S=/c/Users/Ebinezer/AppData/Local/Temp/claude/c--dev-products-sciagent/9dfb0c58-9eaa-41f7-877a-e9727f0935ad/scratchpad
count() { python -c "
import json,glob
print(sum(1 for f in glob.glob('$1/details/*.json') if json.load(open(f,encoding='utf-8'))['key']['config'].get('kind')=='$2'))"; }
room() { [ "$(powershell -NoProfile -Command "\$c=(Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit').CounterSamples; [int](((\$c[1].CookedValue-\$c[0].CookedValue)/1GB) -ge 4)")" = "1" ]; }
until [ "$(count .cache/minipilot-r2 baseline)" -ge 21 ] && [ "$(count .cache/minipilot llm-score)" -ge 12 ]; do sleep 300; done
until room; do sleep 120; done
echo "$(date -Iseconds) stores complete; running checks" | tee $G/summary.txt
A="--ids dev-008,dev-013,dev-017 --seeds 1 --control-seeds 1 --b10f-truths 0 --systems ORACLE,B-lib,B-np,B-rand,B-sym,B-sparse,planted-hint"
run() { name=$1; shift; "$@" > $G/$name.txt 2>&1; echo "$name exit=$?" | tee -a $G/summary.txt; }
run 1-determinism python $S/compare_runs.py .cache/minipilot .cache/minipilot-r2
run 2-p3_gap uv run python .cache/hunt/A/p3_gap.py --store .cache/minipilot --env fit/5
run 3-store_check uv run python .cache/hunt/B/store_check.py --root .cache/minipilot $A
run 4-report_determinism uv run python .cache/hunt/B/report_determinism.py --root .cache/minipilot -- $A
run 5-pilot_report uv run python scripts/pilot_report.py --root .cache/minipilot $A --interim --out $G/PILOT-minipilot.md
python - >> $G/summary.txt <<'PY'
import csv
for name in ('.cache/minipilot-mem.csv', '.cache/minipilot-r2-mem.csv'):
    mx = {}
    for r in csv.DictReader(open(name, encoding='utf-8-sig')):
        if r.get('kind') == 'proc' and 'spawn' in (r.get('cmd') or ''):
            mx[r['pid']] = max(mx.get(r['pid'], 0.0), float(r['peak_commit_mb'] or 0))
    print(name, 'peak worker commit MB:', max(mx.values()) if mx else None)
PY
echo "$(date -Iseconds) done" | tee -a $G/summary.txt
