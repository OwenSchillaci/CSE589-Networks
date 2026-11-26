#!/bin/sh
# T10 test: two flows at the same time through S3
#   H1 -> H3 on step 1 (lagging), 600 packets, last one has LAST
#   H2 -> H4 on step 2 (ahead), 1000 packets
# expected: H2 packets get SYNC until H1's LAST arrives, H1 packets never get SYNC
# run from assignment4/ on the Mac after make project-up (bottleneck off)

OUT=.project-run/sync-test
RUN="docker exec mn-stratum python3 -B project/host_exec.py"
rm -rf $OUT
mkdir -p $OUT

# warm up ARP so the first packets are not delayed
$RUN h1 ping -c 1 -W 3 10.0.0.3 > /dev/null
$RUN h2 ping -c 1 -W 3 10.0.0.4 > /dev/null

# receivers first
$RUN h3 python3 project/receiver.py --count 600 --timeout 5 --output /workdir/$OUT/rx_h3.csv > $OUT/rx_h3.log 2>&1 &
$RUN h4 python3 project/receiver.py --count 1000 --timeout 5 --output /workdir/$OUT/rx_h4.csv > $OUT/rx_h4.log 2>&1 &
sleep 2

# both senders start together at 200 packets/s
$RUN h1 python3 project/sender.py 10.0.0.3 --step-id 1 --count 600 --rate 200 --last --output /workdir/$OUT/tx_h1.csv > $OUT/tx_h1.log 2>&1 &
$RUN h2 python3 project/sender.py 10.0.0.4 --step-id 2 --count 1000 --rate 200 --output /workdir/$OUT/tx_h2.csv > $OUT/tx_h2.log 2>&1 &
wait

echo "== H1 sender (step 1, lagging)";  cat $OUT/tx_h1.log
echo "== H2 sender (step 2, ahead)";    cat $OUT/tx_h2.log
echo "== H3 receiver";                  tail -1 $OUT/rx_h3.log
echo "== H4 receiver";                  tail -1 $OUT/rx_h4.log
