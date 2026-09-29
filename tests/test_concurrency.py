"""Controlled schedules for shared contexts, cancellation and native failures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import threading
import unittest

from privpy import PrivateExecutionError, PrivateRegion, RegionClosedError
from tests import fixtures as f
from tests import workloads as w
from tests.helpers import make_function


class ThreadSchedulingTests(unittest.TestCase):
    def test_copied_contexts_branch_one_private_snapshot_concurrently(self):
        barrier = threading.Barrier(8, timeout=30)
        initial = {"a": 100, "b": 200}
        with PrivateRegion() as region:
            original = f.identity(initial)

            def worker(amount):
                barrier.wait()
                branch = w.transfer(original, "a", "b", amount)
                return region.export(branch), region.export(original)

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(contextvars.copy_context().run, worker, amount) for amount in range(8)]
                results = [future.result(timeout=30) for future in futures]
            for amount, (branch, before) in enumerate(results):
                self.assertEqual(branch, {"a": 100 - amount, "b": 200 + amount})
                self.assertEqual(before, initial)
            self.assertEqual(region.export(original), initial)

    def test_concurrent_first_compilation_and_dependency_registration(self):
        leaf = make_function("return value + 1")
        middle = make_function("return leaf(value) * 2", extra={"leaf": leaf})
        root = make_function("return middle(value) + leaf(value)", extra={"middle": middle, "leaf": leaf})
        barrier = threading.Barrier(8, timeout=30)

        def worker(value):
            with PrivateRegion() as region:
                barrier.wait()
                return region.export(root(value))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, value) for value in range(8)]
            self.assertEqual([future.result(timeout=30) for future in futures],
                             [3 * (value + 1) for value in range(8)])

    def test_native_error_codes_stay_with_the_calling_thread(self):
        barrier = threading.Barrier(6, timeout=30)

        def worker(index):
            with PrivateRegion() as region:
                secret = f.identity({"thread": index})
                for _ in range(20):
                    barrier.wait()
                    if index % 2:
                        with self.assertRaisesRegex(PrivateExecutionError, "KeyError$"):
                            f.item(secret, "missing")
                    else:
                        with self.assertRaisesRegex(PrivateExecutionError, "NumericError$"):
                            f.divide_by_zero(index)
                    self.assertEqual(region.export(secret), {"thread": index})
            return index

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(worker, index) for index in range(6)]
            self.assertEqual([future.result(timeout=30) for future in futures], list(range(6)))

    def test_copied_context_cannot_access_region_after_owner_closes(self):
        ready, proceed = threading.Event(), threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with PrivateRegion() as region:
                ref = f.identity(19)

                def worker():
                    ready.set()
                    if not proceed.wait(timeout=30):
                        raise TimeoutError("owner did not release worker")
                    with self.assertRaises(RegionClosedError):
                        region.export(ref)
                    with self.assertRaises(RegionClosedError):
                        f.identity(ref)

                future = pool.submit(contextvars.copy_context().run, worker)
                try:
                    self.assertTrue(ready.wait(timeout=30))
                except BaseException:
                    proceed.set()
                    raise
            proceed.set()
            future.result(timeout=30)


class AsyncSchedulingTests(unittest.TestCase):
    def test_cancelling_child_region_restores_inherited_parent(self):
        async def scenario():
            ready = asyncio.Event()
            child_refs = []
            with PrivateRegion() as parent:
                parent_ref = f.identity({"parent": 1})

                async def child():
                    try:
                        with PrivateRegion():
                            child_refs.append(f.identity({"child": 2}))
                            ready.set()
                            await asyncio.Event().wait()
                    finally:
                        self.assertEqual(parent.export(parent_ref), {"parent": 1})

                task = asyncio.create_task(child())
                try:
                    await asyncio.wait_for(ready.wait(), timeout=30)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual(parent.export(f.identity(parent_ref)), {"parent": 1})
                with self.assertRaises(RegionClosedError):
                    f.identity(child_refs[0])

        asyncio.run(scenario())

    def test_inherited_task_cannot_close_owner_and_observes_owner_exit(self):
        async def scenario():
            ready, proceed = asyncio.Event(), asyncio.Event()
            with PrivateRegion() as region:
                ref = f.identity(31)

                async def child():
                    try:
                        with self.assertRaises(ValueError):
                            region.__exit__(None, None, None)
                        self.assertEqual(region.export(ref), 31)
                    finally:
                        ready.set()
                    await proceed.wait()
                    with self.assertRaises(RegionClosedError):
                        region.export(ref)

                task = asyncio.create_task(child())
                await asyncio.wait_for(ready.wait(), timeout=30)
                self.assertEqual(region.export(ref), 31)
            proceed.set()
            await asyncio.wait_for(task, timeout=30)

        asyncio.run(scenario())
