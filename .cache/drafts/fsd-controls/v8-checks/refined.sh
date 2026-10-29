set -u
source /tmp/fsd-v8-check/lib8.sh
# Refinements: C7 on --no-merges plus content check of O; D-candidates = non-merge, F ancestor, D0 present in X.
dcands() { local F=$1 x; for x in $(git rev-list --full-history --no-merges $O -- $P/D0-ctlA1.json); do git merge-base --is-ancestor $F $x && git cat-file -e $x:$P/D0-ctlA1.json 2>/dev/null && echo $x; done; }
eval "$(declare -f gate | sed -e 's/rev-list --full-history \$F\.\.HEAD/rev-list --full-history --no-merges $F..HEAD/' -e 's/rev-list --full-history \$F\.\.\$O -- \$fr)" \]/rev-list --full-history --no-merges $F..$O -- $fr)" ] \&\& git diff --quiet $F $O -- $fr/')"
f0() { freeze v0; F0=$(git rev-parse HEAD); git push -q origin v2; row $F0; git push -q origin v2; }
echo "=== S2 refined"
setup; f0; buildpush $F0 '{"F":"F0"}'; git pull -q --rebase origin v2
git checkout -q -b f1 $F0; freeze v1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit f1; row $F1; git rm -q $P/D0-ctlA1.json; c -m "drop D0"; git push -q origin v2
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'; buildpush $F1 '{"F":"F1"}'; gate $F1 test $D | sed 's/^/  [F1 test]/'
echo "=== S3 refined"
setup; f0; buildpush $F0 '{"F":"F0"}'; git pull -q --rebase origin v2
git checkout -q -b f1 $BASE; mkdir -p $P; freeze v1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit -X theirs f1 >/dev/null 2>&1
grep -v "freeze $F0" docs/v2/LEDGER.csv > /tmp/fsd-v8-check/l.csv; cp /tmp/fsd-v8-check/l.csv docs/v2/LEDGER.csv; git add docs/v2/LEDGER.csv; c -m tidy; row $F1; git rm -q $P/D0-ctlA1.json; c -m "drop D0"; git push -q origin v2
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'
echo "=== S1 refined (sanity)"
setup; f0; buildpush $F0 '{"F":"F0"}'; DA=$D; gate $F0 test $DA | sed 's/^/  [F0 test]/'
git pull -q --rebase origin v2; git rm -q $P/D0-ctlA1.json; c -m "rm D0"; freeze v1; F1=$(git rev-parse HEAD); git push -q origin v2; row $F1; git push -q origin v2
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'; buildpush $F1 '{"F":"F1"}'; gate $F1 test $D | sed 's/^/  [F1 test]/'
