import csv
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

from hypothesis import given, strategies as st

from privpy import PrivateRegion, PrivateAccessError, PrivateExecutionError, RegionClosedError
from tests import fixtures as f
from tests.helpers import make_function, run
from tests.strategies import expressions, json_values, rows, small_int, text, values


class ValueProperties(unittest.TestCase):
    @given(values)
    def test_round_trip_supported_values(self, value):
        self.assertEqual(run(f.identity, value), value)

    @given(st.integers(-(2**63), 2**63 - 1))
    def test_full_int64_round_trip(self, value):
        result = run(f.identity, value)
        self.assertIs(type(result), int)
        self.assertEqual(result, value)

    @given(st.floats(allow_nan=False, allow_infinity=False))
    def test_finite_float_round_trip(self, value):
        result = run(f.identity, value)
        self.assertIs(type(result), float)
        self.assertEqual(result, value)
        if value == 0:
            self.assertEqual(math.copysign(1, result), math.copysign(1, value))

    @given(st.binary(max_size=4096))
    def test_arbitrary_binary_round_trip(self, value):
        self.assertEqual(run(f.identity, value), value)

    @given(values)
    def test_truth_testing_inside_matches_python(self, value):
        self.assertEqual(run(f.truth_value, value), bool(value))

    @given(values)
    def test_reference_representation_independent_of_contents(self, value):
        with PrivateRegion():
            ref = f.identity(value)
            self.assertEqual(str(ref), "<PrivateRef>")
            with self.assertRaises(PrivateAccessError):
                bool(ref)

    @given(values)
    def test_ref_is_invalid_after_close(self, value):
        with PrivateRegion() as region:
            ref = f.identity(value)
        with self.assertRaises(RegionClosedError):
            region.export(ref)

    @given(values, values)
    def test_structural_equality(self, a, b):
        self.assertEqual(run(f.equal, a, b), a == b)

    @given(values, values)
    def test_short_circuit_operands_preserved(self, a, b):
        self.assertEqual(run(f.logical, a, b), [a and b, a or b, not a])

    @given(st.lists(values, max_size=8))
    def test_export_is_independent_of_host_mutation(self, value):
        with PrivateRegion() as region:
            ref = f.identity(value)
            a = region.export(ref)
            a.append("changed outside")
            self.assertEqual(region.export(ref), value)


class NumericProperties(unittest.TestCase):
    @given(st.floats(allow_nan=False, allow_infinity=False))
    def test_float_string_conversion_matches_python(self, value):
        self.assertEqual(run(f.as_string, value), str(value))

    @given(st.integers(-(2**63), 2**63 - 1), st.integers(-(2**63), 2**63 - 1))
    def test_integer_arithmetic_checks_overflow(self, a, b):
        for operation, expected in [(f.add, a + b), (f.subtract, a - b), (f.multiply, a * b)]:
            if -(2**63) <= expected < 2**63:
                self.assertEqual(run(operation, a, b), expected)
            else:
                with self.assertRaises(PrivateExecutionError):
                    run(operation, a, b)

    @given(st.integers(-(2**63), 2**63 - 1),
           st.integers(-(2**63), 2**63 - 1).filter(lambda x: x != 0))
    def test_floor_division_and_modulo(self, a, b):
        quotient, remainder = divmod(a, b)
        if quotient < 2**63:
            self.assertEqual(run(f.floor_divide, a, b), quotient)
        else:
            with self.assertRaises(PrivateExecutionError):
                run(f.floor_divide, a, b)
        self.assertEqual(run(f.modulo, a, b), remainder)

    @given(small_int, small_int.filter(lambda x: x != 0))
    def test_true_division(self, a, b):
        self.assertEqual(run(f.divide, a, b), a / b)

    @given(st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False),
           st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False))
    def test_float_arithmetic(self, a, b):
        self.assertEqual(run(f.add, a, b), a + b)
        self.assertEqual(run(f.subtract, a, b), a - b)
        self.assertEqual(run(f.multiply, a, b), a * b)

    @given(st.integers(-(2**63), 2**63 - 1), st.integers(-(2**63), 2**63 - 1))
    def test_integer_comparisons(self, a, b):
        self.assertEqual(run(f.comparisons, a, b), [a == b, a != b, a < b, a <= b, a > b, a >= b])

    @given(small_int, small_int, small_int)
    def test_chained_comparison(self, a, b, c):
        self.assertEqual(run(f.chained, a, b, c), a < b <= c)

    @given(small_int)
    def test_branches(self, value):
        expected = value * 2 if value > 1000 else value + 50
        self.assertEqual(run(f.conditional, value), expected)
        self.assertEqual(run(f.conditional_expression, value), expected)

    @given(st.lists(small_int, max_size=100), small_int)
    def test_sum_and_explicit_loop(self, values, start):
        self.assertEqual(run(f.total, values), sum(values))
        self.assertEqual(run(f.total_with_start, values, start), sum(values, start))
        self.assertEqual(run(f.explicit_sum, values), sum(values))

    @given(st.lists(small_int, min_size=1, max_size=100))
    def test_extrema(self, values):
        self.assertEqual(run(f.extrema, values), [min(values), max(values)])

    @given(st.integers(0, 300))
    def test_while_loop(self, n):
        self.assertEqual(run(f.triangular, n), sum(range(n)))

    @given(st.integers(-100, 100), st.integers(-100, 100),
           st.integers(-20, 20).filter(lambda n: n != 0))
    def test_range(self, start, stop, step):
        self.assertEqual(run(f.make_range, start, stop, step), list(range(start, stop, step)))

    @given(st.integers(0, 20))
    def test_recursion(self, n):
        self.assertEqual(run(f.factorial, n), math.factorial(n))

    @given(expressions, st.integers(-10, 10))
    def test_generated_expression_programs(self, expression, x):
        function = make_function("return " + expression, parameters="x")
        expected = eval(expression, {"__builtins__": {}}, {"x": x})
        if -(2**63) <= expected < 2**63:
            self.assertEqual(run(function, x), expected)


class CollectionProperties(unittest.TestCase):
    @given(text)
    def test_unicode_length_and_encoding(self, value):
        self.assertEqual(run(f.length, value), len(value))
        self.assertEqual(run(f.as_utf8, value), value.encode("utf-8"))
        self.assertEqual(run(f.encode_method, value), value.encode("utf-8"))

    @given(st.lists(values, min_size=1, max_size=20), st.data())
    def test_indexing_and_negative_indexing(self, value, data):
        index = data.draw(st.integers(-len(value), len(value) - 1))
        self.assertEqual(run(f.item, value, index), value[index])

    @given(text.filter(bool), st.data())
    def test_unicode_indexing(self, value, data):
        index = data.draw(st.integers(-len(value), len(value) - 1))
        self.assertEqual(run(f.item, value, index), value[index])

    @given(st.binary(min_size=1, max_size=100), st.data())
    def test_byte_indexing(self, value, data):
        index = data.draw(st.integers(-len(value), len(value) - 1))
        self.assertEqual(run(f.item, value, index), value[index])

    @given(st.lists(values, max_size=10), values)
    def test_list_membership(self, value, needle):
        self.assertEqual(run(f.membership, value, needle), [needle in value, needle not in value])

    @given(text, text)
    def test_string_membership_and_concatenation(self, value, needle):
        self.assertEqual(run(f.membership, value, needle), [needle in value, needle not in value])
        self.assertEqual(run(f.add, value, needle), value + needle)

    @given(st.lists(small_int, max_size=100))
    def test_list_comprehension(self, values):
        self.assertEqual(run(f.squares, values), [value * value for value in values if value >= 0])

    @given(st.lists(values, min_size=1, max_size=8), values, st.data())
    def test_list_updates_do_not_mutate_other_references(self, value, replacement, data):
        index = data.draw(st.integers(-len(value), len(value) - 1))
        expected = value.copy()
        expected[index] = replacement
        with PrivateRegion() as region:
            original = f.identity(value)
            updated = f.update_list(original, index, replacement)
            self.assertEqual(region.export(updated), expected)
            self.assertEqual(region.export(original), value)

    @given(st.dictionaries(text, values, max_size=8), text, values)
    def test_record_updates_do_not_mutate_other_references(self, value, key, replacement):
        expected = dict(value)
        expected[key] = replacement
        with PrivateRegion() as region:
            original = f.identity(value)
            updated = f.update_record(original, key, replacement)
            self.assertEqual(region.export(updated), expected)
            self.assertEqual(region.export(original), value)

    @given(st.dictionaries(text, values, max_size=8), text, values)
    def test_dictionary_views_follow_documented_sorted_order(self, value, key, default):
        keys = sorted(value)
        expected = [value.get(key, default), keys, [value[k] for k in keys], [[k, value[k]] for k in keys]]
        self.assertEqual(run(f.dictionary_views, value, key, default), expected)

    @given(rows)
    def test_record_aggregation(self, rows):
        expected = {}
        for row in rows:
            if row["status"] == "paid":
                expected[row["country"]] = expected.get(row["country"], 0) + row["amount"]
        self.assertEqual(run(f.aggregate, rows), expected)


class IOProperties(unittest.TestCase):
    @given(st.lists(text, max_size=3),
           st.lists(st.lists(st.one_of(text, small_int, st.none(), st.booleans(),
                                     st.floats(allow_nan=False, allow_infinity=False)), max_size=3), max_size=10))
    def test_csv_cells_match_standard_library(self, columns, row_values):
        rows = [dict(zip(columns, items)) for items in row_values]
        expected = io.StringIO(newline="")
        writer = csv.writer(expected)
        writer.writerow(columns)
        writer.writerows([row.get(column) for column in columns] for row in rows)
        self.assertEqual(run(f.any_csv, rows, columns), expected.getvalue().encode("utf-8"))

    @given(values)
    def test_transform_matches_separate_private_call(self, value):
        with PrivateRegion() as region:
            ref = f.identity(value)
            self.assertEqual(region.export(ref, transform=f.identity),
                             region.export(f.identity(ref)))

    @given(json_values)
    def test_json_bytes_round_trip(self, value):
        self.assertEqual(json.loads(run(f.as_json, value)), value)

    @given(json_values)
    def test_native_json_reader_matches_python(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
            self.assertEqual(run(f.from_json, str(path)), value)

    @given(text)
    def test_native_text_reader(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.txt"
            path.write_bytes(value.encode("utf-8"))
            self.assertEqual(run(f.from_text, str(path)), value)

    @given(st.binary(max_size=4096))
    def test_native_byte_reader_and_exact_file_export(self, value):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            destination = root / "export"
            source.write_bytes(value)
            with PrivateRegion() as region:
                ref = f.from_bytes(str(source))
                region.export(ref, to=destination)
                self.assertEqual(region.export(ref), value)
            self.assertEqual(destination.read_bytes(), value)

    @given(st.lists(st.fixed_dictionaries({"country": text, "amount": small_int}), max_size=30))
    def test_csv_encoder_matches_standard_library(self, rows):
        expected = io.StringIO(newline="")
        writer = csv.writer(expected)
        writer.writerow(["country", "amount"])
        writer.writerows([row["country"], row["amount"]] for row in rows)
        self.assertEqual(run(f.as_csv, rows), expected.getvalue().encode("utf-8"))

    @given(st.binary(max_size=2048), st.binary(max_size=2048))
    def test_existing_files_never_overwritten(self, original, replacement):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "existing"
            path.write_bytes(original)
            with PrivateRegion() as region:
                with self.assertRaises(FileExistsError):
                    region.export(f.identity(replacement), to=path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["existing"])
