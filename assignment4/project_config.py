"""Generate controller routes from Mininet's actual topology port allocation."""
import importlib.util
import json


def build_config():
    spec = importlib.util.spec_from_file_location('clos', '2spine2leaf_clos.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    topo = module.MyTopo()
    leaves = {'s3': ['h1', 'h2'], 's4': ['h3', 'h4']}
    spines = ['s1', 's2']
    hosts = {h: '10.0.0.%d' % i for i, h in enumerate(topo.hosts(), 1)}
    port = lambda a, b: topo.port(a, b)[0]
    config = {'switch': {}, 'host': hosts}
    for i, switch in enumerate(topo.switches(), 1):
        routes = []
        if switch in leaves:
            local = leaves[switch]
            for host, ip in hosts.items():
                if host in local:
                    for neighbor in spines + [h for h in local if h != host]:
                        routes.append({'dst': ip, 'ingress': port(switch, neighbor),
                                       'port': port(switch, host)})
                else:
                    for source in local:
                        routes.append({'dst': ip, 'ingress': port(switch, source),
                                       'paths': [port(switch, s) for s in spines]})
            flood_ports = [port(switch, 's1')] + [port(switch, h) for h in local]
        else:
            for leaf, attached in leaves.items():
                other = next(l for l in leaves if l != leaf)
                for host in attached:
                    routes.append({'dst': hosts[host], 'ingress': port(switch, other),
                                   'port': port(switch, leaf)})
            flood_ports = [port(switch, l) for l in leaves] if switch == 's1' else []
        config['switch'][str(50000 + i)] = {
            'name': switch, 'mcast': {'id': 1, 'ports': flood_ports}, 'project': routes}
    return config


if __name__ == '__main__':
    print(json.dumps(build_config(), indent=2))
