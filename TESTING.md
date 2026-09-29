# Test contract and growth plan

All test code is Python. The suite uses `unittest` and real Hypothesis property-based tests, rather than replacing properties with fixed random loops.

## Categories

| Suite | Contract |
| --- | --- |
| `test_api.py` | Lifetime, ownership, task/thread contexts, opacity, export, destination behavior |
| `test_compiler.py` | Allowed syntax, rejected escape operations, bindings, control flow, no callbacks |
| `test_properties.py` | Nested-value round trips, Python differential oracles, generated expressions, transforms, serialization and I/O |
| `test_stateful.py` | Generated create, compute, copy, export, nest, close and reopen sequences |
| `test_native.py` | Malformed parser/ABI inputs, Unicode, numeric boundaries, resource limits, concurrent publication |
| `test_regressions.py` | Minimized float, CSV and scoping failures retained independently of the example database |
| `test_packaging.py` | Release-tag properties, malformed/incomplete distribution rejection, metadata and native-library requirements |

## Properties

- Export of `identity(x)` preserves supported values.
- Safe-range arithmetic and comparisons agree with Python; overflow is rejected.
- Generated expression trees agree with an independent Python oracle.
- Unicode indexing, length and encoding agree with Python.
- CSV encoding agrees with `csv.writer` for documented input types.
- JSON reading and encoding agree with Python for the strict supported subset.
- Inline transforms equal separate protected transformation followed by export.
- File export writes exact bytes; paths never infer formats.
- Existing files remain unchanged under repeated/concurrent exports and symlink attempts.
- Exported mutable objects are independent copies.
- Reference representations are independent of contents.
- Lifetime and ownership hold under generated operation sequences.
- Unsupported callbacks fail before executing host code on private values.
- Malformed native requests produce handled failures rather than crashes.

## Profiles and reproducibility

`dev`: up to 200 examples per property, persistent shrinking database.

`ci`: up to 1,000 examples per property, deterministic generation for a fixed Hypothesis version.

`stress`: up to 10,000 examples per property, persistent shrinking database.

Pinned versions are in `requirements-test.txt`. Hypothesis reports minimized failures and reproduction blobs. Preserve important discovered cases as deterministic regressions too; the local database is not the only record of a bug.

## Adding a feature

1. Specify its semantics and unsupported cases.
2. Add a Python oracle or invariant independent of the native implementation.
3. Extend strategies with valid and invalid inputs.
4. Add state-machine transitions when lifetime or ownership changes.
5. Preserve minimized bug regressions.
6. Run normal and sanitizer suites.

Coverage helps find missing behavior; it is not proof of security. Python coverage does not measure C++. Native coverage needs separate instrumentation and reporting. Sanitizers do not prove absence of bugs or side channels.

The Python coverage report enforces a 90% floor. CI configuration exercises macOS and Linux; only the local platform results recorded in `VALIDATION.md` have actually been run during initial development.

## Exclusions

Tests do not claim hardware isolation, resistance to hostile native memory access, constant-time execution, full zeroization, unrestricted Python/pandas compatibility, or semantic safety of deliberately exported values.
