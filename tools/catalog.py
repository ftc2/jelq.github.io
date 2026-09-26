"""
Import immutable Kodi packages and build an anonymous static repository.

Python 3.8+, standard library only. Run from any working directory:
  python tools/catalog.py import ZIP --addon-id script.jelk --version 0.2.1
  python tools/catalog.py build
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import io
import re
import shutil
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'https://ftc2.github.io/jelk.github.io/'
REPOSITORY_ID = 'repository.jelk'
# Kodi caches the root listing browsed during bootstrap, then resolves the repository
# index through that cache; files absent from the listing fail without a request.
CATALOG_DIRECTORY = 'addons'
PACKAGE_IDS = frozenset(('script.jelk', 'skin.jelk'))
ID_PATTERN = re.compile(r'[a-z0-9][a-z0-9._-]*\Z')
VERSION_PATTERN = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z')
MAX_UNCOMPRESSED_SIZE = 512 * 1024 * 1024
MAX_MEMBERS = 20000
FIRST_PRINTABLE_ASCII = 0x20


class CatalogError(ValueError):
  """A package cannot be safely or consistently published."""


def version_key(version: str) -> tuple[int, ...]:
  if not VERSION_PATTERN.fullmatch(version):
    message = f'Public package versions must be canonical numeric X.Y.Z: {version!r}'
    raise CatalogError(message)
  return tuple(int(part) for part in version.split('.'))


def validate_id(addon_id: str) -> None:
  if not ID_PATTERN.fullmatch(addon_id) or addon_id in ('.', '..'):
    message = f'Invalid add-on ID: {addon_id!r}'
    raise CatalogError(message)


def safe_path(value: str, context: str = 'archive path') -> PurePosixPath:
  """Reject platform-dependent, absolute, and traversal paths."""
  if (
    not value
    or any(character in value for character in '\\:<>"|?*')
    or any(ord(character) < FIRST_PRINTABLE_ASCII for character in value)
  ):
    message = f'Unsafe {context}: {value!r}'
    raise CatalogError(message)
  if value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/')):
    message = f'Unsafe {context}: {value!r}'
    raise CatalogError(message)
  if any(part.rstrip(' .') != part for part in value.split('/')):
    message = f'Unsafe {context}: {value!r}'
    raise CatalogError(message)
  if any(
    re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.IGNORECASE)
    for part in value.split('/')
  ):
    message = f'Unsafe {context}: {value!r}'
    raise CatalogError(message)
  return PurePosixPath(value)


def read_manifest(data: bytes) -> ET.Element:
  if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
    raise CatalogError('XML document types and entity declarations are not allowed')
  try:
    # Document types and entities are rejected above, before parsing package metadata.
    manifest = ET.fromstring(data)  # noqa: S314
  except ET.ParseError as error:
    message = f'Invalid addon.xml: {error}'
    raise CatalogError(message) from error
  if manifest.tag != 'addon':
    raise CatalogError('addon.xml must have an <addon> root')
  addon_id = manifest.get('id', '')
  version = manifest.get('version', '')
  validate_id(addon_id)
  version_key(version)
  return manifest


def asset_paths(manifest: ET.Element) -> list[str]:
  paths: set[str] = set()
  for assets in manifest.findall("./extension[@point='xbmc.addon.metadata']/assets"):
    for asset in assets:
      if asset.text and asset.text.strip():
        value = asset.text.strip()
        safe_path(value, 'metadata asset path')
        paths.add(value)
  return sorted(paths)


class Package:
  def __init__(  # noqa: C901, PLR0912, PLR0915 - one audited pass over the archive.
    self,
    data: bytes,
    expected_id: str | None = None,
    expected_version: str | None = None,
  ) -> None:
    self.data = data
    self.files: dict[str, bytes] = {}
    try:
      with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if (
          len(members) > MAX_MEMBERS
          or sum(item.file_size for item in members) > MAX_UNCOMPRESSED_SIZE
        ):
          raise CatalogError('Package exceeds the archive size limit')
        seen = set()
        roots = set()
        files = set()
        directories = set()
        for item in members:
          # ZipInfo normalizes Windows separators and truncates NULs;
          # validate the original header name before that normalization.
          original_name = item.orig_filename
          name = original_name[:-1] if original_name.endswith('/') else original_name
          path = safe_path(name)
          folded = name.casefold()
          if folded in seen:
            message = f'Duplicate archive path: {name}'
            raise CatalogError(message)
          seen.add(folded)
          mode = item.external_attr >> 16
          kind = stat.S_IFMT(mode)
          if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            message = f'Archive contains a symlink or special file: {name}'
            raise CatalogError(message)
          if (kind == stat.S_IFDIR and not item.is_dir()) or (
            kind == stat.S_IFREG and item.is_dir()
          ):
            message = f'Archive file type disagrees with its path: {name}'
            raise CatalogError(message)
          if item.flag_bits & 1:
            raise CatalogError('Encrypted archives are not supported')
          roots.add(path.parts[0])
          if item.is_dir():
            directories.add(folded)
          else:
            if len(path.parts) == 1:
              raise CatalogError('Package files must be inside one add-on root directory')
            files.add(folded)
            self.files[name] = archive.read(item)
          for parent in path.parents:
            if str(parent) != '.':
              directories.add(str(parent).casefold())
        if files & directories:
          raise CatalogError('An archive path is both a file and a directory')
        if len(roots) != 1:
          raise CatalogError('Package must contain exactly one add-on root directory')
        root = next(iter(roots))
        manifest_data = self.files.get(root + '/addon.xml')
        if manifest_data is None:
          raise CatalogError('Package has no addon.xml at its root')
        self.manifest = read_manifest(manifest_data)
        self.addon_id = self.manifest.attrib['id']
        self.version = self.manifest.attrib['version']
        if root != self.addon_id:
          raise CatalogError('Archive root does not match addon.xml ID')
        if expected_id is not None and self.addon_id != expected_id:
          message = f'Package ID {self.addon_id} does not match expected ID {expected_id}'
          raise CatalogError(message)
        if expected_version is not None and self.version != expected_version:
          message = (
            f'Package version {self.version} does not match expected version {expected_version}'
          )
          raise CatalogError(message)
        self.assets: dict[str, bytes] = {}
        for asset in asset_paths(self.manifest):
          content = self.files.get(self.addon_id + '/' + asset)
          if content is None:
            message = f'Declared metadata asset is missing: {asset}'
            raise CatalogError(message)
          self.assets[asset] = content
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
      message = f'Invalid ZIP package: {error}'
      raise CatalogError(message) from error

  @property
  def filename(self) -> str:
    return f'{self.addon_id}-{self.version}.zip'


def require_local_directory(path: Path, root: Path) -> None:
  """Keep filesystem writes within the chosen repository, without symlinks."""
  root = root.resolve()
  try:
    relative = path.relative_to(root)
  except ValueError as error:
    message = f'Output path is outside repository: {path}'
    raise CatalogError(message) from error
  current = root
  for part in relative.parts:
    current = current / part
    if current.is_symlink():
      message = f'Output path must not contain symlinks: {current}'
      raise CatalogError(message)
    if current.exists() and not current.is_dir():
      message = f'Expected a directory: {current}'
      raise CatalogError(message)


def import_package(zip_path: Path, addon_id: str, version: str, root: Path = ROOT) -> Path:
  root = Path(root).resolve()
  validate_id(addon_id)
  version_key(version)
  if addon_id not in PACKAGE_IDS:
    raise CatalogError('Only script.jelk and skin.jelk may be imported')
  package = Package(Path(zip_path).read_bytes(), addon_id, version)
  directory = root / 'packages' / addon_id
  require_local_directory(directory, root)
  directory.mkdir(parents=True, exist_ok=True)
  target = directory / package.filename
  if target.is_symlink():
    raise CatalogError('Package destination must not be a symlink')
  try:
    with target.open('xb') as stream:
      stream.write(package.data)
  except FileExistsError:
    if target.read_bytes() != package.data:
      message = f'Published version already exists with different bytes: {target.name}'
      raise CatalogError(message) from None
  return target


def repository_package(root: Path) -> Package:
  directory = root / REPOSITORY_ID
  require_local_directory(directory, root)
  if not directory.is_dir():
    raise CatalogError('Missing repository.jelk/ source directory')
  output = io.BytesIO()
  with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for source in sorted(directory.rglob('*')):
      if source.is_symlink():
        message = f'Repository source must not contain symlinks: {source}'
        raise CatalogError(message)
      if source.is_file():
        name = source.relative_to(root).as_posix()
        safe_path(name)
        member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        member.create_system = 3
        member.external_attr = (stat.S_IFREG | 0o644) << 16
        member.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(member, source.read_bytes(), compresslevel=9)
  return Package(output.getvalue(), REPOSITORY_ID)


def load_packages(root: Path) -> list[Package]:
  packages: list[Package] = []
  directory = root / 'packages'
  require_local_directory(directory, root)
  for source in sorted(directory.glob('*/*.zip')):
    require_local_directory(source.parent, root)
    if source.is_symlink():
      message = f'Package input must not be a symlink: {source}'
      raise CatalogError(message)
    package = Package(source.read_bytes(), expected_id=source.parent.name)
    if package.addon_id not in PACKAGE_IDS:
      raise CatalogError('Only script.jelk and skin.jelk may be imported')
    if source.name != package.filename:
      message = f'Package filename disagrees with addon.xml: {source.name}'
      raise CatalogError(message)
    packages.append(package)
  packages.append(repository_package(root))
  return packages


def render_index(latest: dict[str, Package], repository: Package) -> bytes:
  # Kodi's HTTP directory parser requires href first on the bootstrap anchor
  # and its plain filename as the label; retain that order when styling it.
  links = []
  for addon_id, package in sorted(latest.items()):
    href = f'addons/{addon_id}/{package.filename}'
    links.append(
      f'<li><a href="{html.escape(href, quote=True)}">{html.escape(package.filename)}</a></li>'
    )
  template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>jelk Kodi repository</title>
<style>
:root { color-scheme: dark; font-family: system-ui, sans-serif; background: #111315; color: #e9ebed; }
body { max-width: 720px; margin: 0 auto; padding: 64px 24px 48px; line-height: 1.6; }
header { padding-bottom: 28px; border-bottom: 1px solid #353b40; }
h1 { font-size: clamp(2rem, 6vw, 3rem); line-height: 1.12; letter-spacing: -0.045em; margin: 10px 0 18px; }
h2 { font-size: 1.1rem; margin: 30px 0 16px; }
p { margin: 12px 0; }
.label, footer { color: #a4adb5; font-size: 0.85rem; }
.label { text-transform: uppercase; letter-spacing: 0.14em; }
code { display: block; overflow-wrap: anywhere; background: #1b2024; border: 1px solid #353b40; padding: 16px; border-radius: 5px; color: #fff; user-select: all; }
.copy { position: relative; cursor: copy; padding-right: 88px; }
.copy:hover, .copy:focus-visible { border-color: #7c939f; outline: none; }
.copy::after { content: 'Copy'; position: absolute; right: 12px; top: 50%; transform: translateY(-50%); font: 0.75rem system-ui, sans-serif; letter-spacing: 0.08em; text-transform: uppercase; color: #a4adb5; border: 1px solid #353b40; border-radius: 4px; padding: 3px 8px; user-select: none; }
.copy[data-state=copied]::after { content: 'Copied'; color: #111315; background: #a9d1e8; border-color: #a9d1e8; }
a { color: #a9d1e8; text-underline-offset: 3px; overflow-wrap: anywhere; }
a:hover, a:focus { color: #fff; }
ol { padding-left: 24px; }
ol li { padding-left: 8px; margin: 12px 0; }
.download { display: inline-block; border: 1px solid #7c939f; border-radius: 5px; padding: 10px 14px; }
.packages { padding: 0; list-style: none; border-top: 1px solid #353b40; }
.packages li { padding: 12px 0; border-bottom: 1px solid #353b40; }
footer { margin-top: 32px; }
</style>
</head>
<body>
<header>
<div class="label">Kodi / public releases</div>
<h1>jelk repository</h1>
<p>Install jelk and its companion skin. Kodi will find new published versions through this repository.</p>
</header>
<main>
<h2>Set up once</h2>
<ol>
<li>In Kodi, open <strong>Settings → File manager → Add source</strong> and enter this address:
<code class="copy" data-copy tabindex="0" role="button" aria-label="Copy repository address">{base_url}</code></li>
<li>Open <strong>Add-ons → Install from zip file</strong>, select that source, and install <strong>{repository_name}</strong>. Enable Unknown sources if Kodi asks.</li>
<li>Choose <strong>Install from repository → jelk repository → Video add-ons → jelk → Install</strong>. Kodi will offer to install and enable the required <strong>jelk skin</strong>; you can also do so manually from <strong>jelk repository → Look and feel → Skin</strong>.</li>
</ol>
<p><a href="{repository_href}" class="download">{repository_name}</a></p>
<h2>Latest packages</h2>
<ul class="packages">
{package_links}
</ul>
</main>
<footer>Selected upstream releases · No GitHub sign-in required</footer>
<script>
// Copy the source address on click or keyboard activation; selection remains the fallback.
document.querySelectorAll('[data-copy]').forEach(function (block) {
  var timer;
  function copy() {
    var range = document.createRange();
    range.selectNodeContents(block);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(block.textContent.trim()).then(function () {
      block.dataset.state = 'copied';
      clearTimeout(timer);
      timer = setTimeout(function () { delete block.dataset.state; }, 1600);
    }, function () {});
  }
  block.addEventListener('click', copy);
  block.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); copy(); }
  });
});
</script>
</body>
</html>
"""
  # Substitute only known placeholders; CSS uses ordinary braces.
  for key, value in {
    'base_url': html.escape(BASE_URL),
    'repository_name': html.escape(repository.filename),
    'repository_href': html.escape(repository.filename, quote=True),
    'package_links': '\n'.join(links),
  }.items():
    template = template.replace('{' + key + '}', value)
  return template.encode('utf-8')


def write_package(directory: Path, package: Package) -> None:
  (directory / package.filename).write_bytes(package.data)
  digest = hashlib.sha256(package.data).hexdigest().encode('ascii') + b'\n'
  (directory / (package.filename + '.sha256')).write_bytes(digest)


def check_repository_urls(manifest: ET.Element, output: Path) -> None:
  """Require repository URLs to resolve to generated files outside the browsed root."""
  directory = manifest.find("extension[@point='xbmc.addon.repository']/dir")
  if directory is None:
    raise CatalogError('Repository manifest lacks an xbmc.addon.repository <dir>')
  for tag in ('info', 'checksum', 'datadir'):
    url = (directory.findtext(tag) or '').strip()
    if not url.startswith(BASE_URL):
      message = f'Repository <{tag}> must start with {BASE_URL}: {url!r}'
      raise CatalogError(message)
    path = url[len(BASE_URL) :]
    if tag == 'datadir':
      if path != CATALOG_DIRECTORY + '/':
        message = f'Repository <datadir> must be {BASE_URL}{CATALOG_DIRECTORY}/'
        raise CatalogError(message)
      continue
    safe_path(path, 'repository URL')
    if '/' not in path:
      message = f'Repository <{tag}> must not be in the browsed root directory: {url!r}'
      raise CatalogError(message)
    if not output.joinpath(*path.split('/')).is_file():
      message = f'Repository <{tag}> does not name a generated file: {url!r}'
      raise CatalogError(message)


def build(root: Path = ROOT) -> Path:
  root = Path(root).resolve()
  destination = root / 'site'
  require_local_directory(destination, root)
  packages = load_packages(root)
  latest: dict[str, Package] = {}
  for package in packages:
    current = latest.get(package.addon_id)
    if current is None or version_key(package.version) > version_key(current.version):
      latest[package.addon_id] = package
  repository = latest[REPOSITORY_ID]
  with tempfile.TemporaryDirectory(prefix='.catalog-', dir=str(root)) as temporary:
    output = Path(temporary) / 'site'
    output.mkdir()
    for package in packages:
      directory = output / 'addons' / package.addon_id
      directory.mkdir(parents=True, exist_ok=True)
      write_package(directory, package)
    for package in latest.values():
      directory = output / 'addons' / package.addon_id
      for path, content in package.assets.items():
        target = directory.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
          message = f'Metadata asset conflicts with a package file: {path}'
          raise CatalogError(message)
        target.write_bytes(content)
    catalog = ET.Element('addons')
    for addon_id in sorted(latest):
      catalog.append(copy.deepcopy(latest[addon_id].manifest))
    xml = ET.tostring(catalog, encoding='utf-8', xml_declaration=True) + b'\n'
    catalog_dir = output / CATALOG_DIRECTORY
    (catalog_dir / 'addons.xml').write_bytes(xml)
    # Kodi's repository protocol specifies an MD5 change marker, not a security digest.
    digest = hashlib.md5(xml).hexdigest()  # noqa: S324
    (catalog_dir / 'addons.xml.md5').write_bytes(digest.encode('ascii') + b'\n')
    check_repository_urls(repository.manifest, output)
    write_package(output, repository)
    (output / 'index.html').write_bytes(render_index(latest, repository))
    (output / '.nojekyll').touch()
    # The destination was checked above and is a generated directory in root.
    require_local_directory(destination, root)
    if destination.exists():
      shutil.rmtree(destination)
    Path(str(output)).replace(str(destination))
  return destination


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
  )
  commands = parser.add_subparsers(dest='command', required=True)
  importer = commands.add_parser('import', help='Validate and save an immutable built ZIP')
  importer.add_argument('zip', type=Path)
  importer.add_argument('--addon-id', required=True)
  importer.add_argument('--version', required=True)
  commands.add_parser('build', help='Build site/ from approved packages and the repository add-on')
  arguments = parser.parse_args(argv)
  try:
    if arguments.command == 'import':
      result = import_package(arguments.zip, arguments.addon_id, arguments.version)
    else:
      result = build()
  except (CatalogError, OSError) as error:
    parser.exit(1, f'error: {error}\n')
  sys.stdout.write(f'{result}\n')
  return 0


if __name__ == '__main__':
  sys.exit(main())
