"""Run an argv in an existing Mininet host namespace, without an interactive TTY."""
import json
import os
import signal
from pathlib import Path
import sys


def main():
    argv = sys.argv[1:]
    if argv[0] == '--stop':
        record = Path(argv[1])
        if record.exists():
            pid, started = json.loads(record.read_text())
            stat = Path('/proc/%d/stat' % pid)
            try:
                if stat.read_text().split()[21] == started:
                    os.kill(pid, signal.SIGINT)
            except (FileNotFoundError, ProcessLookupError):
                pass
        return
    if argv[0] == '--pid-file':
        Path(argv[1]).write_text(json.dumps(
            [os.getpid(), Path('/proc/self/stat').read_text().split()[21]]))
        argv = argv[2:]
    host, *command = argv
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if ('mininet:' + host).encode() in args:
            # mnexec ships with Mininet and joins the host's network namespace.
            os.execvp('mnexec', ['mnexec', '-a', entry.name] + command)
    sys.exit('Mininet host not found: ' + host)


if __name__ == '__main__':
    main()
