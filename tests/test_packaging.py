"""Release guards: malformed or incomplete bundles must not be published."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from hypothesis import given, strategies as st

from scripts.check_dist import check_distributions, metadata_ok, wheel_target
from scripts.check_release import check_tag


class ReleaseGuardTests(unittest.TestCase):
    @given(st.text().filter(lambda value: value != "v0.1.0"))
    def test_nonmatching_release_tags_rejected(self, tag):
        with self.assertRaises(ValueError):
            check_tag(tag, "0.1.0")

    def test_matching_release_and_prerelease_tags(self):
        for version in ("0.1.0", "1.2.3rc1", "1.2.3.post1"):
            check_tag("v" + version, version)

    def test_wrong_metadata_rejected(self):
        for metadata in (b"", b"Name: another\nVersion: 0.1.0\n",
                         b"Name: privpy\nVersion: 9.9.9\n"):
            with self.assertRaises(ValueError):
                metadata_ok(metadata, "0.1.0")

    def test_platform_tags_and_abi(self):
        self.assertEqual(
            wheel_target(Path("privpy-0.1.0-cp312-cp312-manylinux_2_28_aarch64.whl")),
            ("cp312", "linux-aarch64"),
        )
        self.assertEqual(
            wheel_target(Path("privpy-0.1.0-cp314-cp314-macosx_14_0_arm64.whl")),
            ("cp314", "macos-arm64"),
        )
        for tag in ("cp312-abi3-manylinux_2_28_x86_64", "py3-none-any",
                    "cp312-cp312-win_amd64", "cp312-cp312-macosx_14_0_universal2"):
            with self.assertRaises(ValueError):
                wheel_target(Path("privpy-0.1.0-" + tag + ".whl"))

    def bundle(self, directory, native=True, version="0.1.0"):
        metadata = f"Name: privpy\nVersion: {version}\n".encode()
        wheel = directory / "privpy-0.1.0-cp312-cp312-macosx_14_0_arm64.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("privpy-0.1.0.dist-info/METADATA", metadata)
            archive.writestr("privpy/_version.py", '__version__ = "0.1.0"\n')
            if native:
                # Contents are synthetic; installed wheel tests validate actual executability.
                archive.writestr("privpy/_runtime.dylib", b"nonempty native placeholder")
        with tarfile.open(directory / "privpy-0.1.0.tar.gz", "w:gz") as archive:
            for name in ("PKG-INFO", "pyproject.toml", "setup.py", "README.md",
                         "src/privpy/native/runtime.cpp", "src/privpy/build.py",
                         "src/privpy/_version.py", "tests/test_properties.py",
                         "tests/test_stateful.py"):
                data = metadata if name == "PKG-INFO" else b"source placeholder"
                entry = tarfile.TarInfo("privpy-0.1.0/" + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))

    def test_local_bundle_is_valid_without_full_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(Path(directory))
            self.assertEqual(check_distributions(Path(directory), "0.1.0"), (1, 1))

    def test_release_requires_all_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(Path(directory))
            with self.assertRaises(ValueError):
                check_distributions(Path(directory), "0.1.0", release_matrix=True)

    def test_native_runtime_required(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(Path(directory), native=False)
            with self.assertRaises(ValueError):
                check_distributions(Path(directory), "0.1.0")

    def test_version_mismatch_blocks_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(Path(directory), version="2.0.0")
            with self.assertRaises(ValueError):
                check_distributions(Path(directory), "0.1.0")

    def test_empty_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                check_distributions(Path(directory), "0.1.0")
