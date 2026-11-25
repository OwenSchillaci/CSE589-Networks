"""Small Docker/Make wrapper for the repository's existing Clos workflow."""
import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import pty
from pathlib import Path
import subprocess
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.project-run'
LABEL = 'cse589.project=' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
NETWORK = 'mn-stratum'
CONTROLLERS = ['p4runtime-sh-%d' % port for port in range(50001, 50005)]


def run(argv, timeout=180, **kwargs):
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, **kwargs)
    if result.returncode:
        raise RuntimeError('%s failed (%d):\n%s' %
                           (' '.join(argv), result.returncode, result.stdout[-2000:]))
    return result.stdout


def inspect(name):
    result = subprocess.run(['docker', 'inspect', name], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def owned(info):
    key, value = LABEL.split('=', 1)
    return info and info['Config'].get('Labels', {}).get(key) == value


def save_logs(name):
    result = subprocess.run(['docker', 'logs', name], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
    (STATE / (name + '.log')).write_text(result.stdout)
    return result.stdout


def wait_for(check, description, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise RuntimeError('timeout waiting for ' + description)


def ready(name, marker):
    def check():
        info = inspect(name)
        logs = save_logs(name)
        if not owned(info) or not info['State']['Running']:
            raise RuntimeError('%s stopped; see %s' % (name, STATE / (name + '.log')))
        return marker in logs
    wait_for(check, name + ': ' + marker)


def make(*args, env=None):
    suffix = next((arg.split('=')[1] for arg in args if arg.startswith('grpc_port=')), '')
    log = STATE / ('build-' + args[0].lstrip('.') + ('-' + suffix if suffix else '') + '.log')
    with log.open('w') as output:
        result = subprocess.run(['make', '--no-print-directory'] + list(args), env=env,
                                stdout=output, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError('make failed; see ' + str(log))


def stop(name):
    info = inspect(name)
    if not owned(info):
        return
    if info['State']['Running']:
        if name == NETWORK:
            # Keep the Mininet CLI stdin open in detached mode; exit tears down
            # hosts, links, and switches using Mininet's normal shutdown path.
            master, slave = pty.openpty()
            attach = subprocess.Popen(['docker', 'attach', '--sig-proxy=false', name],
                                      stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            try:
                # Docker requires a real TTY for an interactive container. Its
                # raw-mode transition signals that stdin is ready to forward.
                wait_for(lambda: not termios.tcgetattr(master)[3] & termios.ICANON,
                         'Mininet attach stdin', timeout=5)
                os.write(master, b'exit\n')
                attach.wait(timeout=15)
            except (RuntimeError, subprocess.TimeoutExpired):
                print('Mininet CLI did not exit; using Docker shutdown', flush=True)
            finally:
                if attach.poll() is None:
                    attach.terminate()
                    attach.wait(timeout=5)
                os.close(master)
        else:
            run(['docker', 'kill', '--signal=SIGINT', name])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not inspect(name)['State']['Running']:
                break
            time.sleep(0.2)
        if inspect(name)['State']['Running']:
            run(['docker', 'stop', '--time=3', name])
    save_logs(name)
    run(['docker', 'rm', name])


def down():
    errors = []
    for name in CONTROLLERS + [NETWORK]:
        try:
            stop(name)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            errors.append('%s: %s' % (name, error))
    if errors:
        raise RuntimeError('; '.join(errors))


def start_controller(port, path):
    env = dict(os.environ, P4RUNTIME_SH_RUN_FLAGS='-d --label ' + LABEL)
    make('controller', 'name=bridge', 'topo=mytopo', 'grpc_port=%d' % port,
         'path_id=%d' % path, env=env)
    ready('p4runtime-sh-%d' % port, 'Project path: %d' % path)


def up():
    existing = [inspect(name) for name in [NETWORK] + CONTROLLERS]
    if any(existing):
        if all(owned(info) and info['State']['Running'] for info in existing):
            require_up()
            print('PASS project-up: already running')
            return
        raise RuntimeError('container names already in use or partial startup; '
                           'run project-down for managed containers, or stop manual components')
    try:
        make('project-config')
        env = dict(os.environ, MN_STRATUM_RUN_FLAGS='-dit --label ' + LABEL)
        run(['../scripts/mn-stratum', '--custom', '2spine2leaf_clos.py', '--topo', 'mytopo'], env=env)
        ready(NETWORK, 'Starting CLI:')
        for port in range(50001, 50005):
            start_controller(port, 0)
        print('PASS project-up: Clos and four controllers ready (path 0)')
    except BaseException:
        down()
        raise


def require_up():
    for name in [NETWORK] + CONTROLLERS:
        info = inspect(name)
        if not owned(info) or not info['State']['Running']:
            raise RuntimeError('run make project-up first; missing managed ' + name)
    ready(NETWORK, 'Starting CLI:')
    for name in CONTROLLERS:
        ready(name, 'Project path:')


def select_path(path):
    name = 'p4runtime-sh-50003'
    if 'Project path: %d' % path not in save_logs(name):
        stop(name)
        start_controller(50003, path)


def host_command(host, *args, pid_file=None):
    prefix = ['--pid-file', pid_file] if pid_file else []
    return ['docker', 'exec', NETWORK, 'python3', '-B', 'project/host_exec.py'] + prefix + [host] + list(args)


def traffic(path, count, rate, size, timeout, physical=False):
    select_path(path)
    folder = STATE / ('test-%d-path%d' % (time.time_ns(), path))
    folder.mkdir()
    relative = '/workdir/' + str(folder.relative_to(ROOT))
    config = json.loads((ROOT / 'topo/mytopo.json').read_text())
    destination = config['host']['h3']
    children = []
    files = []

    def launch(argv, filename):
        output = (folder / filename).open('w')
        files.append(output)
        proc = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT, text=True)
        children.append(proc)
        return proc

    def log_ready(proc, filename, marker):
        def check():
            if proc.poll() is not None:
                raise RuntimeError(filename + ' exited early; see ' + str(folder))
            return marker in (folder / filename).read_text()
        wait_for(check, filename, timeout=10)

    try:
        # Warm ARP before starting capture/traffic so it cannot delay the first sends.
        run(host_command('h1', 'ping', '-c', '1', '-W', '3', destination))
        captures = []
        if physical:
            for spine in (0, 1):
                switch = config['switch'][str(50001 + spine)]
                route = next(r for r in switch['project'] if r['dst'] == destination)
                interface = '%s-eth%d' % (switch['name'], route['ingress'])
                filename = 'spine%d.log' % spine
                capture = launch(['docker', 'exec', NETWORK, 'timeout', '-s', 'INT', '4',
                                  'tcpdump', '-l', '-nn', '-i', interface,
                                  'udp', 'dst', 'port', '5000', 'and', 'dst', 'host', destination], filename)
                captures.append(capture)
                log_ready(capture, filename, 'listening on')
        receiver = launch(host_command('h3', 'python3', 'project/receiver.py',
                          '--count', str(count), '--timeout', str(timeout),
                          '--output', relative + '/rx.csv', pid_file=relative + '/receiver.pid'), 'receiver.log')
        log_ready(receiver, 'receiver.log', 'Listening on UDP port 5000')
        output = run(host_command('h1', 'python3', 'project/sender.py', destination,
                     '--count', str(count), '--rate', str(rate), '--payload-size', str(size),
                     '--output', relative + '/tx.csv', pid_file=relative + '/sender.pid'), timeout=count / rate + 30)
        (folder / 'sender.log').write_text(output)
        if receiver.wait(timeout=count / rate + timeout + 10) != 0:
            raise RuntimeError('receiver failed')
        print(output.strip(), flush=True)
        print((folder / 'receiver.log').read_text().strip(), flush=True)
        for filename, stamp in [('tx.csv', 'send_monotonic_ns'), ('rx.csv', 'receive_monotonic_ns')]:
            with (folder / filename).open() as source:
                rows = list(csv.DictReader(source))
            if [int(row['seq_no']) for row in rows] != list(range(1, count + 1)):
                raise RuntimeError(filename + ': missing, duplicate, or reordered sequences')
            stamps = [int(row[stamp]) for row in rows]
            if stamps != sorted(stamps):
                raise RuntimeError(filename + ': timestamps moved backwards')
            if filename == 'rx.csv' and any(int(row['path_id']) != path for row in rows):
                raise RuntimeError('incorrect received path ID')
        for spine, capture in enumerate(captures):
            code = capture.wait(timeout=10)
            if code not in (0, 124):
                raise RuntimeError('tcpdump failed on spine %d' % spine)
            packets = sum(' IP ' in line for line in (folder / ('spine%d.log' % spine)).read_text().splitlines())
            expected = count if spine == path else 0
            if packets != expected:
                raise RuntimeError('spine %d captured %d packets; expected %d' % (spine, packets, expected))
        print('PASS %s: path=%d count=%d; logs: %s' %
              ('physical route + path ID' if physical else 'paced traffic', path, count, folder))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError('%s; test logs: %s' % (error, folder)) from error
    finally:
        for role in ('sender', 'receiver'):
            subprocess.run(['docker', 'exec', NETWORK, 'python3', '-B',
                            'project/host_exec.py', '--stop', relative + '/' + role + '.pid'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        # Receiver has its own idle timeout; captures have a bounded lifetime.
        # Reap docker exec clients even when validation fails or is interrupted.
        for child in children:
            try:
                child.wait(timeout=timeout + 10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
        for output in files:
            output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('project-up', 'project-down', 'test-paths', 'test-paced'))
    parser.add_argument('--path-id', type=int, choices=(0, 1), default=0)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--rate', type=float, default=100)
    parser.add_argument('--payload-size', type=int, default=64)
    parser.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args()
    if (not 1 <= args.count <= 0xffffffff or not math.isfinite(args.rate) or args.rate <= 0
            or not 0 <= args.payload_size <= 1462 or not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error('require positive count/rate/timeout and payload-size 0..1462')
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True)
    with (STATE / 'workflow.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(['docker', 'info'])
            if args.action == 'project-up':
                up()
            elif args.action == 'project-down':
                down()
                print('PASS project-down: managed containers stopped; logs retained')
            else:
                require_up()
                if args.action == 'test-paths':
                    for path in (0, 1):
                        traffic(path, 3, 10, 64, 5, physical=True)
                else:
                    traffic(args.path_id, args.count, args.rate, args.payload_size, args.timeout)
        except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            print('FAIL %s: %s\nLogs: %s' % (args.action, error, STATE), file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
