# Changelog

## 0.1.1 — 2026-09-29

This patch release fixes floating-point negation and expands testing of composed private computations. The public API and supported Python/platform matrix are unchanged.

### Fixed

- **Preserve signed zero when negating a private float.** A protected function evaluating `-value` with `value = 0.0` previously returned `+0.0`; it now returns `-0.0`, matching Python. Floating-point operands are negated directly, while integer overflow checks remain intact. Bit-for-bit property checks and deterministic tests for both zero signs guard against regression.

### Expanded testing

Added **25 tests**, bringing the suite to **238 tests**, including **64 Hypothesis properties and two rule-based state machines**.

- **Transfer ledgers:** verify conservation of totals, reversal of transfers, agreement between sequential and batched calls, and preservation of every historical balance snapshot. A new state machine branches from earlier snapshots while interleaving transfers, failed operations, exports and edits to public copies.
- **Joined reports:** join private order and customer records, filter paid orders and group totals. Check whole-dataset results against partitioned computation followed by a private merge, reversed input order and native JSON-file ingestion. Confirm that changing unused customer names leaves this specific report unchanged.
- **Cyclic graphs:** compare private shortest-path traversal with an independent Python oracle, including self-loops, duplicate edges and Unicode node names.
- **Isolation and recovery:** exercise nested updates, shared public input objects, sanitized error text, failed export transforms and successful retries. Verify that failures preserve existing references and that step and recursion limits leave subsequent calls usable.
- **Concurrency and cancellation:** exercise copied thread contexts, concurrent first compilation, per-thread native errors, owner closure and cancellation of a child region. Check that child cleanup restores its inherited parent context.
- **Numeric representation:** compare finite floating-point unary operations with Python bit for bit, including signed zero and subnormals.

These workloads exercise existing list/dictionary operations and the current `PrivateRegion`, `private_function` and `export` API. Report redaction is a property of the chosen report function, not an automatic library policy.

### Validation and distribution

- The full 238-test suite passed locally with the CI Hypothesis profile, using up to 1,000 examples per property, and under AddressSanitizer plus UndefinedBehaviorSanitizer. AddressSanitizer leak detection was disabled.
- Python combined statement/branch coverage remains **96%**, above the enforced 90% floor.
- GitHub validation passed on Linux and macOS; each of the 24 wheel targets passed all 238 tests. The release workflow rebuilds and retests the versioned artifacts before publishing.
- Wheels cover **CPython 3.9–3.14** on **Linux x86_64/AArch64** and **macOS Intel/Apple Silicon**. A source archive is also published. Existing requirements of glibc 2.28+ or macOS 14+ still apply.

### Upgrade

```sh
python -m pip install --upgrade privpy
```

To install this exact release:

```sh
python -m pip install privpy==0.1.1
```

[Compare changes from v0.1.0](https://github.com/technoyoda/privpy/compare/v0.1.0...v0.1.1)
