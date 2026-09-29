"""Local direct/worker comparison; synthetic data, medians, no pass/fail timing gates."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

from privpy import PrivateRegion as DirectRegion, private_function
from privpy.intrinsics import read_bytes
from privpy.worker import PrivateRegion as WorkerRegion
from privpy._worker import worker_path


@private_function
def identity(value):
    return value


@private_function
def increment(value):
    return value + 1


@private_function
def increment_batch(value, count):
    for _ in range(count):
        value = increment(value)
    return value


@private_function
def grouped(rows):
    totals = {}
    for row in rows:
        group = row["group"]
        totals[group] = totals.get(group, 0) + row["amount"]
    return totals


@private_function
def load_bytes(path):
    return read_bytes(path)


def stats(samples, iterations):
    per_op = sorted(sample / iterations / 1000 for sample in samples)
    return {"median_us": round(statistics.median(per_op), 3),
            "min_us": round(min(per_op), 3), "max_us": round(max(per_op), 3),
            "samples": len(samples), "iterations_per_sample": iterations}


def make_operation(region, case, path):
    """Prepare and register before timing. Every timed result is checked afterward."""
    blob = b"x" * (64 * 1024)
    if case == "scalar_call":
        return lambda: increment(1), lambda result: region.export(result) == 2
    if case == "scalar_export":
        ref = identity(2)
        return lambda: region.export(ref), lambda result: result == 2
    if case == "public_input_64k":
        return lambda: identity(blob), lambda result: region.export(result) == blob
    if case == "export_64k":
        ref = identity(blob)
        return lambda: region.export(ref), lambda result: result == blob
    if case == "private_file_load_1m":
        return lambda: load_bytes(str(path)), lambda result: region.export(result) == b"z" * (1024 * 1024)
    if case == "group_private_1000_rows":
        rows = identity([{"group": str(i % 10), "amount": i} for i in range(1000)])
        expected = {str(group): sum(range(group, 1000, 10)) for group in range(10)}
        return lambda: grouped(rows), lambda result: region.export(result) == expected
    if case in {"python_loop_100_calls", "private_batch_100_calls"}:
        ref = identity(0)

        def python_loop():
            current = ref
            for _ in range(100):
                current = increment(current)
            return current

        operation = python_loop if case == "python_loop_100_calls" else lambda: increment_batch(ref, 100)
        return operation, lambda result: region.export(result) == 100
    raise ValueError("Unknown benchmark case")


def measure(region_type, case, iterations, samples, path):
    timings = []
    for _ in range(samples):
        if case == "region_open_close":
            start = time.perf_counter_ns()
            for _ in range(iterations):
                with region_type():
                    pass
            timings.append(time.perf_counter_ns() - start)
            continue
        # Bound native object retention with a fresh region for every sample.
        with region_type(max_steps=1_000_000) as region:
            operation, valid = make_operation(region, case, path)
            if not valid(operation()):  # Warm compiler, registration, filesystem cache, interpreter.
                raise RuntimeError("Benchmark warm-up produced an incorrect value")
            start = time.perf_counter_ns()
            for _ in range(iterations):
                result = operation()
            timings.append(time.perf_counter_ns() - start)
            if not valid(result):
                raise RuntimeError("Benchmark produced an incorrect value")
    return stats(timings, iterations)


CASES = {
    "region_open_close": 10,
    "scalar_call": 200,
    "scalar_export": 200,
    "public_input_64k": 20,
    "export_64k": 20,
    "private_file_load_1m": 5,
    "group_private_1000_rows": 10,
    "python_loop_100_calls": 10,
    "private_batch_100_calls": 10,
}


def benchmark(samples):
    if not worker_path().is_file():
        raise RuntimeError("Build the worker first: python -m privpy.build --worker")
    # Warm shared-library loading once; region lifecycle measures do not include it.
    for region_type in (DirectRegion, WorkerRegion):
        with region_type() as region:
            if region.export(identity(1)) != 1:
                raise RuntimeError("Backend smoke check failed")
    rows = []
    with tempfile.TemporaryDirectory(prefix="privpy-benchmark-") as directory:
        path = Path(directory) / "public-sample.bin"
        path.write_bytes(b"z" * (1024 * 1024))
        for index, (case, iterations) in enumerate(CASES.items()):
            row = {"case": case}
            backends = [("direct", DirectRegion), ("worker", WorkerRegion)]
            # Alternate first backend across cases to reduce consistent order bias.
            for name, region_type in backends[::1 if index % 2 == 0 else -1]:
                row[name] = measure(region_type, case, iterations, samples, path)
            direct = row["direct"]["median_us"]
            row["worker_over_direct"] = round(row["worker"]["median_us"] / direct, 2) if direct else None
            rows.append(row)
    return {"schema": 1, "platform": sys.platform, "architecture": platform.machine(),
            "python": platform.python_version(), "os_release": platform.release(),
            "units": "microseconds per operation (whole loop for the 100-call cases)",
            "notes": "warm caches; startup/registration excluded except region_open_close; synthetic public inputs",
            "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--json", type=Path, help="Optional report path; excludes local paths and identity")
    args = parser.parse_args()
    if not 1 <= args.samples <= 100:
        parser.error("--samples must be between 1 and 100")
    result = benchmark(args.samples)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Case                              Direct us    Worker us    Ratio")
    for row in result["cases"]:
        print("{:<32} {:>10.3f} {:>12.3f} {:>8.2f}x".format(
            row["case"], row["direct"]["median_us"], row["worker"]["median_us"], row["worker_over_direct"]))


if __name__ == "__main__":
    main()
