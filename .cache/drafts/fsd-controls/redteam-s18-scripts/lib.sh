# Literal transcription of criteria.md v7 gate (L198-211) and test-data rule (L217), for a scratch repo.
P=.cache/drafts/fsd-controls
n=0
c() { n=$((n+1)); d="2026-10-29T10:$(printf %02d $n):00Z"; GIT_COMMITTER_DATE=$d GIT_AUTHOR_DATE=$d git commit -q "$@"; }
setup() {
  T=$(mktemp -d /tmp/fsdgate.XXXX); cd "$T"
  git init -q --bare origin.git; git clone -q origin.git w 2>/dev/null; cd w
  git config user.email t@t; git config user.name t; git checkout -q -b v2
  mkdir -p $P docs/v2; echo ".cache/" > .gitignore
  echo "date,item,provider,hours,usd,stage" > docs/v2/LEDGER.csv
  git add .gitignore docs/v2/LEDGER.csv; c -m base; BASE=$(git rev-parse HEAD)
}
row() { echo "2026-10-29,fsd-controls freeze $1,dm,7.6,0,p4" >> docs/v2/LEDGER.csv; git add docs/v2/LEDGER.csv; c -m "row $1"; }
chain() { git show origin/v2:docs/v2/LEDGER.csv | grep -o 'fsd-controls freeze [0-9a-f]\{40\}' | awk '{print $3}'; }
gate() { # $1 = F, $2 = D (optional)
  local F=$1 D=${2:-} ok=1
  git fetch -q origin
  local ch; ch=$(chain)
  [ "$(echo "$ch" | tail -1)" = "$F" ] || { echo "  REFUSE: F not newest"; ok=0; }
  git merge-base --is-ancestor "$F" origin/v2 || { echo "  REFUSE: F not on origin/v2"; ok=0; }
  [ "$(git log --format=%H -- $P/controls.py)" = "$(echo "$ch" | tac)" ] || { echo "  REFUSE: controls.py log != chain"; ok=0; }
  local fr; fr=$(git ls-tree -r --name-only "$F" -- $P)
  echo "  frozen = $(echo $fr | tr '\n' ' ')"
  [ -z "$(git log --format=%H "$F"..HEAD -- $fr)" ] || { echo "  REFUSE: F..HEAD touches frozen"; ok=0; }
  git diff --quiet "$F" -- $fr || { echo "  REFUSE: working tree != F on frozen"; ok=0; }
  if [ -n "$D" ]; then
    { git merge-base --is-ancestor "$F" "$D" && [ "$F" != "$D" ]; } || { echo "  REFUSE: F not strict ancestor of D"; ok=0; }
    git merge-base --is-ancestor "$D" origin/v2 || { echo "  REFUSE: D not on origin/v2"; ok=0; }
    [ "$(git show --name-only --format= "$D")" = "$P/D0-ctlA1.json" ] || { echo "  REFUSE: D touches other files"; ok=0; }
    git diff --quiet "$D" -- $P/D0-ctlA1.json || { echo "  REFUSE: D0 on disk != D"; ok=0; }
    [ -z "$(git log --format=%H "$D"..HEAD -- $P/D0-ctlA1.json)" ] || { echo "  REFUSE: D..HEAD touches D0"; ok=0; }
  fi
  [ $ok = 1 ] && echo "  GATE PASSES"
  # test-data rule (L217)
  local arr; mapfile -t arr <<< "$ch"; local td=0
  for ((j=0; j+1<${#arr[@]}; j++)); do
    local hits; hits=$(git log --format=%h "${arr[j]}".."${arr[j+1]}" -- $P/D0-ctlA1.json)
    echo "  interval F$j..F$((j+1)) D0 commits: [${hits//$'\n'/ }]"; [ -n "$hits" ] && td=1
  done
  echo "  label: $([ $td = 1 ] && echo 'exploratory deviation' || echo 'PREREGISTERED')"
  echo "  D0 commits on origin/v2 (full history): $(git log --full-history --format=%h origin/v2 -- $P/D0-ctlA1.json | tr '\n' ' ')"
}
