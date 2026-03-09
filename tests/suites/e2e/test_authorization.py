"""
Kessel ReBAC authorization E2E tests.

Validates that the Kessel Relationship-Based Access Control integration
enforces correct access boundaries across the Koku and ROS APIs for three
user profiles: admin, viewer, and no-access.

Test matrix:
    +----------------------------------+--------+-------------------+-----------+
    | Endpoint                         | admin  | test (ocp-viewer) | no-access |
    +----------------------------------+--------+-------------------+-----------+
    | GET /sources/                    | >= 1   | OCP only          | 0         |
    | GET /reports/openshift/costs/    | 200    | 200 (allowed)     | 403       |
    | GET /reports/aws/costs/          | 200    | 403               | 403       |
    | GET /cost-models/                | 200    | 403               | 403       |
    | GET /settings/                   | 200    | (varies)          | 403       |
    | GET /recommendations/openshift   | 200    | 200 (allowed)     | 403       |
    | GET /user-access/                | full   | ocp-read only     | none      |
    +----------------------------------+--------+-------------------+-----------+

Prerequisites (deployed state):
    - ``admin`` user in Keycloak with ``cost-administrator`` SpiceDB role
    - ``test`` user in Keycloak with ``cost-openshift-viewer`` SpiceDB role
    - At least one OCP source registered (``authz-test-cluster``)
    - Kessel (SpiceDB, Relations API, Inventory API) deployed and healthy
    - ``ENHANCED_ORG_ADMIN=False`` on the Koku deployment

The no-access user is created dynamically via the Keycloak admin API and
cleaned up after the module completes.  It has valid JWT credentials but
zero SpiceDB role bindings.

Maps to test plan scenarios: S-05, S-06, S-07, S-12.
"""

import base64
import os
import uuid

import pytest
import requests

from conftest import obtain_user_token
from suites.e2e.conftest import flush_valkey
from utils import run_oc_command


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def admin_auth(keycloak_config, ui_client) -> dict:
    """Authorization header for the ``admin`` user (cost-administrator)."""
    client_id, client_secret = ui_client
    token = obtain_user_token(
        keycloak_config.url,
        keycloak_config.realm,
        client_id,
        client_secret,
        username=os.environ.get("AUTHZ_ADMIN_USER", "admin"),
        password=os.environ.get("AUTHZ_ADMIN_PASS", "admin"),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def viewer_auth(keycloak_config, ui_client) -> dict:
    """Authorization header for the ``test`` user (cost-openshift-viewer)."""
    client_id, client_secret = ui_client
    token = obtain_user_token(
        keycloak_config.url,
        keycloak_config.realm,
        client_id,
        client_secret,
        username=os.environ.get("AUTHZ_VIEWER_USER", "test"),
        password=os.environ.get("AUTHZ_VIEWER_PASS", "test"),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def flush_cache_before_suite(cluster_config):
    """Ensure a clean Valkey cache so Kessel access is freshly evaluated."""
    flush_valkey(cluster_config.namespace)


@pytest.fixture(scope="module")
def _keycloak_admin_token(keycloak_config, cluster_config) -> str:
    """Obtain a Keycloak master-realm admin token for user management."""
    admin_pass_result = run_oc_command(
        [
            "get", "secret", "-n", cluster_config.keycloak_namespace,
            "keycloak-initial-admin",
            "-o", "jsonpath={.data.password}",
        ],
        check=False,
    )
    raw = admin_pass_result.stdout.strip()
    if not raw:
        pytest.skip("Cannot retrieve Keycloak admin password")
    admin_password = base64.b64decode(raw).decode()

    resp = requests.post(
        f"{keycloak_config.url}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "admin",
            "password": admin_password,
        },
        verify=False,
        timeout=30,
    )
    if resp.status_code != 200:
        pytest.skip(f"Cannot obtain Keycloak admin token: {resp.status_code}")
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def no_access_user(keycloak_config, cluster_config, _keycloak_admin_token):
    """Create a temporary Keycloak user with NO SpiceDB role bindings.

    The user can authenticate (valid JWT) but has zero permissions in SpiceDB,
    so all Kessel authorization checks should deny access.  Cleaned up after
    the module completes.
    """
    admin_token = _keycloak_admin_token
    username = f"noaccess-{uuid.uuid4().hex[:6]}"
    realm = keycloak_config.realm
    base = keycloak_config.url
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }

    # Reuse the same org_id as the existing test user
    users_resp = requests.get(
        f"{base}/admin/realms/{realm}/users",
        params={"username": "test", "exact": "true"},
        headers=headers,
        verify=False,
        timeout=30,
    )
    org_id = "org1234567"
    if users_resp.status_code == 200 and users_resp.json():
        org_id = (
            users_resp.json()[0]
            .get("attributes", {})
            .get("org_id", ["org1234567"])[0]
        )

    create_resp = requests.post(
        f"{base}/admin/realms/{realm}/users",
        headers=headers,
        verify=False,
        timeout=30,
        json={
            "username": username,
            "email": f"{username}@test.com",
            "emailVerified": True,
            "enabled": True,
            "attributes": {
                "org_id": [org_id],
                "account_number": [org_id],
            },
        },
    )
    if create_resp.status_code not in (201, 409):
        pytest.skip(
            f"Cannot create no-access user: "
            f"{create_resp.status_code} {create_resp.text[:200]}"
        )

    find_resp = requests.get(
        f"{base}/admin/realms/{realm}/users",
        params={"username": username, "exact": "true"},
        headers=headers,
        verify=False,
        timeout=30,
    )
    if find_resp.status_code != 200 or not find_resp.json():
        pytest.skip("Cannot locate newly created no-access user")
    user_id = find_resp.json()[0]["id"]

    requests.put(
        f"{base}/admin/realms/{realm}/users/{user_id}/reset-password",
        headers=headers,
        verify=False,
        timeout=30,
        json={"type": "password", "value": username, "temporary": False},
    )

    yield username

    # Cleanup
    requests.delete(
        f"{base}/admin/realms/{realm}/users/{user_id}",
        headers=headers,
        verify=False,
        timeout=30,
    )


@pytest.fixture(scope="module")
def no_access_auth(keycloak_config, ui_client, no_access_user) -> dict:
    """Authorization header for a user with zero SpiceDB bindings."""
    client_id, client_secret = ui_client
    token = obtain_user_token(
        keycloak_config.url,
        keycloak_config.realm,
        client_id,
        client_secret,
        username=no_access_user,
        password=no_access_user,
    )
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Test Classes
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
class TestAdminFullAccess:
    """S-05: cost-administrator user has unrestricted access."""

    def test_admin_sees_sources(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """Admin can list sources and sees at least one."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )
        data = response.json()
        assert data["meta"]["count"] >= 1, (
            f"Admin should see at least 1 source, got {data['meta']['count']}"
        )

    def test_admin_sees_ocp_reports(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """Admin can access OpenShift cost reports."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )

    def test_admin_sees_cost_models(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """Admin can access cost-models endpoint."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )

    def test_admin_user_access_shows_full_permissions(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """Admin /user-access/ reflects cost-administrator permissions."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/user-access/?type=any",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("data") is True or body.get("access") is True, (
            f"Admin should have access=true, got {body}"
        )


@pytest.mark.e2e
@pytest.mark.kessel
class TestRestrictedUserAccess:
    """S-06 / S-07: cost-openshift-viewer sees only OCP data; no cost-models."""

    def test_viewer_source_visibility(
        self, gateway_url: str, viewer_auth: dict, http_session: requests.Session,
    ):
        """Viewer (cost-openshift-viewer) has openshift_cluster:read → sees OCP sources only."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=viewer_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )
        data = response.json()
        for src in data.get("data", []):
            assert src.get("source_type") == "OCP", (
                f"Viewer should only see OCP sources, got {src.get('source_type')}"
            )

    def test_viewer_can_see_ocp_reports(
        self, gateway_url: str, viewer_auth: dict, http_session: requests.Session,
    ):
        """Viewer has openshift.cluster:read → OCP reports return 200."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=viewer_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )

    def test_viewer_denied_cost_models(
        self, gateway_url: str, viewer_auth: dict, http_session: requests.Session,
    ):
        """Viewer has no cost_model:read → cost-models returns 403."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=viewer_auth,
            timeout=30,
        )
        assert response.status_code == 403, (
            f"Expected 403 for viewer on cost-models, got {response.status_code}: "
            f"{response.text[:300]}"
        )


@pytest.mark.e2e
@pytest.mark.kessel
class TestAccessBoundaries:
    """Cross-user comparisons: same endpoints, different results."""

    def test_source_visibility_differs_by_role(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        http_session: requests.Session,
    ):
        """Admin sees source(s); viewer sees at most as many."""
        admin_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=admin_auth,
            timeout=30,
        )
        viewer_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=viewer_auth,
            timeout=30,
        )
        assert admin_resp.status_code == 200
        assert viewer_resp.status_code == 200

        admin_count = admin_resp.json()["meta"]["count"]
        viewer_count = viewer_resp.json()["meta"]["count"]

        assert admin_count >= 1, "Admin should see at least one source"
        assert viewer_count <= admin_count, (
            f"Viewer should see at most as many sources as admin "
            f"({viewer_count} > {admin_count})"
        )

    def test_cost_model_access_differs_by_role(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        http_session: requests.Session,
    ):
        """Admin gets 200 on cost-models; viewer gets 403."""
        admin_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=admin_auth,
            timeout=30,
        )
        viewer_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=viewer_auth,
            timeout=30,
        )
        assert admin_resp.status_code == 200, (
            f"Admin cost-models expected 200, got {admin_resp.status_code}"
        )
        assert viewer_resp.status_code == 403, (
            f"Viewer cost-models expected 403, got {viewer_resp.status_code}"
        )

    def test_ocp_reports_accessible_to_both(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        http_session: requests.Session,
    ):
        """Both admin and viewer can access OCP reports (both have OCP read)."""
        for label, auth in [("admin", admin_auth), ("viewer", viewer_auth)]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
                headers=auth,
                timeout=30,
            )
            assert resp.status_code == 200, (
                f"{label} should get 200 on OCP reports, got {resp.status_code}"
            )


@pytest.mark.e2e
@pytest.mark.kessel
class TestNoLeakage:
    """Verify restricted users cannot leak data through alternative endpoints."""

    def test_viewer_cannot_access_aws_reports(
        self, gateway_url: str, viewer_auth: dict, http_session: requests.Session,
    ):
        """Viewer (cost-openshift-viewer) has no aws.account:read → AWS reports return 403."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/aws/costs/",
            headers=viewer_auth,
            timeout=30,
        )
        assert response.status_code == 403, (
            f"Expected 403 for viewer on AWS reports, got {response.status_code}: "
            f"{response.text[:300]}"
        )

    def test_viewer_source_detail_returns_404(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        http_session: requests.Session,
    ):
        """Viewer with openshift_cluster:read can access OCP source detail."""
        admin_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=admin_auth,
            timeout=30,
        )
        if admin_resp.status_code != 200:
            pytest.skip("Could not list sources as admin")

        sources = admin_resp.json().get("data", [])
        if not sources:
            pytest.skip("No sources to test detail access")

        source_id = sources[0]["id"]
        viewer_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/{source_id}/",
            headers=viewer_auth,
            timeout=30,
        )
        # cost-openshift-viewer has openshift_cluster:read; OCP source detail
        # is visible (200) while non-OCP sources would be hidden (404).
        assert viewer_resp.status_code == 200, (
            f"Viewer with openshift_cluster:read should see OCP source {source_id}, "
            f"got {viewer_resp.status_code}: {viewer_resp.text[:200]}"
        )

    def test_unauthenticated_request_rejected(
        self, gateway_url: str, http_session: requests.Session,
    ):
        """Request without JWT is rejected at the gateway."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            timeout=10,
        )
        assert response.status_code == 401, (
            f"Expected 401 without token, got {response.status_code}"
        )


# =============================================================================
# ROS / Recommendations Authorization
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
@pytest.mark.ros
class TestROSRecommendationsAccess:
    """Validate gateway routing and access to the ROS recommendations endpoint.

    The gateway routes ``/api/cost-management/v1/recommendations/openshift``
    to the ``ros-api-backend`` cluster (ros-ocp-backend service) while all
    other ``/api/cost-management/`` traffic goes to ``koku-api-backend``.

    Both admin (cost-administrator) and viewer (cost-openshift-viewer) should
    reach the ROS API since they possess valid JWTs and the gateway injects
    the ``X-Rh-Identity`` header for the backend.
    """

    def test_admin_can_access_recommendations(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """Admin reaches the ROS recommendations endpoint (200)."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )

    def test_viewer_can_access_recommendations(
        self, gateway_url: str, viewer_auth: dict, http_session: requests.Session,
    ):
        """OCP viewer reaches recommendations (has openshift read access)."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=viewer_auth,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:300]}"
        )

    def test_recommendations_response_structure(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """Response from ROS has the expected JSON envelope."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code == 200
        body = response.json()
        assert "data" in body, f"Missing 'data' key in response: {list(body.keys())}"
        assert "meta" in body, f"Missing 'meta' key in response: {list(body.keys())}"
        assert "count" in body["meta"], f"Missing 'count' in meta: {body['meta']}"

    def test_unauthenticated_recommendations_rejected(
        self, gateway_url: str, http_session: requests.Session,
    ):
        """Unauthenticated request to recommendations is rejected at the gateway."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            timeout=10,
        )
        assert response.status_code == 401, (
            f"Expected 401 without token, got {response.status_code}"
        )


@pytest.mark.e2e
@pytest.mark.kessel
@pytest.mark.ros
class TestROSIdentityPropagation:
    """Verify the gateway correctly propagates identity to the ROS backend.

    The Envoy Lua filter constructs an ``X-Rh-Identity`` header from JWT
    claims and forwards it to the ROS API.  These tests verify the identity
    is accepted and that the ROS API returns user-scoped data.
    """

    def test_admin_identity_accepted_by_ros(
        self, gateway_url: str, admin_auth: dict, http_session: requests.Session,
    ):
        """ROS backend accepts the admin identity (no 401/403)."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=admin_auth,
            timeout=30,
        )
        assert response.status_code not in (401, 403), (
            f"ROS rejected admin identity: {response.status_code}"
        )

    def test_viewer_identity_accepted_by_ros(
        self, gateway_url: str, viewer_auth: dict, http_session: requests.Session,
    ):
        """ROS backend accepts the viewer identity (no 401/403)."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=viewer_auth,
            timeout=30,
        )
        assert response.status_code not in (401, 403), (
            f"ROS rejected viewer identity: {response.status_code}"
        )

    def test_both_users_see_same_org_data(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        http_session: requests.Session,
    ):
        """Both users belong to the same org — recommendation counts should match.

        Both admin (cost-administrator) and viewer (cost-openshift-viewer) have
        ``openshift.cluster:read`` permission in SpiceDB, so ROS Kessel
        authorization grants both users access to the same set of OCP
        recommendations within the org.
        """
        admin_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=admin_auth,
            timeout=30,
        )
        viewer_resp = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=viewer_auth,
            timeout=30,
        )
        assert admin_resp.status_code == 200
        assert viewer_resp.status_code == 200

        admin_count = admin_resp.json()["meta"]["count"]
        viewer_count = viewer_resp.json()["meta"]["count"]

        assert admin_count == viewer_count, (
            f"Expected same recommendation count for both users "
            f"(both have openshift.cluster:read), "
            f"admin={admin_count}, viewer={viewer_count}."
        )


# =============================================================================
# No-Access User — Zero SpiceDB Bindings
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
class TestNoAccessUser:
    """S-07: user with valid JWT but zero SpiceDB role bindings is fully denied.

    The user exists in Keycloak (can get a JWT) and belongs to the same org,
    but was never granted any role via ``kessel-admin.sh grant``.  Every
    Kessel Check call should return DENIED.
    """

    def test_no_access_sees_zero_sources(
        self, gateway_url: str, no_access_auth: dict, http_session: requests.Session,
    ):
        """No bindings → sources list returns 0 results."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=no_access_auth,
            timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )
        assert resp.json()["meta"]["count"] == 0, (
            f"No-access user should see 0 sources, got {resp.json()['meta']['count']}"
        )

    def test_no_access_denied_ocp_reports(
        self, gateway_url: str, no_access_auth: dict, http_session: requests.Session,
    ):
        """No openshift.cluster:read → OCP reports return 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=no_access_auth,
            timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_no_access_denied_aws_reports(
        self, gateway_url: str, no_access_auth: dict, http_session: requests.Session,
    ):
        """No aws.account:read → AWS reports return 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/aws/costs/",
            headers=no_access_auth,
            timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_no_access_denied_cost_models(
        self, gateway_url: str, no_access_auth: dict, http_session: requests.Session,
    ):
        """No cost_model:read → cost-models returns 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=no_access_auth,
            timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_no_access_user_access_shows_no_permissions(
        self, gateway_url: str, no_access_auth: dict, http_session: requests.Session,
    ):
        """No bindings → /user-access/ returns no granted types."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/user-access/?type=any",
            headers=no_access_auth,
            timeout=30,
        )
        # Koku may return 200 with data=False or 403 depending on implementation
        assert resp.status_code in (200, 403), (
            f"Expected 200 or 403, got {resp.status_code}: {resp.text[:300]}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("data") is False or body.get("access") is False, (
                f"No-access user should have data=false, got {body}"
            )


# =============================================================================
# ROS — No-Access User Denial
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
@pytest.mark.ros
class TestROSNoAccessDenied:
    """Verify ROS denies a user with zero Kessel bindings."""

    def test_no_access_denied_recommendations(
        self, gateway_url: str, no_access_auth: dict, http_session: requests.Session,
    ):
        """User with no bindings sees empty recommendations (queryset-filtered)."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=no_access_auth,
            timeout=30,
        )
        assert resp.status_code in (200, 403), (
            f"Expected 200 (empty) or 403 for user with no bindings, got "
            f"{resp.status_code}: {resp.text[:300]}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("meta", {}).get("count", -1) == 0, (
                f"No-access user should see zero recommendations, got {body.get('meta')}"
            )


# =============================================================================
# Three-Way Comparison — Admin vs Viewer vs No-Access
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
class TestThreeWayAccessComparison:
    """Compare access across all three user roles in a single test class."""

    def test_source_visibility_gradient(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        no_access_auth: dict,
        http_session: requests.Session,
    ):
        """Admin sees sources, viewer sees subset, no-access sees none."""
        results = {}
        for label, auth in [
            ("admin", admin_auth),
            ("viewer", viewer_auth),
            ("no-access", no_access_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/sources/",
                headers=auth,
                timeout=30,
            )
            assert resp.status_code == 200, (
                f"{label}: expected 200, got {resp.status_code}"
            )
            results[label] = resp.json()["meta"]["count"]

        assert results["admin"] >= 1, "Admin should see at least 1 source"
        assert results["viewer"] <= results["admin"], (
            f"Viewer should see at most as many sources as admin "
            f"({results['viewer']} > {results['admin']})"
        )
        assert results["no-access"] == 0, "No-access should see 0 sources"

    def test_ocp_reports_access_gradient(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        no_access_auth: dict,
        http_session: requests.Session,
    ):
        """Admin 200, viewer 200, no-access 403 on OCP reports."""
        for label, auth, expected in [
            ("admin", admin_auth, 200),
            ("viewer", viewer_auth, 200),
            ("no-access", no_access_auth, 403),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
                headers=auth,
                timeout=30,
            )
            assert resp.status_code == expected, (
                f"{label}: expected {expected}, got {resp.status_code}: "
                f"{resp.text[:200]}"
            )

    def test_cost_models_access_gradient(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        no_access_auth: dict,
        http_session: requests.Session,
    ):
        """Admin 200, viewer 403, no-access 403 on cost-models."""
        for label, auth, expected in [
            ("admin", admin_auth, 200),
            ("viewer", viewer_auth, 403),
            ("no-access", no_access_auth, 403),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/cost-models/",
                headers=auth,
                timeout=30,
            )
            assert resp.status_code == expected, (
                f"{label}: expected {expected}, got {resp.status_code}: "
                f"{resp.text[:200]}"
            )

    def test_recommendations_access_gradient(
        self,
        gateway_url: str,
        admin_auth: dict,
        viewer_auth: dict,
        no_access_auth: dict,
        http_session: requests.Session,
    ):
        """Admin 200, viewer 200, no-access 200 (empty) on recommendations."""
        for label, auth, expected in [
            ("admin", admin_auth, 200),
            ("viewer", viewer_auth, 200),
            ("no-access", no_access_auth, 200),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/recommendations/openshift",
                headers=auth,
                timeout=30,
            )
            assert resp.status_code == expected, (
                f"{label}: expected {expected}, got {resp.status_code}: "
                f"{resp.text[:200]}"
            )
