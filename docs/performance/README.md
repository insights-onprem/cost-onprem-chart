# Performance Documentation

This directory contains all performance-related documentation for Cost On-Prem.

## Contents

| Document | Description |
|----------|-------------|
| [performance-testing-plan.md](performance-testing-plan.md) | Comprehensive testing strategy for FLPATH-4036 |
| [TEST-MATRIX.md](TEST-MATRIX.md) | Complete test matrix with all permutations and parameters |
| [FINDINGS.md](FINDINGS.md) | Issues discovered during testing (Jira-ready summaries) |
| [sizing-guide.md](sizing-guide.md) | Resource sizing recommendations by deployment scale |

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

## Related Resources

- **Test Code**: `tests/suites/performance/` - Actual test implementations
- **Test Data Setup**: `docs/development/test-data-setup.md` - Data generation guide
- **Epic**: [FLPATH-4036](https://redhat.atlassian.net/browse/FLPATH-4036)
