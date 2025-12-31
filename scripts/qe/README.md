# QE Scripts

Quality engineering and CI/CD automation scripts.

## Scripts

### `check-components.sh`

Check for updates to container images tagged as `latest` in `values.yaml`. Used by the GitHub workflow and can be run locally.

**Environment variables:**
- `MODE`: `check-updates` (default) or `list-versions`
- `VALUES_FILE`: Path to values.yaml (default: `cost-onprem/values.yaml`)
- `CACHE_DIR`: Digest cache directory (default: `.digest-cache`)

**Usage:**
```bash
# Check for updates
./check-components.sh

# List current versions
MODE=list-versions ./check-components.sh
```

---

### `test-gh-workflow-locally.sh`

Test GitHub Actions workflows locally using [act](https://github.com/nektos/act).

**Features:**
- Auto-installs `act` if not present (macOS/Linux)
- Loads `GITHUB_TOKEN` from environment, `~/.github_token`, or `gh` CLI
- Passes arbitrary arguments to `act` via `--`

**Usage:**
```bash
# Run default workflow (check-components.yml)
./test-gh-workflow-locally.sh

# Run with workflow input
./test-gh-workflow-locally.sh -- --input mode=list-versions

# Run a different workflow
./test-gh-workflow-locally.sh .github/workflows/lint-and-validate.yml

# Dry run
./test-gh-workflow-locally.sh -- -n
```

**Requirements:**
- Docker (for `act`)
- `GITHUB_TOKEN` for workflows that create issues/PRs

---

## Related

- `.github/workflows/check-components.yml` - GitHub Action that runs `check-components.sh` every 30 minutes
