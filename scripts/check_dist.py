"""Verify the package identity, native contents, and optional full wheel matrix."""
import argparse
from email.parser import BytesParser
from pathlib import Path
import runpy
import tarfile
import zipfile


PYTHONS = {"cp39", "cp310", "cp311", "cp312", "cp313", "cp314"}
PLATFORMS = {"linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-arm64"}


def metadata_ok(data, version):
    metadata = BytesParser().parsebytes(data)
    if metadata["Name"] != "privpy" or metadata["Version"] != version:
        raise ValueError("Distribution name or version does not match the source")


def wheel_target(path):
    _, _, python, abi, platforms = path.stem.rsplit("-", 4)
    if abi != python:
        raise ValueError(f"Unexpected wheel ABI: {path.name}")
    first = platforms.split(".")[0]
    if first.startswith("manylinux_"):
        family = "linux"
    elif first.startswith("macosx_"):
        family = "macos"
    else:
        raise ValueError(f"Unsupported wheel platform: {path.name}")
    architecture = next((a for a in ("x86_64", "aarch64", "arm64") if first.endswith("_" + a)), None)
    if architecture is None:
        raise ValueError(f"Unsupported wheel architecture: {path.name}")
    return python, family + "-" + architecture


def check_distributions(directory, version, release_matrix=False):
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if not wheels or len(sdists) != 1:
        raise ValueError("Expected at least one wheel and exactly one source archive")
    targets = set()
    for path in wheels:
        if not path.name.startswith(f"privpy-{version}-"):
            raise ValueError(f"Wheel filename does not match the source: {path.name}")
        target = wheel_target(path)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError(f"Missing or ambiguous wheel metadata: {path.name}")
            metadata_ok(archive.read(metadata[0]), version)
            native = [name for name in names if name in {"privpy/_runtime.so", "privpy/_runtime.dylib"}]
            if len(native) != 1 or archive.getinfo(native[0]).file_size == 0:
                raise ValueError(f"Missing native runtime: {path.name}")
            expected = "privpy/_runtime.dylib" if target[1].startswith("macos-") else "privpy/_runtime.so"
            if native[0] != expected:
                raise ValueError(f"Native library does not match the platform: {path.name}")
            if "privpy/_version.py" not in names:
                raise ValueError(f"Missing Python package: {path.name}")
        if target in targets:
            raise ValueError(f"Duplicate wheel target: {target}")
        targets.add(target)
    if sdists[0].name != f"privpy-{version}.tar.gz":
        raise ValueError("Source archive filename does not match the source")
    with tarfile.open(sdists[0]) as archive:
        prefix = f"privpy-{version}/"
        names = set(archive.getnames())
        required = {
            "PKG-INFO", "pyproject.toml", "setup.py", "README.md",
            "src/privpy/native/runtime.cpp", "src/privpy/build.py",
            "src/privpy/_version.py", "tests/test_properties.py", "tests/test_stateful.py",
        }
        if not {prefix + name for name in required}.issubset(names):
            raise ValueError("Source archive is missing build or test inputs")
        with archive.extractfile(prefix + "PKG-INFO") as metadata:
            metadata_ok(metadata.read(), version)
    if release_matrix and targets != {(py, platform) for py in PYTHONS for platform in PLATFORMS}:
        raise ValueError(f"Incomplete release wheel matrix: {sorted(targets)}")
    return len(wheels), len(sdists)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--release-matrix", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = runpy.run_path(str(root / "src/privpy/_version.py"))["__version__"]
    wheels, sources = check_distributions(args.directory, version, args.release_matrix)
    print(f"Verified privpy {version}: {wheels} wheel(s), {sources} source archive")
