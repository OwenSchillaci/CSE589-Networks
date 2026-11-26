"""T17: check the ring logs. usage: ring_report.py OUTDIR START_NS [RANKS] [COUNT]

- dependency: no first send of step s, group g before the rank got all of
  group g of step s - 1 (should be 0 violations)
- overlap: first sends of step s that left before the whole step s - 1 had arrived
- skew: every 10 ms, newest step each rank is sending; max - min over ranks still sending
- completion: last step fully received by every rank
"""
import csv
import sys

GROUP = 64


def load(path):
    with open(path) as f:
        rows = list(csv.reader(f))[1:]
    return [[int(x) for x in r] for r in rows]


def main():
    outdir, start_ns = sys.argv[1], int(sys.argv[2])
    ranks = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    tx = {r: load('%s/rank%d_tx.csv' % (outdir, r)) for r in range(ranks)}
    rx = {r: load('%s/rank%d_rx.csv' % (outdir, r)) for r in range(ranks)}
    steps = max(row[0] for r in tx for row in tx[r])
    count = int(sys.argv[4]) if len(sys.argv) > 4 else max(row[1] for r in tx for row in tx[r])

    def gsize(g):
        return min(count, g * GROUP + GROUP - 1) - max(1, g * GROUP) + 1

    checked = violations = overlap = 0
    done_at = {}
    for r in range(ranks):
        # first arrival time of every (step, seq) at rank r
        first = {}
        for s, q, path, flags, qd, t, dup in rx[r]:
            if (s, q) not in first:
                first[(s, q)] = t
        group_done, step_done = {}, {}
        for (s, q), t in first.items():
            g = q // GROUP
            group_done.setdefault((s, g), []).append(t)
            step_done.setdefault(s, []).append(t)
        group_done = {k: max(v) for k, v in group_done.items() if len(v) == gsize(k[1])}
        step_done = {s: max(v) for s, v in step_done.items() if len(v) == count}
        done_at[r] = step_done
        for s, q, t, re in tx[r]:
            if re or s == 1:
                continue
            checked += 1
            need = group_done.get((s - 1, q // GROUP))
            if need is None or t < need:
                violations += 1
            elif s - 1 not in step_done or t < step_done[s - 1]:
                overlap += 1

    # skew every 10 ms
    end_ns = max(row[2] for r in tx for row in tx[r])
    firsts = {r: sorted((t, s) for s, q, t, re in tx[r] if not re) for r in range(ranks)}
    idx = {r: 0 for r in range(ranks)}
    cur = {r: 0 for r in range(ranks)}
    samples = []
    t = start_ns
    while t <= end_ns:
        for r in range(ranks):
            while idx[r] < len(firsts[r]) and firsts[r][idx[r]][0] <= t:
                cur[r] = firsts[r][idx[r]][1]
                idx[r] += 1
        # a rank that sent all its steps is done, leave it out of the skew
        active = {r: cur[r] for r in range(ranks) if idx[r] < len(firsts[r])}
        samples.append((t, dict(cur), active))
        t += 10_000_000
    skews = [max(a.values()) - min(a.values()) if a else 0 for _, _, a in samples]

    print('dependency: checked=%d violations=%d' % (checked, violations))
    print('overlap: %d of %d first sends left before the whole previous step had arrived' %
          (overlap, checked))
    print('skew (steps apart, every 10 ms): max=%d mean=%.2f time_with_skew>=1: %.0f%%' %
          (max(skews), sum(skews) / len(skews), 100.0 * sum(k >= 1 for k in skews) / len(skews)))
    print('timeline (step each rank is sending):')
    for t_ns, c, a in samples[::50]:
        print('  t=%5.2fs  %s' % ((t_ns - start_ns) / 1e9,
                                  '  '.join('r%d:%s' % (r, c[r] if r in a else 'done') for r in range(ranks))))
    finish = []
    for r in range(ranks):
        if len(done_at[r]) == steps:
            finish.append((done_at[r][steps] - start_ns) / 1e9)
    if len(finish) == ranks:
        print('completion time (all ranks got all %d steps): %.3f s' % (steps, max(finish)))
    else:
        print('completion time: not all ranks finished')


if __name__ == '__main__':
    main()
