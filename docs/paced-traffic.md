# Paced project traffic

The tools in `assignment4/project/` use only Python standard libraries and keep
the existing 8-byte `!HIBB` header: step ID, sequence number, path ID, flags.
The source leaf stamps the path ID. P4 forwarding and topology are unchanged.

## Mininet commands

From `assignment4/`, run `make project-config`, then `make mininet`.
In four separate terminals, also from `assignment4/`, run:

```bash
make controller name=bridge grpc_port=50001
make controller name=bridge grpc_port=50002
make controller name=bridge grpc_port=50003 path_id=0
make controller name=bridge grpc_port=50004 path_id=0
```

These commands build/load the existing pipelines. Wait for `Project path:` on
each. Use `sudo` if Docker requires it. See [forwarding setup](project-paths.md)
for topology mappings and IPv4 UDP checksum handling.

Enter each line separately at the Mininet prompt. Check the receiver log for
`Listening on UDP port 5000` before sending:

```text
h3 python3 project/receiver.py --count 100 --timeout 5 --output /tmp/p0-100-rx.csv > /tmp/p0-100.log 2>&1 &
h3 cat /tmp/p0-100.log
h1 python3 project/sender.py 10.0.0.3 --count 100 --rate 100 --payload-size 64 --output /tmp/p0-100-tx.csv
h3 cat /tmp/p0-100.log
```

Then test 1,000 packets at 1,000 packets/sec:

```text
h3 python3 project/receiver.py --count 1000 --timeout 5 --output /tmp/p0-1000-rx.csv > /tmp/p0-1000.log 2>&1 &
h3 cat /tmp/p0-1000.log
h1 python3 project/sender.py 10.0.0.3 --count 1000 --rate 1000 --payload-size 64 --output /tmp/p0-1000-tx.csv
h3 cat /tmp/p0-1000.log
```

Stop only the s3 controller with Ctrl+C, then restart it with:

```bash
make controller name=bridge grpc_port=50003 path_id=1
```

After `Project path: 1`, repeat both tests with `p1` filenames. Expect
`paths={0: 100}` / `{0: 1000}` on path 0 and `{1: 100}` / `{1: 1000}` on path 1.
Expect `unique=count`, `duplicates=0`, `missing=0`, `out_of_order=0`.
Files in Mininet `/tmp` are accessible on the Docker host in `/tmp/mn-stratum/`.
Stop controllers before exiting Mininet.

The original single-packet commands still work:

```text
h3 python3 project/receiver.py --count 1 &
h1 python3 project/sender.py 10.0.0.3 --payload hello
```

## Pacing, payload, and CSVs

Sender defaults remain one packet, first sequence 1, step 1, flags 0, payload
`hello`. `--count` must be positive. `--rate` is finite positive packets/sec
(default 100). `--seq-no` selects the first sequence and each subsequent packet
increments it; wraparound is rejected. `--payload-size` specifies application
bytes, excluding the header. The UTF-8 `--payload` is truncated or zero-padded
to this size. Without it, the original payload is preserved. Sizes 0–1464
keep datagrams within the existing 1500-byte IPv4 MTU without fragmentation.

Packet index i is scheduled at `start + i * 1e9 / rate` using
`time.monotonic_ns()`. Sleeping until absolute deadlines prevents send/loop
overhead from accumulating as drift. Python/Linux scheduling still introduces
jitter. If behind schedule, overdue packets are sent without another wait,
which can produce short bursts. This is application pacing, not a guaranteed
wire transmission schedule.

Sender CSV columns: `step_id,seq_no,send_monotonic_ns`. The timestamp is taken
immediately before `sendto`. Receiver CSV columns:
`step_id,seq_no,path_id,flags,receive_monotonic_ns,duplicate,out_of_order`.
Arrival time is sampled immediately after `recvfrom`. These are application
monotonic timestamps, not NIC timestamps or wall-clock time. They should not
be subtracted across machines as a portable latency measurement.

CSV rows are buffered in memory and written on completion, avoiding per-packet
disk writes during traffic. Use finite runs for large traces. Receiver Ctrl+C
also writes its CSV and summary. With `--output`, per-packet printing is
suppressed; without it, the original field and payload output remains.

Duration is the first-to-last packet timestamp span. Actual rate is
`(packets - 1) / duration`, excluding startup, output writing, and idle timeout.
A one-packet or empty run reports zero rate/duration.

## Receiver statistics

Use one sender and one step per receiver run; statistics are not separated
by sender or step. `--count N` means N expected unique sequences starting at
`--seq-no` (default 1). It finishes when the entire expected range arrives.
Unexpected sequences do not complete the run. Use the same `--seq-no` on both
tools for non-default starts. `--count 0` (default) receives indefinitely.

`--timeout` is a finite positive idle timeout in seconds, including the initial
wait. It resets with each datagram and is not a total run deadline. Supply it
with `--count` so loss cannot cause an indefinite wait. The default has no
timeout, preserving the original behavior.

- **Received:** all valid project datagrams, including duplicates.
- **Unique:** number of distinct sequences.
- **Duplicates:** arrivals whose sequence has already been seen.
- **Out of order:** first arrivals below the highest sequence seen. Duplicates
  are not counted again as reordering.
- **Missing:** absent sequences in the expected range when count is supplied.
  With count 0, only holes between the minimum and maximum observed sequence
  are inferred; leading/trailing loss is unknown. Late arrivals fill gaps.
- **Paths:** arrival counts per path ID, including duplicates.
- **Malformed:** datagrams shorter than 8 bytes.

The receiver stops once its expected range is complete, so packets arriving
later are not counted.

## Tests and actual results

From `assignment4/`:

```bash
python3 -B -m unittest discover -s project -v
git diff --check
```

Six unit tests passed, covering no-drift scheduling with simulated send overhead,
header encoding, original defaults, duplicate/missing/reordering statistics,
empty/unknown-length streams, unexpected sequences, and invalid CLI inputs.

Live Stratum/Mininet h1 → h3 results with 64 application bytes:

| Path | Count | Requested packets/sec | Sender packets/sec | Send duration | Received |
|---|---:|---:|---:|---:|---:|
| 0 | 100 | 100 | 99.99 | 0.990133 s | 100 |
| 0 | 1000 | 1000 | 999.95 | 0.999055 s | 1000 |
| 1 | 100 | 100 | 99.98 | 0.990172 s | 100 |
| 1 | 1000 | 1000 | 999.92 | 0.999078 s | 1000 |

Every run had zero missing, duplicate, out-of-order, or malformed packets.
The CSVs were checked for exact ordered sequences 1..N, nondecreasing
timestamps, and the selected path ID on every received packet. No requested
test was blocked. These timings are observations from this host, not guarantees.

For example, verify saved sequence and path columns on the Docker host:

```bash
python3 - <<'PY'
import csv
with open('/tmp/mn-stratum/p0-100-rx.csv') as f:
    rows = list(csv.DictReader(f))
assert [int(r['seq_no']) for r in rows] == list(range(1, 101))
assert {int(r['path_id']) for r in rows} == {0}
print('100 ordered packets on path 0')
PY
```
