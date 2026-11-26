############################################################################
##
##     This file is part of the University of Michigan (U-M) EECS 489.
##
##     U-M EECS 489 is free software: you can redistribute it and/or modify
##     it under the terms of the GNU General Public License as published by
##     the Free Software Foundation, either version 3 of the License, or
##     (at your option) any later version.
##
##     U-M EECS 489 is distributed in the hope that it will be useful,
##     but WITHOUT ANY WARRANTY; without even the implied warranty of
##     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
##     GNU General Public License for more details.
##
##     You should have received a copy of the GNU General Public License
##     along with U-M EECS 489. If not, see <https://www.gnu.org/licenses/>.
##
#############################################################################

import json
import argparse
import contextlib
import p4runtime_sh.shell as p4sh
from p4.v1 import p4runtime_pb2 as p4rt


###############################################################################
# Default parameters
###############################################################################

# Relative path of the configuration and logs directories
CFG_DIR = 'cfg'
LOGS_DIR = 'logs'

# Bridge ID 
BRIDGE_ID = 1
BRIDGE_CPU_PORT = 255

# Logs threshold
NUM_LOGS_THRESHOLD = 10


###############################################################################
# Helper functions 
###############################################################################

# MAC address in bytes to string
def mac2str(mac):
    return ':'.join('{:02x}'.format(b) for b in mac)


###############################################################################
# Multicast group functions 
###############################################################################

# Create a multicast group entry
def InstallMcastGrpEntry(mcast_group_id, bridge_ports):
    mcast_entry = p4sh.MulticastGroupEntry(mcast_group_id)
    for port in bridge_ports:
        mcast_entry.add(port)
    mcast_entry.insert()

# Delete a multicast group entry
def DeleteMcastGrpEntry(mcast_group_id):
    mcast_entry = p4sh.MulticastGroupEntry(mcast_group_id)
    mcast_entry.delete()


###############################################################################
# Packet processing functions
###############################################################################

# Process incoming packets
def ProcPacketIn(bridge_name, logs_dir, num_logs_threshold):
    try:
        num_logs = 0
        while True:
            rep = p4sh.client.get_stream_packet("packet", timeout=1)
            if rep is not None:
                # Read the raw packet
                payload = rep.packet.payload
                
                # Parse Metadata
                ingress_port_in_bytes = rep.packet.metadata[0].value
                ingress_port = int.from_bytes(ingress_port_in_bytes, "big")

                # Parse Ethernet header (source and destination MAC)
                dst_mac_in_bytes = payload[0:6]
                dst_mac = mac2str(dst_mac_in_bytes)
                src_mac_in_bytes = payload[6:12]
                src_mac = mac2str(src_mac_in_bytes)

                print("PacketIn: dst={0} src={1} port={2}".format(
                    dst_mac, src_mac, ingress_port))

                try:
                    with contextlib.redirect_stdout(None):  # A hack to suppress print statements 
                        # within the table_entry.match get/set objects

                        ##################################################################################
                        # Learning bridge logic - Begins #################################################
                        ##################################################################################
                        
                        # Install a flow entry to drop packets beloning to the same segment
                        table_entry = p4sh.TableEntry('MyIngress.bridge_table')(action='MyIngress.drop')
                        table_entry.match['hdr.ethernet.dstAddr'] = src_mac
                        table_entry.match['standard_metadata.ingress_port'] = str(ingress_port)
                        table_entry.insert()

                        ##################################################################################
                        # Learning bridge logic - Ends ###################################################
                        ##################################################################################

                except:
                    pass

            # Log the Ethernet address to port mapping
            num_logs += 1
            if num_logs == num_logs_threshold:
                num_logs = 0
                with open('{0}/{1}-table.json'.format(logs_dir, bridge_name), 'w') as outfile:
                    with contextlib.redirect_stdout(outfile):
                        p4sh.TableEntry('MyIngress.bridge_table').read(lambda te: print(te))
                print(
                    "INFO: Log committed to {0}/{1}-table.json".format(logs_dir, bridge_name))
                # T12: also save how many packets this switch marked SYNC / CONG
                try:
                    with open('{0}/{1}-counters.txt'.format(logs_dir, bridge_name), 'w') as outfile:
                        with contextlib.redirect_stdout(outfile):
                            p4sh.CounterEntry('MyEgress.sync_marks').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyIngress.cong_marks').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyIngress.exclusions').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyIngress.reroutes').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyIngress.rtt_requests').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyIngress.rtt_slow').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyIngress.no_alternative').read(lambda ce: print(ce))
                            p4sh.CounterEntry('MyEgress.stale_moves').read(lambda ce: print(ce))
                except Exception as error:
                    print('WARNING: could not read counters: {0}'.format(error))
    except KeyboardInterrupt:
        return None


###############################################################################
# Main 
###############################################################################
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Learning Bridge Script')
    parser.add_argument('--grpc-port', help='GRPC Port', required=True,
                        type=str, action="store", default='50001')
    parser.add_argument('--topo-config', help='Topology Configuration File', required=True,
                        type=str, action="store")
    parser.add_argument('--path-id', type=int, choices=(0, 1), default=0)
    # T15: shared = SYNC or CONG excludes a path, separated = only CONG
    # T15: if --mode is not given, read it from feedback_mode.txt (missing file = separated)
    parser.add_argument('--mode', choices=('separated', 'shared'), default=None)
    args = parser.parse_args()
    if args.mode is None:
        try:
            with open('feedback_mode.txt') as infile:
                args.mode = infile.read().strip()
        except FileNotFoundError:
            args.mode = 'separated'
        if args.mode not in ('separated', 'shared'):
            print('WARNING: unknown mode {0!r} in feedback_mode.txt, using separated'.format(args.mode))
            args.mode = 'separated'

    # Create a bridge name postfixed with the grpc port number
    bridge_name = 'bridge-{0}'.format(args.grpc_port)

    # Get Multicast to ports mapping
    with open(args.topo_config, 'r') as infile:
        topo_config = json.loads(infile.read())

    mcast_group_id = topo_config['switch'][args.grpc_port]['mcast']['id']
    mcast_group_ports = topo_config['switch'][args.grpc_port]['mcast']['ports']

    # Setup the P4Runtime connection with the bridge
    p4sh.setup(
        device_id=BRIDGE_ID, grpc_addr='127.0.0.1:{0}'.format(args.grpc_port), election_id=(0, 1),
        config=p4sh.FwdPipeConfig(
            '{0}/{1}-p4info.txt'.format(CFG_DIR, bridge_name),  # Path to P4Info file
            '{0}/{1}.json'.format(CFG_DIR, bridge_name)  # Path to config file
        )
    )

    print("Bridge Started @ Port: {0}".format(args.grpc_port))
    print("Press CTRL+C to stop ...")

    # Install broadcast rule (with CPU port)
    InstallMcastGrpEntry(mcast_group_id, mcast_group_ports + [BRIDGE_CPU_PORT])

    # Optional project routes; linear assignment configurations are unchanged.
    with contextlib.redirect_stdout(None):
        for route in topo_config['switch'][args.grpc_port].get('project', []):
            selecting = 'paths' in route
            action = 'select_path' if selecting else 'forward_to_port'
            entry = p4sh.TableEntry('MyIngress.project_forward')(
                action='MyIngress.' + action)
            entry.match['hdr.ipv4.dstAddr'] = route['dst']
            entry.match['standard_metadata.ingress_port'] = str(route['ingress'])
            if selecting:
                # T13: give the switch both spine ports, it picks one per packet
                entry.action['port0'] = str(route['paths'][0])
                entry.action['port1'] = str(route['paths'][1])
                entry.action['pref'] = str(args.path_id)
            else:
                entry.action['port'] = str(route['port'])
            entry.insert()
    if args.mode == 'shared':
        try:
            entry = p4sh.TableEntry('MyIngress.feedback_mode')(action='MyIngress.set_mode')
            entry.action['exclude_mask'] = str(0x03)
            entry.is_default = True
            entry.modify()
        except Exception as error:
            print('WARNING: could not set shared mode: {0}'.format(error))
    print('Feedback mode: {0}'.format(args.mode))
    print('Project path: {0}'.format(args.path_id))

    # Start the packet-processing loop
    ProcPacketIn(bridge_name, LOGS_DIR, NUM_LOGS_THRESHOLD)

    print("Bridge Stopped")

    # Delete the broadcast rule
    DeleteMcastGrpEntry(mcast_group_id)

    # Close the P4Runtime connection
    p4sh.teardown()
