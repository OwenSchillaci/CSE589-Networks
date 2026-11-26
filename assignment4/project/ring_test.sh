#!/bin/sh
# T17 test: 4 rank ring H1 -> H3 -> H2 -> H4 -> H1 (every hop crosses a spine)
# usage (from assignment4/, network up):
#   sh project/ring_test.sh [count] [rate] [slow_rank] [slow_rate] [late_rank] [late_seconds]
#   sh project/ring_test.sh                  balanced, 320 packets per step at 300 pkt/s
#   sh project/ring_test.sh 320 300 1 150    rank 1 sends at half speed
#   sh project/ring_test.sh 320 300 -1 0 2 1 rank 2 starts 1 s late
#   7th arg cc: 1 = ranks slow down on SYNC/CONG feedback (T19)

COUNT=${1:-320}
RATE=${2:-300}
SLOW=${3:--1}
SLOWRATE=${4:-150}
LATE=${5:--1}
LATES=${6:-1.0}
CC=${7:-0}
OPT=
[ $CC = 1 ] && OPT=--cc
OUT=.project-run/ring-test
RUN="docker exec mn-stratum python3 -B project/host_exec.py"
HOSTS="h1 h3 h2 h4"
IPS="10.0.0.1 10.0.0.3 10.0.0.2 10.0.0.4"
rm -rf $OUT
mkdir -p $OUT

# warm up: one ping from each rank to the next, so ARP and the switch tables
# are ready and the first packets of step 1 are not lost
for R in 0 1 2 3; do
    H=$(echo $HOSTS | cut -d' ' -f$((R + 1)))
    NEXT=$(echo $IPS | cut -d' ' -f$(((R + 1) % 4 + 1)))
    $RUN $H ping -c 2 -W 1 $NEXT > /dev/null 2>&1 || echo "warm-up ping $H -> $NEXT failed"
done

# all ranks start at the same moment (hosts share one clock inside the container)
START=$(docker exec mn-stratum python3 -c 'import time; print(time.monotonic_ns() + 4000000000)')

for R in 0 1 2 3; do
    H=$(echo $HOSTS | cut -d' ' -f$((R + 1)))
    NEXT=$(echo $IPS | cut -d' ' -f$(((R + 1) % 4 + 1)))
    PREV=$(echo $IPS | cut -d' ' -f$(((R + 3) % 4 + 1)))
    MYRATE=$RATE
    [ $R = $SLOW ] && MYRATE=$SLOWRATE
    DELAY=0
    [ $R = $LATE ] && DELAY=$LATES
    $RUN $H python3 project/ring.py --rank $R --next $NEXT --prev $PREV --count $COUNT \
        --rate $MYRATE --start-ns $START --start-delay $DELAY $OPT --outdir /workdir/$OUT > $OUT/rank$R.log 2>&1 &
done
wait

for R in 0 1 2 3; do
    echo "===== rank $R"
    cat $OUT/rank$R.log
done
echo "===== report ($(grep -h 'Feedback mode' .project-run/p4runtime-sh-50003.log | tail -1), cc=$CC)"
docker exec mn-stratum python3 -B project/ring_report.py /workdir/$OUT $START 4 $COUNT
