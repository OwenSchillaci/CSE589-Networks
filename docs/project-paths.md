# Explicit two-path forwarding

Run commands below from `assignment4/`. Use `sudo make` if Docker requires it.
The normal `bridge` pipeline is extended; `switch.p4` and the assignment's linear
configurations are untouched.

## Build and launch

```bash
cd assignment4
make project-config
make .p4-build P4_PROGRAM_NAME=bridge
make mininet
```

`project-config` runs `project_config.py` in the Mininet image, imports the actual
`2spine2leaf_clos.py`, and queries `Topo.port()` for every forwarding port. It
writes `topo/mytopo.json`. Regenerate after topology changes. Host IPs and switch
gRPC ports follow the launcher's default sorted-node allocation; verify with
`net` and `h1 ip -4 addr show` in Mininet if changing launch defaults.

| Switch | gRPC | Port 1 | Port 2 | Port 3 | Port 4 |
|---|---|---|---|---|---|
| s1 (spine 0) | 50001 | s3 | s4 | | |
| s2 (spine 1) | 50002 | s3 | s4 | | |
| s3 (leaf 1) | 50003 | s1 | s2 | h1 | h2 |
| s4 (leaf 2) | 50004 | s1 | s2 | h3 | h4 |

Hosts h1–h4 use 10.0.0.1–10.0.0.4. No MAC rewriting or IP routing is needed:
all hosts share the default /8 and use ARP across the bridge.

In **four separate terminals**, run these controllers and leave them running:

```bash
cd assignment4
make controller name=bridge grpc_port=50001
make controller name=bridge grpc_port=50002
make controller name=bridge grpc_port=50003 path_id=0
make controller name=bridge grpc_port=50004 path_id=0
```

Each command compiles its per-port pipeline, loads it through
`p4runtime_sh.FwdPipeConfig`, installs multicast group 1 and `project_forward`
entries, then runs the existing packet-in bridge learning loop. Wait for
`Project path: ...` before sending. `path_id` accepts 0 or 1; it selects the
uplink and stamped ID at a source leaf. Transit switches preserve that ID.
Destination IP and ingress port select exact forwarding entries. Unconfigured
project destinations drop. Both directions and all four hosts are supported.

Non-project packets use the existing bridge table, multicast pruning, and
controller learning. The Clos multicast configuration uses a spanning tree
through s1, excluding leaf port 2, to avoid a broadcast cycle. Both physical
spines remain available to project traffic. Linear configurations retain their
original multicast memberships.

## Test both paths

Enter each Mininet command on its own line, waiting for the prompt:

```text
h3 python3 project/receiver.py --count 1 > /tmp/project-path0.log 2>&1 &
h1 python3 project/sender.py 10.0.0.3 --step-id 258 --seq-no 16909060 --flags 165 --payload path-zero
h3 cat /tmp/project-path0.log
pingall
```

Expected receiver output:

```text
step_id=258 seq_no=16909060 path_id=0 flags=165 payload=b'path-zero'
```

Stop **only the s3 controller** with Ctrl+C, then restart it:

```bash
make controller name=bridge grpc_port=50003 path_id=1
```

This reloads the source pipeline and replaces the rules; avoid sending during
restart. The other controllers keep running. To select the reverse direction's
path, restart s4 with the desired `path_id` instead.

```text
h3 python3 project/receiver.py --count 1 > /tmp/project-path1.log 2>&1 &
h1 python3 project/sender.py 10.0.0.3 --step-id 258 --seq-no 16909060 --flags 165 --payload path-one
h3 cat /tmp/project-path1.log
```

Expected: the same fields with `path_id=1` and `payload=b'path-one'`.
For physical-route confirmation, start this in another terminal before sending
(use `s1-eth1` for path 0):

```bash
docker exec mn-stratum tcpdump -i s2-eth1 -nn -vv -c 1 udp dst port 5000
```

The sender packs the 8-byte header as `!HIBB`; source leaf configuration chooses
the path, regardless of the sender's initial zero ID. The receiver uses a normal
IPv4 UDP socket. Keep datagrams below MTU (the default payload is small).

## Packet and checksum scope

The wire order is Ethernet, IPv4, UDP, project header, application bytes.
The controller packet-in metadata remains before Ethernet in the deparser.
Only unfragmented IPv4 with IHL=5, UDP destination 5000, and an 8-byte project
header enters explicit forwarding. IPv4 options and fragments stay on the
original bridge path without path stamping. IPv6 and VLAN project routing are
outside this change. Malformed parsed packets drop.

Every explicitly forwarded project datagram has UDP checksum set to zero. This
is a supported IPv4 UDP representation meaning no UDP checksum; it avoids an
invalid checksum when path_id changes, including packets initially checksummed
by Linux. IP fields are unchanged, so the IPv4 header checksum is unchanged.
This approach does not support IPv6 UDP. Non-project UDP checksums are preserved.

Stop controllers with Ctrl+C before exiting Mininet. `exit` triggers the
existing Makefile cleanup of generated pipeline files and controller logs.

## Validation performed

Validated on the repository's Docker Stratum BMv2 target:

- `make project-config` generated port mappings matching live Mininet `net`.
- `make .p4-build P4_PROGRAM_NAME=bridge` compiled successfully; all four
  `make controller name=bridge grpc_port=...` invocations also built and loaded.
- h1 to h3 through path 0: `step_id=258 seq_no=16909060 path_id=0 flags=165
  payload=b'path-zero'`.
- h1 to h3 through path 1: the same header fields with `path_id=1` and
  `payload=b'path-one'`.
- Captures on s1-eth1 and s2-eth1 confirmed the respective physical routes
  and IPv4 UDP checksum zero (`[no cksum]`).
- `pingall`: 0% dropped, 12/12 received, exercising ARP and bridge packet-in learning.
- A normal UDP socket on h3 port 5001 received `b'ok'` from h1, exercising
  the non-project UDP parser/deparser and bridge path.
- `git diff --check` passed.

No required validation remains blocked. IPv4 options, fragmentation, and IPv6
project path stamping are outside the supported packet format described above.
