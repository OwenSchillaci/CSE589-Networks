/*****************************************************************************
 *
 *     This file is part of the University of Michigan (U-M) EECS 489.
 *
 *     U-M EECS 489 is free software: you can redistribute it and/or modify
 *     it under the terms of the GNU General Public License as published by
 *     the Free Software Foundation, either version 3 of the License, or
 *     (at your option) any later version.
 *
 *     U-M EECS 489 is distributed in the hope that it will be useful,
 *     but WITHOUT ANY WARRANTY; without even the implied warranty of
 *     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *     GNU General Public License for more details.
 *
 *     You should have received a copy of the GNU General Public License
 *     along with U-M EECS 489. If not, see <https://www.gnu.org/licenses/>.
 *
 *****************************************************************************/

/* -*- P4_16 -*- */
#include <core.p4>
#include <v1model.p4>

#define MCAST_ID 1
#define CPU_PORT 255
// bits in hdr.project.flags (T11)
#define FLAG_SYNC 0x01
#define FLAG_CONG 0x02
#define FLAG_FEEDBACK 0x04
#define FLAG_LAST 0x08
// T14: on data = RTT request, on feedback = RTT reply (0x10 is ACK, used by the hosts)
#define FLAG_RTT 0x20
// T12: forget the sync state after 0.5 s with no project packets (BMv2 time is in microseconds)
#define SYNC_IDLE_RESET 500000
// T13: how long a path stays excluded after feedback (us), and which marks exclude it
#define PATH_BUSY_TIME 50000
#define EXCLUDE_ON FLAG_CONG   // default = separated mode
// T14: a flow can change path only at the start of a chunk (seq_no % 64 == 0)
#define CHUNK_MASK 63
// T14: RTT above this (us) means the path is slow; normal RTT here is about 1-2 ms
#define RTT_LIMIT 20000
// T14: after a slow RTT this flow avoids that path for 0.5 s (longer than one chunk)
#define RTT_BAD_TIME 500000
/*
  255 is confirmed for opennetworking/p4mn　(Mininet/Stratum on docker)
  192 is confirmed for WEDGE-100BF-32X (2 pipes device)
  320 is probably good for 4 pipes devices
*/

typedef bit<48> macAddr_t;

/**************************************************************************/
/**************************  Headers  *************************************/
/**************************************************************************/

@controller_header("packet_in")
header packet_in_header_t {
    bit<9> ingress_port;
    bit<7> _pad;
}

header project_t {
    bit<16> step_id;
    bit<32> seq_no;
    bit<8>  path_id;
    bit<8>  flags;
    // T07: largest enq_qdepth seen on any switch along the path
    bit<16> qdepth;
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> totalLen;
    bit<16> identification;
    bit<3>  flags;
    bit<13> fragOffset;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> hdrChecksum;
    bit<32> srcAddr;
    bit<32> dstAddr;
}

header udp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<16> length;
    bit<16> checksum;
}


header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16> etherType;
}


struct metadata {
    // T13: filled by select_path, used to choose the path per packet
    bit<1> selected;
    bit<9> port0;
    bit<9> port1;
    bit<8> pref;
    // T15: which marks exclude a path (separated = CONG, shared = SYNC | CONG)
    bit<8> exclude_mask;
}

struct headers {
    ethernet_t ethernet;
    ipv4_t ipv4;
    udp_t udp;
    project_t project;
    packet_in_header_t packet_in;
}

/**************************************************************************/
/***************************  Parser  *************************************/
/**************************************************************************/

parser MyParser(packet_in packet,
                out headers hdr,
                inout metadata meta,
                inout standard_metadata_t standard_metadata) {

    state start { transition parse_ethernet; }

    state parse_ethernet {
        packet.extract(hdr.ethernet);

        transition select(hdr.ethernet.etherType) {
            0x0800: parse_ipv4;
            default: accept;
        }
    }
    
    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        // Options and fragments stay opaque and retain bridge behavior.
        transition select(hdr.ipv4.version, hdr.ipv4.ihl,
                          hdr.ipv4.flags & 1, hdr.ipv4.fragOffset,
                          hdr.ipv4.protocol) {
            (4, 5, 0, 0, 17): parse_udp;
            default: accept;
        }

    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition select(hdr.udp.dstPort) {
            5000: parse_project;
            default: accept;
        }

    }
    state parse_project {
        verify(hdr.udp.length >= 16 &&
               hdr.ipv4.totalLen >= hdr.udp.length + 20,
               error.PacketTooShort);
        packet.extract(hdr.project);
        transition accept;

    }
}

/**************************************************************************/
/*********************  Checksum Verification  *****************************/
/**************************************************************************/

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { }
}

/**************************************************************************/
/***********************  Ingress Processing  *****************************/
/**************************************************************************/

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    // T12: how many packets this switch marked CONG (first switch to set it)
    counter(1, CounterType.packets) cong_marks;

    // T13: per path, the time until which the path is excluded (source leaf)
    register<bit<48>>(2) busy_until;
    counter(1, CounterType.packets) exclusions;   // a free path became excluded
    counter(1, CounterType.packets) reroutes;     // T14: a flow moved to the other path

    // T14: per flow state at the source leaf, flow index = (src host, dst host)
    register<bit<1>>(64)  flow_has_path;
    register<bit<8>>(64)  flow_path;     // path the flow is on right now
    register<bit<48>>(64) rtt_sent;      // time the RTT request left, 0 = none out
    register<bit<8>>(64)  rtt_path;      // path the RTT request went on
    register<bit<48>>(64) flow_last_seen;
    register<bit<48>>(128) rtt_bad_until;  // index flow*2 + path, slow for this flow until then
    counter(1, CounterType.packets) rtt_requests;
    counter(1, CounterType.packets) rtt_slow;        // reply too late or never came
    counter(1, CounterType.packets) no_alternative;  // wanted to move but both paths busy

    action drop() {
        mark_to_drop(standard_metadata);
    }

    action flood() {
        standard_metadata.mcast_grp = MCAST_ID;
    }

    // T15: the controller picks the mode by changing this table's default action
    action set_mode(bit<8> exclude_mask) {
        meta.exclude_mask = exclude_mask;
    }

    table feedback_mode {
        actions = { set_mode; }
        default_action = set_mode(EXCLUDE_ON);
    }

    action forward_to_port(bit<9> port) {
        standard_metadata.egress_spec = port;
    }

    // T13: the controller gives both spine ports and the preferred path,
    // the real choice is made in apply() after looking at busy_until
    action select_path(bit<9> port0, bit<9> port1, bit<8> pref) {
        meta.selected = 1;
        meta.port0 = port0;
        meta.port1 = port1;
        meta.pref = pref;
    }

    // Entries on source leaves select a path; transit/destination entries
    // preserve the stamped ID. Missing project routes fail closed.
    table project_forward {
        key = {
            hdr.ipv4.dstAddr: exact;
            standard_metadata.ingress_port: exact;
        }
        actions = { select_path; forward_to_port; drop; }
        size = 128;
        default_action = drop();
    }

    table bridge_table {
        key = {
            hdr.ethernet.dstAddr: exact;
            standard_metadata.ingress_port: exact;
        }
        actions = {
            drop;
            flood;
        }
        size = 1024;
        default_action = flood;
    }
    
    apply {
        if (standard_metadata.parser_error != error.NoError) {
            drop();
        } else if (hdr.project.isValid()) {
            // T09: feedback carries the forward path_id, remember it before the table runs
            bit<8> fwd_path = hdr.project.path_id;
            meta.selected = 0;
            feedback_mode.apply();
            project_forward.apply();
            bit<48> now = standard_metadata.ingress_global_timestamp;

            if (meta.selected == 1) {
                // T13: we are the source leaf, pick the path for this packet
                bit<8> chosen = meta.pref;
                if ((hdr.project.flags & FLAG_FEEDBACK) == 0) {
                    bit<32> flow = (((bit<32>) hdr.ipv4.srcAddr & 7) << 3) | ((bit<32>) hdr.ipv4.dstAddr & 7);
                    bit<1> has;
                    bit<8> cur;
                    bit<48> sent;
                    flow_has_path.read(has, flow);
                    flow_path.read(cur, flow);
                    rtt_sent.read(sent, flow);
                    // T14: like T12, a 0.5 s gap = a new trial, start again on the preferred path
                    bit<48> fseen;
                    flow_last_seen.read(fseen, flow);
                    if (now - fseen > SYNC_IDLE_RESET) {
                        has = 0;
                        sent = 0;
                    }
                    flow_last_seen.write(flow, now);
                    if (has == 0) {
                        cur = meta.pref;
                    }

                    // T14: RTT request still not back after RTT_LIMIT -> that path is slow
                    if (sent != 0 && now - sent > RTT_LIMIT) {
                        bit<8> rp;
                        rtt_path.read(rp, flow);
                        rtt_bad_until.write(flow * 2 + (bit<32>) rp, now + RTT_BAD_TIME);
                        rtt_slow.count(0);
                        sent = 0;
                    }

                    bit<48> b0;
                    bit<48> b1;
                    busy_until.read(b0, 0);
                    busy_until.read(b1, 1);
                    // T14: RTT says slow for this flow, or CONG excluded it for everyone (T13)
                    bit<48> r0;
                    bit<48> r1;
                    rtt_bad_until.read(r0, flow * 2);
                    rtt_bad_until.read(r1, flow * 2 + 1);
                    bool busy0 = b0 > now || r0 > now;
                    bool busy1 = b1 > now || r1 > now;

                    // T14: only at the first packet of a chunk can the flow change path,
                    // so one chunk never gets split over two paths
                    if ((hdr.project.seq_no & CHUNK_MASK) == 0) {
                        bool cur_busy = (cur == 0 && busy0) || (cur == 1 && busy1);
                        bool other_busy = (cur == 0 && busy1) || (cur == 1 && busy0);
                        if (cur_busy && !other_busy) {
                            cur = cur ^ 1;
                            reroutes.count(0);
                        } else if (cur_busy) {
                            // both paths are excluded, nowhere better to go, stay
                            no_alternative.count(0);
                        }
                        // one RTT request per chunk, only when the last one is back
                        if (sent == 0) {
                            hdr.project.flags = hdr.project.flags | FLAG_RTT;
                            sent = now;
                            rtt_path.write(flow, cur);
                            rtt_requests.count(0);
                        }
                    }
                    flow_has_path.write(flow, 1);
                    flow_path.write(flow, cur);
                    rtt_sent.write(flow, sent);
                    chosen = cur;
                }
                if (chosen == 0) {
                    standard_metadata.egress_spec = meta.port0;
                } else {
                    standard_metadata.egress_spec = meta.port1;
                }
                hdr.project.path_id = chosen;
            }

            // select_path on the way back would overwrite it, so put it back
            if ((hdr.project.flags & FLAG_FEEDBACK) != 0) {
                hdr.project.path_id = fwd_path;
                // T13: feedback reached the leaf of the original sender (not chosen
                // by select_path here), so exclude the forward path it names
                if (meta.selected == 0 && (hdr.project.flags & meta.exclude_mask) != 0) {
                    bit<48> old_busy;
                    busy_until.read(old_busy, (bit<32>) fwd_path);
                    if (old_busy <= now) {
                        exclusions.count(0);
                    }
                    busy_until.write((bit<32>) fwd_path, now + PATH_BUSY_TIME);
                }
                // T14: RTT reply back at the sender's leaf. The reply goes dst -> src,
                // so swap the addresses to find the sender's flow
                if (meta.selected == 0 && (hdr.project.flags & FLAG_RTT) != 0) {
                    bit<32> rflow = (((bit<32>) hdr.ipv4.dstAddr & 7) << 3) | ((bit<32>) hdr.ipv4.srcAddr & 7);
                    bit<48> rsent;
                    rtt_sent.read(rsent, rflow);
                    if (rsent != 0) {
                        if (now - rsent > RTT_LIMIT) {
                            rtt_bad_until.write(rflow * 2 + (bit<32>) fwd_path, now + RTT_BAD_TIME);
                            rtt_slow.count(0);
                        } else {
                            // RTT is fine again on this path
                            rtt_bad_until.write(rflow * 2 + (bit<32>) fwd_path, 0);
                        }
                        rtt_sent.write(rflow, 0);
                    }
                }
            }
            // T11: if the slow cable marked ECN CE (11) on this packet, save the
            // cause in our own CONG flag so it is not mixed up with SYNC later
            if ((hdr.ipv4.diffserv & 0x03) == 0x03) {
                if ((hdr.project.flags & FLAG_CONG) == 0) {
                    cong_marks.count(0);
                }
                hdr.project.flags = hdr.project.flags | FLAG_CONG;
            }
            // IPv4 permits checksum zero (UDP checksum disabled). No IP
            // fields change, so the original IPv4 checksum remains valid.
            hdr.udp.checksum = 0;
        } else {
            bridge_table.apply();
        }
    }
}

/**************************************************************************/
/************************  Egress Processing  *****************************/
/**************************************************************************/

control MyEgress(inout headers hdr,
                 inout metadata meta,
                 inout standard_metadata_t standard_metadata) {
    action drop() {
        mark_to_drop(standard_metadata);
    }

    action to_controller() {
        hdr.packet_in.setValid();
        hdr.packet_in.ingress_port = standard_metadata.ingress_port;
    }

    // T10: Symphony-style progress state, one job only so index 0
    register<bit<1>>(1)  sync_started;
    register<bit<16>>(1) sync_step_min;   // slowest step still running
    register<bit<32>>(1) sync_psn_rec;    // highest seq_no seen in step_min
    register<bit<48>>(1) sync_last_seen;  // T12: time of the last data packet
    // T12: how many packets this switch marked SYNC (first switch to set it)
    counter(1, CounterType.packets) sync_marks;

    apply {
        // Prune multicast packets going to ingress port to prevent loops
        if (standard_metadata.egress_port == standard_metadata.ingress_port)
            drop();

        // Send a copy of the packet to the controller for learning
        if (standard_metadata.egress_port == CPU_PORT)
            to_controller();

        // T07: This records how many packets were already waiting in this port's
        // queue when this packet joined it. It further Keeps the maximum across S3/S1/S4.
        if (hdr.project.isValid()) {
            bit<16> q = (bit<16>) standard_metadata.enq_qdepth;
            if (q > hdr.project.qdepth) {
                hdr.project.qdepth = q;
            }
        }

        // T10: mark SYNC on data packets whose step is ahead of step_min
        // (feedback packets only echo a step, so skip them)
        if (hdr.project.isValid() && (hdr.project.flags & FLAG_FEEDBACK) == 0) {
            bit<1> started;
            bit<16> step_min;
            bit<32> psn_rec;
            sync_started.read(started, 0);
            sync_step_min.read(step_min, 0);
            sync_psn_rec.read(psn_rec, 0);
            bit<16> step = hdr.project.step_id;
            bit<32> seq = hdr.project.seq_no;

            // T12: long gap since the last packet = a new trial, so start fresh
            bit<48> now = standard_metadata.egress_global_timestamp;
            bit<48> last_seen;
            sync_last_seen.read(last_seen, 0);
            if (now - last_seen > SYNC_IDLE_RESET) {
                started = 0;
            }
            sync_last_seen.write(0, now);

            // first packet ever: start from its step
            if (started == 0) {
                started = 1;
                step_min = step;
                psn_rec = 0;
            }
            if ((hdr.project.flags & FLAG_LAST) != 0) {
                // this step is finished, the next one becomes the slowest
                step_min = step + 1;
                psn_rec = 0;
            } else if (step < step_min) {
                // a lagging packet, go back to its step
                step_min = step;
                psn_rec = seq;
            } else if (step == step_min) {
                if (seq > psn_rec) {
                    psn_rec = seq;
                }
            }
            if (step > step_min) {
                if ((hdr.project.flags & FLAG_SYNC) == 0) {
                    sync_marks.count(0);
                }
                hdr.project.flags = hdr.project.flags | FLAG_SYNC;
            }

            sync_started.write(0, started);
            sync_step_min.write(0, step_min);
            sync_psn_rec.write(0, psn_rec);
        }
    }
}

/**************************************************************************/
/*********************  Checksum Computation  *****************************/
/**************************************************************************/

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
    apply { }
}

/**************************************************************************/
/**************************  Deparser  ************************************/
/**************************************************************************/

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        packet.emit(hdr.packet_in);
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.project); 
   }
}

/**************************************************************************/
/***************************  Bridge  *************************************/
/**************************************************************************/

V1Switch(
MyParser(),
MyVerifyChecksum(),
MyIngress(),
MyEgress(),
MyComputeChecksum(),
MyDeparser()
) main;
