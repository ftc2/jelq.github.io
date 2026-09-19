import hashlib
import io
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import catalog

REPOSITORY_MANIFEST = (catalog.ROOT / 'repository.jelq' / 'addon.xml').read_bytes()
REPOSITORY_VERSION = ET.fromstring(REPOSITORY_MANIFEST).get('version')  # noqa: S314 - local manifest.
REPOSITORY_ZIP = f'repository.jelq-{REPOSITORY_VERSION}.zip'


def manifest(addon_id='script.jelq', version='0.1.0', assets=''):
  return (
    f'<addon id="{addon_id}" version="{version}" name="Test" provider-name="ftc2">'
    f'<extension point="xbmc.addon.metadata"><assets>{assets}</assets></extension>'
    '</addon>'
  ).encode()


def zip_bytes(addon_id='script.jelq', version='0.1.0', extras=None, assets=''):
  output = io.BytesIO()
  with zipfile.ZipFile(output, 'w') as archive:
    archive.writestr(addon_id + '/addon.xml', manifest(addon_id, version, assets))
    archive.writestr(addon_id + '/LICENSE', 'License text must survive unchanged.\n')
    for name, content in (extras or {}).items():
      archive.writestr(name, content)
  return output.getvalue()


@pytest.fixture
def root(tmp_path: Path) -> Path:
  source = tmp_path / 'repository.jelq'
  source.mkdir()
  (source / 'addon.xml').write_bytes(REPOSITORY_MANIFEST)
  return tmp_path


def import_zip(root, version='0.1.0', extras=None, assets=''):
  data = zip_bytes(version=version, extras=extras, assets=assets)
  source = root / 'input.zip'
  source.write_bytes(data)
  return catalog.import_package(source, 'script.jelq', version, root), data


@pytest.mark.parametrize(
  ('expected_id', 'expected_version'), [('skin.jelq', '0.1.0'), ('script.jelq', '0.2.0')]
)
def test_rejects_wrong_identity_or_version(expected_id, expected_version):
  with pytest.raises(catalog.CatalogError):
    catalog.Package(zip_bytes(), expected_id, expected_version)


@pytest.mark.parametrize(
  'path',
  [
    'script.jelq/../outside',
    '/absolute',
    'script.jelq/C:/bad',
    'another/file',
    'script.jelq//file',
    'script.jelq/file.',
    'script.jelq/NUL.txt',
  ],
)
def test_rejects_traversal_absolute_and_wrong_roots(path):
  with pytest.raises(catalog.CatalogError):
    catalog.Package(zip_bytes(extras={path: 'bad'}))


@pytest.mark.parametrize('unsafe', [b'script.jelq\\bad', b'script.jelq/ba\x00'])
def test_original_archive_names_are_checked_before_zipfile_normalizes_them(unsafe):
  data = zip_bytes(extras={'script.jelq/bad': 'bad'})
  with pytest.raises(catalog.CatalogError):
    catalog.Package(data.replace(b'script.jelq/bad', unsafe))


@pytest.mark.parametrize(
  'extras',
  [
    {'script.jelq/ADDON.XML': 'collision'},
    {'script.jelq/resources': 'file', 'script.jelq/resources/icon.png': b'image'},
  ],
)
def test_rejects_case_collisions_and_file_directory_collisions(extras):
  with pytest.raises(catalog.CatalogError):
    catalog.Package(zip_bytes(extras=extras))


def test_rejects_duplicate_paths():
  output = io.BytesIO(zip_bytes())
  with zipfile.ZipFile(output, 'a') as archive, pytest.warns(UserWarning, match='Duplicate name'):
    archive.writestr('script.jelq/addon.xml', manifest())
  with pytest.raises(catalog.CatalogError):
    catalog.Package(output.getvalue())


def test_rejects_symlink():
  output = io.BytesIO(zip_bytes())
  with zipfile.ZipFile(output, 'a') as archive:
    member = zipfile.ZipInfo('script.jelq/link')
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(member, '../../outside')
  with pytest.raises(catalog.CatalogError):
    catalog.Package(output.getvalue())


@pytest.mark.parametrize(
  'asset', ['resources/missing.png', '../outside', 'https://example.com/image.png']
)
def test_assets_must_be_present_and_safe(asset):
  with pytest.raises(catalog.CatalogError):
    catalog.Package(zip_bytes(assets=f'<icon>{asset}</icon>'))


@pytest.mark.parametrize('version', ['1.0', '1.0.0-beta1', '1.00.0', '../1.0.0'])
def test_only_numeric_canonical_versions_allowed(version):
  with pytest.raises(catalog.CatalogError):
    catalog.Package(zip_bytes(version=version))


def test_import_is_byte_preserving_idempotent_and_immutable(root):
  target, original = import_zip(root)
  assert target.read_bytes() == original
  assert target == import_zip(root)[0]
  with pytest.raises(catalog.CatalogError, match='different bytes'):
    import_zip(root, extras={'script.jelq/new.py': 'changed'})
  assert target.read_bytes() == original


@pytest.mark.parametrize('addon_id', ['repository.jelq', 'script.unrelated'])
def test_only_the_two_approved_addons_can_be_imported(root, addon_id):
  source = root / 'input.zip'
  source.write_bytes(zip_bytes(addon_id=addon_id))
  with pytest.raises(catalog.CatalogError, match=r'Only script\.jelq and skin\.jelq'):
    catalog.import_package(source, addon_id, '0.1.0', root)


def test_build_catalog_latest_checksum_assets_and_old_packages(root):
  old_path, old_data = import_zip(root, '0.2.9')
  new_path, new_data = import_zip(
    root,
    '0.2.10',
    assets='<icon>resources/icon.png</icon>',
    extras={
      'script.jelq/resources/icon.png': b'icon bytes',
      'script.jelq/private.py': b'only inside ZIP',
    },
  )
  site = catalog.build(root)
  xml = (site / 'addons/addons.xml').read_bytes()
  entries = {entry.get('id'): entry.get('version') for entry in ET.fromstring(xml)}  # noqa: S314
  assert entries == {'repository.jelq': REPOSITORY_VERSION, 'script.jelq': '0.2.10'}
  # Kodi's repository protocol specifies an MD5 change marker, not a security digest.
  digest = hashlib.md5(xml).hexdigest()  # noqa: S324
  assert (site / 'addons/addons.xml.md5').read_text().strip() == digest
  package_dir = site / 'addons' / 'script.jelq'
  assert (package_dir / old_path.name).read_bytes() == old_data
  assert (package_dir / new_path.name).read_bytes() == new_data
  assert (package_dir / 'resources/icon.png').read_bytes() == b'icon bytes'
  assert not (package_dir / 'private.py').exists()
  assert not (package_dir / 'LICENSE').exists()
  assert f'href="{REPOSITORY_ZIP}"' in (site / 'index.html').read_text()
  assert (site / '.nojekyll').exists()


def test_repository_zip_is_deterministic_and_build_cleans_stale_output(root):
  site = catalog.build(root)
  first = (site / REPOSITORY_ZIP).read_bytes()
  (site / 'stale.txt').write_text('old generated file')
  catalog.build(root)
  second = (site / REPOSITORY_ZIP).read_bytes()
  assert first == second
  assert not (site / 'stale.txt').exists()
  assert catalog.Package(second).addon_id == 'repository.jelq'


def test_bootstrap_zip_is_visible_to_kodi_http_directory(root):
  import_zip(root)
  site = catalog.build(root)
  # Kodi 21.3 HTTPDirectory.cpp requires href first and a filename label
  # matching the relative link. A browser's HTML parser is more permissive.
  # https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/filesystem/HTTPDirectory.cpp
  matches = re.findall(
    r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>(.+?)(?=<a|</tr|$)',
    (site / 'index.html').read_text(encoding='utf-8'),
    re.IGNORECASE | re.DOTALL,
  )
  visible = [link for link, label, _ in matches if label.strip() == link]
  assert visible == [REPOSITORY_ZIP]
  installer = site / REPOSITORY_ZIP
  assert catalog.Package(installer.read_bytes()).addon_id == 'repository.jelq'


@pytest.mark.parametrize('tag', ['info', 'checksum'])
def test_repository_index_is_outside_the_browsed_root(root, tag):
  # Browsing the root to install the bootstrap ZIP caches its listing; Kodi
  # then fails any root file absent from that listing without a request.
  site = catalog.build(root)
  installed = catalog.Package((site / REPOSITORY_ZIP).read_bytes()).manifest
  url = installed.findtext("extension[@point='xbmc.addon.repository']/dir/" + tag)
  path = url[len(catalog.BASE_URL) :]
  assert url.startswith(catalog.BASE_URL)
  assert '/' in path
  assert site.joinpath(*path.split('/')).is_file()
  assert not (site / 'addons.xml').exists()
  assert not (site / 'addons.xml.md5').exists()


def test_build_rejects_repository_index_in_the_browsed_root(root):
  source = root / 'repository.jelq/addon.xml'
  source.write_bytes(
    REPOSITORY_MANIFEST.replace(b'jelq.github.io/addons/addons.xml', b'jelq.github.io/addons.xml')
  )
  with pytest.raises(catalog.CatalogError, match='browsed root'):
    catalog.build(root)


def test_every_package_and_bootstrap_zip_has_a_sha256_sidecar(root):
  import_zip(root, '0.2.9')
  import_zip(root, '0.2.10')
  site = catalog.build(root)
  packages = sorted(site.rglob('*.zip'))
  assert sorted(package.name for package in packages) == [
    REPOSITORY_ZIP,
    REPOSITORY_ZIP,
    'script.jelq-0.2.10.zip',
    'script.jelq-0.2.9.zip',
  ]
  for package in packages:
    expected = hashlib.sha256(package.read_bytes()).hexdigest().encode('ascii') + b'\n'
    assert package.with_name(package.name + '.sha256').read_bytes() == expected


def test_failed_build_preserves_previous_site(root):
  site = catalog.build(root)
  before = (site / 'addons/addons.xml').read_bytes()
  package_dir = root / 'packages/script.jelq'
  package_dir.mkdir(parents=True)
  (package_dir / 'script.jelq-0.1.0.zip').write_bytes(b'invalid')
  with pytest.raises(catalog.CatalogError):
    catalog.build(root)
  assert (site / 'addons/addons.xml').read_bytes() == before


def test_metadata_cannot_overwrite_package(root):
  import_zip(
    root,
    assets='<icon>script.jelq-0.1.0.zip</icon>',
    extras={'script.jelq/script.jelq-0.1.0.zip': b'not a package'},
  )
  with pytest.raises(catalog.CatalogError, match='conflicts'):
    catalog.build(root)


def test_metadata_cannot_overwrite_package_hash(root):
  filename = 'script.jelq-0.1.0.zip.sha256'
  import_zip(
    root, assets=f'<icon>{filename}</icon>', extras={'script.jelq/' + filename: b'fake digest'}
  )
  with pytest.raises(catalog.CatalogError, match='conflicts'):
    catalog.build(root)
