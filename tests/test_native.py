import ctypes
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest

from hypothesis import given, strategies as st

from privpy import PrivateRegion, PrivateExecutionError, ResourceLimitError
from privpy._native import native
from tests import fixtures as f
from tests.helpers import make_function, run


class SourceAndRuntimeTests(unittest.TestCase):
    def test_invalid_json_corpus(self):
        invalid = [
            b"", b" ", b"{", b"[", b"null x", b"true false", b"01", b"-",
            b"1.", b"1e", b"+1", b"[1,]", b'{"x":1,}', b'{"x":1,"x":2}',
            b'"\xff"', b'"\\ud800"', b'"\\udc00"', b'"\\ud800\\u1234"',
            b'"\\x00"', b'"\x00"', b"NaN", b"Infinity", b"1e9999",
            b"9223372036854775808", b"-9223372036854775809",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            for payload in invalid:
                path.write_bytes(payload)
                with self.subTest(payload=payload), self.assertRaises(PrivateExecutionError):
                    run(f.from_json, str(path))

    def test_invalid_utf8_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            for value in [b"\xff", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2", b"\x80"]:
                path.write_bytes(value)
                with self.subTest(value=value), self.assertRaises(PrivateExecutionError):
                    run(f.from_text, str(path))

    def test_json_unicode_surrogate_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b'"\\ud83d\\ude80"')
            self.assertEqual(run(f.from_json, str(path)), "🚀")

    def test_source_file_errors_sanitized(self):
        with self.assertRaises(PrivateExecutionError) as error:
            run(f.from_json, "/missing/SECRET-PATH-MARKER")
        self.assertNotIn("SECRET-PATH-MARKER", str(error.exception))

    def test_directory_and_fifo_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PrivateExecutionError):
                run(f.from_bytes, directory)
            fifo = Path(directory) / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(PrivateExecutionError):
                run(f.from_bytes, str(fifo))

    def test_null_byte_input_path_rejected(self):
        with self.assertRaises(PrivateExecutionError):
            run(f.from_text, "/tmp/secret\0suffix")

    def test_input_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large"
            path.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
            with self.assertRaises(ResourceLimitError):
                run(f.from_bytes, str(path))

    def test_full_byte_limit_round_trip(self):
        # Regression guard against imposing the plaintext size cap on hexadecimal transport.
        value = b"x" * (4 * 1024 * 1024)
        self.assertEqual(run(f.identity, value), value)

    def test_native_depth_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested"
            path.write_text("[" * 100 + "0" + "]" * 100)
            with self.assertRaises(ResourceLimitError):
                run(f.from_json, str(path))

    def test_string_growth_limit(self):
        function = make_function("while True:\n    value = value + value\nreturn value")
        with self.assertRaises(ResourceLimitError):
            run(function, "x")

    def test_collection_depth_cannot_grow_without_bound(self):
        function = make_function("for x in range(100):\n    value = [value]\nreturn value")
        with self.assertRaises(ResourceLimitError):
            run(function, 0)

    def test_shared_collection_expansion_has_a_bound(self):
        function = make_function("for x in range(40):\n    value = [value, value]\nreturn value")
        with self.assertRaises(ResourceLimitError):
            run(function, 0)

    def test_sum_rejects_text_start(self):
        with self.assertRaises(PrivateExecutionError):
            run(f.total_with_start, ["b"], "a")

    def test_output_item_limit(self):
        with self.assertRaises(ResourceLimitError):
            run(f.make_range, 0, 100001, 1)

    def test_negative_int64_minimum_literal(self):
        self.assertEqual(run(make_function("return -9223372036854775808"), None), -(2**63))

    def test_numeric_overflow_edges(self):
        cases = [(f.add, (2**63 - 1, 1)), (f.subtract, (-(2**63), 1)),
                 (f.multiply, (2**62, 4)), (f.floor_divide, (-(2**63), -1)),
                 (f.divide, (1, 0))]
        for function, args in cases:
            with self.subTest(function=function.__name__), self.assertRaises(PrivateExecutionError):
                run(function, *args)
        self.assertEqual(run(f.modulo, -(2**63), -1), 0)

    def test_subnormal_and_signed_zero(self):
        for value in [5e-324, -5e-324, 1e-308, -0.0]:
            with self.subTest(value=value):
                self.assertEqual(run(f.identity, value), value)

    def test_float_floor_division_explicitly_unsupported(self):
        for operation in [f.floor_divide, f.modulo]:
            with self.assertRaises(PrivateExecutionError):
                run(operation, 5.5, 2.0)

    def test_ambiguous_mixed_comparison_rejected(self):
        with self.assertRaises(PrivateExecutionError):
            run(f.equal, 2**53 + 1, float(2**53))

    def test_only_one_concurrent_export_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "winner"
            outcomes = []
            barrier = threading.Barrier(8)
            def worker(n):
                with PrivateRegion() as region:
                    ref = f.identity(str(n).encode())
                    barrier.wait()
                    try:
                        region.export(ref, to=path)
                        outcomes.append(("success", n))
                    except FileExistsError:
                        outcomes.append(("exists", n))
            threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            successful = [n for result, n in outcomes if result == "success"]
            self.assertEqual(len(successful), 1)
            self.assertEqual(len(outcomes), 8)
            self.assertEqual(path.read_bytes(), str(successful[0]).encode())
            self.assertEqual([p.name for p in path.parent.iterdir()], ["winner"])


class NativeBoundaryProperties(unittest.TestCase):
    @given(st.binary(max_size=4000))
    def test_malformed_program_registration_never_escapes_native_error_handling(self, payload):
        lib = native().lib
        region = lib.pr_create(1000)
        try:
            status = lib.pr_register(region, b"fuzz", payload)
            self.assertIn(status, (0, 1))
            if status == 0:
                self.assertTrue(lib.pr_last_error())
        finally:
            self.assertEqual(lib.pr_close(region), 1)

    @given(st.binary(max_size=4000))
    def test_malformed_call_arguments_never_crash(self, payload):
        lib = native().lib
        region = lib.pr_create(1000)
        try:
            program = b'{"params":[],"body":[{"k":"return","v":{"k":"literal","v":["null"]}}]}'
            self.assertEqual(lib.pr_register(region, b"test", program), 1)
            handle = lib.pr_call(region, b"test", payload)
            if handle:
                exported = lib.pr_export_value(region, handle)
                self.assertTrue(exported)
                lib.pr_free(exported)
            else:
                self.assertTrue(lib.pr_last_error())
        finally:
            self.assertEqual(lib.pr_close(region), 1)

    @given(st.integers(2**62, 2**63 - 1))
    def test_unknown_region_ids_rejected(self, region):
        lib = native().lib
        self.assertEqual(lib.pr_close(region), 0)
        self.assertEqual(lib.pr_call(region, b"x", b"[]"), 0)
        self.assertFalse(lib.pr_export_value(region, 1))

    @given(st.integers(1, 2**63 - 1))
    def test_unknown_object_ids_rejected(self, handle):
        lib = native().lib
        region = lib.pr_create(1000)
        try:
            self.assertFalse(lib.pr_export_value(region, handle))
            self.assertEqual(lib.pr_last_error(), b"InvalidReference")
        finally:
            lib.pr_close(region)

    @given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=300))
    def test_native_json_reader_accepts_only_its_documented_json_subset(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_text(source, encoding="utf-8")
            try:
                expected = json.loads(source)
            except (ValueError, RecursionError):
                with self.assertRaises(PrivateExecutionError):
                    run(f.from_json, str(path))
            else:
                # The generated corpus is small; unbounded ints/nonfinite floats are explicitly excluded.
                if type(expected) is int and not -(2**63) <= expected < 2**63:
                    with self.assertRaises(PrivateExecutionError):
                        run(f.from_json, str(path))
                    return
                if type(expected) is float and not math.isfinite(expected):
                    with self.assertRaises(PrivateExecutionError):
                        run(f.from_json, str(path))
                    return
                self.assertEqual(run(f.from_json, str(path)), expected)
