"""T17: one rank of the ring workload (simple Ring-AllReduce traffic).

Every rank sends --steps steps of --count packets to the next rank.
Packet seq q of step s (s > 1) can only be sent after this rank got the
whole 64 packet group q // 64 of step s - 1 from the previous rank.
So steps can overlap (pipeline), but a packet never goes out before its input.
"""
import argparse
import csv
import os
import socket
import struct
import time

HDR = '!HIBBH'
SYNC, CONG, FEEDBACK, LAST, ACK, RTT = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
PORT = 5000
GROUP = 64          # same as CHUNK_MASK + 1 in bridge.p4
ACK_EVERY = 0.05    # same as receiver.py (T16)
RESEND_WAIT = 0.2   # same as sender.py (T16)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rank', type=int, required=True)
    p.add_argument('--next', required=True, help='IP of the next rank')
    p.add_argument('--prev', required=True, help='IP of the previous rank')
    p.add_argument('--steps', type=int, default=6)
    p.add_argument('--count', type=int, default=320, help='packets per step')
    p.add_argument('--rate', type=float, default=300.0, help='packets/s')
    p.add_argument('--payload-size', type=int, default=64)
    p.add_argument('--start-ns', type=int, default=0, help='common start time (time.monotonic_ns)')
    p.add_argument('--start-delay', type=float, default=0.0, help='this rank starts later (s)')
    p.add_argument('--timeout', type=float, default=60.0)
    p.add_argument('--outdir', default='.')
    p.add_argument('--bind', default='', help='local IP to bind (only for testing on one machine)')
    return p.parse_args(argv)


def group_size(g, count):
    lo = max(1, g * GROUP)
    hi = min(count, g * GROUP + GROUP - 1)
    return hi - lo + 1


def main(argv=None):
    args = parse_args(argv)
    C = args.count
    steps = range(1, args.steps + 1)
    payload = bytes(args.payload_size)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x02)   # ECT, so RED marks (T07)
    sock.bind((args.bind, PORT))
    sock.setblocking(False)

    # receiving side (data from prev)
    got = {s: set() for s in steps}
    have = {s: {} for s in steps}          # step -> group -> packets received
    rx_done = {}                           # step -> time it was complete
    rx_rows = []
    dups = 0
    # sending side (data to next)
    cur_step, cur_seq = 1, 1
    sent_all = {}                          # step -> time its last new packet left
    ack = {}                               # step -> newest (highest, missing) from next
    acked = set()                          # steps the next rank has completely
    last_resend = {}
    queue = []                             # packets to resend, before new ones
    tx_rows = []
    retransmitted = rounds = 0
    fb = {'sync': 0, 'cong': 0, 'rtt': 0, 'path0': 0, 'path1': 0}

    def ready(s, q):
        if s == 1:
            return True
        g = q // GROUP
        return have[s - 1].get(g, 0) >= group_size(g, C)

    def send_acks(now):
        # one ACK per step from prev: highest seq + missing list (like T16)
        for s in steps:
            if not got[s] or (s in rx_done and now - rx_done[s] > 1.0):
                continue
            highest = max(got[s])
            missing = [q for q in range(1, highest + 1) if q not in got[s]][:350]
            msg = struct.pack(HDR, s, highest, 0, FEEDBACK | ACK, 0)
            msg += struct.pack('!H', len(missing)) + struct.pack('!%dI' % len(missing), *missing)
            sock.sendto(msg, (args.prev, PORT))

    def check_resend(now):
        nonlocal rounds
        for s in steps:
            if s not in sent_all or s in acked:
                continue
            if now - max(sent_all[s], last_resend.get(s, 0)) < RESEND_WAIT:
                continue
            if any(item[0] == s for item in queue):
                continue
            if s in ack:
                highest, missing = ack[s]
                todo = list(missing) + list(range(highest + 1, C + 1))
            else:
                todo = list(range(1, C + 1))   # no ACK at all yet
            if todo:
                # the last resent packet of a step carries LAST again, so the
                # switches move step_min forward again after the late packets
                queue.extend((s, q, 0) for q in todo[:-1])
                queue.append((s, todo[-1], LAST))
                rounds += 1
            last_resend[s] = now + len(todo) / args.rate

    def read_all():
        nonlocal dups
        while True:
            try:
                data, peer = sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                return
            if len(data) < 10:
                continue
            s, q, path, flags, qdepth = struct.unpack(HDR, data[:10])
            now_ns = time.monotonic_ns()
            if flags & FEEDBACK:
                # from the next rank: ACK, mark feedback, or RTT reply
                if flags & ACK:
                    n = struct.unpack('!H', data[10:12])[0]
                    missing = struct.unpack('!%dI' % n, data[12:12 + 4 * n])
                    ack[s] = (q, missing)
                    if q == C and not missing:
                        acked.add(s)
                    continue
                if flags & RTT:
                    fb['rtt'] += 1
                if flags & SYNC:
                    fb['sync'] += 1
                if flags & CONG:
                    fb['cong'] += 1
                if flags & (SYNC | CONG):
                    fb['path%d' % (path & 1)] += 1
                continue
            # data from the previous rank
            if s not in got:
                continue
            dup = q in got[s]
            rx_rows.append((s, q, path, flags, qdepth, now_ns, int(dup)))
            if dup:
                dups += 1
            else:
                got[s].add(q)
                g = q // GROUP
                have[s][g] = have[s].get(g, 0) + 1
                if len(got[s]) == C:
                    rx_done[s] = time.monotonic()
            # T09/T14: feedback for marks and RTT requests goes back to prev
            marks = flags & (SYNC | CONG)
            rtt = flags & RTT
            if marks or rtt:
                sock.sendto(struct.pack(HDR, s, q, path, marks | rtt | FEEDBACK, qdepth),
                            (args.prev, PORT))

    # wait for the common start, plus this rank's own delay
    start_ns = args.start_ns or time.monotonic_ns()
    my_start = start_ns / 1e9 + args.start_delay
    while time.monotonic() < my_start:
        read_all()
        time.sleep(0.001)

    gap = 1.0 / args.rate
    next_time = time.monotonic()
    next_ack = next_time
    deadline = start_ns / 1e9 + args.timeout
    finished_at = None
    while True:
        now = time.monotonic()
        read_all()
        if now >= next_ack:
            send_acks(now)
            check_resend(now)
            next_ack = now + ACK_EVERY

        all_sent_ok = len(acked) == args.steps
        all_received = len(rx_done) == args.steps
        if all_sent_ok and all_received:
            if finished_at is None:
                finished_at = now
            if now - finished_at > 1.0:   # keep sending ACKs a bit for prev
                break
        if now > deadline:
            break

        if now >= next_time:
            pkt = None
            if queue:
                s, q, flags = queue.pop(0)
                pkt = (s, q, 1, flags)
            elif cur_step <= args.steps and ready(cur_step, cur_seq):
                pkt = (cur_step, cur_seq, 0, LAST if cur_seq == C else 0)
                if cur_seq == C:
                    sent_all[cur_step] = now
                    cur_step, cur_seq = cur_step + 1, 1
                else:
                    cur_seq += 1
            if pkt:
                s, q, re, flags = pkt
                sock.sendto(struct.pack(HDR, s, q, 0, flags, 0) + payload, (args.next, PORT))
                tx_rows.append((s, q, time.monotonic_ns(), re))
                retransmitted += re
                next_time += gap
                if next_time < now - 0.05:
                    next_time = now     # no burst after waiting
            else:
                next_time = now         # waiting for input, check again soon
                time.sleep(0.0005)
        else:
            time.sleep(min(next_time - now, 0.001))

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, 'rank%d_tx.csv' % args.rank), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('step_id', 'seq_no', 'send_monotonic_ns', 'retransmit'))
        w.writerows(tx_rows)
    with open(os.path.join(args.outdir, 'rank%d_rx.csv' % args.rank), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('step_id', 'seq_no', 'path_id', 'flags', 'qdepth', 'receive_monotonic_ns', 'duplicate'))
        w.writerows(rx_rows)

    done = {s: '%.3f' % (rx_done[s] - start_ns / 1e9) for s in sorted(rx_done)}
    print('rank=%d steps=%d count=%d rate=%.0f start_delay=%.2f' %
          (args.rank, args.steps, C, args.rate, args.start_delay))
    print('sent=%d retransmitted=%d rounds=%d acked_steps=%d' %
          (len(tx_rows), retransmitted, rounds, len(acked)))
    print('received_unique=%d duplicates=%d step_done=%s' %
          (sum(len(v) for v in got.values()), dups, done))
    print('feedback sync=%d cong=%d path0=%d path1=%d rtt_replies=%d' %
          (fb['sync'], fb['cong'], fb['path0'], fb['path1'], fb['rtt']))
    print('complete=%s' % (all_sent_ok and all_received))


if __name__ == '__main__':
    main()
