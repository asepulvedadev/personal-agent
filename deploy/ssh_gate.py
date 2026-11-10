#!/usr/bin/python3
"""Forced SSH command. Install root-owned; never execute a checkout copy."""
import os
import re
import subprocess
import sys

POLICY = '/srv/personal-agent/deploy-policy/release_gate.py'


def parse_command(command):
    if command == 'status':
        return ['status']
    if re.fullmatch(r'(deploy|rollback) [0-9a-f]{40}', command):
        return command.split(' ')
    raise ValueError('Unsupported deployment command')


def main():
    try:
        args = parse_command(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
        return subprocess.run(
            ['/usr/bin/sudo', '-n', '/usr/bin/python3', '-I', POLICY, *args],
            stdin=subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
            check=False,
        ).returncode
    except ValueError:
        print('Unsupported deployment command', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
