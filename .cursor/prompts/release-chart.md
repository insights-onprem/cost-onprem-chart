# Release Cost-Onprem Chart

Cut a new release of the cost-onprem Helm chart by bumping the version and creating a release PR.

## Prerequisites

1. **On a clean branch**: `git status` shows no uncommitted changes
2. **Not on main**: Create or switch to a release branch first
3. **Script available**: `scripts/bump-version.sh` exists and is executable

## Steps

### 1. Determine Bump Type

Ask the user which version bump to apply:
- **patch** - Bug fixes, config changes, image tag updates (e.g., 0.2.9 -> 0.2.10)
- **minor** - New features, backward-compatible values.yaml changes (e.g., 0.2.9 -> 0.3.0)
- **major** - Breaking changes to values.yaml contract or upgrade path (e.g., 0.2.9 -> 1.0.0)
- **rc** - Release candidate for CI/QE validation before stable release

### 2. Bump the Version

```bash
# Stable releases
./scripts/bump-version.sh --<type>

# Release candidates
./scripts/bump-version.sh --rc              # patch-scope RC (e.g., 0.2.19 -> 0.2.20-rc1)
./scripts/bump-version.sh --rc --minor      # minor-scope RC (e.g., 0.2.19 -> 0.3.0-rc1)
./scripts/bump-version.sh --rc --major      # major-scope RC (e.g., 0.2.19 -> 1.0.0-rc1)
./scripts/bump-version.sh --rc              # iterate RC    (e.g., 0.2.20-rc1 -> 0.2.20-rc2)
```

Verify the change:

```bash
git diff cost-onprem/Chart.yaml
```

### 3. Commit the Change

```bash
git add cost-onprem/Chart.yaml
git commit -s -m "chore: release cost-onprem v<NEW_VERSION>"
```

Replace `<NEW_VERSION>` with the version printed by the bump script.

### 4. Push and Create PR

```bash
git push -u origin HEAD
gh pr create \
  --title "chore: release cost-onprem v<NEW_VERSION>" \
  --body "Bump cost-onprem chart version to v<NEW_VERSION>.

This PR triggers the chart-releaser workflow on merge to main,
which publishes the chart to the GitHub Pages Helm repository."
```

## RC Workflow

Release candidates allow CI/QE validation before promoting to a stable customer-visible release.

### Lifecycle

```
0.2.19 (stable, current)
  -> 0.2.20-rc1 (dev/QE)       ./scripts/bump-version.sh --rc
  -> 0.2.20-rc2 (dev/QE)       ./scripts/bump-version.sh --rc
  -> 0.2.20 (stable, promoted)  ./scripts/bump-version.sh --patch
```

### RC Behavior

- RC releases are marked as **pre-release** on GitHub
- They do **not** appear as the "latest" release
- Helm users must use `--devel` to see them: `helm search repo --devel cost-onprem`
- OCI registry pushes work normally — the tag includes the `-rcN` suffix

### Promoting an RC to Stable

When QE approves an RC, promote it by stripping the RC suffix:

```bash
./scripts/bump-version.sh --patch    # 0.2.20-rc3 -> 0.2.20
git add cost-onprem/Chart.yaml
git commit -s -m "chore: release cost-onprem v0.2.20"
```

You can also skip directly to a minor or major bump if needed:

```bash
./scripts/bump-version.sh --minor    # 0.2.20-rc3 -> 0.3.0
./scripts/bump-version.sh --major    # 0.2.20-rc3 -> 1.0.0
```
