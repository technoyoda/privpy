# Validation

## Expanded workloads after v0.1.0

Validated on macOS ARM64 with Python 3.12.14 and Apple Clang 21. The suite now contains 238 tests, including 64 Hypothesis properties and two rule-based state machines. The CI profile uses up to 1,000 generated examples per property.

| Check | Result |
| --- | --- |
| Full suite, CI profile, Python coverage enabled | 238 tests passed |
| Python combined statement/branch coverage | 96%; above the 90% floor |
| Full suite, CI profile, AddressSanitizer + UndefinedBehaviorSanitizer | 238 tests passed; no sanitizer diagnostics |
| Workflow, isolation, concurrency, state-machine and regression suites on Python 3.9 | 32 tests passed |

New workloads cover transfer conservation and reversal, historical ledger branches, joined and partitioned reports, native JSON ingestion, cyclic-graph shortest paths, shared contexts, task cancellation and recovery after failed computation or export. See [TESTING.md](TESTING.md) for the invariants and oracle design.

Bit-level floating-point checks exposed a runtime bug: negating positive zero returned positive zero instead of negative zero. The runtime now negates floating-point operands directly; integer overflow checks remain intact. Both zero signs have a deterministic regression test in addition to the generated finite-float property.

AddressSanitizer leak detection was disabled. Native coverage was not remeasured for this expansion; the figures below belong to the initial implementation.

## Initial validation (historical)

Validated on macOS ARM64 with Python 3.12.14 and Apple Clang 21.0.0. All test code is Python. The package version is 0.1.0.

### Results

| Check | Result |
| --- | --- |
| Full Python suite | 211 tests passed |
| Property coverage | 51 Hypothesis properties plus a rule-based state machine |
| Python runtime coverage | 96% combined statement/branch coverage; 90% enforced floor |
| CI profile + AddressSanitizer + UndefinedBehaviorSanitizer | 211 tests passed; no sanitizer diagnostics |
| Native line coverage | 93.80% |
| Native branch coverage | 79.33% |
| Native function coverage | 100% of 85 functions |
| GitHub Actions static validation | actionlint 1.7.12 passed |
| cibuildwheel configuration | 24 intended Python/platform build identifiers verified |
| Local wheel and source archive | Built successfully; strict Twine metadata checks passed |
| Release guards | Tag matching, missing native runtime, wrong versions and incomplete bundles tested |

The native coverage values come from the final CI-profile sanitizer run. AddressSanitizer leak detection was disabled; these results do not establish leak freedom. Python coverage excludes the compiler-invocation build helper and does not measure C++.

The default Hypothesis profile exercises up to 200 examples per property; the CI run uses up to 1,000. Finite strategy domains can be exhausted earlier. The state machine generates region creation, computation, export, nesting, closure and invalid-reference sequences.

### Failures caught during development

- Valid subnormal floating-point input was rejected by the original number parser.
- Binary wire encoding incorrectly applied a plaintext limit to its expanded representation.
- Float rendering differed from Python at notation boundaries.
- CSV needed quotes around a single empty field.
- Comprehension variable scope incorrectly affected later name resolution.
- Repeated structural nesting and shared-value expansion needed explicit limits.
- A generated unknown-method test allowed Python keywords; the strategy now generates legal identifiers.

Runtime regressions are preserved as deterministic cases alongside generated tests. The release suite also rejects incomplete or incorrectly identified package bundles.

### Scope at the initial checkpoint

The initial local implementation was validated on macOS ARM64 and Python 3.12, before remote CI or publishing. Subsequently, [the successful v0.1.0 release workflow](https://github.com/technoyoda/privpy/actions/runs/36525568038) tested 213 cases per wheel across CPython 3.9–3.14 on Linux and macOS, on Intel and ARM64, and published 24 wheels plus the source archive to PyPI. Those release results precede the expanded tests above.

The commit candidates were checked for local home/workspace paths, personal Git identity, email addresses, credential patterns, generated artifacts and logs. Local build outputs, virtual environments, profiling files and test databases are excluded. Repository author configuration uses a neutral identity. The explicitly chosen public GitHub repository is retained in package metadata.

These checks do not establish a production security boundary. The limits described in README.md and DESIGN.md still apply, including hostile native code, side channels and lack of guaranteed zeroization.
