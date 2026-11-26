#!/bin/sh
# T20 test: one small case for each marking cause and progress-state change
#   A  SYNC + step change: H1 step 1 (600, LAST) and H2 step 2 (1000) together
#   B  CONG only: one flow through the slow cable
#   C  both causes: two flows at 700 pkt/s through the slow cable, H2 ahead
#   D  fresh job: lone flow on step 5 after an idle gap
#   E  missed LAST: H1 step 1 stops without LAST, H2 step 2 keeps going
# run from assignment4/ on the Mac after make project-up (bottleneck off at start)

OUT=.project-run/marking-test
RUN="docker exec mn-stratum python3 -B project/host_exec.py"
rm -rf $OUT
mkdir -p $OUT

$RUN h1 ping -c 2 -W 1 10.0.0.3 > /dev/null
$RUN h2 ping -c 2 -W 1 10.0.0.4 > /dev/null

# flow CASE NAME SRC DST DST_IP COUNT SENDER_OPTIONS...
flow() {
    CASE=$1; NAME=$2; SRC=$3; DST=$4; IP=$5; N=$6
    shift 6
    $RUN $DST python3 project/receiver.py --count $N --timeout 3 --output /workdir/$OUT/${CASE}_rx_$NAME.csv > $OUT/${CASE}_rx_$NAME.log 2>&1 &
    sleep 1
    $RUN $SRC python3 project/sender.py $IP --count $N "$@" --output /workdir/$OUT/${CASE}_tx_$NAME.csv > $OUT/${CASE}_tx_$NAME.log 2>&1 &
}

echo "case A: SYNC and step change"
flow A h1 h1 h3 10.0.0.3 600 --step-id 1 --rate 200 --last
flow A h2 h2 h4 10.0.0.4 1000 --step-id 2 --rate 200
wait
sleep 2

echo "case B: CONG only"
sh project/bottleneck.sh on > /dev/null
flow B h1 h1 h3 10.0.0.3 2400 --step-id 1 --rate 1200 --payload-size 64
wait
sh project/bottleneck.sh off > /dev/null
sleep 2

echo "case C: SYNC and CONG together"
sh project/bottleneck.sh on > /dev/null
flow C h1 h1 h3 10.0.0.3 1400 --step-id 1 --rate 700 --payload-size 64 --last
flow C h2 h2 h4 10.0.0.4 1400 --step-id 2 --rate 700 --payload-size 64
wait
sh project/bottleneck.sh off > /dev/null
sleep 2

echo "case D: fresh job after idle"
flow D h2 h2 h4 10.0.0.4 200 --step-id 5 --rate 200
wait
sleep 2

echo "case E: LAST never comes"
flow E h1 h1 h3 10.0.0.3 300 --step-id 1 --rate 200
flow E h2 h2 h4 10.0.0.4 1000 --step-id 2 --rate 200
wait

docker exec mn-stratum python3 -B project/marking_check.py /workdir/$OUT
