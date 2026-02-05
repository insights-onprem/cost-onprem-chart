# Sources API Test Suite

Tests for the Sources API endpoints now served by Koku.

## Overview

The Sources API has been merged into Koku. All sources endpoints are now available via
`/api/cost-management/v1/` using `X-Rh-Identity` header for authentication.

This test suite validates:
- Source CRUD operations (create, read, update, delete)
- Authentication and authorization error handling
- API response schema validation
- Infrastructure removal validation (old sources-api components removed)

## Test Files

| File | Description |
|------|-------------|
| `test_sources_api.py` | Core Sources API functionality tests |
| `test_api_schemas.py` | API response schema validation |
| `test_sources_removal.py` | Validates old sources-api infrastructure is removed |

## Running Tests

```bash
# Run all sources tests
pytest -m sources -v

# Run by path
pytest tests/suites/sources/ -v

# Run specific test types
pytest -m "sources and component" -v    # Component tests only
pytest -m "sources and integration" -v  # Integration tests only
pytest -m "sources and smoke" -v        # Smoke tests only
```

## Fixtures

Key fixtures provided by `conftest.py`:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `koku_api_reads_url` | module | Koku API URL for GET requests |
| `koku_api_writes_url` | module | Koku API URL for POST/PUT/DELETE |
| `ingress_pod` | module | Pod name for executing internal API calls |
| `rh_identity_header` | module | Valid X-Rh-Identity header for test org |
| `invalid_identity_headers` | module | Dict of invalid headers for error testing |
| `test_source` | function | Creates a test source with auto-cleanup |

## Authentication

All Sources API endpoints require the `X-Rh-Identity` header containing a base64-encoded
JSON payload with:
- `identity.org_id` - Organization ID
- `identity.user.email` - User email
- `identity.user.is_org_admin` - Admin flag
- `entitlements.cost_management.is_entitled` - Entitlement flag
