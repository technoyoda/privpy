"""Measure OS inspection routes against owned children, with positive controls.

No arbitrary PID input, debugger entitlement changes, sudo, or system changes.
Exit: 0 = measured denials with controls, 1 = exposure/error, 2 = inconclusive.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import tempfile

from privpy._worker import worker_path

ROOT = Path(__file__).resolve().parents[1]
SUCCEEDED = {"read_succeeded", "attach_succeeded"}
DENIED = {"denied", "task_port_unavailable", "attach_signalled"}


def classify(control, worker):
    """An inaccessible control must never become evidence for our protection."""
    if worker["outcome"] in SUCCEEDED or worker["outcome"] == "task_port_acquired_read_failed":
        return "exposed"
    if worker["outcome"] not in DENIED:
        return "error"
    if control["outcome"] not in DENIED | SUCCEEDED:
        return "error"
    if control["outcome"] not in SUCCEEDED:
        return "inconclusive"
    return "denied_with_control"


def run_case(executable, target, method, worker):
    command = [str(executable), target, method, str(executable if target == "control" else worker)]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
                          env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}, text=True) as process:
        try:
            output, _ = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            # Each case has a fresh process group; this also stops its target.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return {"outcome": "timeout"}
        if process.returncode != 0:
            return {"outcome": "probe_failed", "returncode": process.returncode}
    try:
        result = json.loads(output)
        if not isinstance(result, dict) or not isinstance(result.get("outcome"), str):
            raise ValueError
        return result
    except (ValueError, TypeError):
        return {"outcome": "invalid_probe_output"}


def probe():
    worker = worker_path()
    if not worker.is_file():
        raise RuntimeError("Build the worker first: python -m privpy.build --worker")
    if sys.platform == "darwin":
        methods = ["mach_vm_read", "ptrace", "ptrace_exceptions"]
    elif sys.platform.startswith("linux"):
        methods = ["process_vm_readv", "proc_mem", "ptrace"]
    else:
        raise RuntimeError("Inspection study supports macOS and Linux")
    with tempfile.TemporaryDirectory(prefix="privpy-inspection-") as directory:
        executable = Path(directory) / "inspection-probe"
        command = shlex.split(os.environ.get("CXX", "c++"))
        command += ["-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic",
                    str(ROOT / "scripts/native/inspection_probe.cpp"), "-o", str(executable)]
        subprocess.run(command, check=True)
        rows = []
        for method in methods:
            control = run_case(executable, "control", method, worker)
            protected = run_case(executable, "worker", method, worker)
            rows.append({"route": method, "control": control, "worker": protected,
                         "verdict": classify(control, protected)})
    return {"schema": 1, "platform": sys.platform, "architecture": platform.machine(),
            "os_release": platform.release(), "elevated": os.geteuid() == 0,
            "scope": "fresh child processes after startup; no inherited inspection handles",
            "routes": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Optional report path; contains no local paths or identity")
    args = parser.parse_args()
    result = probe()
    encoded = json.dumps(result, indent=2) + "\n"
    if args.json:
        args.json.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    verdicts = {row["verdict"] for row in result["routes"]}
    if verdicts & {"exposed", "error"}:
        return 1
    return 2 if "inconclusive" in verdicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
