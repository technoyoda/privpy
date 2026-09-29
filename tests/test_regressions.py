"""Minimized failures remain in source control independently of Hypothesis's database."""
import struct
import unittest

from tests import fixtures as f
from tests.csv_oracle import csv_bytes as expected_csv_bytes
from tests.helpers import make_function, run


class DiscoveredRegressions(unittest.TestCase):
    def test_float_negation_preserves_signed_zero(self):
        negate = make_function("return -value")
        for value in (0.0, -0.0):
            self.assertEqual(struct.pack(">d", run(negate, value)), struct.pack(">d", -value))

    def test_smallest_subnormal_float(self):
        self.assertEqual(run(f.identity, 5e-324), 5e-324)

    def test_float_scientific_notation_boundary(self):
        value = 1.0000000000000002e16
        self.assertEqual(run(f.as_string, value), str(value))

    def test_single_empty_csv_cell_must_be_quoted(self):
        self.assertEqual(run(f.any_csv, [{"": ""}], [""]), b'""\r\n""\r\n')

    def test_csv_nul_is_preserved_in_headers_and_values(self):
        self.assertEqual(run(f.any_csv, [{"\0": "\0"}], ["\0"]), b"\0\r\n\0\r\n")
        self.assertEqual(run(f.as_csv, [{"country": "\0", "amount": 0}]),
                         b"country,amount\r\n\0,0\r\n")
        self.assertEqual(expected_csv_bytes([["\0"], ["\0"]]), b"\0\r\n\0\r\n")

    def test_csv_oracle_escape_character_cannot_change_input(self):
        cells = [["\ue000\ue001\0", 'a,"b', "line\r\nend"]]
        self.assertEqual(expected_csv_bytes(cells),
                         '\ue000\ue001\0,"a,""b","line\r\nend"\r\n'.encode("utf-8"))

    def test_comprehension_keeps_global_binding(self):
        function = make_function("out = [X for X in value]\nreturn [out, X]", extra={"X": 77})
        self.assertEqual(run(function, [1]), [[1], 77])
