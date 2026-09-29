"""Study the worker bridge: PYTHONPATH=src python examples/worker_region.py.

The sample input is deliberately public. The worker reads it directly; the
parent never receives its parsed contents or private intermediates.
"""
import argparse
import os
from pathlib import Path

from privpy import private_function
from privpy.intrinsics import json_bytes, read_json
from privpy.worker import PrivateRegion


@private_function
def load_orders(path):
    return read_json(path)


@private_function
def summarize(orders):
    paid = [order for order in orders if order["status"] == "paid"]
    return {"paid_orders": len(paid), "total": sum([order["amount"] for order in paid])}


@private_function
def encode_report(report):
    return json_bytes(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).with_name("orders.json"))
    parser.add_argument("--output", type=Path, help="Optional absolute, previously nonexistent destination")
    args = parser.parse_args()
    print("1. Parent process:", os.getpid())
    with PrivateRegion() as region:
        print("2. Separate worker:", region.worker_pid)
        print("   Startup protections:", region.worker_protections)
        orders = load_orders(str(args.input.resolve()))
        print("3. Loader replied with:", orders)
        report = summarize(orders)
        print("4. Calculation replied with:", report)
        if args.output is not None:
            region.export(report, to=args.output, transform=encode_report)
            print("5. Worker wrote the explicit file export.")
        else:
            print("5. Explicit export received:", region.export(report))
    print("6. Region closed; worker exited and references are invalid.")


if __name__ == "__main__":
    main()
