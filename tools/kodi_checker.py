"""
Run Kodi's add-on checker without depending on Kodi's rate-limited mirror redirector.

kodi-addon-checker downloads ten official Kodi repository indexes from
`http://mirrors.kodi.tv/addons/{branch}/addons.xml.gz`. That host only redirects
each request to a random community mirror, and it rate-limits: after about six
requests in a few seconds it answers 429 without `Retry-After`. The checker
retries each 429 up to five times, sleeping 0, 20, 40, 80 and 120 s, so an
ordinary run spends most of a minute asleep. When a request is refused six times
in a row it gives up, swallows the error and returns without setting `addons`, so
the run later fails with an unrelated
`AttributeError: 'Repository' object has no attribute 'addons'`.

Instead, fetch each index straight from community mirrors that carry the same
files, in a shuffled order so no single mirror takes every run, moving to the next
mirror on any failure. The redirector is kept as the last resort. If every source
fails, stop with a message that names the index and the sources tried.

Tested against kodi-addon-checker 0.0.36. The tests pin the internals this relies
on, so an upgrade that changes them fails there rather than silently.
"""

from __future__ import annotations

import random
import sys
import xml.etree.ElementTree as ET
from typing import Any, Callable

from kodi_addon_checker.addons.Repository import Repository  # type: ignore[import-untyped]
from urllib3.util.retry import Retry

REDIRECTOR = 'http://mirrors.kodi.tv'
# Community mirrors observed serving byte-identical indexes on 2026-09-21. Order
# does not matter; each run shuffles them.
MIRRORS = (
  'https://mirror.math.princeton.edu/pub/xbmc',
  'https://mirrors.xmission.com/kodi',
  'https://mirrors.dotsrc.org/kodi',
  'https://ftp.fau.de/xbmc',
  'https://ftp.halifax.rwth-aachen.de/xbmc',
  'https://mirror.netcologne.de/xbmc',
  'https://ftp.hosteurope.de/mirror/xbmc.org',
  'https://kodi.mirror.garr.it',
)
# A failing mirror is abandoned for the next one rather than retried. A 429 from
# the redirector fallback still gets a short backoff.
ONE_TRY_PER_SOURCE = Retry(
  total=None,
  connect=0,
  read=0,
  other=0,
  status=2,
  backoff_factor=2,
  status_forcelist={429},
  allowed_methods=None,
)
# Failures that can come from a source that answered with something other than
# an index, such as an error page or a truncated download.
BAD_CONTENT = (ET.ParseError, OSError, EOFError, ValueError)


class IndexUnavailableError(RuntimeError):
  """No source returned an official Kodi index."""


def sources() -> list[str]:
  """Return the index sources to try, in order."""
  mirrors = list(MIRRORS)
  random.shuffle(mirrors)
  return [*mirrors, REDIRECTOR]


def _index_suffix(path: str) -> str:
  if not path.startswith(REDIRECTOR + '/'):
    message = f'Unexpected Kodi index URL {path!r}; kodi-addon-checker may have changed.'
    raise IndexUnavailableError(message)
  return path[len(REDIRECTOR) :]


def _mirrored_init(original: Callable[..., None]) -> Callable[..., None]:
  def init(self: Any, version: str, path: str) -> None:
    suffix = _index_suffix(path)
    tried: list[str] = []
    for source in sources():
      tried.append(source)
      try:
        original(self, version, source + suffix)
      except BAD_CONTENT:
        # The checker sets `addons = []` before parsing, so a source that
        # answered with something other than an index must not count as empty.
        self.__dict__.pop('addons', None)
      if hasattr(self, 'addons'):
        return
      sys.stderr.write(f'Kodi {version} index unavailable from {source}; trying another.\n')
    message = (
      f'Could not download the official Kodi {version} index ({suffix}) from any of: '
      + ', '.join(tried)
      + '. This is a network problem, not a problem with the checked add-on.'
    )
    raise IndexUnavailableError(message)

  return init


def install() -> None:
  """Patch the checker's index downloads in this process."""
  Repository._adapter.max_retries = ONE_TRY_PER_SOURCE  # noqa: SLF001 - pinned internal, see tests.
  setattr(Repository, '__init__', _mirrored_init(Repository.__init__))  # noqa: B010


def main() -> None:
  """Run kodi-addon-checker's command line with the patched downloads."""
  install()
  from kodi_addon_checker.__main__ import main as checker_main  # type: ignore[import-untyped]  # noqa: PLC0415

  checker_main()


if __name__ == '__main__':
  main()
