"""Create a project virtual environment and install the local Skill application.

Uses normal pip indexes and may download dependencies. No proxy, system Python,
font or security configuration is changed. Existing environments are rejected.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', type=Path, default=Path('.venv'))
    parser.add_argument('--dev', action='store_true', help='Include pytest')
    parser.add_argument('--locked', action='store_true', help='Use the tested platform version constraints')
    parser.add_argument('--without-construction', action='store_true', help='Do not request optional skia-pathops')
    parser.add_argument('--dry-run', action='store_true', help='Print commands without creating/installing')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or newer is required')
    target = args.venv.expanduser().resolve()
    if target.exists():
        parser.error(f'Environment already exists; choose a new path: {target}')
    extras = ([] if args.without_construction else ['construction']) + (['dev'] if args.dev else [])
    requirement = str(root) + ('[' + ','.join(extras) + ']' if extras else '')
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    install = [str(python), '-I', '-m', 'pip', 'install']
    if args.locked:
        if sys.version_info[:2] != (3, 12) or sys.platform not in {'win32', 'linux'}:
            parser.error('Tested constraints support Windows/Linux Python 3.12 only')
        platform = 'win' if sys.platform == 'win32' else 'linux'
        install += ['--constraint', str(root / f'requirements-{platform}-py312.lock.txt')]
    install += [requirement]
    commands = [install, [str(python), '-I', '-m', 'scientific_figure.cli', 'doctor', '--json']]
    if args.dry_run:
        import json
        print(json.dumps({'create_venv': str(target), 'system_site_packages': False,
                          'commands': commands}, indent=2))
        return
    venv.EnvBuilder(with_pip=True).create(target)
    for command in commands:
        subprocess.run(command, cwd=root, check=True)
    print(f'Installed. Environment: {target}')


if __name__ == '__main__':
    main()
