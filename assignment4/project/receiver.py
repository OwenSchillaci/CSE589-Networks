"""Receive project datagrams and print their header and application bytes."""
import argparse
import csv
import math
import socket
import struct
import time
from collections import Counter


class Statistics:
    """Sequence accounting for one sender/step, without sequence wraparound."""
    def __init__(self, count=0, first_seq=1):
        self.count = count
        self.first_seq = first_seq
        self.seen = set()
        self.highest = None
        self.received = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.paths = Counter()

    def record(self, seq, path):
        self.received += 1
        self.paths[path] += 1
        duplicate = seq in self.seen
        out_of_order = not duplicate and self.highest is not None and seq < self.highest
        self.duplicates += duplicate
        self.out_of_order += out_of_order
        self.seen.add(seq)
        self.highest = seq if self.highest is None else max(self.highest, seq)
        return duplicate, out_of_order

    def missing(self):
        if self.count:
            end = self.first_seq + self.count
            return self.count - sum(self.first_seq <= seq < end for seq in self.seen)
        if not self.seen:
            return 0
        return max(self.seen) - min(self.seen) + 1 - len(self.seen)

    def complete(self):
        return bool(self.count) and self.missing() == 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=0, help='expected unique packets; 0 is unlimited')
    parser.add_argument('--seq-no', type=int, default=1, help='first expected sequence number')
    parser.add_argument('--timeout', type=float, default=None, help='idle timeout in seconds')
    parser.add_argument('--output', help='CSV filename; suppresses per-packet printing')
    args = parser.parse_args(argv)
    if args.count < 0:
        parser.error('count must be nonnegative')
    if not 0 <= args.seq_no <= 0xffffffff or args.seq_no + max(args.count - 1, 0) > 0xffffffff:
        parser.error('sequence range must fit 32 bits (no wraparound)')
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error('timeout must be finite and positive')
    return args


def main(argv=None):
    args = parse_args(argv)
    stats = Statistics(args.count, args.seq_no)
    rows = []
    malformed = 0
    feedback_sent = 0
    acks_sent = 0
    sender = None
    last_step = 0
    last_ack = time.monotonic()
    last_data = time.monotonic()

    def send_ack():
        # T16: tell the sender the highest seq we have and which ones are still missing
        highest = stats.highest or 0
        missing = [s for s in range(stats.first_seq, highest + 1) if s not in stats.seen][:350]
        header = struct.pack('!HIBBH', last_step, highest, 0, 0x04 | 0x10, 0)
        sock.sendto(header + struct.pack('!H%dI' % len(missing), len(missing), *missing), (sender, 5000))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('0.0.0.0', 5000))
        # ask Linux to give us the TOS byte of each packet so we can see the ECN bits
        sock.setsockopt(socket.IPPROTO_IP, getattr(socket, 'IP_RECVTOS', 13), 1)
        sock.settimeout(0.05)
        print('Listening on UDP port 5000', flush=True)
        try:
            while not stats.complete():
                # T16: ACK every 50 ms while data is coming
                if sender and time.monotonic() - last_ack >= 0.05:
                    send_ack()
                    acks_sent += 1
                    last_ack = time.monotonic()
                try:
                    data, ancdata, _, peer = sock.recvmsg(65535, socket.CMSG_SPACE(1))
                except socket.timeout:
                    if args.timeout is not None and time.monotonic() - last_data > args.timeout:
                        break
                    continue
                last_data = time.monotonic()
                timestamp = time.monotonic_ns()
                # ECN is the last 2 bits of TOS: 2 = ECT (not marked), 3 = CE (marked by RED)
                ecn = 0
                for level, kind, value in ancdata:
                    if level == socket.IPPROTO_IP and kind == socket.IP_TOS and value:
                        ecn = value[0] & 0x03
                if len(data) < 10:
                    malformed += 1
                    print('short project datagram from %s' % (peer,), flush=True)
                    continue
                step, seq, path, flags, qdepth = struct.unpack('!HIBBH', data[:10])
                sender = peer[0]
                last_step = step
                duplicate, reordered = stats.record(seq, path)
                rows.append((step, seq, path, flags, qdepth, timestamp, int(duplicate), int(reordered), ecn))
                # T09: packet has a mark (SYNC 0x01 or CONG 0x02) -> send feedback back to the sender
                # keep path_id = the forward path, copy the mark bits, add FEEDBACK 0x04
                marks = flags & 0x03
                if marks:
                    feedback = struct.pack('!HIBBH', step, seq, path, marks | 0x04, qdepth)
                    sock.sendto(feedback, (peer[0], 5000))
                    feedback_sent += 1
                if not args.output:
                    print('step_id=%d seq_no=%d path_id=%d flags=%d qdepth=%d payload=%r' %
                          (step, seq, path, flags, qdepth, data[10:]), flush=True)
            # T16: all data is here, send the final ACK a few times in case one is lost
            if sender and stats.complete():
                for _ in range(3):
                    send_ack()
                    acks_sent += 1
        except KeyboardInterrupt:
            pass
    if args.output:
        with open(args.output, 'w', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(('step_id', 'seq_no', 'path_id', 'flags', 'qdepth',
                             'receive_monotonic_ns', 'duplicate', 'out_of_order', 'ecn'))
            writer.writerows(rows)
    duration = (rows[-1][5] - rows[0][5]) / 1_000_000_000 if len(rows) > 1 else 0.0
    rate = (len(rows) - 1) / duration if duration > 0 else 0.0
    print('received=%d unique=%d duplicates=%d missing=%d out_of_order=%d malformed=%d '
          'paths=%s max_qdepth=%d ce_marked=%d cong_flag=%d feedback_sent=%d acks_sent=%d duration=%.6fs actual_rate=%.2f packets/s' %
          (stats.received, len(stats.seen), stats.duplicates, stats.missing(),
           stats.out_of_order, malformed, dict(stats.paths),
           max((row[4] for row in rows), default=0),
           sum(row[8] == 3 for row in rows),
           sum((row[3] & 0x02) != 0 for row in rows), feedback_sent, acks_sent, duration, rate))


if __name__ == '__main__':
    main()
