source /tmp/fsdcheck-scripts/lib.sh
setup
echo v0 > $P/controls.py; git add -f $P/controls.py; c -m F0; F0=$(git rev-parse HEAD); git push -q origin v2
echo '{}' > $P/D0-ctlA1.json; git add -f $P/D0-ctlA1.json; c -m D_local; D=$(git rev-parse HEAD)   # never pushed
echo "D on origin/v2 before tamper: $(git merge-base --is-ancestor $D origin/v2 && echo yes || echo no)"
git update-ref refs/remotes/origin/v2 $D
git remote set-url origin /nonexistent/origin.git
git fetch origin 2>/dev/null; echo "git fetch origin exit: $?"
echo "D on origin/v2 after failed fetch: $(git merge-base --is-ancestor $D origin/v2 && echo yes || echo no)"
git remote set-url origin ../origin.git; git fetch -q origin; echo "after a successful fetch: $(git merge-base --is-ancestor $D origin/v2 && echo yes || echo no)"
