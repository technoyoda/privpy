"""Small composed workloads executed only by the protected interpreter."""
from privpy import private_function
from privpy.intrinsics import json_bytes, read_json


@private_function
def transfer(balances, sender, recipient, amount):
    balances[sender] = balances[sender] - amount
    balances[recipient] = balances[recipient] + amount
    return balances


@private_function
def transfer_batch(balances, transfers):
    for sender, recipient, amount in transfers:
        balances = transfer(balances, sender, recipient, amount)
    return balances


@private_function
def ledger_summary(balances):
    return {"accounts": len(balances), "total": sum(balances.values())}


@private_function
def ledger_summary_bytes(balances):
    return json_bytes(ledger_summary(balances))


@private_function
def grouped_sales(orders, customers):
    totals = {}
    for order in orders:
        customer = customers.get(order["customer"])
        if customer is not None and order["paid"]:
            group = customer["group"]
            totals[group] = totals.get(group, 0) + order["units"] * order["price"]
    return totals


@private_function
def merge_totals(left, right):
    for group, total in right.items():
        left[group] = left.get(group, 0) + total
    return left


@private_function
def report_from_file(path):
    data = read_json(path)
    return grouped_sales(data["orders"], data["customers"])


@private_function
def distances(graph, start):
    seen = {start: 0}
    frontier = [start]
    while frontier:
        following = []
        for node in frontier:
            for neighbor in graph.get(node, []):
                if neighbor not in seen:
                    seen[neighbor] = seen[node] + 1
                    following = following + [neighbor]
        frontier = following
    return seen


@private_function
def replace_nested(record, key, replacement):
    original = record
    nested = record["nested"]
    nested[key] = replacement
    record["nested"] = nested
    return [original, record]


@private_function
def mutate_then_fail(record, replacement):
    record["value"] = replacement
    return record["absent"]


@private_function
def float_unaries(value):
    return [-value, +value, abs(value)]


@private_function
def repeat_work(count):
    total = 0
    for index in range(count):
        total += index
    return total


@private_function
def composed_work(count):
    return repeat_work(count) + repeat_work(count)
