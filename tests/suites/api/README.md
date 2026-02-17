# External API Tests

This test suite validates the external API contract by making direct HTTP calls through the gateway route with JWT authentication.

## Purpose

These tests verify:
- **Gateway routing**: Envoy correctly routes requests to backend services
- **API contract**: Endpoints return expected responses and status codes
- **Error handling**: Invalid requests return appropriate error responses

**Note**: JWT authentication tests are in `suites/auth/test_gateway_auth.py` to avoid redundancy.

## Test Files

| File | Tests | Description |
|------|-------|-------------|
| `test_reports.py` | 7 | Cost report endpoints (costs, compute, memory, volumes) |
| `test_ingress.py` | 3 | Data upload validation via ingress endpoint |
| `test_cost_models.py` | 6 | Cost model CRUD and rate configuration |
| `test_tagging.py` | 7 | Tag-based filtering and grouping |

**Note**: ROS/recommendations tests are in `suites/ros/test_recommendations.py`.

## Running Tests

```bash
# Run all API tests
pytest -m api

# Run specific test file
pytest tests/suites/api/test_reports.py

# Run with verbose output
pytest -m api -v
```

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `gateway_url` | session | External gateway route URL |
| `jwt_token` | function | Fresh JWT token from Keycloak |
| `authenticated_session` | function | requests.Session with JWT auth |
| `ocp_source_type_id` | session | OpenShift source type ID |

## Architecture

```
┌─────────────────┐
│   Test Client   │
│  (pytest/HTTP)  │
└────────┬────────┘
         │ HTTPS (JWT)
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
│  - Route        │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌───────┐
│ Koku  │ │  ROS  │
│  API  │ │  API  │
└───────┘ └───────┘
```

## Notes

- Tests use function-scoped JWT tokens to prevent expiration (5-minute TTL)
- SSL verification is disabled for self-signed certificates
- Tests should be idempotent and clean up after themselves
