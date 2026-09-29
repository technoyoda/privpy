import asyncio
import contextvars
import copy
import gc
import io
import logging
import pickle
from pathlib import Path, PurePath
import tempfile
import threading
import unittest
import weakref

from privpy import (
    PrivateRegion, PrivateRef, private_function, PrivateAccessError, CrossRegionError,
    NoActiveRegionError, RegionClosedError, UnsupportedSyntaxError, PrivateExecutionError,
    ResourceLimitError, ExportError,
)
from privpy.intrinsics import read_json
from tests import fixtures as f
from tests.helpers import make_function, run


class RegionLifecycleTests(unittest.TestCase):
    def test_requires_active_region(self):
        with self.assertRaises(NoActiveRegionError):
            f.identity(1)

    def test_enter_returns_region(self):
        region = PrivateRegion()
        with region as active:
            self.assertIs(active, region)
            self.assertEqual(region.export(f.identity(42)), 42)

    def test_closed_export(self):
        with PrivateRegion() as region:
            value = f.identity(42)
        with self.assertRaises(RegionClosedError):
            region.export(value)

    def test_closed_reference_in_new_region(self):
        with PrivateRegion():
            value = f.identity(42)
        with PrivateRegion():
            with self.assertRaises(RegionClosedError):
                f.identity(value)

    def test_exception_closes_region(self):
        with self.assertRaisesRegex(RuntimeError, "public"):
            with PrivateRegion() as region:
                value = f.identity(42)
                raise RuntimeError("public")
        with self.assertRaises(RegionClosedError):
            region.export(value)
        with self.assertRaises(NoActiveRegionError):
            f.identity(1)

    def test_nested_regions_restore_outer(self):
        with PrivateRegion() as outer:
            value = f.identity(7)
            with PrivateRegion() as inner:
                self.assertEqual(inner.export(f.identity(8)), 8)
                with self.assertRaises(CrossRegionError):
                    inner.export(value)
                with self.assertRaises(CrossRegionError):
                    f.identity(value)
                with self.assertRaises(CrossRegionError):
                    outer.export(value)
            self.assertEqual(outer.export(value), 7)

    def test_reentry_rejected(self):
        region = PrivateRegion()
        with region:
            with self.assertRaises(RegionClosedError):
                region.__enter__()
        with self.assertRaises(RegionClosedError):
            region.__enter__()

    def test_unentered_region_export(self):
        with self.assertRaises(NoActiveRegionError):
            PrivateRegion().export(None)

    def test_public_exports_survive(self):
        with PrivateRegion() as region:
            result = region.export(f.identity({"secret": [1, 2]}))
        self.assertEqual(result, {"secret": [1, 2]})

    def test_export_is_deliberate_permission(self):
        with PrivateRegion() as region:
            self.assertEqual(region.export(f.identity("a-secret")), "a-secret")

    def test_no_policy_parameter(self):
        with self.assertRaises(TypeError):
            PrivateRegion(policy="billing")

    def test_invalid_step_limits(self):
        for limit in [0, -1, 10_000_001, True, 1.2, "100", None]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                PrivateRegion(max_steps=limit)

    def test_step_limit(self):
        with PrivateRegion(max_steps=100):
            with self.assertRaises(ResourceLimitError):
                f.endless(0)

    def test_recursion_limit(self):
        with PrivateRegion():
            with self.assertRaises(ResourceLimitError):
                f.factorial(10000)

    def test_failure_does_not_poison_region(self):
        with PrivateRegion() as region:
            with self.assertRaises(PrivateExecutionError):
                f.divide_by_zero(2)
            self.assertEqual(region.export(f.add(2, 3)), 5)

    def test_thread_has_no_implicit_active_region(self):
        outcomes = []
        def worker():
            try:
                f.identity(1)
            except NoActiveRegionError:
                outcomes.append("blocked")
        with PrivateRegion():
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        self.assertEqual(outcomes, ["blocked"])

    def test_independent_thread_regions(self):
        outcomes = []
        def worker(n):
            with PrivateRegion() as region:
                outcomes.append(region.export(f.add(n, 1)))
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), list(range(1, 13)))

    def test_async_context_separation(self):
        async def worker(n):
            with PrivateRegion() as region:
                value = f.identity(n)
                await asyncio.sleep(0)
                return region.export(value)
        async def main():
            return await asyncio.gather(*(worker(n) for n in range(6)))
        self.assertEqual(asyncio.run(main()), list(range(6)))

    def test_wrong_context_exit_does_not_close(self):
        with PrivateRegion() as region:
            value = f.identity(9)
            ctx = contextvars.copy_context()
            with self.assertRaises(ValueError):
                ctx.run(region.__exit__, None, None, None)
            self.assertEqual(region.export(value), 9)

    def test_reference_does_not_keep_closed_region_alive(self):
        with PrivateRegion() as region:
            ref = f.identity(1)
        owner = weakref.ref(region)
        del region
        gc.collect()
        self.assertIsNone(owner())
        with PrivateRegion():
            with self.assertRaises(RegionClosedError):
                f.identity(ref)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.region = PrivateRegion()
        self.region.__enter__()
        self.ref = f.identity({"private_marker": [1, 2, 3]})

    def tearDown(self):
        self.region.__exit__(None, None, None)

    def test_representation_fixed(self):
        self.assertEqual(repr(self.ref), "<PrivateRef>")
        self.assertEqual(str(self.ref), "<PrivateRef>")
        self.assertEqual(f"{self.ref}", "<PrivateRef>")

    def test_log_has_only_placeholder(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("private-test")
        logger.addHandler(handler)
        try:
            logger.warning("result=%s", self.ref)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(stream.getvalue(), "result=<PrivateRef>\n")

    def test_interpretation_rejected(self):
        operations = {
            "bool": bool, "len": len, "list": list, "int": int, "float": float,
            "bytes": bytes, "iter": iter, "next": next,
            "index": lambda x: x[0], "contains": lambda x: 1 in x,
            "equal": lambda x: x == x, "not_equal": lambda x: x != x,
            "ordering": lambda x: x < x, "add": lambda x: x + 1,
            "radd": lambda x: 1 + x, "sub": lambda x: x - 1,
            "mul": lambda x: x * 2, "div": lambda x: x / 2,
            "attribute": lambda x: x.shape, "format": lambda x: format(x, ".2f"),
            "pickle": pickle.dumps,
        }
        for label, operation in operations.items():
            with self.subTest(operation=label), self.assertRaises(PrivateAccessError):
                operation(self.ref)

    def test_handle_copy_does_not_export(self):
        self.assertIs(copy.copy(self.ref), self.ref)
        self.assertIs(copy.deepcopy(self.ref), self.ref)

    def test_not_hashable(self):
        with self.assertRaises(TypeError):
            hash(self.ref)

    def test_public_constructor_rejected(self):
        with self.assertRaises(TypeError):
            PrivateRef(None, self.region, 1)

    def test_independent_export_copies(self):
        a = self.region.export(self.ref)
        a["private_marker"].append(4)
        self.assertEqual(self.region.export(self.ref), {"private_marker": [1, 2, 3]})

    def test_reference_storage_contains_no_plaintext(self):
        self.assertIsInstance(self.ref._handle, int)
        self.assertIsInstance(self.ref._owner, weakref.ReferenceType)
        self.assertEqual(PrivateRef.__slots__, ("_owner", "_handle"))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.region = PrivateRegion()
        self.region.__enter__()

    def tearDown(self):
        self.region.__exit__(None, None, None)
        self.temp.cleanup()

    def test_file_exact_bytes(self):
        data = bytes(range(256))
        path = self.directory / "output.bin"
        self.assertIsNone(self.region.export(f.identity(data), to=path))
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_empty_file(self):
        path = self.directory / "empty"
        self.region.export(f.identity(b""), to=path)
        self.assertEqual(path.read_bytes(), b"")

    def test_transform_before_file_export(self):
        path = self.directory / "anything.not-an-inferred-format"
        self.region.export(f.identity("héllo"), to=path, transform=f.as_utf8)
        self.assertEqual(path.read_bytes(), "héllo".encode())

    def test_transform_before_python_export(self):
        self.assertEqual(self.region.export(f.identity(2), transform=f.conditional), 52)

    def test_file_requires_bytes(self):
        for value in ["text", 1, [1], {"a": 1}, None]:
            with self.subTest(value=value), self.assertRaises(ExportError):
                self.region.export(f.identity(value), to=self.directory / "absent")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_no_overwrite(self):
        path = self.directory / "existing"
        path.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self.region.export(f.identity(b"replacement"), to=path)
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(sorted(x.name for x in self.directory.iterdir()), ["existing"])

    def test_destination_symlink_rejected(self):
        target = self.directory / "target"
        target.write_bytes(b"original")
        link = self.directory / "link"
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.region.export(f.identity(b"replacement"), to=link)
        self.assertEqual(target.read_bytes(), b"original")

    def test_parent_symlink_rejected(self):
        actual = self.directory / "actual"
        actual.mkdir()
        alias = self.directory / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ExportError):
            self.region.export(f.identity(b"data"), to=alias / "out")
        self.assertEqual(list(actual.iterdir()), [])

    def test_relative_and_traversal_rejected(self):
        for path in [Path("relative"), self.directory / ".." / "bad", Path("/x\0y")]:
            with self.subTest(path=str(path)), self.assertRaises(ValueError):
                self.region.export(f.identity(b"data"), to=path)

    def test_destination_types_rejected(self):
        for destination in ["/tmp/report", "monthly-report", "https://example.com", 2, io.BytesIO(), PurePath("/x")]:
            with self.subTest(destination=repr(destination)), self.assertRaises(TypeError):
                self.region.export(f.identity(b"data"), to=destination)

    def test_missing_parent_not_created(self):
        path = self.directory / "missing" / "out"
        with self.assertRaises(ExportError):
            self.region.export(f.identity(b"data"), to=path)
        self.assertFalse(path.parent.exists())

    def test_root_destination_rejected(self):
        with self.assertRaises(ExportError):
            self.region.export(f.identity(b"data"), to=Path("/"))

    def test_transform_error_creates_no_file(self):
        with self.assertRaises(PrivateExecutionError):
            self.region.export(f.identity(3), to=self.directory / "out", transform=f.divide_by_zero)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_normal_callback_transform_never_called(self):
        called = []
        def callback(value):
            called.append(value)
            return b"oops"
        with self.assertRaises(TypeError):
            self.region.export(f.identity(3), transform=callback)
        self.assertEqual(called, [])

    def test_transform_wrong_arity(self):
        with self.assertRaises(TypeError):
            self.region.export(f.identity(3), transform=f.add)

    def test_no_format_argument(self):
        with self.assertRaises(TypeError):
            self.region.export(f.identity(3), format="json")

    def test_export_requires_private_reference(self):
        for value in [1, "secret", None, [], b"data"]:
            with self.subTest(value=value), self.assertRaises(TypeError):
                self.region.export(value)


class FunctionContractTests(unittest.TestCase):
    def test_keyword_arguments_at_host_boundary(self):
        self.assertEqual(run(f.add, b=2, a=3), 5)

    def test_wrong_call_arity(self):
        with PrivateRegion():
            for args in [(), (1,), (1, 2, 3)]:
                with self.subTest(args=args), self.assertRaises(TypeError):
                    f.add(*args)
            with self.assertRaises(TypeError):
                f.add(a=1, nope=2)

    def test_nested_private_calls(self):
        self.assertEqual(run(f.nested_calls, 10), 17)

    def test_intrinsic_not_callable_from_host(self):
        with self.assertRaises(PrivateAccessError):
            read_json("/not-read")

    def test_ordinary_code_in_with_still_ordinary(self):
        with PrivateRegion():
            public = {"a": 1}
            private = f.identity(public)
            public["a"] = 2
            self.assertEqual(public, {"a": 2})
            self.assertEqual(str(private), "<PrivateRef>")

    def test_list_updates_are_value_semantics(self):
        with PrivateRegion() as region:
            original = f.identity([1, 2])
            changed = f.update_list(original, 0, 99)
            self.assertEqual(region.export(original), [1, 2])
            self.assertEqual(region.export(changed), [99, 2])

    def test_record_updates_are_value_semantics(self):
        with PrivateRegion() as region:
            original = f.identity({"a": 1})
            changed = f.update_record(original, "a", 2)
            self.assertEqual(region.export(original), {"a": 1})
            self.assertEqual(region.export(changed), {"a": 2})

    def test_unsupported_public_inputs(self):
        cyclic = []
        cyclic.append(cyclic)
        for value in [object(), {1: "bad"}, (1,), {1}, bytearray(b"x"), cyclic, 2**63, -(2**63)-1, float("nan"), float("inf")]:
            with self.subTest(type=type(value).__name__), PrivateRegion(), self.assertRaises((TypeError, ValueError)):
                f.identity(value)

    def test_async_function_rejected(self):
        async def fn(value):
            return value
        with self.assertRaises(UnsupportedSyntaxError):
            private_function(fn)

    def test_errors_do_not_include_private_data(self):
        marker = "DO_NOT_DISCLOSE_724126"
        with PrivateRegion():
            value = f.identity({marker: marker})
            with self.assertRaises(PrivateExecutionError) as caught:
                f.fail_private(value)
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(marker, repr(caught.exception))
