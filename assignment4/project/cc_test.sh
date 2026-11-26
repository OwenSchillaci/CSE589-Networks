#!/bin/sh
# T18 test: same H1 -> H3 traffic twice, first without --cc, then with --cc
# usage (from assignment4/, network up): sh project/cc_test.sh [count] [rate]
# turn on the jams you want first, e.g. sh project/bottleneck.sh on

COUNT=${1:-4500}
RATE=${2:-1500}
OUT=.project-run/cc-test
RUN="docker exec mn-stratum python3 -B project/host_exec.py"
rm -rf $OUT
mkdir -p $OUT

for CC in off on; do
    if [ $CC = on ]; then OPT=--cc; else OPT=; fi
    $RUN h3 python3 project/receiver.py --count $COUNT --timeout 5 --output /workdir/$OUT/rx_$CC.csv > $OUT/rx_$CC.log 2>&1 &
    sleep 2
    $RUN h1 python3 project/sender.py 10.0.0.3 --count $COUNT --rate $RATE --payload-size 64 $OPT --output /workdir/$OUT/tx_$CC.csv > $OUT/tx_$CC.log 2>&1
    wait
    echo "===== cc $CC"
    cat $OUT/tx_$CC.log
    tail -1 $OUT/rx_$CC.log
    # give RED time to forget the last jam before the next run
    sleep 2
done
