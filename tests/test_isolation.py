"""Snapshot isolation, recovery, and observable failure contracts."""
import copy
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import struct
import tempfile
import unittest

from hypothesis import example, given, strategies as st

from privpy import PrivateExecutionError, PrivateRegion, ResourceLimitError
from tests import fixtures as f
from tests import workloads as w
from tests.helpers import make_function
from tests.strategies import text, values


class IsolationProperties(unittest.TestCase):
    @given(values, values)
    def test_nested_updates_preserve_all_previous_snapshots(self, value, replacement):
        public = {"nested": {"value": value}}
        original = copy.deepcopy(public)
        with PrivateRegion() as region:
            ref = f.identity(public)
            public["nested"]["value"] = replacement
            updated = w.replace_nested(ref, "value", replacement)
            self.assertEqual(region.export(updated), [original, {"nested": {"value": replacement}}])
            self.assertEqual(region.export(ref), original)

    @given(values, values)
    def test_shared_public_children_import_as_independent_values(self, value, replacement):
        child = [value]
        public = [child, child]
        with PrivateRegion() as region:
            ref = f.identity(public)
            child[0] = replacement
            exported = region.export(ref)
            self.assertIsNot(exported[0], exported[1])
            exported[0][0] = replacement
            self.assertEqual(exported[1], [value])
            self.assertEqual(region.export(ref), [[value], [value]])

    @given(values, values)
    def test_failed_update_cannot_change_input_or_poison_region(self, value, replacement):
        with PrivateRegion() as region:
            ref = f.identity({"value": value})
            with self.assertRaises(PrivateExecutionError):
                w.mutate_then_fail(ref, replacement)
            self.assertEqual(region.export(ref), {"value": value})
            self.assertEqual(region.export(f.update_record(ref, "value", replacement)),
                             {"value": replacement})

    @given(text)
    def test_native_errors_and_output_streams_do_not_include_private_marker(self, suffix):
        marker = "PRIVATE-CONTENT:" + suffix
        stdout, stderr = io.StringIO(), io.StringIO()
        with PrivateRegion() as region:
            secret = f.identity(marker)
            messages = []
            with redirect_stdout(stdout), redirect_stderr(stderr):
                for function in (f.divide_by_zero, f.fail_private):
                    with self.assertRaises(PrivateExecutionError) as caught:
                        function(secret)
                    error = caught.exception
                    messages.append((type(error), error.args))
                    self.assertNotIn(marker, str(error))
                    self.assertNotIn(marker, repr(error))
            # Input-independent error text for these two fixed failing operations.
            for function, expected in zip((f.divide_by_zero, f.fail_private), messages):
                with self.assertRaises(PrivateExecutionError) as caught:
                    function(f.identity("public-control"))
                self.assertEqual((type(caught.exception), caught.exception.args), expected)
            self.assertEqual(region.export(secret), marker)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    @given(values, st.binary(max_size=100))
    def test_failed_export_transform_publishes_nothing_and_can_retry(self, value, replacement):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "result"
            with PrivateRegion() as region:
                ref = f.identity({"value": value})
                with self.assertRaises(PrivateExecutionError):
                    region.export(ref, to=destination, transform=f.fail_private)
                self.assertEqual(list(root.iterdir()), [])
                self.assertEqual(region.export(ref), {"value": value})
                region.export(f.identity(replacement), to=destination)
            self.assertEqual(destination.read_bytes(), replacement)
            self.assertEqual(list(root.iterdir()), [destination])

    @example(0.0)
    @example(-0.0)
    @given(st.floats(allow_nan=False, allow_infinity=False))
    def test_float_unary_operations_match_python_bits(self, value):
        with PrivateRegion() as region:
            actual = region.export(w.float_unaries(value))
        self.assertEqual(len(actual), 3)
        for result, expected in zip(actual, [-value, +value, abs(value)]):
            self.assertIs(type(result), float)
            self.assertEqual(struct.pack(">d", result), struct.pack(">d", expected))


class RecoveryTests(unittest.TestCase):
    def test_step_budget_resets_after_both_success_and_failure(self):
        with PrivateRegion(max_steps=100) as region:
            for _ in range(5):
                self.assertEqual(region.export(w.repeat_work(7)), 21)
                with self.assertRaises(ResourceLimitError):
                    f.endless(0)
                self.assertEqual(region.export(f.identity(42)), 42)

    def test_composed_calls_share_one_step_budget(self):
        with PrivateRegion(max_steps=100) as region:
            self.assertEqual(region.export(w.repeat_work(7)), 21)
            with self.assertRaises(ResourceLimitError):
                w.composed_work(7)
            self.assertEqual(region.export(w.repeat_work(7)), 21)

    def test_recursion_failure_does_not_affect_next_call(self):
        with PrivateRegion() as region:
            with self.assertRaises(ResourceLimitError):
                f.factorial(10000)
            self.assertEqual(region.export(f.factorial(10)), 3628800)

    def test_short_circuit_skips_failing_and_resource_exhausting_branches(self):
        bodies = [
            "return value and divide_by_zero(1)",
            "return 7 if value == 0 else endless(0)",
            "return 1 < value < divide_by_zero(1)",
            "return value == 0 or endless(0)",
        ]
        with PrivateRegion(max_steps=100) as region:
            for body, expected in zip(bodies, [0, 7, False, True]):
                function = make_function(body, extra={"divide_by_zero": f.divide_by_zero, "endless": f.endless})
                self.assertEqual(region.export(function(0)), expected)
