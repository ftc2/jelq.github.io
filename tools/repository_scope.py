"""
Decide whether a change can alter the repository add-on Kodi's checker validates.

The checker validates only `repository.jelq`. It also downloads ten official Kodi
indexes through `mirrors.kodi.tv`, a redirector that rate-limits bursts of
requests; the checker backs off for minutes when refused and can then fail.
Package imports never change the repository add-on, so a publish should not wait
on those downloads; a change to the repository add-on or its tooling still has to
pass the checker before it deploys.

Python 3.8+, standard library only. In CI:
  python -m tools.repository_scope --base SHA --head SHA

The decision is written to `$GITHUB_OUTPUT` as `check=true|false` when that
variable is set, and printed either way. Anything uncertain answers true.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
# A trailing slash marks a directory prefix; anything else is an exact path.
RELEVANT_PATHS = (
  'repository.jelq/',
  'tools/catalog.py',
  'tools/check.py',
  'tools/check_package.py',
  'pyproject.toml',
  'uv.lock',
  '.github/workflows/pages.yml',
)


def needs_repository_check(paths: Iterable[str]) -> bool:
  """Report whether any changed path can alter the checked repository add-on."""
  for path in paths:
    for entry in RELEVANT_PATHS:
      if path == entry or (entry.endswith('/') and path.startswith(entry)):
        return True
  return False


def changed_paths(base: str, head: str, root: Path = ROOT) -> list[str] | None:
  """
  List paths changed between two commits, or None when that cannot be known.

  A missing or all-zero base is what GitHub sends for a new branch or a first
  push. The base commit may also be absent from a shallow checkout, so fetch it
  first; an unreachable base is treated as unknown rather than as no change.
  """
  executable = shutil.which('git')
  if executable is None or not base or not head or set(base) == {'0'}:
    return None
  git = (executable, '-C', str(root))
  subprocess.run(  # noqa: S603 - fixed git arguments; a failure is judged by the diff.
    (*git, 'fetch', '--quiet', '--no-tags', '--depth=1', 'origin', base),
    check=False,
    capture_output=True,
  )
  result = subprocess.run(  # noqa: S603 - fixed git arguments.
    (*git, 'diff', '--name-only', base, head),
    check=False,
    capture_output=True,
    text=True,
  )
  if result.returncode:
    return None
  return [line for line in result.stdout.splitlines() if line]


def decide(base: str, head: str, root: Path = ROOT) -> tuple[bool, str]:
  """Return whether to run the checker and a short reason for the log."""
  paths = changed_paths(base, head, root)
  if paths is None:
    return True, 'changed paths are unknown, so run the checker'
  if needs_repository_check(paths):
    return True, 'the repository add-on or its tooling changed'
  return False, 'only packages or unrelated files changed'


def main(argv: Sequence[str] | None = None) -> int:
  """Write the decision for a GitHub Actions step and report it."""
  parser = argparse.ArgumentParser(
    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
  )
  parser.add_argument('--base', default='')
  parser.add_argument('--head', default='')
  arguments = parser.parse_args(argv)
  check, reason = decide(arguments.base, arguments.head, ROOT)
  value = 'true' if check else 'false'
  output = os.environ.get('GITHUB_OUTPUT')
  if output:
    with Path(output).open('a', encoding='utf-8') as stream:
      stream.write(f'check={value}\n')
  sys.stdout.write(f'Kodi add-on checker: {value} ({reason})\n')
  return 0


if __name__ == '__main__':
  sys.exit(main())
