set -u
source /tmp/fsd-v8-check/lib8.sh
f0() { freeze v0; F0=$(git rev-parse HEAD); git push -q origin v2; row $F0; git push -q origin v2; }

echo "=== S1 honest linear re-freeze"
setup; f0
gate $F0 calibrate | sed 's/^/  [F0 calibrate]/'
buildpush $F0 '{"F":"F0"}'; DA=$D; echo "  D_A pushed in $ATTEMPTS attempt(s)"
gate $F0 test $DA | sed 's/^/  [F0 test D_A]/'
git pull -q --rebase origin v2; git rm -q $P/D0-ctlA1.json; c -m "rm D0"
freeze v1; F1=$(git rev-parse HEAD); git push -q origin v2; row $F1; git push -q origin v2
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'
buildpush $F1 '{"F":"F1"}'; D1=$D
gate $F1 test $D1 | sed 's/^/  [F1 test D1]/'

echo "=== S2 F1 on branch from F0, merged --no-ff"
setup; f0
buildpush $F0 '{"F":"F0"}'; DA=$D; git pull -q --rebase origin v2
git checkout -q -b f1 $F0; freeze v1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit f1; row $F1; git push -q origin v2
rm -f $P/D0-ctlA1.json; git rm -q --cached $P/D0-ctlA1.json 2>/dev/null; git ls-files $P | grep -q D0 && { c -m "drop D0"; git push -q origin v2; }
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'
buildpush $F1 '{"F":"F1"}'; D1=$D
gate $F1 test $D1 | sed 's/^/  [F1 test D1]/'

echo "=== S3 F1 from BASE, merged -X theirs, F0 row deleted"
setup; f0
buildpush $F0 '{"F":"F0"}'; DA=$D; git pull -q --rebase origin v2
git checkout -q -b f1 $BASE; mkdir -p $P; freeze v1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit -X theirs f1 2>&1 | tail -1
grep -v "freeze $F0" docs/v2/LEDGER.csv > /tmp/fsd-v8-check/l.csv; cp /tmp/fsd-v8-check/l.csv docs/v2/LEDGER.csv; git add docs/v2/LEDGER.csv; c -m "ledger tidy"
row $F1; git rm -q $P/D0-ctlA1.json 2>/dev/null && c -m "drop D0"; git push -q origin v2
fetchO; echo "  chain: $(for x in $(chain); do printf '%s ' ${x:0:7}; done)(F0=${F0:0:7} F1=${F1:0:7}); rows ever added: $(for x in $(rows); do printf '%s ' ${x:0:7}; done)"
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'

echo "=== S4 pre-push crash, rerun under F0"
setup; f0
PREPUSH='echo "  (killed before push)"; return 2' buildpush $F0 '{"F":"F0"}'; echo "  first calibrate rc=$?; D0 on disk: $([ -e $P/D0-ctlA1.json ] && echo yes || echo no)"
gate $F0 calibrate | sed 's/^/  [F0 calibrate rerun]/'
buildpush $F0 '{"F":"F0"}'; DA=$D
gate $F0 test $DA | sed 's/^/  [F0 test]/'

echo "=== S5 pre-push crash, then re-freeze F1 (no D ever pushed)"
setup; f0
PREPUSH='return 2' buildpush $F0 '{"F":"F0"}'
freeze v1; F1=$(git rev-parse HEAD); git push -q origin v2; row $F1; git push -q origin v2
gate $F1 calibrate | sed 's/^/  [F1 calibrate]/'
buildpush $F1 '{"F":"F1"}'; D1=$D
gate $F1 test $D1 | sed 's/^/  [F1 test]/'

echo "=== S6 concurrent push between fetch and push"
setup; f0
git clone -q ../origin.git ../pusher 2>/dev/null; (cd ../pusher; git checkout -q v2; git config user.email t@t; git config user.name t)
PREPUSH='(cd ../pusher; git pull -q origin v2; echo y >> other.txt; git commit -qam lane; git push -q origin v2); echo "  (lane pushed $(git -C ../pusher rev-parse --short HEAD) before our first push)"'
buildpush $F0 '{"F":"F0"}'; echo "  rc=$? attempts=$ATTEMPTS D=${D:0:7} parents=$(($(git rev-list --parents -n1 $D | wc -w)-1)) parent=$(git rev-parse --short $D^) diff=[$(git show --name-only --format= $D)]"
gate $F0 test $D | sed 's/^/  [F0 test]/'

echo "=== S7 hand-moved private ref + failed fetch"
setup; f0
echo '{}' > $P/D0-ctlA1.json; git add -f $P/D0-ctlA1.json; c -m D_local; DL=$(git rev-parse HEAD)
git update-ref refs/fsd-controls/v2 $DL
URL=/nonexistent/origin.git
git fetch -q --no-tags $URL +refs/heads/v2:refs/fsd-controls/v2 2>/dev/null; echo "  fetch exit $?; private ref still at D_local: $([ $(git rev-parse refs/fsd-controls/v2) = $DL ] && echo yes || echo no)"
gate $F0 test $DL | sed 's/^/  [F0 test, bad URL]/'
URL=../origin.git
fetchO; echo "  after good forced fetch: ref = server tip: $([ $O = $(git -C ../origin.git rev-parse v2) ] && echo yes || echo no); D_local on it: $(git merge-base --is-ancestor $DL $O && echo yes || echo no)"
