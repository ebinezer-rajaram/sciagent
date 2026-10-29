source /tmp/fsdcheck-scripts/lib.sh
eval "orig_$(declare -f gate)"
gate() {
  orig_gate "$@" | grep -E "REFUSE|PASSES|label"
  local F=$1 td=0 x
  for x in $(git rev-list --full-history origin/v2 -- $P/D0-ctlA1.json); do git merge-base --is-ancestor "$F" "$x" || td=1; done
  local rows code
  rows=$(git log -p --format= origin/v2 -- docs/v2/LEDGER.csv | grep '^+' | grep -o 'fsd-controls freeze [0-9a-f]\{40\}' | awk '{print $3}' | sort -u)
  code=$(git rev-list --full-history --no-merges origin/v2 -- $P/controls.py | sort -u)
  echo "  PROPOSED: label $([ $td = 1 ] && echo 'exploratory deviation' || echo 'preregistered'); chain(rows ever added)==controls.py commits(full history): $([ "$rows" = "$code" ] && echo yes || echo NO)"
  local b; b=$(git rev-list --full-history origin/v2 -- $P/D0-ctlB.json | wc -l); echo "  PROPOSED for ctlB (no D ever): D0-ctlB commits on origin = $b -> preregistered"
}
