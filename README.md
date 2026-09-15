# jelq Kodi repository

Public installation packages for [jelq](https://github.com/ftc2/jelq) and its
[companion skin](https://github.com/ftc2/jelq-skin). The source repositories may
remain private; the packaged Python, XML, and assets are public downloads.

## Install in Kodi

File source: **https://ftc2.github.io/jelq.github.io/**

1. In Kodi 21 or later, open **Settings → File manager → Add source** and enter
   the URL above. Give the source a name such as `jelq`.
2. Open **Add-ons → Install from zip file**, select that source, and install
   `repository.jelq-1.0.0.zip`. Enable **Unknown sources** if Kodi requests it.
3. Open **Install from repository → jelq repository**. Install **jelq** under
   **Program add-ons**, and optionally the companion skin under **Look and feel
   → Skin**.
4. Launch jelq from program add-ons. Kodi can now discover future versioned
   updates through this repository.

The website also provides direct ZIP links for manual installation.

## One-time GitHub setup

An administrator of `ftc2/jelq.github.io` must:

1. Make **this distribution repository** public, or keep it private on a GitHub
   plan that permits public Pages from private repositories. Keep `ftc2/jelq`
   and `ftc2/jelq-skin` private.
2. In **Settings → Pages → Build and deployment → Source**, select **GitHub
   Actions**. Keep the resulting site publicly accessible.
3. From the `ftc2` account, create a fine-grained personal access token restricted
   to **only `ftc2/jelq.github.io`**, with **Contents: Read and write**. No private
   source repository access is needed. Choose an expiration and renew the token
   before it expires. An outside collaborator's fine-grained token cannot target
   another personal account's repository.
4. Store that token as the Actions repository secret **`KODI_REPO_TOKEN`** in
   **both** `ftc2/jelq` and `ftc2/jelq-skin`. Enter the token directly in GitHub's
   secret settings; do not commit it or put it in a release description.

Normal Pages deployments use this repository's built-in `GITHUB_TOKEN`.
The cross-repository token only lets a selected source release push validated
ZIPs into this distribution repository; that push triggers its Pages workflow.

## Publish a public update

The initial distribution includes script and skin version `0.1.0`. Use a new
version, such as `0.1.1`, for the next changed package.

In the appropriate **upstream** source repository:

1. Increase the add-on's `addon.xml` version to a new numeric `X.Y.Z` version.
2. Commit the change and publish a GitHub Release tagged **`kodi-vX.Y.Z`** at
   that commit. The tag's version must exactly match the manifest. A published
   prerelease with this tag pattern is also an explicit public distribution.
3. Its **Checks** workflow validates and builds the package, then pushes only
   that ZIP to this repository. **Kodi repository** regenerates the catalog and
   deploys Pages.

The moving `development` releases do not publish here. Ordinary fork builds and
pull requests never publish here. A given add-on version is immutable: retrying
the same ZIP is safe, but different bytes require a new version. Script and skin
versions can advance independently. Concurrent publications retry against the
latest distribution branch, preserving both packages.

Publish releases through the GitHub UI or an appropriately authenticated client.
GitHub Releases created with a workflow's built-in `GITHUB_TOKEN` do not trigger
another release workflow; a future automated releaser must explicitly invoke the
public publishing workflow or use a suitable app token.

## Local maintenance

Install [uv](https://docs.astral.sh/uv/). Host tooling targets Python 3.8 and uses
only the standard library:

```sh
uv run python tools/catalog.py import /path/script.jelq-0.1.0.zip --addon-id script.jelq --version 0.1.0
uv run python tools/catalog.py import /path/skin.jelq-0.1.0.zip --addon-id skin.jelq --version 0.1.0
uv run python -m unittest discover -s tests -v
uv run python tools/catalog.py build
```

`packages/` contains approved versioned ZIPs. `repository.jelq/` defines the
repository add-on. `site/` is generated and ignored; it is the only deployment
input. The build produces a catalog, checksum, plain HTML download links, the
repository installer, versioned ZIPs with SHA-256 checksums, and each add-on's
declared artwork. Kodi verifies ZIPs using their `.zip.sha256` sidecar files.

Do not add private source checkouts, development notes, credentials, local Kodi
profiles, or captured media-library data. All shipped license notices remain
inside the original packages.

Based on the layout described by the
[Kodi repository documentation](https://kodi.wiki/view/Add-on_repositories) and
[repository.example](https://github.com/drinfernoo/repository.example). Its
source-copy/submodule generator is not used: this pipeline imports approved ZIPs.
