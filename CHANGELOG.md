# Changelog

Versions are `R_VERSION.REVISION`: `4.6.2.3` bundles R 4.6.2, packaging revision 3. Only the revision changes for packaging-only fixes.

## Unreleased

- Add `RSession`, a persistent R worker process with `eval`, `assign` and `call`, returning plain Python data (pandas data frames when pandas is installed). No compiler or R packages needed.

## 4.6.2.3

- README: restore the logo using an absolute URL so it renders on PyPI.
- Add PyPI project links (homepage, source, issues, changelog), more classifiers and keywords.
- Restore the Wheels CI status badge.
- Add a "Why r4py?" comparison and troubleshooting section to the README.
- Export `__version__` from `r4py`.
- CI: update GitHub Actions to current majors (Node 24) and pin runners to `ubuntu-24.04`.
- CI: cache the built macOS R runtime so packaging-only releases skip the R compile.
- Add a pre-commit configuration (ruff, pyright, file hygiene).
- Add this changelog.

## 4.6.2.2

- Replace the logo with a text heading and use a public PyPI wheel badge while the repository was private.

## 4.6.2.1

- Bundle relocatable CA certificates so `install.packages()` can use HTTPS.
- Use bottled GNU Fortran 14 for macOS R builds; native macOS wheel tags.
- Filter Java bytecode before delocate; keep Windows wheels under the default PyPI size limit.

## 4.6.2.0

- Initial release bundling R 4.6.2.
