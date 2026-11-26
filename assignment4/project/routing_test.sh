#!/bin/sh
# T21 test: routing behaviour, one H1 -> H3 flow per case (preferred path 0)
#   A  CONG exclusion: slow cable with RED/ECN on path 0 -> flow moves to path 1
#   B  expiry: right after A, jam off, slow flow -> back on path 0
#   C  RTT trigger: 30 ms delay on path 0, no marks -> flow moves to path 1
#   D  expiry after RTT: delay off -> back on path 0
#   E  no alternative: 30 ms delay on both paths -> flow stays put, retries ~every 0.5 s
# run from assignment4/ on the Mac after make project-up (separated mode, nothing on the cables)

OUT=.project-run/routing-test
RUN="docker exec mn-stratum python3 -B project/host_exec.py"
TC="docker exec mn-stratum tc qdisc"
rm -rf $OUT
mkdir -p $OUT

$RUN h1 ping -c 2 -W 1 10.0.0.3 > /dev/null

# flow CASE COUNT RATE
flow() {
    $RUN h3 python3 project/receiver.py --count $2 --timeout 3 --output /workdir/$OUT/$1_rx.csv > $OUT/$1_rx.log 2>&1 &
    sleep 1
    $RUN h1 python3 project/sender.py 10.0.0.3 --count $2 --rate $3 --payload-size 64 --output /workdir/$OUT/$1_tx.csv > $OUT/$1_tx.log 2>&1
    wait
}

echo "case A: CONG exclusion"
sh project/bottleneck.sh on > /dev/null
flow A 3600 1200
sh project/bottleneck.sh off > /dev/null
sleep 1

echo "case B: exclusion expired"
flow B 500 100
sleep 1

echo "case C: RTT trigger"
$TC add dev s1-eth2 root netem delay 30ms
flow C 3000 500
$TC del dev s1-eth2 root
sleep 1

echo "case D: RTT mark expired"
flow D 500 100
sleep 1

echo "case E: no alternative"
$TC add dev s1-eth2 root netem delay 30ms
$TC add dev s2-eth2 root netem delay 30ms
flow E 3000 500
$TC del dev s1-eth2 root
$TC del dev s2-eth2 root

docker exec mn-stratum python3 -B project/routing_check.py /workdir/$OUT
