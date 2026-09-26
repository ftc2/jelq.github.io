# jelk Kodi repository

Public installation packages for [jelk](https://github.com/ftc2/jelk) and its
[skin](https://github.com/ftc2/jelk-skin). The source repositories may
remain private; the packaged Python, XML, and assets are public downloads.

## Install in Kodi

File source: **https://ftc2.github.io/jelk.github.io/**

1. In Kodi 21.3, open **Settings → File manager → Add source** and enter
   the URL above. Give the source a name such as `jelk`.
2. Open **Add-ons → Install from zip file**, select that source, and install
   the `repository.jelk` ZIP. Enable **Unknown sources** if Kodi requests it.
3. Open **Install from repository → jelk repository → Video add-ons →
   jelk → Install**. Kodi will offer to install and enable the required **jelk
   skin**; you can also do so manually from **jelk repository → Look and feel →
   Skin**.
4. Launch jelk from Video add-ons. Kodi can now discover future versioned
   updates through this repository.

The website also provides direct ZIP links for manual installation.

## One-time GitHub setup

An administrator of `ftc2/jelk.github.io` must:

1. Make **this distribution repository** public, or keep it private on a GitHub
   plan that permits public Pages from private repositories. Keep `ftc2/jelk`
   and `ftc2/jelk-skin` private.
2. In **Settings → Pages → Build and deployment → Source**, select **GitHub
   Actions**. Keep the resulting site publicly accessible.
3. From the `ftc2` account, create a fine-grained personal access token restricted
   to **only `ftc2/jelk.github.io`**, with **Contents: Read and write**. No private
   source repository access is needed. Choose an expiration and renew the token
   before it expires. An outside collaborator's fine-grained token cannot target
   another personal account's repository.
4. Store that token as the Actions repository secret **`KODI_REPO_TOKEN`** in
   **both** `ftc2/jelk` and `ftc2/jelk-skin`. Enter the token directly in GitHub's
   secret settings; do not commit it or put it in a release description.

Normal Pages deployments use this repository's built-in `GITHUB_TOKEN`.
The cross-repository token only lets a selected source release push validated
ZIPs into this distribution repository; that push triggers its Pages workflow.

## Publish a public update

In the appropriate **upstream** source repository:

1. Increase the add-on's `addon.xml` version to a new numeric `X.Y.Z` version.
2. Commit the change and publish a GitHub Release tagged **`kodi-vX.Y.Z`** at
   that commit. The tag's version must exactly match the manifest. A published
   prerelease with this tag pattern is also an explicit public distribution.
3. Its **Checks** workflow validates and builds the package, then pushes only
   that ZIP to this repository. **Kodi repository** regenerates the catalog and
   deploys Pages.

**Kodi repository** runs Kodi's add-on checker only when a push or pull request
changes `repository.jelk` or the tooling that builds and checks it
(`tools/repository_scope.py` decides), and on manual runs. A checker failure then
blocks that deployment. Package imports skip it: they cannot change the checked
add-on, and the checker depends on Kodi's official index mirrors being reachable.
The checker runs through `tools/kodi_checker.py`, which fetches those indexes
directly from community mirrors rather than through the rate-limited
`mirrors.kodi.tv` redirector. It uses short per-source timeouts and, if no source
answers with a valid index, fails clearly and names the sources tried.

Ordinary fork builds and
pull requests never publish here. A given add-on version is immutable: retrying
the same ZIP is safe, but different bytes require a new version. Script and skin
versions can otherwise advance independently; whenever the script raises its
minimum skin dependency, publish the compatible skin first. Concurrent
publications retry against the latest distribution branch, preserving both
packages.

Publish releases through the GitHub UI or an appropriately authenticated client.
GitHub Releases created with a workflow's built-in `GITHUB_TOKEN` do not trigger
another release workflow; a future automated releaser must explicitly invoke the
public publishing workflow or use a suitable app token.

## Local maintenance

Install [uv](https://docs.astral.sh/uv/). Host tooling targets Python 3.8; the
catalog tool itself uses only the standard library. Import approved packages and
build the site with:

```sh
uv run --frozen --no-dev python -m tools.catalog import /path/skin.jelk-X.Y.Z.zip --addon-id skin.jelk --version X.Y.Z
uv run --frozen --no-dev python -m tools.catalog import /path/script.jelk-X.Y.Z.zip --addon-id script.jelk --version X.Y.Z
uv run --frozen --no-dev python -m tools.catalog build
```

Development checks use the same tier structure and Ruff style as the `jelk`
source repository. Initialize the locked environment with `uv sync --frozen`,
apply automatic fixes with `uv run --frozen python -m tools.fix`, and run the
quality tier (mypy, Ruff lint and formatting, pytest) before submitting changes:

```sh
uv run --frozen python -m tools.check
```

Before pushing, run `uv run --frozen python -m tools.check full`. It adds the
lockfile check, builds `site/`, and validates the repository add-on with Kodi's
official add-on checker, which needs network access. CI runs the same tiers
before every Pages deployment.

`packages/` contains approved versioned ZIPs. `repository.jelk/` defines the
repository add-on. `site/` is generated and ignored; it is the only deployment
input. The build produces a catalog, checksum, plain HTML download links, the
repository installer, versioned ZIPs with SHA-256 checksums, and each add-on's
declared artwork. Kodi verifies ZIPs using their `.zip.sha256` sidecar files.

The catalog (`addons/addons.xml` and its checksum) deliberately stays out of the
site root. Installing the bootstrap ZIP makes Kodi cache the root's HTML listing,
and Kodi fails any later root-file request absent from that listing without
contacting the server; the repository would then report "Could not connect"
until Kodi restarts. The build rejects a repository manifest that points there.
Changing `repository.jelk/` requires a new repository add-on version.

Do not add private source checkouts, development notes, credentials, local Kodi
profiles, or captured media-library data. All shipped license notices remain
inside the original packages.

Based on the layout described by the
[Kodi repository documentation](https://kodi.wiki/view/Add-on_repositories) and
[repository.example](https://github.com/drinfernoo/repository.example). Its
source-copy/submodule generator is not used: this pipeline imports approved ZIPs.
