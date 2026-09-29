"""Differential and metamorphic tests for composed private applications."""
from collections import Counter, deque
import json
from pathlib import Path
import tempfile
import unittest

from hypothesis import given, strategies as st

from privpy import PrivateAccessError, PrivateRegion
from tests import fixtures as f
from tests import workloads as w
from tests.strategies import text


account_ids = st.sampled_from(["a", "b", "c", "d"])
transfers = st.lists(st.tuples(account_ids, account_ids, st.integers(-1000, 1000)), max_size=25)
balances = st.fixed_dictionaries({key: st.integers(-10000, 10000) for key in "abcd"})


@st.composite
def sales(draw):
    customers = draw(st.dictionaries(account_ids, st.fixed_dictionaries({
        "group": st.sampled_from(["north", "south", ""]), "name": text,
    }), max_size=4))
    orders = draw(st.lists(st.fixed_dictionaries({
        "customer": st.sampled_from(["a", "b", "c", "d", "missing"]),
        "paid": st.booleans(), "units": st.integers(0, 30), "price": st.integers(-1000, 1000),
    }), max_size=25))
    return orders, customers


@st.composite
def graphs(draw):
    nodes = draw(st.lists(text, min_size=1, max_size=7, unique=True))
    edges = draw(st.lists(st.tuples(st.sampled_from(nodes), st.sampled_from(nodes)), max_size=25))
    graph = {node: [] for node in nodes}
    for source, destination in edges:
        graph[source].append(destination)
    return graph, draw(st.sampled_from(nodes))


def sales_oracle(orders, customers):
    totals = Counter()
    for order in orders:
        if order["paid"] and order["customer"] in customers:
            totals[customers[order["customer"]]["group"]] += order["units"] * order["price"]
    return dict(totals)


def graph_oracle(graph, start):
    queue = deque([(start, 0)])
    seen = {}
    while queue:
        node, distance = queue.popleft()
        if node in seen:
            continue
        seen[node] = distance
        queue.extend((neighbor, distance + 1) for neighbor in graph.get(node, []))
    return seen


class WorkflowProperties(unittest.TestCase):
    @given(balances, transfers)
    def test_ledger_composition_conservation_and_snapshots(self, initial, operations):
        expected = dict(initial)
        with PrivateRegion() as region:
            original = f.identity(initial)
            current = original
            for sender, recipient, amount in operations:
                previous = current
                before = dict(expected)
                current = w.transfer(current, sender, recipient, amount)
                expected[sender] -= amount
                expected[recipient] += amount
                self.assertEqual(region.export(previous), before)
            batched = w.transfer_batch(original, [list(item) for item in operations])
            self.assertEqual(region.export(current), expected)
            self.assertEqual(region.export(batched), expected)
            self.assertEqual(region.export(original), initial)
            self.assertEqual(region.export(w.ledger_summary(current)),
                             {"accounts": 4, "total": sum(initial.values())})
            with self.assertRaises(PrivateAccessError):
                bool(w.ledger_summary(current))

    @given(balances, transfers)
    def test_reversing_ledger_operations_restores_original(self, initial, operations):
        inverse = [[recipient, sender, amount] for sender, recipient, amount in reversed(operations)]
        with PrivateRegion() as region:
            result = w.transfer_batch(initial, [list(item) for item in operations])
            restored = w.transfer_batch(result, inverse)
            self.assertEqual(region.export(restored), initial)

    @given(balances)
    def test_private_summary_transform_to_exact_destination(self, initial):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory).resolve() / "summary"
            with PrivateRegion() as region:
                value = f.identity(initial)
                region.export(value, to=destination, transform=w.ledger_summary_bytes)
                self.assertEqual(region.export(value), initial)
            self.assertEqual(json.loads(destination.read_bytes()),
                             {"accounts": 4, "total": sum(initial.values())})

    @given(sales(), st.integers(0, 25))
    def test_join_group_and_partitioned_merge_agree(self, dataset, split):
        orders, customers = dataset
        expected = sales_oracle(orders, customers)
        with PrivateRegion() as region:
            people = f.identity(customers)
            full = w.grouped_sales(f.identity(orders), people)
            left = w.grouped_sales(orders[:split], people)
            right = w.grouped_sales(orders[split:], people)
            merged = w.merge_totals(left, right)
            self.assertEqual(region.export(full), expected)
            self.assertEqual(region.export(merged), expected)
            self.assertEqual(region.export(left), sales_oracle(orders[:split], customers))
            self.assertEqual(region.export(right), sales_oracle(orders[split:], customers))
            self.assertEqual(region.export(w.grouped_sales(list(reversed(orders)), people)), expected)

    @given(sales(), text)
    def test_chosen_report_is_independent_of_unused_customer_names(self, dataset, replacement):
        # This is a property of this report, not automatic redaction by privpy.
        orders, customers = dataset
        changed = {key: dict(value, name=replacement) for key, value in customers.items()}
        with PrivateRegion() as region:
            before = w.grouped_sales(orders, customers)
            after = w.grouped_sales(orders, changed)
            self.assertEqual(region.export(before, transform=f.as_json),
                             region.export(after, transform=f.as_json))

    @given(sales())
    def test_native_file_source_feeds_same_composed_report(self, dataset):
        orders, customers = dataset
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve() / "source.json"
            source.write_text(json.dumps({"orders": orders, "customers": customers}), encoding="utf-8")
            with PrivateRegion() as region:
                result = w.report_from_file(str(source))
                self.assertEqual(region.export(result), sales_oracle(orders, customers))

    @given(graphs())
    def test_cyclic_graph_shortest_paths_and_duplicate_edge_invariance(self, dataset):
        graph, start = dataset
        expected = graph_oracle(graph, start)
        changed = {node: list(reversed(edges)) + edges + [node] for node, edges in graph.items()}
        with PrivateRegion() as region:
            graph_ref = f.identity(graph)
            result = w.distances(graph_ref, f.identity(start))
            self.assertEqual(region.export(result), expected)
            self.assertEqual(region.export(w.distances(changed, start)), expected)
            self.assertEqual(region.export(graph_ref), graph)
