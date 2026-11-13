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


struct metadata { bit<1> unused; }

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

    action drop() {
        mark_to_drop(standard_metadata);
    }

    action flood() {
        standard_metadata.mcast_grp = MCAST_ID;
    }

    action forward_to_port(bit<9> port) {
        standard_metadata.egress_spec = port;
    }

    action select_path(bit<9> port, bit<8> path_id) {
        standard_metadata.egress_spec = port;
        hdr.project.path_id = path_id;
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
            project_forward.apply();
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

    apply {
        // Prune multicast packets going to ingress port to prevent loops
        if (standard_metadata.egress_port == standard_metadata.ingress_port)
            drop();

        // Send a copy of the packet to the controller for learning
        if (standard_metadata.egress_port == CPU_PORT)
            to_controller();
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
