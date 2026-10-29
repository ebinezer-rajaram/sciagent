# v8 gate rules transcribed for a scratch repo (no real origin, no gh).
P=.cache/drafts/fsd-controls
n=0
c() { n=$((n+1)); local d="2026-10-29T10:$(printf %02d $((n%60))):$(printf %02d $((n/60))) Z"; d="2026-10-29T10:$(printf %02d $((n%60))):$(printf %02d $((n/60)))Z"; GIT_COMMITTER_DATE=$d GIT_AUTHOR_DATE=$d git commit -q "$@"; }
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t
URL=../origin.git
setup() {
  T=$(mktemp -d /tmp/fsd-v8-check/repo.XXXX); cd "$T"
  git init -q --bare origin.git; git clone -q origin.git w 2>/dev/null; cd w
  git config user.email t@t; git config user.name t; git checkout -q -b v2
  mkdir -p $P docs/v2; echo ".cache/" > .gitignore; echo x > other.txt
  echo "date,item,provider,hours,usd,stage" > docs/v2/LEDGER.csv
  git add .gitignore docs/v2/LEDGER.csv other.txt; c -m base; BASE=$(git rev-parse HEAD); git push -q origin v2
}
row() { echo "2026-10-29,fsd-controls freeze $1 ceiling 7.6 CPU h,dm,0,0,p4" >> docs/v2/LEDGER.csv; git add docs/v2/LEDGER.csv; c -m "row $1"; }
freeze() { echo "$1" > $P/controls.py; echo "crit $1" > $P/criteria.md; git add -f $P/controls.py $P/criteria.md; c -m "F $1"; }
fetchO() { git fetch -q --no-tags $URL +refs/heads/v2:refs/fsd-controls/v2 2>/dev/null || return 1; O=$(git rev-parse refs/fsd-controls/v2); }
chain() { git rev-list --full-history --no-merges --topo-order --reverse $O -- $P/controls.py; }
rows() { git log --full-history --no-merges -p --format= $O -- docs/v2/LEDGER.csv | grep '^+' | grep -v '^+++' | cut -d, -f2 | grep -o 'fsd-controls freeze [0-9a-f]\{40\}' | awk '{print $3}'; }
d0list() { git rev-list --full-history $O -- $P/D0-ctlA1.json; }
label() { local F=$1 x td=0; for x in $(d0list); do git merge-base --is-ancestor $F $x || td=1; done; [ $td = 1 ] && echo exploratory || echo preregistered; }
dcands() { local F=$1 x; for x in $(d0list); do git merge-base --is-ancestor $F $x && echo $x; done; }
# gate $1=F $2=phase(calibrate|test) $3=D
gate() {
  local F=$1 ph=$2 D=${3:-} ok=1 why=""
  fetchO || { echo "  REFUSE fetch-failed"; return 1; }
  local ch; ch=$(chain); [ -n "$ch" ] || { ok=0; why="$why C1-empty"; }
  local arr; mapfile -t arr <<< "$ch"
  for ((j=0; j+1<${#arr[@]}; j++)); do git merge-base --is-ancestor ${arr[j]} ${arr[j+1]} || { ok=0; why="$why C2(${arr[j]:0:7}!<${arr[j+1]:0:7})"; }; done
  local rs; rs=$(rows); for x in $ch; do echo "$rs" | grep -qx $x || { ok=0; why="$why C3-norow(${x:0:7})"; }; done
  [ "${arr[-1]}" = "$F" ] || { ok=0; why="$why C4-not-newest"; }
  local fr; fr=$(git ls-tree -r --name-only $F -- $P)
  echo "$fr" | grep -qE '/(D0-[^/]*\.json|runs-[^/]*|score-[^/]*)$' && { ok=0; why="$why C6-output-in-F"; }
  git diff --quiet $F -- $fr || { ok=0; why="$why C7-worktree"; }
  [ -z "$(git rev-list --full-history $F..HEAD -- $fr)" ] || { ok=0; why="$why C7-HEAD"; }
  [ -z "$(git rev-list --full-history $F..$O -- $fr)" ] || { ok=0; why="$why C7-O"; }
  local dc; dc=$(dcands $F)
  if [ $ph = calibrate ]; then [ -z "$dc" ] || { ok=0; why="$why CAL-D-exists(${dc:0:7})"; }; fi
  if [ $ph = test ]; then
    [ "$dc" = "$D" ] || { ok=0; why="$why T-dcands!={D}"; }
    [ $(git rev-list --parents -n1 $D | wc -w) = 2 ] || { ok=0; why="$why T-not-single-parent"; }
    [ "$(git show --name-only --format= $D)" = "$P/D0-ctlA1.json" ] || { ok=0; why="$why T-D-diff"; }
    [ "$(sha256sum < $P/D0-ctlA1.json 2>/dev/null)" = "$(git cat-file blob $D:$P/D0-ctlA1.json | sha256sum)" ] || { ok=0; why="$why T-bytes"; }
  fi
  [ $ok = 1 ] && echo "  GATE PASSES ($ph), label $(label $F)" || echo "  REFUSE$why ($ph), label $(label $F)"
}
# calibrate's D builder: $1=F $2=D0 content; optional hook $PREPUSH run once before the first push. Sets D, ATTEMPTS.
buildpush() {
  local F=$1 content=$2 a idx
  for a in 1 2 3 4 5; do
    ATTEMPTS=$a
    fetchO || continue
    idx=$(mktemp /tmp/fsd-v8-check/idx.XXXX); rm -f $idx
    GIT_INDEX_FILE=$idx git read-tree $O
    local blob; blob=$(printf '%s\n' "$content" | git hash-object -w --stdin)
    GIT_INDEX_FILE=$idx git update-index --add --cacheinfo 100644,$blob,$P/D0-ctlA1.json
    local tree; tree=$(GIT_INDEX_FILE=$idx git write-tree); rm -f $idx
    D=$(git commit-tree $tree -p $O -m "fsd-controls D0 ctlA1 under $F")
    if [ $a = 1 ] && [ -n "${PREPUSH:-}" ]; then eval "$PREPUSH"; fi
    git push -q $URL $D:refs/heads/v2 2>/dev/null
    fetchO && git merge-base --is-ancestor $D $O && { printf '%s\n' "$content" > $P/D0-ctlA1.json; return 0; }
  done
  return 1
}
