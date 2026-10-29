set -u
source /tmp/fsdcheck-scripts/lib.sh
common() { # F0 pushed + row; D_A (test data under F0) pushed
  setup
  echo v0 > $P/controls.py; echo crit0 > $P/criteria.md
  git add -f $P/controls.py $P/criteria.md; c -m F0; F0=$(git rev-parse HEAD); git push -q origin v2
  row $F0; git push -q origin v2
  echo '{"F":"F0"}' > $P/D0-ctlA1.json; git add -f $P/D0-ctlA1.json; c -m D_A; DA=$(git rev-parse HEAD); git push -q origin v2
  echo "  [under F0, test gate with D_A]"; gate $F0 $DA
}
echo "=== S1: honest linear re-freeze F1 on top of D_A, then recalibrate D' ==="
common
echo v1 > $P/controls.py; git add -f $P/controls.py; c -m F1; F1=$(git rev-parse HEAD); git push -q origin v2; row $F1; git push -q origin v2
echo "  [under F1, calibrate gate]"; gate $F1
echo '{"F":"F1"}' > $P/D0-ctlA1.json; git add -f $P/D0-ctlA1.json; c -m "D'"; D1=$(git rev-parse HEAD); git push -q origin v2
echo "  [under F1, test gate with D']"; gate $F1 $D1

echo; echo "=== S2: F1 made on a branch from F0 (not from D_A), merged into v2 (as 'git pull' without rebase does) ==="
common
git checkout -q -b f1 $F0; echo v1 > $P/controls.py; git add -f $P/controls.py; c -m F1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit f1; git push -q origin v2; row $F1; git push -q origin v2
git rm -q --cached $P/D0-ctlA1.json; rm $P/D0-ctlA1.json; c -m "drop old D0"; git push -q origin v2
echo "  [under F1, calibrate gate]"; gate $F1
echo '{"F":"F1"}' > $P/D0-ctlA1.json; git add -f $P/D0-ctlA1.json; c -m "D'"; D1=$(git rev-parse HEAD); git push -q origin v2
echo "  [under F1, test gate with D']"; gate $F1 $D1

echo; echo "=== S3: no force-push. F1 on a branch from BASE, merged taking F1's files; F0's LEDGER row deleted by an ordinary commit ==="
common
git checkout -q -b f1 $BASE; mkdir -p $P; echo v1 > $P/controls.py; echo crit1 > $P/criteria.md; git add -f $P/controls.py $P/criteria.md; c -m F1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit -X theirs f1 2>&1 | tail -1
git show HEAD:$P/controls.py | sed 's/^/  merged controls.py: /'
grep -v "freeze $F0" docs/v2/LEDGER.csv > /tmp/fsdcheck-scripts/l.csv; cp /tmp/fsdcheck-scripts/l.csv docs/v2/LEDGER.csv; git add docs/v2/LEDGER.csv; c -m "ledger tidy"
row $F1; git push -q origin v2
git rm -q --cached $P/D0-ctlA1.json; rm $P/D0-ctlA1.json; c -m "drop old D0"; git push -q origin v2
echo "  default   git log -- controls.py: $(git log --format=%h -- $P/controls.py | tr '\n' ' ')"
echo "  --full-history log -- controls.py: $(git log --full-history --format=%h -- $P/controls.py | tr '\n' ' ')  (F0=${F0:0:7} F1=${F1:0:7})"
echo "  [under F1, calibrate gate]"; gate $F1
echo '{"F":"F1"}' > $P/D0-ctlA1.json; git add -f $P/D0-ctlA1.json; c -m "D'"; D1=$(git rev-parse HEAD); git push -q origin v2
echo "  [under F1, test gate with D']"; gate $F1 $D1
