"""Minimized failures remain in source control independently of Hypothesis's database."""
import unittest

from tests import fixtures as f
from tests.helpers import make_function, run


class DiscoveredRegressions(unittest.TestCase):
    def test_smallest_subnormal_float(self):
        self.assertEqual(run(f.identity, 5e-324), 5e-324)

    def test_float_scientific_notation_boundary(self):
        value = 1.0000000000000002e16
        self.assertEqual(run(f.as_string, value), str(value))

    def test_single_empty_csv_cell_must_be_quoted(self):
        self.assertEqual(run(f.any_csv, [{"": ""}], [""]), b'""\r\n""\r\n')

    def test_comprehension_keeps_global_binding(self):
        function = make_function("out = [X for X in value]\nreturn [out, X]", extra={"X": 77})
        self.assertEqual(run(function, [1]), [[1], 77])
