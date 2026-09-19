"""Build the site, then run the official Kodi checker against the repository add-on."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from tools.catalog import REPOSITORY_ID, ROOT, build


def main() -> None:
  site = build()
  (installer,) = site.glob(f'{REPOSITORY_ID}-*.zip')
  with TemporaryDirectory(prefix='addon-check-', dir=ROOT) as temporary:
    # Only extract the archive just built here from the reviewed source directory.
    with ZipFile(installer) as archive:
      archive.extractall(temporary)
    result = subprocess.run(  # noqa: S603 -- Fixed local executable, without a shell.
      [
        sys.executable,
        '-m',
        'kodi_addon_checker',
        str(Path(temporary) / REPOSITORY_ID),
        '--branch',
        'omega',
        '--enable-debug-log',
      ],
      cwd=temporary,
      check=False,
    )
  sys.exit(result.returncode)


if __name__ == '__main__':
  main()
