"""Guard the interpretation of OS probes and the correctness of measured workloads."""
from pathlib import Path
import tempfile
import unittest

from hypothesis import given, strategies as st

from privpy import PrivateRegion
from scripts.benchmark_worker import CASES, make_operation
from scripts.probe_worker_memory import DENIED, SUCCEEDED, classify


class InspectionEvidenceTests(unittest.TestCase):
    @given(st.sampled_from(sorted(DENIED)), st.sampled_from(sorted(DENIED)))
    def test_denied_controls_never_establish_protection(self, control, worker):
        self.assertEqual(classify({"outcome": control}, {"outcome": worker}), "inconclusive")

    @given(st.text(), st.sampled_from(sorted(SUCCEEDED | {"task_port_acquired_read_failed"})))
    def test_worker_access_is_reported_even_if_control_failed(self, control, worker):
        self.assertEqual(classify({"outcome": control}, {"outcome": worker}), "exposed")

    @given(st.sampled_from(sorted(SUCCEEDED)), st.text().filter(lambda value: value not in DENIED | SUCCEEDED))
    def test_unrecognized_failures_are_not_counted_as_denials(self, control, worker):
        self.assertNotEqual(classify({"outcome": control}, {"outcome": worker}), "denied_with_control")

    def test_working_control_and_worker_denial_establish_only_tested_route(self):
        for control in SUCCEEDED:
            for worker in DENIED:
                self.assertEqual(classify({"outcome": control}, {"outcome": worker}), "denied_with_control")

    def test_timeout_crash_and_invalid_address_are_probe_errors(self):
        for error in ("timeout", "probe_failed", "invalid_probe_output", "control_mismatch", "error"):
            with self.subTest(error=error):
                self.assertEqual(classify({"outcome": "read_succeeded"}, {"outcome": error}), "error")
                self.assertEqual(classify({"outcome": error}, {"outcome": "denied"}), "error")


class BenchmarkWorkloadTests(unittest.TestCase):
    def test_all_measured_workloads_produce_expected_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture"
            path.write_bytes(b"z" * (1024 * 1024))
            for case in CASES:
                if case == "region_open_close":
                    continue
                with self.subTest(case=case), PrivateRegion(max_steps=1_000_000) as region:
                    operation, valid = make_operation(region, case, path)
                    self.assertTrue(valid(operation()))
