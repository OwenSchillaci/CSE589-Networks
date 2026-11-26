"""Send IPv4 UDP project packets (the source leaf stamps path_id)."""
import argparse
import csv
import math
import socket
import struct
import time


def positive_rate(value):
    rate = float(value)
    if not math.isfinite(rate) or rate <= 0:
        raise argparse.ArgumentTypeError('rate must be finite and positive')
    return rate


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    parser.add_argument('--step-id', type=int, default=1)
    parser.add_argument('--seq-no', type=int, default=1, help='first sequence number')
    parser.add_argument('--flags', type=int, default=0)
    # T10: put LAST (0x08) on the final packet so the switch knows the step is finished
    parser.add_argument('--last', action='store_true')
    # T18: slow down on SYNC or CONG feedback (simple DCQCN-like rule), off by default
    parser.add_argument('--cc', action='store_true')
    parser.add_argument('--payload', default='hello')
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--rate', type=positive_rate, default=100.0)
    parser.add_argument('--payload-size', type=int, help='application bytes, excluding header')
    parser.add_argument('--output', help='CSV filename')
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error('count must be positive')
    if not 0 <= args.step_id <= 65535 or not 0 <= args.flags <= 255:
        parser.error('step-id must fit 16 bits and flags must fit 8 bits')
    if not 0 <= args.seq_no <= 0xffffffff or args.seq_no + args.count - 1 > 0xffffffff:
        parser.error('sequence range must fit 32 bits (no wraparound)')
    # 1500-byte MTU minus IPv4, UDP, and project headers; avoid fragmentation.
    size = len(args.payload.encode()) if args.payload_size is None else args.payload_size
    if not 0 <= size <= 1462:
        parser.error('payload-size must be between 0 and 1462 bytes')
    args.data = args.payload.encode()[:size].ljust(size, b'\0')
    return args


def wait_until(deadline_ns):
    while True:
        remaining = deadline_ns - time.monotonic_ns()
        if remaining <= 0:
            return
        time.sleep(remaining / 1_000_000_000)


def read_feedback(sock, fb, deadline=None):
    # T09: read feedback packets from the receiver without blocking the sending loop
    # T16: with a deadline, keep reading until that time (used by listen)
    while True:
        try:
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                sock.settimeout(left)
                data = sock.recv(2048)
            else:
                # timeout 0 = do not wait at all (listen() may have left a timeout on the socket)
                sock.settimeout(0)
                data = sock.recv(2048)
        except (BlockingIOError, socket.timeout):
            return
        if len(data) < 10:
            continue
        step, seq, path, flags, qdepth = struct.unpack('!HIBBH', data[:10])
        if not flags & 0x04:
            continue
        if flags & 0x10:
            # T16: ACK = highest seq the receiver has + list of missing seqs in the payload
            count = struct.unpack('!H', data[10:12])[0]
            missing = struct.unpack('!%dI' % count, data[12:12 + 4 * count])
            fb['acks'] += 1
            fb['ack'] = (seq, missing)
            continue
        fb['total'] += 1
        fb['path%d' % path] = fb.get('path%d' % path, 0) + 1
        if flags & 0x02:
            fb['cong'] += 1
        if flags & 0x01:
            fb['sync'] += 1


def listen(sock, fb, seconds):
    # T16: read feedback and ACKs for a fixed time
    read_feedback(sock, fb, deadline=time.monotonic() + seconds)


def recover(sock, args, fb, start):
    # T16: after the first pass, ask the latest ACK what is missing and resend it
    last = args.seq_no + args.count - 1
    deadline = time.monotonic() + 15
    listen(sock, fb, 0.2)   # late or reordered packets can still arrive
    while time.monotonic() < deadline:
        # use the newest ACK (the final "nothing missing" one may already be here)
        listen(sock, fb, 0.1)
        if fb['ack'] is None:
            continue
        highest, missing = fb['ack']
        todo = sorted(set(missing) | set(range(highest + 1, last + 1)))
        if not todo:
            fb['complete'] = True
            fb['completion'] = (time.monotonic_ns() - start) / 1_000_000_000
            return
        fb['rounds'] += 1
        resend_start = time.monotonic_ns()
        for index, seq in enumerate(todo):
            wait_until(resend_start + int(index * 1_000_000_000 / args.rate))
            flags = args.flags
            if args.last and seq == last:
                flags = flags | 0x08
            header = struct.pack('!HIBBH', args.step_id, seq, 0, flags, 0)
            sock.sendto(header + args.data, (args.destination, 5000))
            fb['retransmitted'] += 1
            read_feedback(sock, fb)
        listen(sock, fb, 0.2)


def adjust_rate(args, fb, cc, now):
    # T18: same rule for SYNC and CONG, and for shared and separated mode
    # new mark and last cut was >= 50 ms ago -> cut rate in half
    # no new mark for 50 ms                  -> add 5% of the start rate back
    if fb['total'] > cc['marks'] and now - cc['last_cut'] >= 50_000_000:
        cc['rate'] = max(cc['min'], cc['rate'] * 0.5)
        cc['marks'] = fb['total']
        cc['last_cut'] = now
        cc['last_up'] = now
        cc['cuts'] += 1
    elif fb['total'] == cc['marks'] and now - cc['last_up'] >= 50_000_000:
        cc['rate'] = min(args.rate, cc['rate'] + 0.05 * args.rate)
        cc['last_up'] = now
    cc['lowest'] = min(cc['lowest'], cc['rate'])


def send_packets(sock, args, fb):
    rows = []
    start = time.monotonic_ns()
    cc = {'rate': args.rate, 'min': max(1.0, 0.02 * args.rate), 'marks': 0,
          'last_cut': 0, 'last_up': start, 'cuts': 0, 'lowest': args.rate}
    fb['cc'] = cc
    next_send = start
    for index in range(args.count):
        if args.cc:
            # T18: the gap to the next packet depends on the current rate
            wait_until(next_send)
            next_send = max(next_send, time.monotonic_ns()) + int(1_000_000_000 / cc['rate'])
        else:
            # Absolute deadlines avoid accumulating send/loop overhead as drift.
            wait_until(start + int(index * 1_000_000_000 / args.rate))
        seq = args.seq_no + index
        flags = args.flags
        if args.last and index == args.count - 1:
            flags = flags | 0x08
        header = struct.pack('!HIBBH', args.step_id, seq, 0, flags, 0)
        timestamp = time.monotonic_ns()
        sock.sendto(header + args.data, (args.destination, 5000))
        rows.append((args.step_id, seq, timestamp, round(cc['rate'], 1)))
        read_feedback(sock, fb)
        if args.cc:
            adjust_rate(args, fb, cc, time.monotonic_ns())
    recover(sock, args, fb, start)
    # wait a bit for the last feedback packets to come back
    listen(sock, fb, 0.5)
    return rows


def main(argv=None):
    args = parse_args(argv)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # set ECT(0) in the IP header so RED marks our packets instead of dropping them
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x02)
        # listen on 5000 too, the receiver sends feedback here
        sock.bind(('0.0.0.0', 5000))
        fb = {'total': 0, 'cong': 0, 'sync': 0, 'acks': 0, 'ack': None,
              'rounds': 0, 'retransmitted': 0, 'complete': False, 'completion': 0.0}
        rows = send_packets(sock, args, fb)
    if args.output:
        with open(args.output, 'w', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(('step_id', 'seq_no', 'send_monotonic_ns', 'rate'))
            writer.writerows(rows)
    duration = (rows[-1][2] - rows[0][2]) / 1_000_000_000
    rate = (len(rows) - 1) / duration if duration > 0 else 0.0
    print('sent=%d duration=%.6fs actual_rate=%.2f packets/s' %
          (len(rows), duration, rate))
    print('feedback=%d cong=%d sync=%d path0=%d path1=%d' %
          (fb['total'], fb['cong'], fb['sync'], fb.get('path0', 0), fb.get('path1', 0)))
    print('acks=%d retransmitted=%d rounds=%d complete=%s completion=%.3fs' %
          (fb['acks'], fb['retransmitted'], fb['rounds'], fb['complete'], fb['completion']))
    if args.cc:
        cc = fb['cc']
        print('cc=on cuts=%d lowest_rate=%.1f final_rate=%.1f packets/s' %
              (cc['cuts'], cc['lowest'], cc['rate']))


if __name__ == '__main__':
    main()
