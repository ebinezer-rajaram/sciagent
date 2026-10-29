source /tmp/fsdcheck-scripts/lib.sh
setup
git push -q origin v2
git clone -q ../origin.git ../pusher 2>/dev/null; cd ../pusher; git config user.email t@t; git config user.name t; git checkout -q v2 2>/dev/null || git checkout -q -b v2 origin/v2
fails=0
for i in $(seq 1 40); do
  echo $i > f; git add f; git commit -q -m "u$i"; git push -q origin v2
  ( cd ../w; git fetch -q origin 2>>../err.txt; echo $? > ../a.$i ) &
  ( cd ../w; git fetch -q origin 2>>../err.txt; echo $? > ../b.$i ) &
  wait
  [ "$(cat ../a.$i)" = 0 ] || fails=$((fails+1)); [ "$(cat ../b.$i)" = 0 ] || fails=$((fails+1))
done
echo "nonzero fetch exits in 40 concurrent pairs: $fails"
sort -u ../err.txt | head -3
