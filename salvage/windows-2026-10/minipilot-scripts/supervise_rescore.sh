#!/usr/bin/env bash
# Relaunch the v2 rescore into .cache/minipilot (1 worker) whenever idle and
# headroom >= 5 GB, until 12 llm-score cells exist.
set -u
R=C:/dev/products/sciagent/.cache/minipilot
n=0
scored() { python -c "
import json,glob
print(sum(1 for f in glob.glob('$R/details/*.json') if json.load(open(f,encoding='utf-8'))['key']['config'].get('kind')=='llm-score'))"; }
idle() { [ "$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { \$_.CommandLine -like '*--root $R *' }).Count" | tr -d '\r')" = "0" ]; }
room() { [ "$(powershell -NoProfile -Command "\$c=(Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit').CounterSamples; [int](((\$c[1].CookedValue-\$c[0].CookedValue)/1GB) -ge 5)")" = "1" ]; }
while [ "$(scored)" -lt 12 ]; do
  if idle && room; then
    n=$((n+1)); rm -f "$R/STOP"; echo "$(date -Iseconds) launch rescore (scored=$(scored))"
    (cd /c/dev/products/sciagent && uv run python scripts/pilot.py --root $R --ids dev-008,dev-013,dev-017 --seeds 1 --control-seeds 1 --b10f-truths 0 --systems ORACLE,B-lib,B-np,B-rand,B-sym,B-sparse,planted-hint --workers 1 --rescore-llm --no-baselines > C:/dev/products/sciagent/.cache/minipilot-rescore$n-stdout.txt 2>&1)
  fi
  sleep 120
done
echo "$(date -Iseconds) rescore complete: $(scored) scored"
