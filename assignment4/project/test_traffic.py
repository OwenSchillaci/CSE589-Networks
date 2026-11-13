"""Run with: python3 -B -m unittest discover -s project -v"""
import contextlib
import io
import struct
import unittest
from unittest.mock import patch

import receiver
import sender


class TrafficTests(unittest.TestCase):
    def test_sequence_statistics(self):
        stats = receiver.Statistics(count=5, first_seq=10)
        for seq in (10, 12, 12, 11, 14):
            stats.record(seq, 1)
        self.assertEqual((stats.received, len(stats.seen), stats.duplicates,
                          stats.out_of_order, stats.missing()), (5, 4, 1, 1, 1))
        self.assertFalse(stats.complete())
        stats.record(13, 0)
        self.assertTrue(stats.complete())
        self.assertEqual(stats.paths, {1: 5, 0: 1})

    def test_unknown_length_and_empty(self):
        stats = receiver.Statistics()
        self.assertEqual(stats.missing(), 0)
        for seq in (4, 7, 5, 4):
            stats.record(seq, 0)
        self.assertEqual(stats.missing(), 1)
        self.assertFalse(stats.complete())

    def test_unexpected_sequences_do_not_complete(self):
        stats = receiver.Statistics(2)
        stats.record(0, 0)
        stats.record(3, 0)
        self.assertEqual(stats.missing(), 2)
        self.assertFalse(stats.complete())

    def test_invalid_arguments(self):
        cases = [(sender.parse_args, ['127.0.0.1'] + option) for option in (
            ['--rate', '0'], ['--rate', 'nan'], ['--rate', 'inf'],
            ['--count', '0'], ['--payload-size', '-1'], ['--payload-size', '1465'],
            ['--step-id', '65536'], ['--flags', '-1'],
            ['--seq-no', '4294967295', '--count', '2'])]
        cases += [(receiver.parse_args, option) for option in (
            ['--count', '-1'], ['--timeout', '0'], ['--timeout', 'nan'],
            ['--timeout', 'inf'], ['--seq-no', '-1'])]
        for parse, argv in cases:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse(argv)
                self.assertEqual(error.exception.code, 2)

    def test_absolute_pacing_and_wire_format(self):
        args = sender.parse_args(['127.0.0.1', '--count', '3', '--rate', '100',
                                  '--seq-no', '10', '--payload-size', '8'])
        now = [0]
        packets = []

        class Socket:
            def sendto(self, data, destination):
                packets.append((data, destination))
                now[0] += 4_000_000

        def sleep(seconds):
            now[0] += round(seconds * 1_000_000_000)

        with patch.object(sender.time, 'monotonic_ns', side_effect=lambda: now[0]), \
                patch.object(sender.time, 'sleep', side_effect=sleep):
            rows = sender.send_packets(Socket(), args)
        self.assertEqual([row[2] for row in rows], [0, 10_000_000, 20_000_000])
        self.assertEqual([struct.unpack('!HIBB', p[0][:8]) for p in packets],
                         [(1, 10, 0, 0), (1, 11, 0, 0), (1, 12, 0, 0)])
        self.assertEqual(packets[0][0][8:], b'hello\0\0\0')

    def test_existing_sender_defaults(self):
        args = sender.parse_args(['127.0.0.1'])
        self.assertEqual((args.count, args.seq_no, args.data), (1, 1, b'hello'))


if __name__ == '__main__':
    unittest.main()
