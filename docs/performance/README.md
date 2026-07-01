# Performance Documentation

This directory contains all performance-related documentation for Cost On-Prem
(FLPATH-4036 / COST-7567).

**Status**: Small through XLarge profiles validated with 0-failure runs.
Stress profiles (P99/Max) and soak tests pending.

## Contents

| Document | Description |
|----------|-------------|
| [performance-testing-plan.md](performance-testing-plan.md) | Strategy, success criteria, and progress tracking |
| [TEST-MATRIX.md](TEST-MATRIX.md) | Complete test matrix with all permutations and parameters |
| [FINDINGS.md](FINDINGS.md) | Product issues discovered during testing (Jira-ready summaries) |
| [sizing-guide.md](sizing-guide.md) | Resource sizing recommendations validated through testing |
| [OBSERVABILITY.md](OBSERVABILITY.md) | Metrics collection, S3 archival, and report generation |
| [cost-onprem-group-by-investigation.md](cost-onprem-group-by-investigation.md) | API group_by dimension limit investigation |

## Quick Links

### For Test Engineers
- Start with [TEST-MATRIX.md](TEST-MATRIX.md) to understand available tests
- See [performance-testing-plan.md](performance-testing-plan.md) for success criteria
- Track issues in [FINDINGS.md](FINDINGS.md)

### For Operators
- See [sizing-guide.md](sizing-guide.md) for resource recommendations
- Check FINDINGS.md for known limitations and workarounds

### For Developers
- Review FINDINGS.md for performance bugs needing attention
- See TEST-MATRIX.md for regression test coverage
- See OBSERVABILITY.md for metrics infrastructure

## Related Resources

- **Test Code**: `tests/suites/performance/` — test implementations
- **Test Data Setup**: `docs/development/test-data-setup.md` — data generation guide
- **Deploy Script**: `scripts/deploy-test-cost-onprem.sh` — integrated test runner
- **Lib Scripts**: `scripts/lib/perf-testing.sh`, `scripts/lib/perf-observability.sh` — profile config and S3 upload
- **Epic**: [FLPATH-4036](https://redhat.atlassian.net/browse/FLPATH-4036) / [COST-7567](https://redhat.atlassian.net/browse/COST-7567)
