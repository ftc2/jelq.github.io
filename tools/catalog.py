#!/usr/bin/env python3
"""Import immutable Kodi packages and build an anonymous static repository.

Python 3.8+, standard library only. Run from any working directory:
  python tools/catalog.py import ZIP --addon-id script.jelq --version 0.2.1
  python tools/catalog.py build
"""

import argparse
import copy
import hashlib
import html
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://ftc2.github.io/jelq.github.io/"
REPOSITORY_ID = "repository.jelq"
PACKAGE_IDS = frozenset(("script.jelq", "skin.jelq"))
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
MAX_UNCOMPRESSED_SIZE = 512 * 1024 * 1024
MAX_MEMBERS = 20000


class CatalogError(ValueError):
    """A package cannot be safely or consistently published."""


def version_key(version):
    if not VERSION_PATTERN.fullmatch(version):
        raise CatalogError("Public package versions must be canonical numeric X.Y.Z: %r" % version)
    return tuple(int(part) for part in version.split("."))


def validate_id(addon_id):
    if not ID_PATTERN.fullmatch(addon_id) or addon_id in (".", ".."):
        raise CatalogError("Invalid add-on ID: %r" % addon_id)


def safe_path(value, context="archive path"):
    """Reject platform-dependent, absolute, and traversal paths."""
    if not value or any(character in value for character in '\\:<>"|?*') or any(ord(character) < 32 for character in value):
        raise CatalogError("Unsafe %s: %r" % (context, value))
    if value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise CatalogError("Unsafe %s: %r" % (context, value))
    if any(part.rstrip(" .") != part for part in value.split("/")):
        raise CatalogError("Unsafe %s: %r" % (context, value))
    if any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.IGNORECASE)
           for part in value.split("/")):
        raise CatalogError("Unsafe %s: %r" % (context, value))
    return PurePosixPath(value)


def read_manifest(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise CatalogError("XML document types and entity declarations are not allowed")
    try:
        manifest = ET.fromstring(data)
    except ET.ParseError as error:
        raise CatalogError("Invalid addon.xml: %s" % error) from error
    if manifest.tag != "addon":
        raise CatalogError("addon.xml must have an <addon> root")
    addon_id = manifest.get("id", "")
    version = manifest.get("version", "")
    validate_id(addon_id)
    version_key(version)
    return manifest


def asset_paths(manifest):
    paths = set()
    for assets in manifest.findall("./extension[@point='xbmc.addon.metadata']/assets"):
        for asset in assets:
            if asset.text and asset.text.strip():
                value = asset.text.strip()
                safe_path(value, "metadata asset path")
                paths.add(value)
    return sorted(paths)


class Package:
    def __init__(self, data, expected_id=None, expected_version=None):
        self.data = data
        self.files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > MAX_MEMBERS or sum(item.file_size for item in members) > MAX_UNCOMPRESSED_SIZE:
                    raise CatalogError("Package exceeds the archive size limit")
                seen = set()
                roots = set()
                files = set()
                directories = set()
                for item in members:
                    # ZipInfo normalizes Windows separators and truncates NULs;
                    # validate the original header name before that normalization.
                    original_name = item.orig_filename
                    name = original_name[:-1] if original_name.endswith("/") else original_name
                    path = safe_path(name)
                    folded = name.casefold()
                    if folded in seen:
                        raise CatalogError("Duplicate archive path: %s" % name)
                    seen.add(folded)
                    mode = item.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise CatalogError("Archive contains a symlink or special file: %s" % name)
                    if (kind == stat.S_IFDIR and not item.is_dir()) or (kind == stat.S_IFREG and item.is_dir()):
                        raise CatalogError("Archive file type disagrees with its path: %s" % name)
                    if item.flag_bits & 1:
                        raise CatalogError("Encrypted archives are not supported")
                    roots.add(path.parts[0])
                    if item.is_dir():
                        directories.add(folded)
                    else:
                        if len(path.parts) < 2:
                            raise CatalogError("Package files must be inside one add-on root directory")
                        files.add(folded)
                        self.files[name] = archive.read(item)
                    for parent in path.parents:
                        if str(parent) != ".":
                            directories.add(str(parent).casefold())
                if files & directories:
                    raise CatalogError("An archive path is both a file and a directory")
                if len(roots) != 1:
                    raise CatalogError("Package must contain exactly one add-on root directory")
                root = next(iter(roots))
                manifest_data = self.files.get(root + "/addon.xml")
                if manifest_data is None:
                    raise CatalogError("Package has no addon.xml at its root")
                self.manifest = read_manifest(manifest_data)
                self.addon_id = self.manifest.get("id")
                self.version = self.manifest.get("version")
                if root != self.addon_id:
                    raise CatalogError("Archive root does not match addon.xml ID")
                if expected_id is not None and self.addon_id != expected_id:
                    raise CatalogError("Package ID %s does not match expected ID %s" % (self.addon_id, expected_id))
                if expected_version is not None and self.version != expected_version:
                    raise CatalogError("Package version %s does not match expected version %s" % (self.version, expected_version))
                self.assets = {}
                for asset in asset_paths(self.manifest):
                    content = self.files.get(self.addon_id + "/" + asset)
                    if content is None:
                        raise CatalogError("Declared metadata asset is missing: %s" % asset)
                    self.assets[asset] = content
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
            raise CatalogError("Invalid ZIP package: %s" % error) from error

    @property
    def filename(self):
        return "%s-%s.zip" % (self.addon_id, self.version)


def require_local_directory(path, root):
    """Keep filesystem writes within the chosen repository, without symlinks."""
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise CatalogError("Output path is outside repository: %s" % path) from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise CatalogError("Output path must not contain symlinks: %s" % current)
        if current.exists() and not current.is_dir():
            raise CatalogError("Expected a directory: %s" % current)


def import_package(zip_path, addon_id, version, root=ROOT):
    root = Path(root).resolve()
    validate_id(addon_id)
    version_key(version)
    if addon_id not in PACKAGE_IDS:
        raise CatalogError("Only script.jelq and skin.jelq may be imported")
    package = Package(Path(zip_path).read_bytes(), addon_id, version)
    directory = root / "packages" / addon_id
    require_local_directory(directory, root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / package.filename
    if target.is_symlink():
        raise CatalogError("Package destination must not be a symlink")
    try:
        with target.open("xb") as stream:
            stream.write(package.data)
    except FileExistsError:
        if target.read_bytes() != package.data:
            raise CatalogError("Published version already exists with different bytes: %s" % target.name)
    return target


def repository_package(root):
    directory = root / REPOSITORY_ID
    require_local_directory(directory, root)
    if not directory.is_dir():
        raise CatalogError("Missing repository.jelq/ source directory")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(directory.rglob("*")):
            if source.is_symlink():
                raise CatalogError("Repository source must not contain symlinks: %s" % source)
            if source.is_file():
                name = source.relative_to(root).as_posix()
                safe_path(name)
                member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | 0o644) << 16
                member.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(member, source.read_bytes(), compresslevel=9)
    return Package(output.getvalue(), REPOSITORY_ID)


def load_packages(root):
    packages = []
    directory = root / "packages"
    require_local_directory(directory, root)
    for source in sorted(directory.glob("*/*.zip")):
        require_local_directory(source.parent, root)
        if source.is_symlink():
            raise CatalogError("Package input must not be a symlink: %s" % source)
        package = Package(source.read_bytes(), expected_id=source.parent.name)
        if package.addon_id not in PACKAGE_IDS:
            raise CatalogError("Only script.jelq and skin.jelq may be imported")
        if source.name != package.filename:
            raise CatalogError("Package filename disagrees with addon.xml: %s" % source.name)
        packages.append(package)
    packages.append(repository_package(root))
    return packages


def render_index(latest, repository):
    # Kodi's HTTP directory parser requires href first on the bootstrap anchor
    # and its plain filename as the label; retain that order when styling it.
    links = []
    for addon_id, package in sorted(latest.items()):
        href = "addons/%s/%s" % (addon_id, package.filename)
        links.append('<li><a href="%s">%s</a></li>' % (html.escape(href, quote=True), html.escape(package.filename)))
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>jelq Kodi repository</title>
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
<h1>jelq repository</h1>
<p>Install jelq and its companion skin. Kodi will find new published versions through this repository.</p>
</header>
<main>
<h2>Set up once</h2>
<ol>
<li>In Kodi, open <strong>Settings → File manager → Add source</strong> and enter this address:
<code>{base_url}</code></li>
<li>Open <strong>Add-ons → Install from zip file</strong>, select that source, and install <strong>{repository_name}</strong>. Enable Unknown sources if Kodi asks.</li>
<li>Choose <strong>Install from repository → jelq repository</strong> to install jelq and the skin.</li>
</ol>
<p><a href="{repository_href}" class="download">{repository_name}</a></p>
<h2>Latest packages</h2>
<ul class="packages">
{package_links}
</ul>
</main>
<footer>Selected upstream releases · No GitHub sign-in required</footer>
</body>
</html>
"""
    # Substitute only known placeholders; CSS uses ordinary braces.
    for key, value in {
        "base_url": html.escape(BASE_URL),
        "repository_name": html.escape(repository.filename),
        "repository_href": html.escape(repository.filename, quote=True),
        "package_links": "\n".join(links),
    }.items():
        template = template.replace("{" + key + "}", value)
    return template.encode("utf-8")


def write_package(directory, package):
    (directory / package.filename).write_bytes(package.data)
    digest = hashlib.sha256(package.data).hexdigest().encode("ascii") + b"\n"
    (directory / (package.filename + ".sha256")).write_bytes(digest)


def build(root=ROOT):
    root = Path(root).resolve()
    destination = root / "site"
    require_local_directory(destination, root)
    packages = load_packages(root)
    latest = {}
    for package in packages:
        current = latest.get(package.addon_id)
        if current is None or version_key(package.version) > version_key(current.version):
            latest[package.addon_id] = package
    repository = latest[REPOSITORY_ID]
    with tempfile.TemporaryDirectory(prefix=".catalog-", dir=str(root)) as temporary:
        output = Path(temporary) / "site"
        output.mkdir()
        for package in packages:
            directory = output / "addons" / package.addon_id
            directory.mkdir(parents=True, exist_ok=True)
            write_package(directory, package)
        for package in latest.values():
            directory = output / "addons" / package.addon_id
            for path, content in package.assets.items():
                target = directory.joinpath(*PurePosixPath(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise CatalogError("Metadata asset conflicts with a package file: %s" % path)
                target.write_bytes(content)
        catalog = ET.Element("addons")
        for addon_id in sorted(latest):
            catalog.append(copy.deepcopy(latest[addon_id].manifest))
        xml = ET.tostring(catalog, encoding="utf-8", xml_declaration=True) + b"\n"
        (output / "addons.xml").write_bytes(xml)
        (output / "addons.xml.md5").write_bytes(hashlib.md5(xml).hexdigest().encode("ascii") + b"\n")
        write_package(output, repository)
        (output / "index.html").write_bytes(render_index(latest, repository))
        (output / ".nojekyll").touch()
        # The destination was checked above and is a generated directory in root.
        require_local_directory(destination, root)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(str(output), str(destination))
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Validate and save an immutable built ZIP")
    importer.add_argument("zip", type=Path)
    importer.add_argument("--addon-id", required=True)
    importer.add_argument("--version", required=True)
    commands.add_parser("build", help="Build site/ from approved packages and the repository add-on")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "import":
            result = import_package(arguments.zip, arguments.addon_id, arguments.version)
        else:
            result = build()
    except (CatalogError, OSError) as error:
        parser.exit(1, "error: %s\n" % error)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
