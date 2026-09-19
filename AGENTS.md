# Agent instructions

- If `../jelq-notes/AGENTS.md` exists, read and follow its additional internal
  development guidance. Resolve this path relative to this file. The notes
  repository is private; if it is absent, continue with this file alone. Read
  each instruction file once and do not recursively reload cross-references.
- This is the public distribution repository for jelq, hosted on GitHub Pages.
- Source projects are the private upstream repositories `ftc2/jelq` and
  `ftc2/jelq-skin`. Their forks are for development only.
- Keep every approved package version immutable. Publish updates only from
  explicitly selected `kodi-vX.Y.Z` releases.
  Public ZIPs contain Python/XML source; private development files and Git
  history must stay out.
- Keep approved, immutable add-on ZIPs under `packages/`. Do not copy source
  checkouts, private notes, local Kodi profiles, credentials, or CI logs here.
- Host tooling uses Python 3.8 and `uv`. `tools/catalog.py` uses only the standard
  library; mypy, Ruff, pytest, and Kodi's add-on checker are locked development
  tools. Follow `ruff.toml`, which matches the `jelq` source repository's style.
- `tools/catalog.py import ZIP --addon-id ID --version X.Y.Z` validates and imports
  an approved package. Never replace different bytes at a published version.
- Apply automatic fixes with `uv run --frozen python -m tools.fix`. Run
  `uv run --frozen python -m tools.check` before submitting changes, and
  `uv run --frozen python -m tools.check full` before pushing; the full tier also
  checks the lockfile, builds `site/`, and runs Kodi's add-on checker (network).
  These repository-specific tiers supersede generic Python check command lists.
- `site/` is generated, ignored, and is the only directory deployed to Pages.
- Base URL: `https://ftc2.github.io/jelq.github.io/`. Keep repository metadata,
  links, and documentation consistent with it.
