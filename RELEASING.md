# Releasing (maintainer notes)

Publishing uses **PyPI Trusted Publishing** (GitHub Actions OIDC) — there are
no API tokens to store or rotate.

## One-time setup (already done once; repeat only for a new project)

1. PyPI account with 2FA.
2. PyPI → *Your account* → *Publishing* → **Add a new pending publisher**:
   - PyPI project name: `mcp-proton-email`
   - Owner: `alex-pradas`  ·  Repository: `mcp-proton-email`
   - Workflow: `publish.yml`  ·  Environment: `pypi`
3. GitHub repo → *Settings* → *Environments* → create environment `pypi`
   (optionally require reviewers for a manual gate).

## Each release

```bash
# bump version in pyproject.toml, commit, push, then:
git tag v0.1.0
git push origin v0.1.0
```

The `publish` workflow builds (`uv build`) and uploads to PyPI. Verify with:

```bash
uvx mcp-proton-email@latest --help 2>&1 | head -1   # cold-start from PyPI
```

The public history was squashed from a private development branch. Keep any
local pre-squash backups local — never push them.
