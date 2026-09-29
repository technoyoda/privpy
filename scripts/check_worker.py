"""Run the existing contract suite through the local experimental worker backend."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

from hypothesis import settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=int, default=25, help="Generated examples per property; default 25")
    parser.add_argument("--sanitizer-log", type=Path, help="Capture worker stderr during these synthetic tests and fail on UBSan diagnostics")
    args = parser.parse_args()
    if not 1 <= args.examples <= 10000:
        parser.error("--examples must be between 1 and 10000")
    import tests  # Register the normal profiles before selecting this local profile.
    from privpy._core import PrivateRegion
    from privpy._worker import WorkerNative, worker_path
    if not worker_path().is_file():
        parser.error("Build the worker first: python -m privpy.build --worker")
    settings.register_profile("worker_contract", max_examples=args.examples, stateful_step_count=15,
                              deadline=None, derandomize=True)
    settings.load_profile("worker_contract")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    # Existing fixtures import the original region class. Replacing its factory
    # exercises their API unchanged, without changing production defaults.
    with ExitStack() as stack:
        if args.sanitizer_log:
            output = stack.enter_context(args.sanitizer_log.open("w", encoding="utf-8"))
            popen = subprocess.Popen

            def capture(*command, **kwargs):
                kwargs["stderr"] = output
                return popen(*command, **kwargs)

            stack.enter_context(patch("privpy._worker.subprocess.Popen", side_effect=capture))
        stack.enter_context(patch.object(PrivateRegion, "_make_native", lambda self: WorkerNative()))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    if args.sanitizer_log and re.search(r"runtime error:|UndefinedBehaviorSanitizer|SUMMARY:.*[Ss]anitizer",
                                       args.sanitizer_log.read_text(encoding="utf-8", errors="replace")):
        print("Worker sanitizer diagnostics detected; inspect the captured log", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
