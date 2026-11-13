# CSE589-Networks

## Overview

This project investigates whether separating synchronization-related feedback from genuine congestion feedback can improve routing decisions for distributed collective communication.

Inspired by Symphony and ConWeave, we implement a simplified P4-based datacenter network and evaluate how different feedback and routing policies affect collective completion time, path changes, and packet reordering.

## Network Topology

We use a two-spine, two-leaf Clos topology consisting of:

- 2 spine switches (s1, s2)
- 2 leaf switches (s3, s4)
- 4 hosts (h1–h4)
- 2 equal-cost paths between leaves

Hosts h1 and h2 attach to s3, while h3 and h4 attach to s4. Every leaf connects to both spines.

Switches run Stratum BMv2 inside a Docker-based Mininet environment.

## Prerequisites

- Linux environment
- Docker with permission to run containers
- GNU Make
- Course-provided `scripts/` directory and Stratum Docker environment

Mininet and the P4 toolchain run inside Docker. A separate local Mininet installation is not required.

## Automated development workflow

Run from `assignment4/` (use `sudo make` if Docker requires it):

```bash
make project-up
make test-paths
make test-paced count=100 rate=100 path_id=0
make test-paced count=1000 rate=1000 path_id=1
make project-down
```

- `project-up` generates port mappings from the actual Clos topology, launches
  Mininet, and builds/installs the bridge pipeline through the existing controller
  target on all four switches. It waits for the Mininet CLI and completed
  forwarding-entry installation before reporting PASS. Startup failures clean up
  the managed containers and return a nonzero exit status. Repeating startup
  while everything is ready reports “already running”.
- `test-paths` selects each path automatically and sends three h1 → h3 packets.
  It verifies exact sequence delivery and path IDs, plus packet captures showing
  all three packets on the selected spine and none on the other. Capture
  interfaces come from the generated topology configuration.
- `test-paced` selects the requested source-leaf path automatically and runs the
  existing sender/receiver. Options are `count=100`, `rate=100`, `path_id=0`,
  `payload_size=64`, and `test_timeout=5` (receiver idle seconds). It prints both
  tools' statistics and returns FAIL on loss, duplicates, reordering, incorrect
  path IDs, process errors, or readiness timeouts. Both test targets require a
  successful `project-up` and leave the environment running for debugging.
- `project-down` interrupts controllers and sends `exit` to the Mininet CLI so
  hosts, links, and switches are removed normally. It falls back to bounded
  Docker shutdown if a component is unresponsive. It is safe to repeat. Build
  artifacts and logs remain available; the existing `make clean` still removes
  `cfg/` and `logs/` when explicitly requested.

No extra Python packages are needed: orchestration uses Python 3 standard
libraries, Docker, Make, and the repository's existing images. Physical-route
checks use `tcpdump` and `timeout` already present in the Mininet image. Host
commands use Mininet's `mnexec`, without needing interactive terminals.

Logs and test CSVs are kept under `assignment4/.project-run/` (gitignored).
Each test prints its own result directory with `sender.log`, `receiver.log`,
`tx.csv`, `rx.csv`, and, for path tests, `spine0.log` / `spine1.log`.
Mininet/controller logs are saved at the top level there on readiness checks
and before shutdown. Build logs include the controller gRPC port. Live logs:

```bash
docker logs -f mn-stratum
docker logs -f p4runtime-sh-50003
make host name=h1
```

The existing manual `make mininet`, `make controller`, and `make host` commands
remain available. Automation uses the standard container names and gRPC ports
50001–50004; stop manual components before `project-up`. Ownership labels ensure
`project-down` never removes containers belonging to manual launches or another
checkout. A lock rejects concurrent automation commands in the same checkout.
Tests run one h1/h3 stream at a time; avoid concurrent manual traffic on port
5000 during route captures. Switching paths automatically restarts only the s3
controller, so do not send during that transition. This workflow targets the
existing default Clos topology; linear topologies remain a manual debugging path.

Workflow validation completed on the Docker Stratum target:

| Check | Result |
|---|---|
| Startup and repeated startup | PASS; four controllers ready, no duplicate containers |
| Both physical paths | PASS; 3/3 packets on selected spine, 0 on other, correct IDs |
| Paced path 0, 100 at 100 packets/sec | PASS; 100 received, sender 99.98 packets/sec |
| Paced path 1, 1000 at 1000 packets/sec | PASS; 1000 received, sender 999.81 packets/sec |
| Deliberately disabled spine 1 link | Expected FAIL with missing=3 and nonzero status |
| Restored link | PASS; 3/3 received |
| Shutdown | PASS; Mininet log confirms all eight links, four switches, and four hosts stopped |

The paced runs had zero loss, duplicates, or reordering. All nine unit tests and shell
syntax/whitespace checks also passed. Lifecycle tests cover startup rollback,
cleanup continuing after an error, and preserving unmanaged containers. See [paced traffic](docs/paced-traffic.md)
for timestamp and statistics definitions.

## Manual Getting Started

Run the following commands from the `assignment4/` directory.

### 1. Start the network

```bash
make mininet
```

This launches the custom topology defined in `2spine2leaf_clos.py`.

### 2. Verify the topology

Inside the Mininet CLI:

```bash
nodes
net
links
```

Expected nodes: `h1 h2 h3 h4 s1 s2 s3 s4`.

The four Stratum switches should listen on:

| Switch | gRPC Port | Role |
|---|---|---|
| s1 | 50001 | Spine |
| s2 | 50002 | Spine |
| s3 | 50003 | Leaf |
| s4 | 50004 | Leaf |

### 3. Test connectivity

```bash
pingall
```

Note: Connectivity will fail until the P4 pipelines and forwarding rules are configured. Successful topology startup does not imply packet forwarding is working.

### 4. Exit Mininet

```bash
exit
```

If Mininet leaves stale resources after an abnormal exit, perform cleanup before relaunching.

## P4 Programming

The project uses P4-16 with the BMv2 v1model target. P4Runtime is used to load switch pipelines and install forwarding entries.

The Assignment 4 infrastructure provides:

- P4 compilation
- P4Runtime controller scripts
- Switch configuration and logs
- Mininet/Stratum execution

## Available Topologies

The project supports both the custom Clos topology and the original Assignment 4 linear topologies.

**Custom Clos topology (default)**

```bash
make mininet
```

Creates two spine switches, two leaf switches, and four hosts.

**Linear topology — 2 switches**

```bash
make mininet topo=linear,2,2
```

Useful for validating P4 packet processing and basic forwarding.

**Linear topology — 3 switches**

```bash
make mininet topo=linear,3,2
```

Useful for testing forwarding and packet processing across multiple switches.

The custom Clos topology is used for the project's primary experiments. Linear topologies are retained for debugging and regression testing.

**Note:** Starting a topology does not automatically install the P4 forwarding rules. Controller configuration must match the selected topology.



## Explicit project paths

See [project setup and validation](docs/project-paths.md) for the normal bridge
build/controller commands, path selection, UDP tools, and checksum behavior.
