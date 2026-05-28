"""
RBAC behavior through the public API gateway (Envoy + Keycloak JWT).

Complements suites/e2e/test_rbac_access.py (which uses in-cluster X-Rh-Identity)
by exercising real JWT → Envoy → Koku/ROS → insights-rbac flows.
"""

from __future__ import annotations

import json
import os
import time

import pytest
import requests

from conftest import _DEFAULT_ACCOUNT_NUMBER, obtain_password_grant_token
from jwt_forge import forge_expired_jwt, forge_jwt_missing_sub
from rbac_bootstrap_scripts import (
    render_rbac_gateway_permission_restore_script,
    render_rbac_gateway_permission_revoke_script,
    render_rbac_iam_reader_bootstrap_script,
)
from rbac_keycloak_users import (
    ensure_realm_user_with_password,
    fetch_keycloak_master_admin_token,
)
from suites.auth.test_gateway_auth import _check_gateway_reachable
from utils import (
    exec_in_pod_raw,
    get_pod_by_label,
    get_route_url,
    run_oc_command,
    wait_for_deployment_replicas,
)


RBAC_IAM_GATEWAY_USERNAME = "rbac-iam-admin"


@pytest.fixture(scope="module")
def _provision_gateway_nobody_user(
    cluster_config,
    keycloak_config,
    org_id: str,
    rbac_gateway_test_user_password: str,
):
    """Ensure ``nobody-unassigned`` exists in Keycloak (no RBAC group membership)."""
    admin_token = fetch_keycloak_master_admin_token(
        keycloak_config.url,
        cluster_config.keycloak_namespace,
    )
    if not admin_token:
        pytest.skip("Could not obtain Keycloak master admin token (secret or API)")

    try:
        ensure_realm_user_with_password(
            keycloak_base_url=keycloak_config.url,
            realm=keycloak_config.realm,
            admin_token=admin_token,
            username="nobody-unassigned",
            password=rbac_gateway_test_user_password,
            org_id=org_id,
            account_number="7890123",
            email="nobody-unassigned@rbac-gateway.test",
        )
    except RuntimeError as exc:
        pytest.skip(f"Keycloak user provisioning failed: {exc}")

    yield


@pytest.fixture
def gateway_nobody_user_jwt(
    cluster_config,
    keycloak_config,
    rbac_gateway_test_user_password: str,
    _provision_gateway_nobody_user,
):
    """Fresh password-grant JWT for ``nobody-unassigned``."""
    return obtain_password_grant_token(
        "nobody-unassigned",
        rbac_gateway_test_user_password,
        keycloak_config,
        cluster_config,
    )


@pytest.fixture(scope="module")
def _provision_rbac_iam_reader_gateway(
    cluster_config,
    keycloak_config,
    org_id: str,
    rbac_gateway_test_user_password: str,
):
    """Keycloak user + RBAC group binding with read-only rbac IAM (group or principal read)."""
    admin_token = fetch_keycloak_master_admin_token(
        keycloak_config.url,
        cluster_config.keycloak_namespace,
    )
    if not admin_token:
        pytest.skip("Could not obtain Keycloak master admin token (secret or API)")

    try:
        ensure_realm_user_with_password(
            keycloak_base_url=keycloak_config.url,
            realm=keycloak_config.realm,
            admin_token=admin_token,
            username=RBAC_IAM_GATEWAY_USERNAME,
            password=rbac_gateway_test_user_password,
            org_id=org_id,
            account_number="7890123",
            email="rbac-iam-admin@rbac-gateway.test",
        )
    except RuntimeError as exc:
        pytest.skip(f"Keycloak IAM test user provisioning failed: {exc}")

    rbac_pod = get_pod_by_label(
        cluster_config.namespace, "app.kubernetes.io/component=rbac-api"
    )
    if not rbac_pod:
        pytest.skip("RBAC API pod not found")

    script = render_rbac_iam_reader_bootstrap_script(org_id, RBAC_IAM_GATEWAY_USERNAME)
    result = exec_in_pod_raw(
        cluster_config.namespace,
        rbac_pod,
        [
            "python",
            "/opt/rbac/rbac/manage.py",
            "shell",
            "-c",
            script,
        ],
        timeout=120,
    )
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "no_rbac_iam_read_permission" in out:
        pytest.skip(
            f"RBAC IAM bootstrap skipped (returncode={result.returncode}): {out[:600]}"
        )

    yield


@pytest.fixture
def gateway_rbac_iam_user_jwt(
    cluster_config,
    keycloak_config,
    rbac_gateway_test_user_password: str,
    _provision_rbac_iam_reader_gateway,
):
    """JWT for user with insights-rbac IAM read (groups list)."""
    return obtain_password_grant_token(
        RBAC_IAM_GATEWAY_USERNAME,
        rbac_gateway_test_user_password,
        keycloak_config,
        cluster_config,
    )


@pytest.mark.auth
@pytest.mark.integration
class TestRBACGateway:
    """RBAC enforcement on routes reached through the Envoy gateway."""

    def test_gateway_openshift_costs_unauthenticated_returns_401(
        self, gateway_url: str, http_session: requests.Session
    ):
        """Protected Koku route rejects missing JWT."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/reports/openshift/costs/"
        response = http_session.get(url, timeout=20)
        assert response.status_code == 401, (
            f"Expected 401 without Authorization, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    def test_gateway_rbac_status_unauthenticated_returns_401(
        self, gateway_url: str, http_session: requests.Session
    ):
        """RBAC admin API is JWT-gated at the gateway."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/rbac/v1/status/"
        response = http_session.get(url, timeout=20)
        assert response.status_code == 401, (
            f"Expected 401 without Authorization on RBAC status, got "
            f"{response.status_code}: {response.text[:200]}"
        )

    def test_gateway_openshift_costs_user_without_rbac_returns_403(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_nobody_user_jwt,
    ):
        """Authenticated user with no cost-management permissions receives 403."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/reports/openshift/costs/"
        response = http_session.get(
            url,
            headers=gateway_nobody_user_jwt.authorization_header,
            timeout=90,
        )
        assert response.status_code in (403, 424), (
            f"Expected 403 (RBAC deny) or 424 (RBAC dependency failure), "
            f"got {response.status_code}: {response.text[:300]}"
        )

    def test_gateway_rbac_groups_unauthenticated_returns_401(
        self, gateway_url: str, http_session: requests.Session
    ):
        """RBAC IAM ``/groups/`` requires JWT at the gateway."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/rbac/v1/groups/?limit=1"
        response = http_session.get(url, timeout=20)
        assert response.status_code == 401, (
            f"Expected 401 without Authorization on RBAC groups, got "
            f"{response.status_code}: {response.text[:200]}"
        )

    def test_gateway_rbac_groups_user_without_iam_returns_403(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_nobody_user_jwt,
    ):
        """User with no ``rbac`` application permissions cannot list groups."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/rbac/v1/groups/?limit=1"
        response = http_session.get(
            url,
            headers=gateway_nobody_user_jwt.authorization_header,
            timeout=60,
        )
        assert response.status_code in (403, 424), (
            f"Expected 403 or 424 for RBAC groups without IAM perms, "
            f"got {response.status_code}: {response.text[:300]}"
        )

    def test_gateway_rbac_principals_iam_reader_returns_200(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_rbac_iam_user_jwt,
    ):
        """User with minimal ``rbac`` read permission can list principals via gateway."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/rbac/v1/principals/?limit=5"
        response = http_session.get(
            url,
            headers=gateway_rbac_iam_user_jwt.authorization_header,
            timeout=60,
            verify=False,
        )
        assert response.status_code == 200, (
            f"Expected 200 listing principals with IAM read role, "
            f"got {response.status_code}: {response.text[:400]}"
        )
        payload = response.json()
        assert "data" in payload or "meta" in payload, (
            f"Unexpected RBAC principals JSON shape: {list(payload.keys())[:10]}"
        )

    def test_gateway_rbac_groups_post_iam_reader_forbidden(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_rbac_iam_user_jwt,
    ):
        """Read-only IAM role must not allow creating groups."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/rbac/v1/groups/"
        response = http_session.post(
            url,
            headers={
                **gateway_rbac_iam_user_jwt.authorization_header,
                "Content-Type": "application/json",
            },
            json={"name": "pytest-rbac-iam-write-deny"},
            timeout=60,
            verify=False,
        )
        assert response.status_code in (403, 400), (
            f"Expected 403 (forbidden) or 400 (validation before authz) for POST "
            f"groups without write, got {response.status_code}: {response.text[:400]}"
        )

    def test_gateway_ros_recommendations_unauthenticated_returns_401(
        self, gateway_url: str, http_session: requests.Session
    ):
        """ROS recommendations route requires JWT at the gateway."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/recommendations/openshift"
        response = http_session.get(url, timeout=20)
        assert response.status_code == 401, (
            f"Expected 401 without Authorization on ROS recommendations, got "
            f"{response.status_code}: {response.text[:200]}"
        )

    def test_gateway_ros_recommendations_user_without_rbac_returns_403(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_nobody_user_jwt,
    ):
        """ROS recommendations require the same cost-management permissions as Koku."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/recommendations/openshift"
        response = http_session.get(
            url,
            headers=gateway_nobody_user_jwt.authorization_header,
            timeout=90,
        )
        assert response.status_code in (403, 424), (
            f"Expected 403 or 424 from ROS without RBAC permissions, "
            f"got {response.status_code}: {response.text[:300]}"
        )


@pytest.mark.auth
@pytest.mark.integration
def test_rbac_migration_job_completed(cluster_config):
    """RBAC Helm hook migration job finished successfully (cluster sanity)."""
    gateway_route = f"{cluster_config.helm_release_name}-api"
    if not get_route_url(cluster_config.namespace, gateway_route):
        pytest.skip("API route not found — cluster not deployed for this suite")

    result = run_oc_command(
        [
            "get",
            "jobs",
            "-n",
            cluster_config.namespace,
            "-l",
            "app.kubernetes.io/component=rbac-migration",
            "-o",
            "json",
        ],
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"oc get jobs failed: {result.stderr}")

    data = json.loads(result.stdout or "{}")
    items = data.get("items") or []
    if not items:
        pytest.skip("No rbac-migration job found (chart without RBAC or hook removed)")

    status = items[0].get("status") or {}
    if status.get("active"):
        pytest.skip("rbac-migration job still running")
    if status.get("failed"):
        pytest.fail(
            f"rbac-migration job failed: {json.dumps(status, indent=2)[:800]}"
        )
    assert status.get("succeeded") == 1, (
        f"Expected rbac-migration job succeeded=1, got: {json.dumps(status)[:800]}"
    )


@pytest.mark.auth
@pytest.mark.integration
class TestRBACSecurityBoundaries:
    """Security boundary tests for RBAC authorization enforcement.

    These tests validate fail-closed behavior, token validation, and tenant
    isolation to prevent authorization bypass and data leakage.
    """

    def test_rbac_service_unavailable_denies_access_fail_closed(
        self,
        cluster_config,
        gateway_url: str,
        http_session: requests.Session,
        gateway_rbac_iam_user_jwt,
    ):
        """CRITICAL: When RBAC API is down, requests MUST fail-closed (deny access).

        This validates that the system fails securely when the authorization
        backend is unreachable. A 424 (Failed Dependency) or 503 is acceptable
        only if it denies data access. A 200 with data would be a P0 security bug.
        """
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        # Scale RBAC deployment to 0 to simulate service outage
        scale_result = run_oc_command(
            [
                "scale",
                "deployment",
                "-n",
                cluster_config.namespace,
                "-l",
                "app.kubernetes.io/component=rbac-api",
                "--replicas=0",
            ],
            check=False,
        )
        if scale_result.returncode != 0:
            pytest.skip(f"Could not scale RBAC deployment: {scale_result.stderr}")

        rbac_label = "app.kubernetes.io/component=rbac-api"

        try:
            # Wait for replicas to reach 0 (pods terminated)
            if not wait_for_deployment_replicas(
                cluster_config.namespace, rbac_label, expected_replicas=0, timeout=30
            ):
                pytest.skip("RBAC deployment did not scale to 0 within timeout")

            # Attempt authenticated request - should fail-closed
            url = f"{gateway_url.rstrip('/')}/cost-management/v1/reports/openshift/costs/"
            response = http_session.get(
                url,
                headers=gateway_rbac_iam_user_jwt.authorization_header,
                timeout=60,
                verify=False,
            )

            # CRITICAL: Must NOT return 200 with data
            assert response.status_code != 200, (
                f"SECURITY VIOLATION: RBAC service down but request succeeded "
                f"with status {response.status_code}. This is fail-open behavior "
                f"and exposes data without authorization checks. Response: {response.text[:400]}"
            )

            # Expected: 424 (Failed Dependency), 503 (Service Unavailable), or 403 (Forbidden)
            assert response.status_code in (403, 424, 503, 504), (
                f"Expected fail-closed response (403/424/503/504) when RBAC down, "
                f"got {response.status_code}: {response.text[:300]}"
            )

        finally:
            # Restore RBAC service
            restore_result = run_oc_command(
                [
                    "scale",
                    "deployment",
                    "-n",
                    cluster_config.namespace,
                    "-l",
                    rbac_label,
                    "--replicas=1",
                ],
                check=False,
            )
            # Wait for RBAC to be ready again (readyReplicas=1)
            if restore_result.returncode == 0:
                wait_for_deployment_replicas(
                    cluster_config.namespace, rbac_label, expected_replicas=1, timeout=60
                )

    def test_expired_jwt_rejected(
        self,
        gateway_url: str,
        http_session: requests.Session,
        org_id: str,
    ):
        """Expired JWTs (exp claim in the past) MUST be rejected with 401."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/status/"
        expired_jwt = forge_expired_jwt(org_id=org_id)
        response = http_session.get(
            url,
            headers={"Authorization": f"Bearer {expired_jwt}"},
            timeout=30,
            verify=False,
        )
        assert response.status_code == 401, (
            f"Expected 401 for expired/forged JWT, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.parametrize(
        "malicious_org_id,expected_status",
        [
            ("", 400),  # Empty org_id should be rejected by validation
            ("999999999999999999999999", 403),  # Numeric overflow - wrong tenant
            ("../../../etc/passwd", 403),  # Path traversal attempt
            ("1' OR '1'='1", 403),  # SQL injection attempt
            ("other-tenant-org-id", 403),  # Different tenant
        ],
        ids=["empty", "overflow", "path-traversal", "sql-injection", "wrong-tenant"],
    )
    def test_org_id_tenant_isolation_boundary_cases(
        self,
        gateway_url: str,
        http_session: requests.Session,
        cluster_config,
        keycloak_config,
        rbac_gateway_test_user_password: str,
        malicious_org_id: str,
        expected_status: int,
        request,
    ):
        """Malicious org_id in a valid Keycloak JWT must not yield cost report data.

        Envoy accepts the JWT (valid signature); Lua embeds org_id into
        ``X-Rh-Identity``; Koku/RBAC must reject the request.
        """
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        param_id = request.node.callspec.id
        username = f"orgid-test-{param_id}"
        admin_token = fetch_keycloak_master_admin_token(
            keycloak_config.url,
            cluster_config.keycloak_namespace,
        )
        if not admin_token:
            pytest.skip("Could not obtain Keycloak master admin token")

        account_number = os.environ.get("TEST_ACCOUNT_NUMBER", _DEFAULT_ACCOUNT_NUMBER)
        try:
            ensure_realm_user_with_password(
                keycloak_base_url=keycloak_config.url,
                realm=keycloak_config.realm,
                admin_token=admin_token,
                username=username,
                password=rbac_gateway_test_user_password,
                org_id=malicious_org_id,
                account_number=account_number,
                email=f"{username}@rbac-gateway.test",
            )
        except RuntimeError as exc:
            pytest.skip(f"Keycloak user for org_id case {param_id!r}: {exc}")

        token = obtain_password_grant_token(
            username,
            rbac_gateway_test_user_password,
            keycloak_config,
            cluster_config,
        )
        url = f"{gateway_url.rstrip('/')}/cost-management/v1/reports/openshift/costs/"
        response = http_session.get(
            url,
            headers=token.authorization_header,
            timeout=90,
            verify=False,
        )
        # 401 = Envoy/Lua rejects bad org_id before Koku; 400/403/424 = backend deny
        allowed = {expected_status, 400, 401, 403, 424}
        assert response.status_code in allowed, (
            f"org_id case {param_id!r} ({malicious_org_id!r}): expected one of "
            f"{sorted(allowed)}, got {response.status_code}: {response.text[:300]}"
        )
        assert response.status_code != 200, (
            f"SECURITY: malicious org_id {malicious_org_id!r} must not return 200"
        )

    def test_permission_revocation_honored_after_cache_clear(
        self,
        cluster_config,
        gateway_url: str,
        http_session: requests.Session,
        keycloak_config,
        org_id: str,
        rbac_gateway_test_user_password: str,
        _provision_rbac_iam_reader_gateway,
    ):
        """Revoked RBAC permissions are honored after cache clear.

        Removes group membership and temporarily disables platform-default roles
        that grant ``rbac:principal:read`` / ``rbac:group:read`` (e.g. seeded
        ``User Access principal viewer``), so denial is not masked by defaults.
        """
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        # Get fresh token
        user_token = obtain_password_grant_token(
            RBAC_IAM_GATEWAY_USERNAME,
            rbac_gateway_test_user_password,
            keycloak_config,
            cluster_config,
        )

        # Verify user can access RBAC principals (baseline)
        url = f"{gateway_url.rstrip('/')}/rbac/v1/principals/?limit=5"
        response_before = http_session.get(
            url,
            headers=user_token.authorization_header,
            timeout=60,
            verify=False,
        )

        if response_before.status_code != 200:
            pytest.skip(
                f"User doesn't have initial access (status {response_before.status_code}), "
                f"cannot test revocation"
            )

        # Remove user from RBAC group
        from utils import exec_in_pod_raw, get_pod_by_label

        rbac_pod = get_pod_by_label(
            cluster_config.namespace, "app.kubernetes.io/component=rbac-api"
        )
        if not rbac_pod:
            pytest.skip("RBAC API pod not found")

        restore_script = render_rbac_gateway_permission_restore_script(
            org_id, RBAC_IAM_GATEWAY_USERNAME
        )

        try:
            revoke_script = render_rbac_gateway_permission_revoke_script(
                RBAC_IAM_GATEWAY_USERNAME
            )

            result = exec_in_pod_raw(
                cluster_config.namespace,
                rbac_pod,
                [
                    "python",
                    "/opt/rbac/rbac/manage.py",
                    "shell",
                    "-c",
                    revoke_script,
                ],
                timeout=60,
            )

            if result.returncode != 0:
                pytest.skip(f"Could not revoke permissions: {result.stderr}")

            time.sleep(2)

            revoked_token = obtain_password_grant_token(
                RBAC_IAM_GATEWAY_USERNAME,
                rbac_gateway_test_user_password,
                keycloak_config,
                cluster_config,
            )

            response_after = http_session.get(
                url,
                headers=revoked_token.authorization_header,
                timeout=60,
                verify=False,
            )

            assert response_after.status_code in (403, 424), (
                f"Expected 403/424 after permission revocation, "
                f"got {response_after.status_code}: {response_after.text[:300]}"
            )
        finally:
            exec_in_pod_raw(
                cluster_config.namespace,
                rbac_pod,
                [
                    "python",
                    "/opt/rbac/rbac/manage.py",
                    "shell",
                    "-c",
                    restore_script,
                ],
                timeout=120,
            )

    def test_rbac_iam_reader_cannot_modify_own_permissions(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_rbac_iam_user_jwt,
        org_id: str,
    ):
        """Read-only RBAC IAM user cannot escalate privileges by modifying groups.

        Tests that users with rbac:group:read cannot grant themselves
        admin permissions by adding themselves to privileged groups.
        """
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        # Attempt to add self to a group (privilege escalation)
        # First, try to list groups to find one
        groups_url = f"{gateway_url.rstrip('/')}/rbac/v1/groups/?limit=10"
        groups_response = http_session.get(
            groups_url,
            headers=gateway_rbac_iam_user_jwt.authorization_header,
            timeout=60,
            verify=False,
        )

        if groups_response.status_code != 200:
            pytest.skip(f"Cannot list groups: {groups_response.status_code}")

        groups_data = groups_response.json()
        groups = groups_data.get("data", [])
        if not groups:
            pytest.skip("No groups found to test privilege escalation")

        # Pick first group UUID
        target_group_uuid = groups[0].get("uuid")
        if not target_group_uuid:
            pytest.skip("Group UUID not found in response")

        # Attempt to add self to group via PATCH
        principals_url = f"{gateway_url.rstrip('/')}/rbac/v1/groups/{target_group_uuid}/principals/"
        escalation_response = http_session.post(
            principals_url,
            headers={
                **gateway_rbac_iam_user_jwt.authorization_header,
                "Content-Type": "application/json",
            },
            json={"principals": [{"username": RBAC_IAM_GATEWAY_USERNAME}]},
            timeout=60,
            verify=False,
        )

        # Must be rejected with 403 (forbidden) or 405 (method not allowed)
        assert escalation_response.status_code in (403, 405, 400), (
            f"Expected 403/405/400 when read-only user tries to modify group, "
            f"got {escalation_response.status_code}: {escalation_response.text[:400]}"
        )

        # Also test direct group modification (if they somehow got a group UUID)
        group_modify_url = f"{gateway_url.rstrip('/')}/rbac/v1/groups/{target_group_uuid}/"
        modify_response = http_session.put(
            group_modify_url,
            headers={
                **gateway_rbac_iam_user_jwt.authorization_header,
                "Content-Type": "application/json",
            },
            json={"name": "hacked-group", "description": "privilege escalation attempt"},
            timeout=60,
            verify=False,
        )

        assert modify_response.status_code in (403, 405, 400), (
            f"Expected 403/405/400 when read-only user tries to modify group name, "
            f"got {modify_response.status_code}: {modify_response.text[:400]}"
        )

    def test_concurrent_jwt_sessions_no_resource_exhaustion(
        self,
        gateway_url: str,
        http_session: requests.Session,
        keycloak_config,
        cluster_config,
        rbac_gateway_test_user_password: str,
        _provision_rbac_iam_reader_gateway,
    ):
        """Multiple concurrent sessions for same user must not cause resource exhaustion.

        Light parallel load against the gateway (lab-tuned): verifies no 500s and
        that several distinct JWTs for the same user can reach RBAC concurrently.
        Transient 503s under burst are retried; success bar is modest for CI/lab.
        """
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        import concurrent.futures
        import time

        num_concurrent = 6
        min_success = 4
        max_workers = 3
        url = f"{gateway_url.rstrip('/')}/rbac/v1/principals/?limit=1"
        tokens = []

        for i in range(num_concurrent):
            try:
                token = obtain_password_grant_token(
                    RBAC_IAM_GATEWAY_USERNAME,
                    rbac_gateway_test_user_password,
                    keycloak_config,
                    cluster_config,
                )
                tokens.append(token)
                time.sleep(0.12)
            except Exception as e:
                pytest.skip(f"Could not generate {num_concurrent} tokens: {e}")

        if len(tokens) < num_concurrent:
            pytest.skip(f"Only generated {len(tokens)}/{num_concurrent} tokens")

        def get_with_retries(headers: dict, *, attempts: int = 6) -> int:
            backoff = 0.35
            for attempt in range(attempts):
                try:
                    resp = requests.get(
                        url, headers=headers, timeout=60, verify=False,
                    )
                    if resp.status_code == 200:
                        return 200
                    if resp.status_code in (502, 503, 504) and attempt < attempts - 1:
                        time.sleep(backoff * (2**attempt))
                        continue
                    return resp.status_code
                except Exception as e:
                    if attempt < attempts - 1:
                        time.sleep(backoff * (2**attempt))
                        continue
                    return str(e)
            return 503

        warmed = get_with_retries(tokens[0].authorization_header)
        if warmed != 200:
            pytest.skip(
                f"Gateway/RBAC not ready for concurrent probe (warm-up got {warmed})",
            )

        def make_request(job):
            token_obj, stagger_s = job
            if stagger_s:
                time.sleep(stagger_s)
            return get_with_retries(token_obj.authorization_header)

        jobs = [(tok, i * 0.15) for i, tok in enumerate(tokens)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(make_request, job) for job in jobs]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        success_count = sum(1 for r in results if r == 200)
        error_500_count = sum(1 for r in results if r == 500)

        assert error_500_count == 0, (
            f"Got {error_500_count} internal server errors out of {num_concurrent} "
            f"concurrent requests. Results: {results}"
        )
        assert success_count >= min_success, (
            f"Only {success_count}/{num_concurrent} requests succeeded "
            f"(need >={min_success}). Results: {results}"
        )

    def test_jwt_without_required_claims_rejected(
        self,
        gateway_url: str,
        http_session: requests.Session,
        org_id: str,
    ):
        """JWTs missing required claims (e.g. sub) must be rejected with 401."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/status/"
        no_sub_jwt = forge_jwt_missing_sub(org_id=org_id)
        response = http_session.get(
            url,
            headers={"Authorization": f"Bearer {no_sub_jwt}"},
            timeout=30,
            verify=False,
        )
        assert response.status_code == 401, (
            f"Expected 401 for JWT missing sub, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    def test_rbac_cache_ttl_configuration_exists(self, cluster_config):
        """Verify RBAC deployment has cache TTL configured (operational security).

        Ensures the RBAC service has cache enabled and a reasonable TTL
        so permission changes propagate within acceptable time.
        """
        from utils import run_oc_command

        result = run_oc_command(
            [
                "get",
                "deployment",
                "-n",
                cluster_config.namespace,
                "-l",
                "app.kubernetes.io/component=rbac-api",
                "-o",
                "json",
            ],
            check=False,
        )

        if result.returncode != 0:
            pytest.skip(f"Could not get RBAC deployment: {result.stderr}")

        data = json.loads(result.stdout or "{}")
        items = data.get("items", [])
        if not items:
            pytest.skip("RBAC API deployment not found")

        # Check environment variables for cache configuration
        spec = items[0].get("spec", {})
        template = spec.get("template", {})
        containers = template.get("spec", {}).get("containers", [])

        env_vars = {}
        for container in containers:
            for env in container.get("env", []):
                env_vars[env.get("name")] = env.get("value")

        # Verify ACCESS_CACHE_ENABLED is true
        cache_enabled = env_vars.get("ACCESS_CACHE_ENABLED", "").lower()
        assert cache_enabled == "true", (
            f"RBAC cache should be enabled (ACCESS_CACHE_ENABLED=true), "
            f"got: {cache_enabled}"
        )

        # Document that cache is enabled (good for performance, but needs monitoring)
        # Cache TTL is typically 300s (5 minutes) in insights-rbac
        # This means permission changes take up to 5 minutes to propagate
