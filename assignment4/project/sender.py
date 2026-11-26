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


def read_feedback(sock, fb, wait=False):
    # T09: read feedback packets from the receiver without blocking the sending loop
    while True:
        try:
            if wait:
                data = sock.recv(2048)
            else:
                data = sock.recv(2048, socket.MSG_DONTWAIT)
        except (BlockingIOError, socket.timeout):
            return
        if len(data) < 10:
            continue
        step, seq, path, flags, qdepth = struct.unpack('!HIBBH', data[:10])
        if not flags & 0x04:
            continue
        fb['total'] += 1
        fb['path%d' % path] = fb.get('path%d' % path, 0) + 1
        if flags & 0x02:
            fb['cong'] += 1
        if flags & 0x01:
            fb['sync'] += 1


def send_packets(sock, args, fb):
    rows = []
    start = time.monotonic_ns()
    for index in range(args.count):
        # Absolute deadlines avoid accumulating send/loop overhead as drift.
        wait_until(start + int(index * 1_000_000_000 / args.rate))
        seq = args.seq_no + index
        header = struct.pack('!HIBBH', args.step_id, seq, 0, args.flags,0)
        timestamp = time.monotonic_ns()
        sock.sendto(header + args.data, (args.destination, 5000))
        rows.append((args.step_id, seq, timestamp))
        read_feedback(sock, fb)
    # wait a bit for the last feedback packets to come back
    sock.settimeout(1.0)
    read_feedback(sock, fb, wait=True)
    return rows


def main(argv=None):
    args = parse_args(argv)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # set ECT(0) in the IP header so RED marks our packets instead of dropping them
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x02)
        # listen on 5000 too, the receiver sends feedback here
        sock.bind(('0.0.0.0', 5000))
        fb = {'total': 0, 'cong': 0, 'sync': 0}
        rows = send_packets(sock, args, fb)
    if args.output:
        with open(args.output, 'w', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(('step_id', 'seq_no', 'send_monotonic_ns'))
            writer.writerows(rows)
    duration = (rows[-1][2] - rows[0][2]) / 1_000_000_000
    rate = (len(rows) - 1) / duration if duration > 0 else 0.0
    print('sent=%d duration=%.6fs actual_rate=%.2f packets/s' %
          (len(rows), duration, rate))
    print('feedback=%d cong=%d sync=%d path0=%d path1=%d' %
          (fb['total'], fb['cong'], fb['sync'], fb.get('path0', 0), fb.get('path1', 0)))


if __name__ == '__main__':
    main()
