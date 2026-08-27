# Publish a release to PyPI

Releases are automated by `.github/workflows/release.yml`, which publishes via
**Trusted Publishing (OIDC)** — no API token is stored in the repository. It
builds once, with [build provenance attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations),
and publishes that same artefact to one of two indexes:

| Trigger | Goes to | Environment |
|---|---|---|
| every push to `main` | [test.pypi.org](https://test.pypi.org) | `release-test-pypi` |
| publishing a GitHub Release | [pypi.org](https://pypi.org) | `pypi` |

Test PyPI on every commit is deliberate: it exercises the publishing path
continuously, so release day is not the first time it runs. Those uploads use
`skip-existing`, because the version only changes at a release: between two
releases every push offers a file the index already has, and it would
otherwise refuse each one with `400 File already exists`.

The workflow calls the repository's CI rather than restating its checks — a
release gate that duplicates them drifts from them, and it drifts silently. A
tag is additionally checked against the version in `pyproject.toml`, so a
mistyped tag or a forgotten bump fails before upload rather than on PyPI,
where the wrong number cannot be taken back.

Release steps:

```bash
# 1. bump the version in pyproject.toml (must match the tag)
# 2. commit, tag and push
git commit -am "release: v0.6.0"
git tag v0.6.0
git push && git push --tags
# 3. publish a GitHub Release for that tag -- this is what uploads to PyPI
```

One-time setup, per index:

1. On the index (<https://pypi.org> or <https://test.pypi.org>) → *Your
   projects* → *Publishing*, add a **pending trusted publisher**:
   - **Project name:** `terminal-status-panel`
   - **Owner:** `edutap-collective`
   - **Repository name:** `terminal_status_panel`
   - **Workflow name:** `release.yml` — exactly as the file is named. A
     publisher whose workflow name does not match matches nothing at all, and
     the upload is rejected as `invalid-publisher`. Both indexes must name the
     same file, since there is only one: pypi.org has named `release.yml`
     since v0.4.0 and is the one that must not be disturbed, so Test PyPI
     follows it. This is a deliberate departure from the sibling packages,
     which call the file `release.yaml`.
   - **Environment name:** `pypi` for PyPI, `release-test-pypi` for Test PyPI
2. In the GitHub repository → *Settings → Environments*, create the matching
   environment. Required reviewers on `pypi` gate every upload behind an
   approval; `release-test-pypi` is better left ungated, since it fires on
   every push to `main`.

   `pypi` already exists and already carries a working publisher — it is what
   released v0.5.0. The environment names here follow that rather than the
   other way round: renaming it would mean deleting and re-registering the
   publisher on pypi.org, which is real risk on a live index in exchange for
   nothing but a tidier name.
