# Test contract and growth plan

All test code is Python. The suite uses `unittest` and real Hypothesis property-based tests, rather than replacing properties with fixed random loops.

## Categories

| Suite | Contract |
| --- | --- |
| `test_api.py` | Lifetime, ownership, task/thread contexts, opacity, export, destination behavior |
| `test_compiler.py` | Allowed syntax, rejected escape operations, bindings, control flow, no callbacks |
| `test_properties.py` | Nested-value round trips, Python differential oracles, generated expressions, transforms, serialization and I/O |
| `test_workflows.py` | Transfer ledgers, joined sales reports, partitioned aggregation, native file ingestion, cyclic graph traversal |
| `test_isolation.py` | Nested snapshots, public aliases, failure recovery, export retries, error text, signed-zero arithmetic, shared step budgets |
| `test_concurrency.py` | Shared thread contexts, first compilation, native error ownership, owner exit, async cancellation |
| `test_stateful.py` | Region lifetime sequences and branching ledger histories with failed transfers and edited exports |
| `test_native.py` | Malformed parser/ABI inputs, Unicode, numeric boundaries, resource limits, concurrent publication |
| `test_regressions.py` | Minimized float, CSV and scoping failures retained independently of the example database |
| `test_packaging.py` | Release-tag properties, malformed/incomplete distribution rejection, metadata and native-library requirements |

## Properties

- Export of `identity(x)` preserves supported values.
- Safe-range arithmetic and comparisons agree with Python; overflow is rejected.
- Generated expression trees agree with an independent Python oracle.
- Unicode indexing, length and encoding agree with Python.
- CSV encoding agrees with `csv.writer` for documented input types.
- The CSV oracle uses an escape character absent from the generated input to avoid older CPython's incidental NUL quoting. NUL values remain covered, including deterministic regressions.
- JSON reading and encoding agree with Python for the strict supported subset.
- Inline transforms equal separate protected transformation followed by export.
- File export writes exact bytes; paths never infer formats.
- Existing files remain unchanged under repeated/concurrent exports and symlink attempts.
- Exported mutable objects are independent copies.
- Reference representations are independent of contents.
- Lifetime and ownership hold under generated operation sequences.
- Unsupported callbacks fail before executing host code on private values.
- Malformed native requests produce handled failures rather than crashes.

## Composed workloads

The workload functions in `tests/workloads.py` run through the same public API as application code. Their oracles and invariants are implemented separately in Python.

- **Transfer ledger:** sequential calls and a private batch produce identical balances; transfers conserve totals, their inverse restores the original, and every historical snapshot remains unchanged. A second state machine branches from arbitrary earlier snapshots and interleaves failed transfers, summary exports and public-copy edits.
- **Joined reports:** private order records join private customer records, filter paid orders and group totals. Whole-dataset results match partitioned computation followed by a private merge, reversed input order and native JSON-file ingestion. Changing unused customer names leaves this particular report unchanged; the library does not infer or enforce redaction.
- **Cyclic graphs:** a private breadth-first traversal matches an independent queue-based shortest-path oracle, including duplicate edges, self-loops and arbitrary Unicode node names. Reordering or duplicating edges preserves distances.
- **Failure recovery:** a failed call cannot change a previously returned value. Failed export transforms publish no file and leave the destination available for a successful retry. Step and recursion failures leave later calls usable; composed functions share one invocation's step budget.
- **Concurrency:** event/barrier-controlled schedules exercise shared references, simultaneous initial compilation, per-thread native errors and region closure. Async cancellation closes the child's region and restores its inherited parent context. Worker exceptions are propagated to the test runner.
- **Numeric representation:** finite floating-point unary operations are compared bit for bit with Python, including both signed zeros and subnormals. Numeric equality alone cannot detect a lost zero sign.

These workloads use lists and dictionaries supported by the current interpreter. They do not add a dataframe, account-management or graph API.

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

The Python coverage report enforces a 90% floor. GitHub Actions exercises macOS and Linux; `VALIDATION.md` distinguishes historical results from subsequent validation.

## Exclusions

Tests do not claim hardware isolation, resistance to hostile native memory access, constant-time execution, full zeroization, unrestricted Python/pandas compatibility, or semantic safety of deliberately exported values.
