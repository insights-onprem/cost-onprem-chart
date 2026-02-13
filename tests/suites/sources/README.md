# Sources API Test Suite

Tests for the Sources API endpoints now served by Koku.

## Overview

The Sources API has been merged into Koku. All sources endpoints are now available via
`/api/cost-management/v1/`.

This test suite contains **TWO types of tests**:

| Type | Marker | Auth Method | Route | Classes |
|------|--------|-------------|-------|---------|
| **External API** | `api` | JWT (Keycloak) | Gateway → Koku | `TestSourcesExternal*` |
| **Interpod** | `interpod` | X-Rh-Identity | ClusterIP → Koku | `TestKokuSources*`, `TestSourceTypes*`, etc. |

## Architecture

### External API Tests (via Gateway)

```
┌─────────────────┐
│   Test Client   │
│  (pytest/HTTP)  │
└────────┬────────┘
         │ HTTPS + JWT
         ▼
┌─────────────────┐
│  Gateway Route  │
│  (OpenShift)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Envoy Gateway  │
│  - JWT validate │
│  - Add headers  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Koku API      │
└─────────────────┘
```

### Interpod Tests (Internal)

```
┌─────────────────┐
│   Test Client   │
│    (pytest)     │
└────────┬────────┘
         │ kubectl exec
         ▼
┌─────────────────┐
│  Ingress Pod    │
│  (curl client)  │
└────────┬────────┘
         │ HTTP + X-Rh-Identity
         ▼
┌─────────────────┐
│   Koku API      │
│  (ClusterIP)    │
└─────────────────┘
```

## Test Files

| File | Description |
|------|-------------|
| `test_sources_api.py` | External API + Interpod tests for Sources API |
| `conftest.py` | Suite fixtures (pod_session, test_source, etc.) |

## Running Tests

```bash
# Run ALL sources tests (both external and interpod)
pytest -m sources -v

# Run ONLY external API tests (via gateway with JWT)
pytest -m "sources and api" -v

# Run ONLY interpod tests (internal cluster communication)
pytest -m "sources and interpod" -v

# Run by path
pytest tests/suites/sources/ -v

# Run specific test types
pytest -m "sources and component" -v    # Component tests only
pytest -m "sources and integration" -v  # Integration tests only
pytest -m "sources and smoke" -v        # Smoke tests only
```

## Fixtures

### External API Fixtures (from root conftest.py)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `gateway_url` | session | External gateway route URL |
| `jwt_token` | function | Fresh JWT token from Keycloak |
| `authenticated_session` | function | requests.Session with JWT auth |

### Interpod Fixtures (from suite conftest.py)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `koku_api_url` | module | Koku API internal URL |
| `ingress_pod` | module | Pod name for executing internal API calls |
| `pod_session` | module | requests.Session routed through ingress pod |
| `pod_session_no_auth` | module | Session without auth headers (for error tests) |
| `rh_identity_header` | module | Valid X-Rh-Identity header for test org |
| `invalid_identity_headers` | module | Dict of invalid headers for error testing |
| `test_source` | function | Creates a test source with auto-cleanup |

## Authentication

### External API (JWT)

External tests use JWT tokens from Keycloak via the `authenticated_session` fixture.
The gateway validates the JWT and injects appropriate headers for Koku.

### Interpod (X-Rh-Identity)

Internal tests use the `X-Rh-Identity` header containing a base64-encoded JSON payload:
- `identity.org_id` - Organization ID
- `identity.user.email` - User email
- `identity.user.is_org_admin` - Admin flag
- `entitlements.cost_management.is_entitled` - Entitlement flag

## Why Both Test Types?

| Aspect | External API | Interpod |
|--------|--------------|----------|
| **Tests** | Full auth flow, gateway routing | X-Rh-Identity handling, internal routing |
| **Speed** | Slower (TLS, JWT validation) | Faster (direct ClusterIP) |
| **Auth** | Keycloak JWT required | X-Rh-Identity header only |
| **Use case** | User-facing API contract | Service-to-service communication |
