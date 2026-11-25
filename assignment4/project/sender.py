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


def send_packets(sock, args):
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
    return rows


def main(argv=None):
    args = parse_args(argv)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        rows = send_packets(sock, args)
    if args.output:
        with open(args.output, 'w', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(('step_id', 'seq_no', 'send_monotonic_ns'))
            writer.writerows(rows)
    duration = (rows[-1][2] - rows[0][2]) / 1_000_000_000
    rate = (len(rows) - 1) / duration if duration > 0 else 0.0
    print('sent=%d duration=%.6fs actual_rate=%.2f packets/s' %
          (len(rows), duration, rate))


if __name__ == '__main__':
    main()
