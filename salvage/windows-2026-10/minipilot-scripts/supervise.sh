#!/usr/bin/env bash
# Relaunch each unfinished mini-pilot store (1 worker) whenever it is idle and
# commit headroom >= 5 GB. A store is finished when it holds 21 baseline cells
# (3 truths x 7 systems). The memory samplers' STOP guard is left in place.
# Exits when both stores are finished.
set -u
ROOT=C:/dev/products/sciagent/.cache
MP=/c/dev/products/sciagent-mp
want=21

cells() {  # baseline cells in a store
  python - "$1" <<'EOF'
import json, sys, glob
n = 0
for f in glob.glob(sys.argv[1] + "/details/*.json"):
    if json.load(open(f, encoding="utf-8"))["key"]["config"].get("kind") == "baseline":
        n += 1
print(n)
EOF
}
running() {  # is a pilot process on this root alive?
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { \$_.CommandLine -like '*--root $1 *' }).Count"
}
headroom_ok() {
  [ "$(powershell -NoProfile -Command "\$c=(Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit').CounterSamples; [int](((\$c[1].CookedValue-\$c[0].CookedValue)/1GB) -ge 5)")" = "1" ]
}

n=0
while :; do
  done_all=1
  for store in minipilot minipilot-r2; do
    root=$ROOT/$store
    c=$(cells "$root")
    [ "$c" -ge "$want" ] && continue
    done_all=0
    r=$(running "$root" | tr -d '\r')
    if [ "$r" = "0" ] && headroom_ok; then
      n=$((n+1))
      rm -f "$root/STOP"
      echo "$(date -Iseconds) launch $store (cells=$c)"
      (cd "$MP" && uv run python scripts/pilot.py --root "$root" --ids dev-008,dev-013,dev-017 \
        --seeds 1 --control-seeds 1 --b10f-truths 0 \
        --systems ORACLE,B-lib,B-np,B-rand,B-sym,B-sparse,planted-hint --workers 1 --no-llm \
        > "$ROOT/$store-sup$n-stdout.txt" 2>&1 &)
      sleep 120
    fi
  done
  [ "$done_all" = "1" ] && { echo "$(date -Iseconds) both stores complete"; exit 0; }
  sleep 120
done
