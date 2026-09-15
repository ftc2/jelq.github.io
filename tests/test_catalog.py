import hashlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from tools import catalog


def manifest(addon_id="script.jelq", version="0.1.0", assets=""):
    return ('<addon id="%s" version="%s" name="Test" provider-name="ftc2">'
            '<extension point="xbmc.addon.metadata"><assets>%s</assets></extension>'
            '</addon>' % (addon_id, version, assets)).encode()


def zip_bytes(addon_id="script.jelq", version="0.1.0", extras=None, assets=""):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(addon_id + "/addon.xml", manifest(addon_id, version, assets))
        archive.writestr(addon_id + "/LICENSE", "License text must survive unchanged.\n")
        for name, content in (extras or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


class PackageSafetyTests(unittest.TestCase):
    def test_rejects_wrong_identity_or_version(self):
        data = zip_bytes()
        for expected_id, expected_version in [("skin.jelq", "0.1.0"), ("script.jelq", "0.2.0")]:
            with self.subTest(expected_id=expected_id, expected_version=expected_version):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Package(data, expected_id, expected_version)

    def test_rejects_traversal_absolute_and_wrong_roots(self):
        for path in ["script.jelq/../outside", "/absolute", "script.jelq/C:/bad",
                     "another/file", "script.jelq//file", "script.jelq/file.", "script.jelq/NUL.txt"]:
            with self.subTest(path=path):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Package(zip_bytes(extras={path: "bad"}))

    def test_original_archive_names_are_checked_before_zipfile_normalizes_them(self):
        data = zip_bytes(extras={"script.jelq/bad": "bad"})
        for unsafe in [b"script.jelq\\bad", b"script.jelq/ba\x00"]:
            with self.subTest(path=unsafe):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Package(data.replace(b"script.jelq/bad", unsafe))

    def test_rejects_case_collisions_and_file_directory_collisions(self):
        for extras in [{"script.jelq/ADDON.XML": "collision"},
                       {"script.jelq/resources": "file", "script.jelq/resources/icon.png": b"image"}]:
            with self.subTest(extras=extras):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Package(zip_bytes(extras=extras))

    def test_rejects_duplicate_paths(self):
        output = io.BytesIO(zip_bytes())
        with zipfile.ZipFile(output, "a") as archive:
            with self.assertWarns(UserWarning):
                archive.writestr("script.jelq/addon.xml", manifest())
        with self.assertRaises(catalog.CatalogError):
            catalog.Package(output.getvalue())

    def test_rejects_symlink(self):
        output = io.BytesIO(zip_bytes())
        with zipfile.ZipFile(output, "a") as archive:
            member = zipfile.ZipInfo("script.jelq/link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "../../outside")
        with self.assertRaises(catalog.CatalogError):
            catalog.Package(output.getvalue())

    def test_assets_must_be_present_and_safe(self):
        for asset in ["resources/missing.png", "../outside", "https://example.com/image.png"]:
            with self.subTest(asset=asset):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Package(zip_bytes(assets="<icon>%s</icon>" % asset))

    def test_only_numeric_canonical_versions_allowed(self):
        for version in ["1.0", "1.0.0-beta1", "1.00.0", "../1.0.0"]:
            with self.subTest(version=version):
                with self.assertRaises(catalog.CatalogError):
                    catalog.Package(zip_bytes(version=version))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "repository.jelq"
        source.mkdir()
        (source / "addon.xml").write_bytes(manifest("repository.jelq", "1.0.0"))

    def import_zip(self, version="0.1.0", extras=None, assets=""):
        data = zip_bytes(version=version, extras=extras, assets=assets)
        source = self.root / "input.zip"
        source.write_bytes(data)
        return catalog.import_package(source, "script.jelq", version, self.root), data

    def test_import_is_byte_preserving_idempotent_and_immutable(self):
        target, original = self.import_zip()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target, self.import_zip()[0])
        with self.assertRaisesRegex(catalog.CatalogError, "different bytes"):
            self.import_zip(extras={"script.jelq/new.py": "changed"})
        self.assertEqual(target.read_bytes(), original)

    def test_only_the_two_approved_addons_can_be_imported(self):
        for addon_id in ["repository.jelq", "script.unrelated"]:
            with self.subTest(addon_id=addon_id):
                source = self.root / "input.zip"
                source.write_bytes(zip_bytes(addon_id=addon_id))
                with self.assertRaisesRegex(catalog.CatalogError, "Only script.jelq and skin.jelq"):
                    catalog.import_package(source, addon_id, "0.1.0", self.root)

    def test_build_catalog_latest_checksum_assets_and_old_packages(self):
        old_path, old_data = self.import_zip("0.2.9")
        new_path, new_data = self.import_zip("0.2.10", assets="<icon>resources/icon.png</icon>",
                                             extras={"script.jelq/resources/icon.png": b"icon bytes",
                                                     "script.jelq/private.py": b"only inside ZIP"})
        site = catalog.build(self.root)
        xml = (site / "addons.xml").read_bytes()
        entries = {entry.get("id"): entry.get("version") for entry in ET.fromstring(xml)}
        self.assertEqual(entries, {"repository.jelq": "1.0.0", "script.jelq": "0.2.10"})
        self.assertEqual((site / "addons.xml.md5").read_text().strip(), hashlib.md5(xml).hexdigest())
        package_dir = site / "addons" / "script.jelq"
        self.assertEqual((package_dir / old_path.name).read_bytes(), old_data)
        self.assertEqual((package_dir / new_path.name).read_bytes(), new_data)
        self.assertEqual((package_dir / "resources/icon.png").read_bytes(), b"icon bytes")
        self.assertFalse((package_dir / "private.py").exists())
        self.assertFalse((package_dir / "LICENSE").exists())
        self.assertIn('href="repository.jelq-1.0.0.zip"', (site / "index.html").read_text())
        self.assertTrue((site / ".nojekyll").exists())

    def test_repository_zip_is_deterministic_and_build_cleans_stale_output(self):
        site = catalog.build(self.root)
        first = (site / "repository.jelq-1.0.0.zip").read_bytes()
        (site / "stale.txt").write_text("old generated file")
        catalog.build(self.root)
        second = (site / "repository.jelq-1.0.0.zip").read_bytes()
        self.assertEqual(first, second)
        self.assertFalse((site / "stale.txt").exists())
        self.assertEqual(catalog.Package(second).addon_id, "repository.jelq")

    def test_every_package_and_bootstrap_zip_has_a_sha256_sidecar(self):
        self.import_zip("0.2.9")
        self.import_zip("0.2.10")
        site = catalog.build(self.root)
        packages = sorted(site.rglob("*.zip"))
        self.assertEqual(len(packages), 4)
        for package in packages:
            with self.subTest(package=package.relative_to(site)):
                expected = hashlib.sha256(package.read_bytes()).hexdigest().encode("ascii") + b"\n"
                self.assertEqual(package.with_name(package.name + ".sha256").read_bytes(), expected)

    def test_failed_build_preserves_previous_site(self):
        site = catalog.build(self.root)
        before = (site / "addons.xml").read_bytes()
        package_dir = self.root / "packages/script.jelq"
        package_dir.mkdir(parents=True)
        (package_dir / "script.jelq-0.1.0.zip").write_bytes(b"invalid")
        with self.assertRaises(catalog.CatalogError):
            catalog.build(self.root)
        self.assertEqual((site / "addons.xml").read_bytes(), before)

    def test_metadata_cannot_overwrite_package(self):
        self.import_zip(assets="<icon>script.jelq-0.1.0.zip</icon>",
                        extras={"script.jelq/script.jelq-0.1.0.zip": b"not a package"})
        with self.assertRaisesRegex(catalog.CatalogError, "conflicts"):
            catalog.build(self.root)

    def test_metadata_cannot_overwrite_package_hash(self):
        filename = "script.jelq-0.1.0.zip.sha256"
        self.import_zip(assets="<icon>%s</icon>" % filename,
                        extras={"script.jelq/" + filename: b"fake digest"})
        with self.assertRaisesRegex(catalog.CatalogError, "conflicts"):
            catalog.build(self.root)


if __name__ == "__main__":
    unittest.main()
