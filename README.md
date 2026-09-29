# privpy

**Private computations, native-owned values, explicit export.**

Experimental same-process Python module. The runtime is C++17; the entire test suite is Python, using `unittest` and Hypothesis.

```python
from pathlib import Path
from privpy import PrivateRegion, private_function
from privpy.intrinsics import read_json, json_bytes

@private_function
def read_records(path):
    return read_json(path)

@private_function
def total_paid(records):
    total = 0
    for row in records:
        if row["status"] == "paid":
            total += row["amount"]
    return total

@private_function
def encode_total(total):
    return json_bytes({"total": total})

with PrivateRegion() as region:
    records = read_records("/absolute/orders.json")
    total = total_paid(records)
    print(total)  # <PrivateRef>

    # Deliberate disclosure: the caller chooses what gets exported.
    public_total = region.export(total)
    region.export(total, to=Path("/absolute/new-report.json"), transform=encode_total)
```

There is no `policy`, `region.run`, universal `load`, `format`, or destination alias. The context manager establishes the active region. Decorated functions execute in a native interpreter; their original Python bodies are never invoked with private inputs.

## Build and run

Requires Python 3.9+ and macOS or Linux. Building from source needs a C++17 compiler with floating-point `std::to_chars` support. Release wheels target macOS 14+ and glibc 2.28+ Linux. The pinned development environment uses Python 3.12.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt
PYTHONPATH=src python -m privpy.build
PYTHONPATH=src python examples/orders.py
PYTHONPATH=src python examples/orders.py --output /absolute/new-report.csv
```

If a virtual environment already exists, activate and reuse it.

`python -m pip install .` builds the native library during installation. There are no Python runtime dependencies. Source-checkout use only needs `PYTHONPATH=src` and the explicit build command.

## Implemented contract

- A region owns native values. Outside Python receives a `PrivateRef` containing an owner reference and integer handle.
- Public arguments are copied in; their original Python copies remain public.
- Native source readers retrieve regular files without routing plaintext through Python. They do not make a public source file secret.
- Private calls remain native. Outside callbacks, dynamic imports and arbitrary Python libraries are rejected.
- Every result stays private, including booleans, lengths and aggregates.
- Export is deliberate disclosure. It checks mechanics, not whether the contents are semantically safe to reveal.
- `export(value)` returns an independent supported Python value.
- `export(value, to=Path(...))` requires bytes and writes them directly from native storage.
- An optional decorated transform runs before export. There is no implicit formatting, encoding or encryption.
- Destinations must be absolute. Existing files and symlinks are never replaced. Symlink components are rejected; use an already-canonical directory path. Complete output is published atomically from a mode-0600 temporary file in the same directory.
- Closing a region invalidates all its references. Exported values and files survive.

## Supported subset

Data: null, booleans, signed 64-bit integers, finite binary64 floats, Unicode strings, bytes, lists and string-keyed dictionaries.

Syntax: local assignment, single-level subscript updates, unpacking, arithmetic, comparisons, boolean expressions, indexing, conditional expressions, `if`, `for`, `while`, loop `else`, `break`, `continue`, return, one-generator list comprehensions and calls between private functions.

Builtins: `len`, `bool`, scalar `str`, `abs`, `sum`, `min`, `max`, `range`.

Methods: dictionary `get`, `keys`, `values`, `items`; string UTF-8 `encode`.

Source and encoding operations:

```python
from privpy.intrinsics import (
    read_json, read_text, read_bytes, json_bytes, csv_bytes, utf8_bytes,
)
```

Prototype semantics:

- CSV uses minimal quoting with comma separators and CRLF record endings. NUL characters are preserved and do not by themselves force quoting.
- Collection updates use value semantics; updating one binding does not mutate another reference.
- Dictionaries iterate in sorted key order, and their view methods return lists.
- Integers are bounded. Overflow raises a sanitized execution error.
- Floor division and modulo currently accept integers only.
- Mixed integer/float comparisons reject integers outside binary64's exact integer range.
- Functions require inspectable source. Closures, defaults, variadic/keyword-only parameters and keyword arguments inside protected calls are unsupported. Named arguments at the public call boundary work.
- Limits bound input/value sizes, collections, native nesting, recursion and interpreter steps. `PrivateRegion(max_steps=...)` adjusts a per-call resource limit.
- Unsupported syntax fails explicitly, even in unreachable branches.

Full dataframe engines, secrets-manager SDKs, network clients, encryption, arbitrary third-party libraries, custom classes and asynchronous private functions are not implemented. The example demonstrates record processing without claiming pandas compatibility. [DESIGN.md](DESIGN.md) contains the target API and architecture sketch.

## Security boundary

This prototype prevents accidental disclosure through supported operations. Native values are not a hidden Python object graph. Source parsing and file export do not route contents through Python callbacks.

It is **not a hostile-code sandbox or a production-grade secrets facility**. Native-memory access, debuggers, the operating system, compromised native code and a developer deliberately exporting secrets remain outside the guarantee. Timing, exception occurrence, termination, file sizes and I/O side effects can reveal information. Native release does not guarantee full zeroization.

Source/file operations run with the process's filesystem rights. The application developer is trusted. There is intentionally no independent policy engine.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -t . -v
PYTHONPATH=src python -m coverage run -m unittest discover -s tests -t . -q
python -m coverage report
PRIVPY_HYPOTHESIS_PROFILE=ci PYTHONPATH=src python -m unittest discover -s tests -t . -q
```

Hypothesis provides generated data, shrinking, a persistent regression database, generated programs and a state machine for lifetime/ownership sequences.

Profiles: `dev` uses up to 200 examples per property, `ci` up to 1,000 with deterministic generation, and `stress` up to 10,000. Finite input spaces may be exhausted earlier. Failures include minimized examples and reproduction instructions. `PRIVPY_TEST_STATE` selects the local database directory.

Run the same **Python** suite against an UndefinedBehaviorSanitizer build:

```sh
PYTHONPATH=src python -m privpy.build --sanitize --output /absolute/work/runtime-sanitized.dylib
PRIVPY_NATIVE=/absolute/work/runtime-sanitized.dylib PYTHONPATH=src python -m unittest discover -s tests -t . -q
```

On Linux use a `.so` output path. Test cases remain Python; the native library is rebuilt with instrumentation.

See [TESTING.md](TESTING.md) for extending the suite.

## Builds and PyPI

GitHub Actions runs tests, native sanitizer checks, source builds, and wheels for CPython 3.9–3.14 on Linux and macOS, each on Intel and ARM. Published GitHub releases trigger verified PyPI uploads through Trusted Publishing.

See [RELEASING.md](RELEASING.md) for the exact PyPI publisher fields and release steps, and [VALIDATION.md](VALIDATION.md) for local verification results.
