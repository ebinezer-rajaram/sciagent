set -u
source /tmp/fsd-v8-check/lib8.sh
f0() { freeze v0; F0=$(git rev-parse HEAD); git push -q origin v2; row $F0; git push -q origin v2; }
for mode in identical different; do
  echo "=== S8 ($mode on-disk D0, local v2 behind origin by D, one unpushed local commit)"
  setup; f0
  buildpush $F0 '{"F":"F0"}'; DA=$D            # writes identical D0 on disk (ignored, untracked)
  [ $mode = different ] && echo '{"tampered":1}' > $P/D0-ctlA1.json
  echo z >> other.txt; git commit -qam "local unpushed"
  echo "  before: status=[$(git status --porcelain --ignored -- $P | tr '\n' ' ')] HEAD has D: $(git merge-base --is-ancestor $DA HEAD && echo yes || echo no)"
  out=$(git pull --rebase origin v2 2>&1); rc=$?
  echo "  pull --rebase rc=$rc; msg: $(echo "$out" | grep -iE 'error|overwritten|would' | head -2 | tr '\n' ' ')"
  echo "  after: HEAD has D: $(git merge-base --is-ancestor $DA HEAD && echo yes || echo no); on-disk D0 = D's blob: $([ "$(sha256sum < $P/D0-ctlA1.json)" = "$(git cat-file blob $DA:$P/D0-ctlA1.json | sha256sum)" ] && echo yes || echo no); content: $(cat $P/D0-ctlA1.json); status=[$(git status --porcelain --ignored -- $P | tr '\n' ' ')]"
done
echo "=== S9 calibrate when a D under F exists; restore D0 from D"
setup; f0
buildpush $F0 '{"F":"F0"}'; DA=$D; rm $P/D0-ctlA1.json
gate $F0 calibrate | sed 's/^/  [F0 calibrate again]/'
gate $F0 test $DA | sed 's/^/  [F0 test, D0 missing on disk]/'
git show $DA:$P/D0-ctlA1.json > $P/D0-ctlA1.json
gate $F0 test $DA | sed 's/^/  [F0 test, D0 restored by git show]/'
echo "=== S10 git diff --quiet D -- D0 with D0 on disk, untracked, HEAD without D"
echo "  HEAD has D: $(git merge-base --is-ancestor $DA HEAD && echo yes || echo no); tracked: $(git ls-files --error-unmatch $P/D0-ctlA1.json >/dev/null 2>&1 && echo yes || echo no)"
git diff --quiet $DA -- $P/D0-ctlA1.json; echo "  identical bytes: git diff --quiet D -- D0 exit $?"
echo '{"tampered":1}' > $P/D0-ctlA1.json
git diff --quiet $DA -- $P/D0-ctlA1.json; echo "  tampered bytes:  git diff --quiet D -- D0 exit $?"
git diff --stat $DA -- $P/D0-ctlA1.json | tail -1 | sed 's/^/  diff --stat: /'
