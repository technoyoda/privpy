import builtins
import keyword
import unittest

from hypothesis import given, strategies as st

from privpy import PrivateRegion, PrivateExecutionError, UnsupportedSyntaxError
from tests.helpers import make_function, run
from tests import fixtures as f


# Each rejected syntax form has an individually reported regression test.
UNSUPPORTED = {
    "import": "import os\nreturn value",
    "import_from": "from os import system\nreturn value",
    "print": "print(value)\nreturn value",
    "eval": "return eval(value)",
    "exec": "exec(value)\nreturn value",
    "open": "return open(value)",
    "getattr": "return getattr(value, 'secret')",
    "vars": "return vars(value)",
    "globals": "return globals()",
    "locals": "return locals()",
    "type": "return type(value)",
    "attribute": "return value.__class__",
    "method_dunder": "return value.__repr__()",
    "host_method": "return value.to_pandas()",
    "lambda": "return (lambda x: x)(value)",
    "lambda_capture": "callback = lambda: value\nreturn callback()",
    "nested_function": "def inner():\n    return value\nreturn inner()",
    "class": "class Thing:\n    pass\nreturn value",
    "global": "global leaked\nleaked = value\nreturn value",
    "nonlocal": "nonlocal leaked\nreturn value",
    "with": "with value:\n    pass\nreturn value",
    "try": "try:\n    return value\nexcept Exception:\n    return None",
    "raise": "raise RuntimeError(value)",
    "yield": "yield value",
    "yield_from": "yield from value",
    "set": "return {value}",
    "set_comprehension": "return {x for x in value}",
    "dict_comprehension": "return {x: x for x in value}",
    "generator": "return sum(x for x in value)",
    "tuple_result": "return (value, value)",
    "slice": "return value[:2]",
    "ellipsis": "return ...",
    "complex": "return 1j",
    "fstring": "return f'{value}'",
    "walrus": "return (x := value)",
    "star_args": "return sum(*value)",
    "kwargs": "return sum(value, start=0)",
    "dict_unpack": "return {**value}",
    "list_unpack": "return [*value]",
    "chained_assignment": "a = b = value\nreturn a",
    "attribute_assignment": "value.secret = 1\nreturn value",
    "nested_subscript_assignment": "value[0][0] = 1\nreturn value",
    "augmented_subscript": "value[0] += 1\nreturn value",
    "delete": "del value\nreturn None",
    "assert": "assert value\nreturn value",
    "power": "return value ** 2",
    "bitwise": "return value | 1",
    "invert": "return ~value",
    "matmul": "return value @ value",
    "local_callback": "fn = value\nreturn fn()",
    "multiple_comprehension_generators": "return [x for x in value for y in value]",
    "unreachable_escape": "if False:\n    print(value)\nreturn value",
    "append_method": "value.append(1)\nreturn value",
}


class UnsupportedSyntaxTests(unittest.TestCase):
    pass


def rejected_test(body):
    def test(self):
        try:
            function = make_function(body)
        except SyntaxError:
            # Python itself rejects nonlocal without a binding before our compiler runs.
            return
        with PrivateRegion(), self.assertRaises(UnsupportedSyntaxError):
            function(None)
    return test


for label, body in UNSUPPORTED.items():
    setattr(UnsupportedSyntaxTests, "test_reject_" + label, rejected_test(body))


class CompilerBehaviorTests(unittest.TestCase):
    def test_mutable_global_not_captured(self):
        function = make_function("return PUBLIC", extra={"PUBLIC": [1, 2]})
        with PrivateRegion(), self.assertRaises(UnsupportedSyntaxError):
            function(None)

    def test_immutable_global_snapshot(self):
        function = make_function("return value + OFFSET", extra={"OFFSET": 2})
        self.assertEqual(run(function, 3), 5)
        function._function.__globals__["OFFSET"] = 99
        self.assertEqual(run(function, 3), 5)

    def test_host_callback_is_never_invoked(self):
        called = []
        def callback(value):
            called.append(value)
            return value
        function = make_function("return callback(value)", extra={"callback": callback})
        with PrivateRegion(), self.assertRaises(UnsupportedSyntaxError):
            function(42)
        self.assertEqual(called, [])

    def test_shadowed_builtin_not_silently_used(self):
        function = make_function("return len(value)", extra={"len": lambda x: 7})
        with PrivateRegion(), self.assertRaises(UnsupportedSyntaxError):
            function([1])

    def test_default_parameters_rejected(self):
        function = make_function("return value", parameters="value=1")
        with PrivateRegion(), self.assertRaises(UnsupportedSyntaxError):
            function(1)

    def test_variadic_parameters_rejected(self):
        for parameters in ["*value", "**value", "*, value"]:
            function = make_function("return value", parameters=parameters)
            with PrivateRegion(), self.assertRaises((UnsupportedSyntaxError, TypeError)):
                function(1)

    def test_docstring_and_pass(self):
        function = make_function('"""public documentation"""\npass\nreturn value')
        self.assertEqual(run(function, 42), 42)

    def test_implicit_and_explicit_none(self):
        for body in ["pass", "return"]:
            self.assertIsNone(run(make_function(body), 42))

    def test_break_continue_for_else(self):
        function = make_function(
            "result = []\nfor x in value:\n    if x < 0:\n        continue\n"
            "    if x > 10:\n        break\n    result = result + [x]\n"
            "else:\n    result = result + [99]\nreturn result"
        )
        self.assertEqual(run(function, [-1, 2, 12, 4]), [2])
        self.assertEqual(run(function, [-1, 2, 4]), [2, 4, 99])
        self.assertEqual(run(function, []), [99])

    def test_while_else_and_continue(self):
        function = make_function(
            "i = 0\nresult = []\nwhile i < value:\n    i += 1\n"
            "    if i == 2:\n        continue\n    if i == 4:\n        break\n"
            "    result = result + [i]\nelse:\n    result = result + [99]\nreturn result"
        )
        self.assertEqual(run(function, 3), [1, 3, 99])
        self.assertEqual(run(function, 9), [1, 3])

    def test_boolean_short_circuit_avoids_error(self):
        for expression, expected in [("False and (1 / 0)", False), ("True or (1 / 0)", True),
                                     ("0 < -1 < (1 / 0)", False),
                                     ("1 if True else (1 / 0)", 1)]:
            self.assertEqual(run(make_function("return " + expression), None), expected)

    def test_local_before_assignment_fails(self):
        with self.assertRaises(PrivateExecutionError):
            run(make_function("if value:\n    x = 1\nreturn x"), False)

    def test_comprehension_variable_does_not_replace_parameter(self):
        function = make_function("result = [value for value in [1, 2]]\nreturn [result, value]")
        self.assertEqual(run(function, 99), [[1, 2], 99])

    def test_comprehension_variable_does_not_shadow_global_afterward(self):
        function = make_function("result = [X for X in value]\nreturn [result, X]", extra={"X": 77})
        self.assertEqual(run(function, [1, 2]), [[1, 2], 77])

    def test_comprehension_iterator_uses_enclosing_scope(self):
        function = make_function("return [X for X in X]", extra={"X": "abc"})
        self.assertEqual(run(function, None), ["a", "b", "c"])

    def test_nested_comprehension_scopes(self):
        function = make_function("return [[y + x for y in [1, 2]] for x in value]")
        self.assertEqual(run(function, [10, 20]), [[11, 12], [21, 22]])

    def test_multiple_assignment_unpacking(self):
        self.assertEqual(run(make_function("a, b = value\nreturn a + b"), [2, 3]), 5)
        with self.assertRaises(PrivateExecutionError):
            run(make_function("a, b = value\nreturn a + b"), [1])

    def test_positional_only_arguments(self):
        function = make_function("return value", parameters="value, /")
        self.assertEqual(run(function, 7), 7)
        with PrivateRegion(), self.assertRaises(TypeError):
            function(value=7)

    def test_mutual_recursion(self):
        first = make_function("if value == 0:\n    return True\nreturn second(value - 1)")
        second = make_function("if value == 0:\n    return False\nreturn first(value - 1)")
        first._function.__globals__["second"] = second
        second._function.__globals__["first"] = first
        self.assertEqual(run(first, 10), True)
        self.assertEqual(run(first, 11), False)

    @given(st.from_regex(r"[a-z]{1,18}", fullmatch=True).filter(
        lambda name: not keyword.iskeyword(name) and name not in {"get", "keys", "values", "items", "encode"}))
    def test_generated_unknown_methods_rejected(self, name):
        function = make_function("return value." + name + "()")
        with PrivateRegion(), self.assertRaises(UnsupportedSyntaxError):
            function(None)

    @given(st.integers(-1000, 1000), st.integers(-1000, 1000))
    def test_public_constant_binding(self, offset, value):
        function = make_function("return value + OFFSET", extra={"OFFSET": offset})
        self.assertEqual(run(function, value), value + offset)
