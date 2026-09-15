# Agent instructions

- This is the public distribution repository for jelq, hosted on GitHub Pages.
- Source projects are the private upstream repositories `ftc2/jelq` and
  `ftc2/jelq-skin`. Their forks are for development only.
- The initial bootstrap contains approved script and skin `0.1.0` packages.
  Publish later updates only from explicitly selected `kodi-vX.Y.Z` releases.
  Public ZIPs contain Python/XML source; private development files and Git
  history must stay out.
- Keep approved, immutable add-on ZIPs under `packages/`. Do not copy source
  checkouts, private notes, local Kodi profiles, credentials, or CI logs here.
- Host tooling uses Python 3.8, the standard library, and `uv`.
- `tools/catalog.py import ZIP --addon-id ID --version X.Y.Z` validates and imports
  an approved package. Never replace different bytes at a published version.
- `uv run python -m unittest discover -s tests -v` and
  `uv run python tools/catalog.py build` validate the distribution.
- `site/` is generated, ignored, and is the only directory deployed to Pages.
- Base URL: `https://ftc2.github.io/jelq.github.io/`. Keep repository metadata,
  links, and documentation consistent with it.
