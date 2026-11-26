#!/bin/sh
# T07 bottleneck: make the S1 -> S4 cable slow (only path 0 uses it)
# and let a RED queue mark ECN when the line gets long, like DCQCN in the papers.
# run from assignment4/ on the Mac while the network is up:
#   sh project/bottleneck.sh on | off | show

DEV=s1-eth2
RUN="docker exec mn-stratum"

case "$1" in
on)
    # start clean, it is ok if there was nothing to delete
    $RUN tc qdisc del dev $DEV root 2>/dev/null
    set -e
    $RUN tc qdisc add dev $DEV root handle 1: htb default 10
    # lane 1:10 = 1 Mbit (~1080 of our packets/s) with RED + ECN for project packets
    $RUN tc class add dev $DEV parent 1: classid 1:10 htb rate 1mbit ceil 1mbit
    # min/max are Kmin/Kmax (~10 and ~40 packets), probability is Pmax like the papers
    $RUN tc qdisc add dev $DEV parent 1:10 handle 10: red limit 12000 min 1200 max 4800 \
        avpkt 116 burst 21 probability 0.2 bandwidth 1mbit ecn
    # lane 1:20 = plain queue so ARP/ping are not dropped by RED
    $RUN tc class add dev $DEV parent 1: classid 1:20 htb rate 1mbit ceil 1mbit
    $RUN tc qdisc add dev $DEV parent 1:20 handle 20: pfifo limit 100
    # ECN bits = 10 (ECT, set by sender.py) -> RED lane, everything else -> plain lane
    $RUN tc filter add dev $DEV parent 1: protocol ip prio 1 u32 match ip tos 0x02 0x03 flowid 1:10
    $RUN tc filter add dev $DEV parent 1: protocol all prio 2 u32 match u32 0 0 flowid 1:20
    echo "bottleneck on: $DEV limited to 1mbit with RED/ECN"
    ;;
off)
    $RUN tc qdisc del dev $DEV root 2>/dev/null
    echo "bottleneck off: $DEV back to normal"
    ;;
show)
    $RUN tc -s qdisc show dev $DEV
    $RUN tc filter show dev $DEV
    ;;
*)
    echo "usage: sh project/bottleneck.sh on | off | show"
    exit 1
    ;;
esac
