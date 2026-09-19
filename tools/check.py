"""Run the distribution repository's canonical host-side validation tiers."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Tuple

Command = Tuple[str, Tuple[str, ...]]


def _uv_executable() -> str:
  executable = shutil.which('uv')
  if executable is None:
    raise RuntimeError('uv is required to validate the lockfile.')
  return executable


def _quality_commands() -> tuple[Command, ...]:
  return (
    ('Type checking', (sys.executable, '-m', 'mypy', '.')),
    ('Lint', (sys.executable, '-m', 'ruff', 'check')),
    ('Formatting', (sys.executable, '-m', 'ruff', 'format', '--check')),
    ('Tests', (sys.executable, '-m', 'pytest', '-q')),
  )


def _dependency_commands() -> tuple[Command, ...]:
  return (('Lockfile', (_uv_executable(), 'lock', '--check')),)


def _package_commands() -> tuple[Command, ...]:
  # check_package builds site/ before running Kodi's official checker.
  return (('Kodi repository add-on', (sys.executable, '-m', 'tools.check_package')),)


def commands(tier: str) -> tuple[Command, ...]:
  """Return the commands belonging to a validation tier."""
  if tier == 'quality':
    return _quality_commands()
  if tier == 'dependencies':
    return _dependency_commands()
  if tier == 'package':
    return _package_commands()
  return _quality_commands() + _dependency_commands() + _package_commands()


def run(selected: Sequence[Command]) -> int:
  """Run commands in order and return the first failing status."""
  for label, command in selected:
    sys.stdout.write(f'\n==> {label}\n')
    sys.stdout.flush()
    result = subprocess.run(command, check=False)  # noqa: S603 - fixed developer commands.
    if result.returncode:
      return result.returncode
  return 0


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    'tier',
    choices=('quality', 'dependencies', 'package', 'full'),
    default='quality',
    nargs='?',
    help='validation tier to run (default: quality)',
  )
  args = parser.parse_args()
  try:
    status = run(commands(args.tier))
  except RuntimeError as error:
    parser.exit(1, f'Validation setup failed: {error}\n')
  raise SystemExit(status)


if __name__ == '__main__':
  main()
