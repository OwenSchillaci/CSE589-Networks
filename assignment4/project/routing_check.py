"""T21: check the routing_test.sh logs and print PASS/FAIL for each case."""
import csv
import sys

CHUNK = 64
results = []


def load(out, case):
    # rx.csv: step_id, seq_no, path_id, flags, qdepth, receive_ns, duplicate, out_of_order, ecn
    with open('%s/%s_rx.csv' % (out, case)) as f:
        rows = [[int(x) for x in r] for r in list(csv.reader(f))[1:]]
    with open('%s/%s_tx.csv' % (out, case)) as f:
        sent_at = {int(r[1]): int(r[2]) for r in list(csv.reader(f))[1:]}
    sent = len(sent_at)
    # tx.csv has only the first send; a packet that arrives > 0.5 s after it
    # was the resend (T16), sent later on whatever path the flow used then
    resent = {r[1] for r in rows if r[5] - sent_at.get(r[1], r[5]) > 500_000_000}
    by_seq = {}
    for r in rows:
        # a resent packet goes on whatever path the flow uses later, so leave it out
        if r[1] not in resent:
            by_seq.setdefault(r[1], r)
    unique = len({r[1] for r in rows})
    return rows, by_seq, (sent, unique)


def runs(by_seq):
    # path changes in seq order: list of (path, first seq, last seq)
    out = []
    for q in sorted(by_seq):
        p = by_seq[q][2]
        if out and out[-1][0] == p:
            out[-1][2] = q
        else:
            out.append([p, q, q])
    return out


def check(case, text, ok, detail):
    results.append(ok)
    print('%s  %s: %s  (%s)' % ('PASS' if ok else 'FAIL', case, text, detail))


def chunk_rule(case, by_seq):
    r = runs(by_seq)
    bad = [start for p, start, end in r[1:] if start % CHUNK != 0]
    check(case, 'path changes only at chunk starts', not bad,
          '%d changes, not at a chunk start: %s' % (len(r) - 1, bad[:5]))
    return r


def main():
    out = sys.argv[1]

    rows, by_seq, sent = load(out, 'A')
    r = chunk_rule('A', by_seq)
    p1 = sum(1 for q in by_seq if by_seq[q][2] == 1)
    check('A', 'CONG feedback moved the flow to path 1', p1 > 0,
          'paths %s, first change at seq %s' % ([(p, a, b) for p, a, b in r][:3], r[1][1] if len(r) > 1 else '-'))
    check('A', 'all data arrived', sent[0] == sent[1], 'sent %d unique %d' % sent)

    rows, by_seq, sent = load(out, 'B')
    p0 = sum(1 for q in by_seq if by_seq[q][2] == 0)
    check('B', 'exclusion expired, flow back on path 0', p0 == len(by_seq) and sent[0] == sent[1],
          'path 0: %d of %d' % (p0, sent[0]))

    rows, by_seq, sent = load(out, 'C')
    r = chunk_rule('C', by_seq)
    ce = sum(1 for x in rows if x[8] == 3)
    check('C', 'RTT alone moved the flow to path 1', len(r) == 2 and r[1][0] == 1 and ce == 0,
          'runs %s, CE=%d' % (r, ce))

    rows, by_seq, sent = load(out, 'D')
    p0 = sum(1 for q in by_seq if by_seq[q][2] == 0)
    check('D', 'RTT mark expired, flow back on path 0', p0 == len(by_seq) and sent[0] == sent[1],
          'path 0: %d of %d' % (p0, sent[0]))

    rows, by_seq, sent = load(out, 'E')
    r = chunk_rule('E', by_seq)
    middle = [b - a + 1 for p, a, b in r[1:-1]]
    check('E', 'no alternative: stays put several chunks, no ping-pong', bool(middle) and min(middle) >= 3 * CHUNK,
          '%d changes, packets per stay %s' % (len(r) - 1, middle))
    check('E', 'all data arrived', sent[0] == sent[1], 'sent %d unique %d' % sent)

    print('%d of %d checks passed' % (sum(results), len(results)))


if __name__ == '__main__':
    main()
