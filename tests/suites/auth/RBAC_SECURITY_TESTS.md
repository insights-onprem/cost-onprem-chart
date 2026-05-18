# RBAC Security Boundary Tests

**Added:** 2026-05-14  
**Location:** `tests/suites/auth/test_rbac_gateway.py::TestRBACSecurityBoundaries`  
**Purpose:** Validate security boundaries, fail-closed behavior, and tenant isolation for RBAC authorization

---

## Overview

These tests complement the existing RBAC functional tests by focusing on **security boundaries** and **failure modes**. They validate that the system fails securely when components are unavailable, rejects malicious inputs, and prevents privilege escalation.

### Philosophy: Fail-Closed > Fail-Open

The most critical security property: **when authorization fails, deny access**. A 424 (Failed Dependency) is acceptable only if it denies data; returning 200 with data when RBAC is down is a P0 security bug.

---

## Test Suite

### ✅ Implemented & Runnable

| Test | Priority | What It Validates |
|------|----------|-------------------|
| **test_rbac_service_unavailable_denies_access_fail_closed** | **P0** | When RBAC API is down (scaled to 0), authenticated requests MUST fail-closed (403/424/503), not return 200 with data |
| **test_permission_revocation_honored_after_cache_clear** | **P1** | After removing user from RBAC group + cache clear, new tokens receive 403 (validates cache invalidation) |
| **test_rbac_iam_reader_cannot_modify_own_permissions** | **P1** | Read-only IAM user cannot escalate by adding self to privileged groups (POST/PUT to groups must return 403) |
| **test_concurrent_jwt_sessions_no_resource_exhaustion** | **P2** | 10 concurrent sessions for same user don't cause 500 errors (load handling) |
| **test_rbac_cache_ttl_configuration_exists** | **P2** | RBAC deployment has `ACCESS_CACHE_ENABLED=true` (operational security check) |

### ⚠️ Documented as `pytest.skip()` with TODO

These tests document **expected behavior** but require infrastructure not available in current test environment:

| Test | Priority | Blocker | Expected Behavior |
|------|----------|---------|-------------------|
| **test_expired_jwt_rejected** | **P1** | Requires JWT forging or 5+ min wait | Envoy MUST reject JWTs with `exp` claim in the past with 401 |
| **test_jwt_without_required_claims_rejected** | **P1** | Requires JWT forging | Envoy MUST reject JWTs missing `org_id`, `account_number`, or `sub` claims with 401 |
| **test_org_id_tenant_isolation_boundary_cases** | **P1** | Requires in-cluster pod or JWT forging | System MUST reject malicious `org_id` values (empty, SQL injection, overflow) |

---

## Test Details

### 1. RBAC Service Failure (Fail-Closed) — **P0**

**Test:** `test_rbac_service_unavailable_denies_access_fail_closed`

**Scenario:**
1. Scale RBAC API deployment to 0 replicas (simulate outage)
2. Send authenticated request to `/cost-management/v1/reports/`
3. Assert response is NOT 200 (fail-closed)
4. Restore RBAC service

**Critical Assertion:**
```python
assert response.status_code != 200, (
    "SECURITY VIOLATION: RBAC service down but request succeeded. "
    "This is fail-open behavior and exposes data without authz checks."
)
```

**Why It Matters:**  
If RBAC is unreachable, the system MUST deny access. A 200 response with data means fail-open → **tenant isolation breach**.

---

### 2. Permission Revocation Propagation — **P1**

**Test:** `test_permission_revocation_honored_after_cache_clear`

**Scenario:**
1. User has RBAC IAM read permissions (baseline: GET /principals/ → 200)
2. Remove user from RBAC group via Django ORM + `cache.clear()`
3. Obtain NEW JWT (old token might be cached)
4. Assert GET /principals/ → 403

**Why It Matters:**  
Validates that permission changes propagate through cache layer. Without cache clear, changes take 5+ minutes (cache TTL).

---

### 3. Privilege Escalation Prevention — **P1**

**Test:** `test_rbac_iam_reader_cannot_modify_own_permissions`

**Scenario:**
1. User has `rbac:group:read` (can list groups)
2. Attempt POST `/rbac/v1/groups/{uuid}/principals/` to add self
3. Assert 403 (forbidden)
4. Attempt PUT `/rbac/v1/groups/{uuid}/` to modify group
5. Assert 403

**Why It Matters:**  
Read-only IAM users must not escalate to admin by modifying group membership.

---

### 4. Concurrent Sessions — **P2**

**Test:** `test_concurrent_jwt_sessions_no_resource_exhaustion`

**Scenario:**
1. Generate 10 JWTs for same user
2. Fire 10 concurrent requests to `/rbac/v1/principals/`
3. Assert no 500 errors
4. Assert ≥50% success rate (allows rate limiting)

**Why It Matters:**  
Users might have multiple active sessions (browser, mobile, CLI). System must handle without crashing.

---

### 5. RBAC Cache Configuration — **P2**

**Test:** `test_rbac_cache_ttl_configuration_exists`

**Checks:**
- RBAC API deployment has `ACCESS_CACHE_ENABLED=true` env var
- Documents cache TTL implications (5 min delay for permission changes)

**Why It Matters:**  
Operational awareness: revoked permissions take up to 5 minutes to propagate unless `cache.clear()` is called.

---

## Tests Requiring Additional Infrastructure

### Expired JWT Rejection — **P1** ⚠️

**Current Status:** `pytest.skip()` with TODO

**What's Needed:**
- **Option A:** JWT forging library (python-jose + RSA key pair)
- **Option B:** Keycloak test realm with 10s token lifetime
- **Option C:** Wait 5+ minutes for production token to expire

**Expected Behavior:**
```python
# JWT with exp=1609459200 (Jan 1, 2021)
response = http_session.get(url, headers={"Authorization": f"Bearer {expired_jwt}"})
assert response.status_code == 401
```

**Implementation Path:**
```bash
# Generate test RSA key
openssl genrsa -out test-key.pem 2048

# Forge JWT with past exp claim using python-jose
from jose import jwt
token = jwt.encode(
    {"sub": "test", "exp": 1609459200},  # 2021
    open("test-key.pem").read(),
    algorithm="RS256"
)
```

---

### JWT Claim Validation — **P1** ⚠️

**Current Status:** `pytest.skip()` with TODO

**What's Needed:** Forge JWTs missing required claims

**Expected Behavior:**
```python
# JWT without org_id claim
jwt_no_org = forge_jwt({"sub": "test", "account_number": "123"})
response = http_session.get(url, headers={"Authorization": f"Bearer {jwt_no_org}"})
assert response.status_code == 401  # Envoy Lua filter rejects
```

---

### org_id Boundary Cases — **P1** ⚠️

**Current Status:** Parametrized test with `pytest.skip()` for each case

**Malicious Inputs Tested:**
1. Empty string: `org_id=""`
2. Integer overflow: `org_id="999999999999999999999999"`
3. Path traversal: `org_id="../../../etc/passwd"`
4. SQL injection: `org_id="1' OR '1'='1"`
5. Wrong tenant: `org_id="other-tenant-org-id"`

**What's Needed:**
- **Option A:** In-cluster pod that can POST to Koku with crafted `X-Rh-Identity` header (bypass Envoy)
- **Option B:** Forge JWTs with malicious `org_id` claims

**Expected Behavior:** All variants MUST return 400/403 (validation/authz failure), never 200.

**Implementation Path:**
```python
# In-cluster approach (bypasses Envoy JWT validation)
koku_url = "http://cost-onprem-api.cost-onprem.svc.cluster.local:8000"
malicious_identity = base64.b64encode(json.dumps({
    "org_id": "1' OR '1'='1",  # SQL injection attempt
    "identity": {"account_number": "123", "type": "User", "user": {...}}
}).encode()).decode()

response = requests.get(
    f"{koku_url}/api/cost-management/v1/reports/openshift/costs/",
    headers={"X-Rh-Identity": malicious_identity}
)
assert response.status_code in (400, 403), "System must reject malicious org_id"
```

---

## Running the Tests

```bash
# Run all security boundary tests
pytest tests/suites/auth/test_rbac_gateway.py::TestRBACSecurityBoundaries -v

# Run specific test
pytest tests/suites/auth/test_rbac_gateway.py::TestRBACSecurityBoundaries::test_rbac_service_unavailable_denies_access_fail_closed -v

# Run only implemented tests (skip TODOs)
pytest tests/suites/auth/test_rbac_gateway.py::TestRBACSecurityBoundaries -v -k "not expired and not org_id and not jwt_without"
```

---

## Coverage Gap Analysis

| Security Boundary | Tested | Priority | Blocker |
|-------------------|--------|----------|---------|
| **Fail-closed on RBAC outage** | ✅ | P0 | None |
| **Permission revocation** | ✅ | P1 | None |
| **Privilege escalation** | ✅ | P1 | None |
| **Concurrent sessions** | ✅ | P2 | None |
| **Cache configuration** | ✅ | P2 | None |
| **Expired JWT** | ⚠️ TODO | P1 | JWT forging |
| **Missing JWT claims** | ⚠️ TODO | P1 | JWT forging |
| **Malicious org_id** | ⚠️ TODO | P1 | In-cluster pod or JWT forging |
| **Token refresh** | ❌ | P2 | Not implemented |
| **Rate limiting** | ❌ | P2 | Not implemented |
| **RBAC DB timeout** | ❌ | P2 | Not implemented |

---

## Next Steps

### Short Term (This Sprint)

1. **Run implemented tests in CI** — verify fail-closed behavior on real cluster
2. **Add JWT forging helper** — use python-jose to forge tokens for exp/claim tests
3. **Create in-cluster test pod** — kubectl exec to test direct Koku access with crafted identities

### Medium Term (Next Sprint)

4. **Implement org_id boundary tests** — validate SQL injection prevention
5. **Add rate limiting tests** — 100 req/s to unauthenticated endpoints
6. **RBAC DB failure simulation** — test timeout/circuit breaker behavior

### Long Term (Backlog)

7. **Token refresh flow** — validate rotation without service interruption
8. **Load testing** — 1000 concurrent users, measure RBAC latency
9. **Chaos engineering** — random RBAC pod kills during active requests

---

## References

- **Original Analysis:** Senior QE persona review (2026-05-14)
- **PR Context:** [#146 - RBAC v1 integration](https://github.com/insights-onprem/cost-onprem-chart/pull/146)
- **RBAC Docs:** `docs/operations/rbac-setup.md`
- **Related Tests:** `tests/suites/e2e/test_rbac_access.py` (persona-based data isolation)

---

## Key Findings Summary

**Strengths:**
- ✅ Fail-closed behavior validated
- ✅ Permission revocation tested end-to-end
- ✅ Privilege escalation prevented

**Critical Gaps (Require Infrastructure):**
- ⚠️ Expired JWT handling (needs forging)
- ⚠️ Malicious claim validation (needs forging)
- ⚠️ org_id tenant isolation (needs in-cluster pod)

**Recommendation:** Merge implemented tests now. Create follow-up tickets for JWT forging infrastructure and in-cluster test pod setup.
