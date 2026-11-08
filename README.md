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

## Getting Started

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


