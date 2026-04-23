# Test Impact Map

> **Location**: `scripts/qe/test-impact-map.yaml`
>
> **Last Updated**: 2026-04-06

## Purpose

The test impact map is a YAML configuration file that maps container image
components and file path patterns to IQE test profiles. It drives two automated
workflows:

- **`check-components.yml`** — when an auto-update PR is created for image
  changes, the workflow posts a `[TEST-ADVISOR]` comment recommending which
  IQE profile to run.
- **`recommend-tests.yml`** — when a human-authored PR touches chart files,
  scripts, or tests, the workflow posts the same recommendation.

Both workflows call `scripts/qe/recommend-tests.sh`, which reads the map at
runtime. Updating the map requires no script changes.

## File Structure

The map has two top-level sections:

### `components` — Container Image Mappings

Keyed by the **image basename** (the last path segment of the repository URL
in `values.yaml`).

```yaml
components:
  koku:
    profile: stable       # minimum IQE profile needed to test this component
    impact: high          # blast radius: low | medium | high
    description: "Core backend — reports, sources, cost models, ingestion"
```

When a PR changes an image tag in `values.yaml`, the script extracts the
repository URL, takes its basename, and looks it up in this section.

| Field | Required | Values | Purpose |
|-------|----------|--------|---------|
| `profile` | yes | `smoke`, `extended`, `stable`, `full` | Minimum profile that exercises this component |
| `impact` | yes | `low`, `medium`, `high` | Shown in PR comment to help reviewers prioritize |
| `description` | yes | free text | Shown in PR comment table |

### `paths` — File Path Pattern Mappings

Keyed by a descriptive rule name. Evaluated against the output of
`git diff --name-only`.

```yaml
paths:
  helm-templates:
    pattern: "^cost-onprem/templates/"
    profile: stable
    impact: high
    description: "Helm templates — affects all deployed resources"

  values-structural:
    pattern: "^cost-onprem/values\\.yaml$"
    diff_pattern: "^\\+.*(resources:|limits:|requests:|replicas:|enabled:)"
    profile: extended
    impact: medium
    description: "Resource limits, replicas, or feature toggles"
```

| Field | Required | Purpose |
|-------|----------|---------|
| `pattern` | yes | Regex matched against changed file paths |
| `diff_pattern` | no | If set, also requires a matching line in the `git diff` content (for content-level rules like structural changes vs. tag-only changes) |
| `profile` | yes | Minimum profile recommended when this rule fires |
| `impact` | yes | Blast radius shown in PR comment |
| `description` | yes | Shown in PR comment table |

## How Profiles Are Selected

The script evaluates all matching components and path rules, then recommends
the **highest-ranked** profile among them:

```
smoke (0) < extended (1) < stable (2) < full (3)
```

If the highest match is `smoke`, no PR comment is posted (the default `e2e`
job already runs smoke). Comments only appear when deeper testing is warranted.

## Common Tasks

### Adding a new container image

When a new image is added to `values.yaml`, add a corresponding entry to the
`components` section:

```yaml
components:
  my-new-service:
    profile: extended
    impact: medium
    description: "Description of what this component does"
```

The key must match the basename of the repository URL. For example, if
`values.yaml` has `repository: quay.io/org/my-new-service`, the key is
`my-new-service`.

### Changing a component's recommended profile

Edit the `profile` field. Use the smoke filter in `scripts/lib/iqe-filters.sh`
as the guide — if the component's tests match the smoke `-k` filter, `smoke`
is sufficient. Otherwise, `extended` or `stable` is needed.

```yaml
  koku-ui-onprem:
    profile: extended    # was: smoke — UI tests don't match smoke's -k filter
```

### Adding a new path-based rule

Add an entry to the `paths` section with a regex pattern:

```yaml
paths:
  my-new-rule:
    pattern: "^some/path/"
    profile: extended
    impact: medium
    description: "What this rule covers"
```

For rules that should only fire on specific types of content changes (not just
any file touch), add a `diff_pattern` that matches against the `git diff`
output.

### Testing changes locally

```bash
# Run against the current branch vs. main
BASE_BRANCH=main ./scripts/qe/recommend-tests.sh

# Override the map location
IMPACT_MAP=/path/to/test-impact-map.yaml BASE_BRANCH=main ./scripts/qe/recommend-tests.sh
```

## Relationship to IQE Profiles

The profiles referenced in this map correspond to the IQE test profiles
defined in `scripts/lib/iqe-filters.sh`:

| Profile | Tests | Duration | Smoke Filter | Skip Groups |
|---------|-------|----------|-------------|-------------|
| `smoke` | ~43 | ~17 min | Positive `-k` filter (source + cost model only) | All optional groups skipped |
| `extended` | ~2100 | ~33 min | No positive filter | `SKIP_INFRA_TESTS=true` |
| `stable` | ~2350 | ~40 min | No positive filter | No optional skips |
| `full` | ~3324 | ~3 hrs | No filter | No skip filters at all |

The key distinction is that **smoke uses a positive `-k` filter** that only
selects `test_api_ocp_source*` and `test_api_cost_model_ocp*`. Any component
whose tests don't match those patterns needs at least `extended`.

See [Skipped IQE Tests](skipped-iqe-tests.md) for full details on skip groups
and profile definitions.

[← Back to Development Index](README.md)
