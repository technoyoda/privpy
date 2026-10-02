"""Local worker transport and lifetime contracts. Build with privpy.build --worker."""
import contextvars
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from privpy import CrossRegionError, ExportError, PrivateAccessError, PrivateExecutionError, RegionClosedError, ResourceLimitError
from privpy import _native
from privpy._worker import MAX_FRAME, WorkerNative, worker_path
from privpy.worker import PrivateRegion, WorkerError
from tests import fixtures as f
from tests import workloads as w
from tests.strategies import values


# Process creation is part of each generated example; keep this suite bounded.
worker_examples = settings(max_examples=min(50, settings.default.max_examples), deadline=None)
worker_available = worker_path().is_file()


@unittest.skipUnless(worker_available, "Build the experimental worker with python -m privpy.build --worker")
class WorkerApiTests(unittest.TestCase):
    def test_startup_does_not_forward_parent_environment_or_standard_streams(self):
        popen = subprocess.Popen
        with patch.dict(os.environ, {"PRIVATE_ENV_VALUE": "not-for-the-worker", "LD_PRELOAD": "/not-a-library"}):
            with patch("privpy._worker.subprocess.Popen", wraps=popen) as launch:
                with PrivateRegion() as region:
                    self.assertEqual(region.export(f.identity(1)), 1)
                options = launch.call_args.kwargs
        self.assertEqual(set(options["env"]), {"PATH", "LANG", "LC_ALL"})
        self.assertTrue(options["close_fds"])
        self.assertEqual(len(options["pass_fds"]), 1)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(options[stream], subprocess.DEVNULL)

    def test_failed_startup_reaps_the_child(self):
        popen = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            process = popen(*args, **kwargs)
            children.append(process)
            return process

        with patch("privpy._worker.subprocess.Popen", side_effect=launch):
            with self.assertRaises(WorkerError):
                WorkerNative(executable=sys.executable)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_separate_process_and_no_parent_native_library_required(self):
        previous = _native._instance
        with patch("privpy._core.native", side_effect=AssertionError("same-process backend used")):
            with PrivateRegion() as region:
                self.assertNotEqual(region.worker_pid, os.getpid())
                self.assertTrue(region.worker_protections["core_dumps_disabled"])
                self.assertIn(region.worker_protections["inspection_restriction"],
                              ("linux-nondumpable", "darwin-deny-attach"))
                report = region.worker_protections
                report.clear()
                self.assertTrue(region.worker_protections)
                self.assertEqual(region.export(f.add(2, 3)), 5)
        self.assertIs(_native._instance, previous)

    def test_private_file_data_and_intermediates_do_not_cross_the_bridge(self):
        marker = "WORKER-ONLY-TEST-CONTENTS"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "source"
            path.write_text(marker, encoding="utf-8")
            with PrivateRegion() as region:
                frames = []
                receive = region._native._read_frame

                def observe(deadline):
                    result = receive(deadline)
                    frames.append(result)
                    return result

                with patch.object(region._native, "_read_frame", side_effect=observe):
                    text = f.from_text(str(path))
                    size = f.length(text)
                    self.assertEqual(repr(text), "<PrivateRef>")
                    with self.assertRaises(PrivateAccessError):
                        bool(size)
                    self.assertNotIn(marker, json.dumps(frames))
                    for frame in frames:
                        self.assertTrue(frame["ok"])
                        self.assertTrue(frame["result"] is None or type(frame["result"]) is int)
                    self.assertEqual(region.export(size), len(marker))
                    self.assertNotIn(marker, json.dumps(frames))
                    self.assertEqual(region.export(text), marker)
                    self.assertIn(marker, json.dumps(frames))

    def test_file_export_does_not_return_its_bytes_to_the_parent(self):
        marker = b"WORKER-FILE-EXPORT-CONTENTS"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve() / "source"
            target = source.with_name("target")
            source.write_bytes(marker)
            with PrivateRegion() as region:
                value = f.from_bytes(str(source))
                receive = region._native._read_frame
                with patch.object(region._native, "_read_frame", wraps=receive) as observed:
                    self.assertIsNone(region.export(value, to=target))
                    self.assertEqual(observed.call_count, 1)
                with self.assertRaises(FileExistsError):
                    region.export(value, to=target)
            self.assertEqual(target.read_bytes(), marker)

    def test_composed_private_calls_use_one_call_request(self):
        with PrivateRegion() as region:
            region._register(w.transfer_batch)
            exchange = region._native._exchange
            with patch.object(region._native, "_exchange", wraps=exchange) as observed:
                result = w.transfer_batch({"a": 100, "b": 200}, [["a", "b", 7], ["b", "a", 3]])
                self.assertEqual(observed.call_count, 1)
                self.assertEqual(observed.call_args.args[0]["op"], "call")
            self.assertEqual(region.export(result), {"a": 96, "b": 204})

    def test_nested_regions_have_distinct_workers_and_restore_parent(self):
        with PrivateRegion() as outer:
            ref = f.identity(17)
            with PrivateRegion() as inner:
                self.assertNotEqual(inner.worker_pid, outer.worker_pid)
                with self.assertRaises(CrossRegionError):
                    f.identity(ref)
                with self.assertRaises(CrossRegionError):
                    inner.export(ref)
            self.assertEqual(outer.export(ref), 17)

    def test_normal_close_reaps_worker_and_invalidates_references(self):
        with PrivateRegion() as region:
            value = f.identity(11)
            process = region._native._process
        self.assertEqual(process.poll(), 0)
        with self.assertRaises(RegionClosedError):
            region.export(value)

    def test_body_exception_closes_worker(self):
        with self.assertRaisesRegex(RuntimeError, "public exception"):
            with PrivateRegion() as region:
                process = region._native._process
                raise RuntimeError("public exception")
        self.assertEqual(process.poll(), 0)

    def test_wrong_context_cannot_close_owners_worker(self):
        with PrivateRegion() as region:
            value = f.identity(12)
            with self.assertRaises(ValueError):
                contextvars.copy_context().run(region.__exit__, None, None, None)
            self.assertIsNone(region._native._process.poll())
            self.assertEqual(region.export(value), 12)

    def test_errors_and_step_limits_leave_region_usable(self):
        with PrivateRegion(max_steps=100) as region:
            with self.assertRaises(PrivateExecutionError):
                f.divide_by_zero(1)
            with self.assertRaises(ResourceLimitError):
                f.endless(0)
            self.assertEqual(region.export(f.add(3, 4)), 7)

    def test_worker_death_fails_closed_without_same_process_fallback(self):
        with PrivateRegion() as region:
            value = f.identity(13)
            region._native._process.kill()
            region._native._process.wait(timeout=5)
            with self.assertRaises(WorkerError):
                region.export(value)
            with self.assertRaises(WorkerError):
                f.identity(14)

    def test_timeout_terminates_and_reaps_worker(self):
        with PrivateRegion(max_steps=10_000_000) as region:
            region._register(f.endless)
            region._native._timeout = 0.01
            with self.assertRaises(WorkerError):
                f.endless(0)
            self.assertIsNotNone(region._native._process.poll())
            with self.assertRaises(WorkerError):
                f.identity(1)

    def test_forked_caller_is_rejected_before_using_the_connection(self):
        with PrivateRegion() as region:
            value = f.identity(15)
            with patch("privpy._worker.os.getpid", return_value=-1):
                with self.assertRaises(WorkerError):
                    region.export(value)
            self.assertEqual(region.export(value), 15)

    def test_missing_executable_has_no_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PRIVPY_WORKER": str(Path(directory) / "absent")}):
                with self.assertRaises(WorkerError):
                    with PrivateRegion():
                        self.fail("region must not open")

    @worker_examples
    @given(values)
    def test_generated_values_round_trip(self, value):
        with PrivateRegion() as region:
            self.assertEqual(region.export(f.identity(value)), value)

    @worker_examples
    @given(values, values)
    def test_generated_mutations_preserve_original_snapshot(self, initial, replacement):
        with PrivateRegion() as region:
            original = f.identity({"nested": {"value": initial}})
            updated = w.replace_nested(original, "value", replacement)
            self.assertEqual(region.export(updated), [
                {"nested": {"value": initial}}, {"nested": {"value": replacement}},
            ])
            self.assertEqual(region.export(original), {"nested": {"value": initial}})

    @worker_examples
    @given(st.dictionaries(st.text(alphabet="abcd", max_size=5), st.integers(-10000, 10000), max_size=5))
    def test_generated_summary_transform(self, balances):
        with PrivateRegion() as region:
            result = region.export(f.identity(balances), transform=w.ledger_summary_bytes)
            self.assertEqual(json.loads(result), {"accounts": len(balances), "total": sum(balances.values())})


@unittest.skipUnless(worker_available, "Build the experimental worker with python -m privpy.build --worker")
class WorkerProtocolTests(unittest.TestCase):
    def test_worker_itself_rejects_unknown_operations_fields_and_handles(self):
        requests = [
            {"op": "evaluate_python", "source": "anything"},
            {"op": "close", "region": 2},
            {"op": "export_value", "handle": 2**62},
            {"op": "export_value", "handle": -1},
            {"op": "register", "name": "contains\0nul", "program": "{}"},
        ]
        with PrivateRegion() as region:
            for request in requests:
                with self.subTest(request=request), self.assertRaises(PrivateExecutionError):
                    region._native._exchange(request)
            self.assertEqual(region.export(f.identity(1)), 1)

    def test_destination_checks_are_enforced_inside_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "existing"
            target.write_bytes(b"keep")
            with PrivateRegion() as region:
                value = f.identity(b"new")
                for path in ("relative", str(root / ".." / "escape"), str(root / "nul") + "\0suffix"):
                    with self.subTest(path=path), self.assertRaises((PrivateExecutionError, ExportError)):
                        region._native._exchange({"op": "export_file", "handle": value._handle, "path": path})
                with self.assertRaises(FileExistsError):
                    region._native._exchange({"op": "export_file", "handle": value._handle, "path": str(target)})
            self.assertEqual(target.read_bytes(), b"keep")
            self.assertEqual(list(root.iterdir()), [target])

    def test_unknown_response_error_is_not_echoed(self):
        with PrivateRegion() as region:
            with patch.object(region._native, "_read_frame", return_value={"ok": False, "error": "PRIVATE-MARKER"}):
                with self.assertRaises(WorkerError) as caught:
                    f.identity(1)
            self.assertNotIn("PRIVATE-MARKER", str(caught.exception))
            self.assertIsNotNone(region._native._process.poll())

    def test_invalid_export_wire_closes_connection(self):
        with PrivateRegion() as region:
            value = f.identity(1)
            with patch.object(region._native, "_exchange", return_value=["unknown-wire-kind"]):
                with self.assertRaises(WorkerError):
                    region.export(value)
            self.assertIsNotNone(region._native._process.poll())

    def test_fragmented_request_and_clean_connection_eof(self):
        bridge = WorkerNative()
        try:
            payload = b'{"op":"create","max_steps":100}'
            for byte in struct.pack(">I", len(payload)) + payload:
                bridge._socket.sendall(bytes([byte]))
            self.assertEqual(bridge._read_frame(time.monotonic() + 5), {"ok": True, "result": 1})
            bridge._socket.shutdown(socket.SHUT_WR)
            self.assertEqual(bridge._process.wait(timeout=5), 0)
        finally:
            bridge._shutdown()

    def test_truncated_and_oversized_frames_terminate_worker(self):
        for payload in (struct.pack(">I", MAX_FRAME + 1), struct.pack(">I", 0), struct.pack(">I", 20) + b"short"):
            with self.subTest(payload=payload):
                bridge = WorkerNative()
                try:
                    bridge._socket.sendall(payload)
                    if len(payload) > 4:  # A truncated body needs EOF; invalid headers close immediately.
                        bridge._socket.shutdown(socket.SHUT_WR)
                    self.assertNotEqual(bridge._process.wait(timeout=5), 0)
                finally:
                    bridge._shutdown()

    @worker_examples
    @given(st.binary(max_size=1000))
    def test_generated_malformed_programs_cannot_crash_worker(self, payload):
        with PrivateRegion() as region:
            try:
                region._native._exchange({"op": "register", "name": "fuzz", "program": payload.decode("latin1")})
            except PrivateExecutionError:
                pass
            self.assertEqual(region.export(f.identity(23)), 23)
