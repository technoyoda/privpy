"""Build the native runtime: python -m privpy.build."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def library_name():
    if sys.platform == "darwin":
        return "_runtime.dylib"
    if sys.platform.startswith("linux"):
        return "_runtime.so"
    raise RuntimeError("The initial native runtime supports macOS and Linux")


def build_native(output=None, sanitize=False):
    return _build(output, sanitize, worker=False)


def build_worker(output=None, sanitize=False):
    return _build(output, sanitize, worker=True)


def _build(output, sanitize, worker):
    name = "_worker_runtime" if worker else library_name()
    if worker:
        library_name()  # Apply the same supported-platform check.
    if sys.platform == "darwin":
        os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "14.0")
    root = Path(__file__).resolve().parent
    destination = Path(output) if output is not None else root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    compiler = shlex.split(os.environ.get("CXX", "c++"))
    if not compiler or shutil.which(compiler[0]) is None:
        raise RuntimeError("A C++17 compiler is required to build privpy")
    command = compiler + ["-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-fPIC"]
    command += shlex.split(os.environ.get("CXXFLAGS", ""))
    if sys.platform == "darwin":
        command += shlex.split(os.environ.get("ARCHFLAGS", ""))
    if worker:
        command += ["-pthread"]
    else:
        command += ["-dynamiclib" if sys.platform == "darwin" else "-shared"]
    command += ["-O1", "-g", "-fsanitize=undefined", "-fno-sanitize-recover=all"] if sanitize else ["-O2"]
    source = "worker.cpp" if worker else "runtime.cpp"
    command += [str(root / "native" / source), "-o", str(destination)]
    command += shlex.split(os.environ.get("LDFLAGS", ""))
    subprocess.run(command, check=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help="Build the local experimental worker executable")
    parser.add_argument("--sanitize", action="store_true", help="Enable UndefinedBehaviorSanitizer")
    args = parser.parse_args()
    build = build_worker if args.worker else build_native
    print(build(args.output, args.sanitize))


if __name__ == "__main__":
    main()
