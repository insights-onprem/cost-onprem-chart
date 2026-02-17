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

### 2. Bump the Version

```bash
./scripts/bump-version.sh --<type>
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

### 5. Post-Merge

After the PR is merged to main, the `release.yml` workflow automatically:
1. Detects the new version in `Chart.yaml`
2. Packages the chart and creates a GitHub Release
3. Updates the `gh-pages` branch `index.yaml`

Verify the release:

```bash
helm repo update cost-onprem
helm search repo cost-onprem
```
