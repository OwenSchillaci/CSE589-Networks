"""T20: check the marking_test.sh logs and print PASS/FAIL for each case."""
import csv
import sys

SYNC, CONG, LAST = 0x01, 0x02, 0x08


def rows(outdir, case, name):
    # rx.csv: step_id, seq_no, path_id, flags, qdepth, receive_ns, duplicate, out_of_order, ecn
    with open('%s/%s_rx_%s.csv' % (outdir, case, name)) as f:
        return [[int(x) for x in r] for r in list(csv.reader(f))[1:]]


def count(rs, test):
    return sum(1 for r in rs if test(r))


results = []


def check(case, text, ok, detail):
    results.append(ok)
    print('%s  %s: %s  (%s)' % ('PASS' if ok else 'FAIL', case, text, detail))


def main():
    out = sys.argv[1]

    # A: lagging step 1 never marked; step 2 marked while step 1 runs, not after its LAST
    h1, h2 = rows(out, 'A', 'h1'), rows(out, 'A', 'h2')
    last_t = max(r[5] for r in h1 if r[3] & LAST)
    before = [r for r in h2 if r[5] < last_t]
    after = [r for r in h2 if r[5] > last_t + 50_000_000]
    s_before = count(before, lambda r: r[3] & SYNC)
    s_after = count(after, lambda r: r[3] & SYNC)
    check('A', 'lagging flow never marked', count(h1, lambda r: r[3] & SYNC) == 0,
          'H1 sync=%d' % count(h1, lambda r: r[3] & SYNC))
    check('A', 'ahead flow marked while step 1 runs', s_before >= 0.9 * len(before),
          'H2 sync %d of %d before LAST' % (s_before, len(before)))
    check('A', 'step change: no marks after LAST', s_after == 0,
          'H2 sync %d of %d after LAST' % (s_after, len(after)))

    # B: CONG only, and only where CE was set
    b = rows(out, 'B', 'h1')
    ce = count(b, lambda r: r[8] == 3)
    cg = count(b, lambda r: r[3] & CONG)
    bad = count(b, lambda r: (r[8] == 3) != bool(r[3] & CONG))
    check('B', 'CONG set exactly on CE packets', cg > 0 and bad == 0,
          'CE=%d CONG=%d mismatches=%d' % (ce, cg, bad))
    check('B', 'one flow alone gets no SYNC', count(b, lambda r: r[3] & SYNC) == 0,
          'sync=%d' % count(b, lambda r: r[3] & SYNC))

    # C: both causes can sit on one packet; lagging flow still never SYNC
    c1, c2 = rows(out, 'C', 'h1'), rows(out, 'C', 'h2')
    both = count(c2, lambda r: (r[3] & SYNC) and (r[3] & CONG))
    bad = count(c1 + c2, lambda r: (r[8] == 3) != bool(r[3] & CONG))
    check('C', 'SYNC and CONG on the same packet', both > 0, 'H2 both=%d' % both)
    check('C', 'lagging flow never SYNC under congestion', count(c1, lambda r: r[3] & SYNC) == 0,
          'H1 sync=%d cong=%d' % (count(c1, lambda r: r[3] & SYNC), count(c1, lambda r: r[3] & CONG)))
    check('C', 'CONG still matches CE', bad == 0, 'mismatches=%d' % bad)

    # D: fresh job after idle starts clean
    d = rows(out, 'D', 'h2')
    check('D', 'fresh job not marked', count(d, lambda r: r[3] & SYNC) == 0,
          'sync=%d of %d' % (count(d, lambda r: r[3] & SYNC), len(d)))

    # E: step 1 stops without LAST; step 2 marked only while step 1 runs + ~200 ms
    e1, e2 = rows(out, 'E', 'h1'), rows(out, 'E', 'h2')
    end1 = max(r[5] for r in e1)
    late = [r for r in e2 if r[5] > end1 + 400_000_000]
    s_late = count(late, lambda r: r[3] & SYNC)
    marked = [r[5] for r in e2 if r[3] & SYNC]
    stop = (max(marked) - end1) / 1e6 if marked else 0
    check('E', 'marks stop after step 1 goes quiet', len(late) > 0 and s_late == 0,
          'H2 sync=%d total, %d of %d later than 0.4 s after H1 stopped; last mark %.0f ms after'
          % (len(marked), s_late, len(late), stop))

    print('%d of %d checks passed' % (sum(results), len(results)))


if __name__ == '__main__':
    main()
