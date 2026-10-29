set -u
source /tmp/fsd-v8-check/lib8.sh
f0() { freeze v0; F0=$(git rev-parse HEAD); git push -q origin v2; row $F0; git push -q origin v2; }
# refined D-candidates: non-merge, descends from F, D0 present in X's tree
dcands2() { local F=$1 x; for x in $(git rev-list --full-history --no-merges $O -- $P/D0-ctlA1.json); do git merge-base --is-ancestor $F $x && git cat-file -e $x:$P/D0-ctlA1.json 2>/dev/null && echo $x; done; }
desc() { for x in "$@"; do printf '    %s parents=%s subject="%s"\n' ${x:0:7} $(($(git rev-list --parents -n1 $x | wc -w)-1)) "$(git log -1 --format=%s $x)"; done; }
echo "=== S2 diagnosis"
setup; f0
buildpush $F0 '{"F":"F0"}'; git pull -q --rebase origin v2
git checkout -q -b f1 $F0; freeze v1; F1=$(git rev-parse HEAD)
git checkout -q v2; GIT_COMMITTER_DATE=2026-10-29T11:00:00Z git merge -q --no-ff --no-edit f1; row $F1; git push -q origin v2
git rm -q $P/D0-ctlA1.json; c -m "drop D0"; git push -q origin v2
fetchO
echo "  C7 hits F1..O on frozen (full-history):"; desc $(git rev-list --full-history $F1..$O -- $(git ls-tree -r --name-only $F1 -- $P))
echo "  C7 hits with --no-merges: [$(git rev-list --full-history --no-merges $F1..$O -- $(git ls-tree -r --name-only $F1 -- $P))]"
echo "  content check git diff --quiet F1 O -- frozen: exit $(git diff --quiet $F1 $O -- $(git ls-tree -r --name-only $F1 -- $P); echo $?)"
echo "  D-candidates (v8 text):"; desc $(dcands $F1)
echo "  D-candidates (refined: --no-merges, D0 present in X): [$(dcands2 $F1)]"
echo "  label: $(label $F1)"
