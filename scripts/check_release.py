"""Reject a GitHub release whose tag does not match the source version."""
import os
from pathlib import Path
import runpy


def check_tag(tag, version):
    if tag != "v" + version:
        raise ValueError(f"Release tag must be v{version}; got {tag!r}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    version = runpy.run_path(str(root / "src/privpy/_version.py"))["__version__"]
    check_tag(os.environ.get("RELEASE_TAG", ""), version)
    print(f"Release tag matches privpy {version}")
