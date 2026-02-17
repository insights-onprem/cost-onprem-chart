# Cursor Prompt Templates

These prompt templates help you quickly invoke common actions in Cursor.

## How to Use

In Cursor, type `@` followed by the prompt name to include it in your conversation:

- `@run-tests` - Run the pytest test suite
- `@troubleshoot-tests` - Diagnose test failures
- `@connect-cluster` - Set up cluster access
- `@deploy-chart` - Deploy the Helm chart
- `@check-logs` - View component logs
- `@debug-e2e` - Debug E2E test failures
- `@download-ci-artifacts` - Download CI artifacts for debugging
- `@maintain-pr-summary` - Maintain a running PR summary document

## Available Prompts

| Prompt | Purpose |
|--------|---------|
| `run-tests.md` | Run pytest with various options (CI mode, extended, specific suites) |
| `troubleshoot-tests.md` | Diagnose common test failures with specific commands |
| `connect-cluster.md` | Set up OpenShift cluster access with credentials |
| `deploy-chart.md` | Deploy the cost-onprem Helm chart |
| `check-logs.md` | View logs for all cost-onprem components |
| `debug-e2e.md` | Step-by-step debugging for each E2E test |
| `download-ci-artifacts.md` | Download CI artifacts from OpenShift CI |
| `maintain-pr-summary.md` | Maintain a running PR summary as work is developed |

## Example Usage

1. **Run tests**: Type `@run-tests` then ask "Run the E2E tests"
2. **Troubleshoot**: Type `@troubleshoot-tests` then paste your error
3. **Connect**: Type `@connect-cluster` then provide your credentials
4. **Download CI logs**: Type `@download-ci-artifacts` then provide the PR number and build ID
5. **PR Summary**: Type `@maintain-pr-summary` then ask "Update the PR summary with recent changes"

## For Claude Code / claude.ai

These prompts are also useful outside Cursor. Copy the relevant prompt content
into your conversation to provide context for your request.

See also: `CLAUDE.md` at project root for consolidated project context.

## CI Artifact Download Script

A standalone script is also available:

```bash
# Download all artifacts for a PR
./scripts/download-ci-artifacts.sh 50 2014360404288868352

# Download from gcsweb URL
./scripts/download-ci-artifacts.sh --url "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/50/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/2014360404288868352/"
```
