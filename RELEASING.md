# Releasing (maintainer notes)

Publishing uses **PyPI Trusted Publishing** (GitHub Actions OIDC) — there are
no API tokens to store or rotate. The `Test and Publish` workflow
(`.github/workflows/publish.yml`) runs tests on every push/PR and publishes to
PyPI when a **GitHub Release is published**.

## One-time setup

1. PyPI account with 2FA.
2. PyPI → *Your account* → *Publishing* → **Add a new pending publisher**:
   - PyPI project name: `mcp-proton-email`
   - Owner: `alex-pradas`  ·  Repository: `mcp-proton-email`
   - Workflow: `publish.yml`  ·  Environment: `pypi`
3. GitHub repo → *Settings* → *Environments* → create environment `pypi`
   (optionally require a reviewer for a manual gate before each publish).

## Each release

1. Bump `version` in `pyproject.toml`.
2. Move the `Unreleased` notes into a new dated section in `CHANGELOG.md`.
3. Commit (e.g. `vX.Y.Z: <summary>`) and push to `main`; confirm CI is green.
4. Tag and create a **GitHub Release** — the Release being *published* triggers
   the publish job:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag
   ```
   (or create the Release in the GitHub UI).

Verify:

```bash
uvx mcp-proton-email@latest --help 2>&1 | head -1   # cold-start from PyPI
```

The public history was squashed from a private development branch. Keep any
local pre-squash backups local — never push them.
