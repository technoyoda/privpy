# Initial validation

Validated on macOS ARM64 with Python 3.12.14 and Apple Clang 21.0.0. All test code is Python. The package version is 0.1.0.

## Results

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

## Failures caught during development

- Valid subnormal floating-point input was rejected by the original number parser.
- Binary wire encoding incorrectly applied a plaintext limit to its expanded representation.
- Float rendering differed from Python at notation boundaries.
- CSV needed quotes around a single empty field.
- Comprehension variable scope incorrectly affected later name resolution.
- Repeated structural nesting and shared-value expansion needed explicit limits.
- A generated unknown-method test allowed Python keywords; the strategy now generates legal identifiers.

Runtime regressions are preserved as deterministic cases alongside generated tests. The release suite also rejects incomplete or incorrectly identified package bundles.

## Scope

Local execution verifies macOS ARM64 and Python 3.12. The Linux, Intel macOS, and other Python jobs are configured in GitHub Actions but have not run remotely during this initial local implementation. Publishing requires the repository changes to be pushed and the PyPI Trusted Publisher to be configured. No PyPI upload was performed.

The commit candidates were checked for local home/workspace paths, personal Git identity, email addresses, credential patterns, generated artifacts and logs. Local build outputs, virtual environments, profiling files and test databases are excluded. Repository author configuration uses a neutral identity. The explicitly chosen public GitHub repository is retained in package metadata.

These checks do not establish a production security boundary. The limits described in README.md and DESIGN.md still apply, including hostile native code, side channels and lack of guaranteed zeroization.
