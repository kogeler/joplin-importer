# Release Process

The root `.version` file is the single version source. Setuptools reads it into
distribution metadata, `joplin_importer.__version__` resolves it from the
checkout or installed metadata, and `make verify-release` checks consistency.

## Steps

1. Increment `.version` with a plain `X.Y.Z` semantic version and move the
   relevant `CHANGELOG.md` entries from `Unreleased` to that version and date.
2. Open a pull request. For non-Dependabot pull requests, the `version
   increment` CI job requires the new version to be strictly greater than the
   pull-request base. The first transition from the former literal
   `pyproject.toml` version is supported explicitly.
3. Run the local release checks with Python 3.14:

   ```sh
   make check
   make package
   make verify-release
   make smoke-artifacts
   ```

   The equivalent isolated validation is `make container-release`.
4. Merge to `main`. Do not create a tag manually.
5. `.github/workflows/release.yml` reruns the full offline suite on Linux
   AMD64/ARM64 and Windows AMD64. Each runner builds and
   launches its own standalone executable with `--version`, `--help`, and
   `scan-onenote --help`. The release job builds the wheel and sdist, downloads
   only those smoke-tested executables, writes one `SHA256SUMS.txt`, verifies
   the complete inventory, creates annotated tag `vX.Y.Z`, and publishes all
   six files to a GitHub Release. Windows ARM64 is excluded while the required
   `cryptography` version does not publish a supported `win_arm64` wheel.
6. A rerun on the tagged release commit rebuilds and verifies the assets. If
   that version already points to an older commit, the workflow reports that
   there is no new release and never moves or replaces the tag. This lets
   Dependabot-only maintenance pass the release test gate without publishing a
   duplicate application version.

PyPI publication is intentionally not configured. Add it only through a
separately reviewed trusted-publishing workflow.

## Compatibility

Persisted snapshot, plan, approval, receipt, and ownership fields are public
contracts. Breaking them requires an explicit migration and an appropriate
SemVer change. The renamed importer writes the current ownership namespace but
continues to recognize managed export sets created before the rename.
