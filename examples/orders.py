"""Run with PYTHONPATH=src python examples/orders.py [--output /absolute/new.csv]."""
import argparse
from pathlib import Path

from privpy import PrivateRegion, private_function
from privpy.intrinsics import csv_bytes, read_json


@private_function
def read_orders(path):
    return read_json(path)


@private_function
def summarize(orders):
    totals = {}
    for order in orders:
        if order["status"] == "paid":
            country = order["country"]
            totals[country] = totals.get(country, 0) + order["amount"]
    rows = []
    for country, amount in totals.items():
        rows = rows + [{"country": country, "amount": amount}]
    return rows


@private_function
def total_amount(rows):
    return sum([row["amount"] for row in rows])


@private_function
def as_csv(rows):
    return csv_bytes(rows, ["country", "amount"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).with_name("orders.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with PrivateRegion() as region:
        orders = read_orders(str(args.input.resolve()))
        report = summarize(orders)
        print("Private result:", report)
        print("Explicitly exported total:", region.export(total_amount(report)))
        if args.output is not None:
            region.export(report, to=args.output, transform=as_csv)
            print("Wrote:", args.output)


if __name__ == "__main__":
    main()
