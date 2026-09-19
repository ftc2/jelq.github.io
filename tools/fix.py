"""Apply the distribution repository's canonical automatic Python formatting and lint fixes."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
  commands = (
    (sys.executable, '-m', 'ruff', 'check', '--fix'),
    (sys.executable, '-m', 'ruff', 'format'),
  )
  status = 0
  for command in commands:
    result = subprocess.run(command, check=False)  # noqa: S603 - fixed developer commands.
    status = status or result.returncode
  raise SystemExit(status)


if __name__ == '__main__':
  main()
