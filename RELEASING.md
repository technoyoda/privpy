# Building and publishing privpy

Repository: https://github.com/technoyoda/privpy.
Distribution and import name: `privpy`.

## PyPI setup

Configure a GitHub **Trusted Publisher** with these exact fields:

| PyPI field | Value |
| --- | --- |
| PyPI project name | `privpy` |
| Owner | `technoyoda` |
| Repository | `privpy` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

For a new project, use PyPI's [pending publisher form](https://pypi.org/manage/account/publishing/). For a project you already own, add the publisher in its Publishing settings. PyPI explains both [new-project setup](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) and [existing-project setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

Create the `pypi` environment in GitHub under repository **Settings → Environments**, and configure its release-tag restrictions as appropriate. If you add required reviewers, the publish job will wait for their approval. No PyPI API token or GitHub secret is needed: the publisher uses GitHub OIDC. [PyPI publishing documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/)

## What the workflows do

- **Tests** (`tests.yml`): Linux and macOS on Python 3.12 and 3.14, the full property-based suite with the 1,000-example CI profile, Python coverage, and a second run against UndefinedBehaviorSanitizer.
- **Build distributions** (`build.yml`): a tested source archive and 24 installed-and-tested wheels for CPython 3.9–3.14, on Linux x86_64/AArch64 and macOS Intel/Apple Silicon. Wheels are built from that source archive. The final bundle must pass metadata, version, native-library, and complete-matrix checks.
- **Publish to PyPI** (`release.yml`): on a **published GitHub release**, require the tag to match the source version, rerun Tests and Build distributions, then publish their validated artifacts through the `pypi` environment.

Pushes to `main` and pull requests run Tests and Build distributions. Both also support manual runs. Neither of those events publishes to PyPI. The publishing job has OIDC permission; build and test jobs have read-only repository permissions. Publishing runs in a separate job without checking out or executing project source.

Published GitHub prereleases also trigger publishing; use a PEP 440 prerelease version such as `0.2.0rc1` and matching tag `v0.2.0rc1`. A GitHub prerelease checkbox alone does not turn `0.2.0` into a PyPI prerelease.

Wheels target glibc 2.28+ Linux and macOS 14+. Windows, musl Linux, PyPy and free-threaded Python are not part of this matrix. Older Python wheel tests resolve a compatible Hypothesis version; the main CI suite uses `requirements-test.txt` pins.

The publisher emits PyPI attestations. [PyPI attestation documentation](https://docs.pypi.org/attestations/producing-attestations/)

## Make a release

1. Change `src/privpy/_version.py`. It is the single version source.
2. Commit and push the changes; check the Tests and Build distributions runs.
3. Create a tag exactly matching that version, for example `v0.1.0`, on the desired commit.
4. Publish a GitHub release for that tag.
5. Watch Publish to PyPI. Downloadable build artifacts are retained in its Actions run.

Only the publish step uploads to PyPI. PyPI versions cannot be overwritten. A partial upload fails visibly; investigate which files arrived before deciding how to recover. The workflow does not silently skip existing distributions.

## Local packaging check

Run inside the project with a virtual environment:

```sh
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_dist.py dist
RELEASE_TAG=v0.1.0 python scripts/check_release.py
```

`python -m build` builds the wheel from the newly produced source archive. Building from source needs a C++17 compiler with floating-point `std::to_chars` support. macOS release builds set `MACOSX_DEPLOYMENT_TARGET=14.0`. To reproduce the full matrix, use the Build distributions workflow; local cibuildwheel requires additional platform tooling. [cibuildwheel documentation](https://cibuildwheel.pypa.io/en/stable/setup/)
