"""
Opt-in access model E2E tests (S-14).

Validates that the multi-workspace, group-based opt-in authorization model
enforces correct access boundaries for three restricted users (test1, test2,
test3) whose permissions derive from group membership and direct workspace
bindings.

The scenario is bootstrapped by ``kessel-admin.sh demo <org_id>`` which
creates:

    Workspaces:  ws-demo, ws-infra, ws-payment, ws-test1
    Groups:      demo (test1), infra (test2), payment (test3)
    Clusters:    cluster-a, cluster-b, cluster-c

    Expected verification matrix (from ADR):
    +---------------+-------+-------------------+-------------------+---------------------+
    | Resource      | admin | test1             | test2             | test3               |
    +---------------+-------+-------------------+-------------------+---------------------+
    | cluster-a     | read  | read  (ws-demo)   | read  (ws-infra)  | DENIED              |
    | cluster-b     | read  | read  (ws-test1)  | DENIED            | read  (has_project) |
    | cluster-c     | read  | DENIED            | read  (ws-infra)  | read  (has_project) |
    | ns demo-a     | read  | read  (ws-demo)   | read  (ws-infra)  | DENIED              |
    | ns demo-b     | read  | read  (ws-test1)  | DENIED            | DENIED              |
    | ns payment-b  | read  | read  (ws-test1)  | DENIED            | read  (ws-payment)  |
    | ns test-c     | read  | DENIED            | read  (ws-infra)  | DENIED              |
    | ns payment-c  | read  | DENIED            | read  (ws-infra)  | read  (ws-payment)  |
    +---------------+-------+-------------------+-------------------+---------------------+

Without NISE-ingested cost data for clusters A/B/C, these tests verify at the
API status-code level: HTTP 200 means the user has *some* access to the
resource type (Kessel grants permission), while 403 means denial.  Data-scoped
assertions (test1 sees cluster-A rows but not cluster-C rows) require ingested
data and are deferred to a future phase.

Prerequisites (deployed state):
    - ``test1``, ``test2``, ``test3`` users in Keycloak (password = username)
    - ``kessel-admin.sh demo <org_id>`` has been run
    - Kessel (SpiceDB, Relations API, Inventory API) deployed and healthy
    - ``ENHANCED_ORG_ADMIN=False`` on the Koku deployment

Maps to test plan scenario: S-14.
"""

import os

import pytest
import requests

from conftest import obtain_user_token
from suites.e2e.conftest import flush_valkey


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def flush_cache_before_suite(cluster_config):
    """Ensure a clean Valkey cache so Kessel access is freshly evaluated."""
    flush_valkey(cluster_config.namespace)


def _auth_header(keycloak_config, ui_client, username: str) -> dict:
    """Build an Authorization header for the given user (password = username)."""
    client_id, client_secret = ui_client
    token = obtain_user_token(
        keycloak_config.url,
        keycloak_config.realm,
        client_id,
        client_secret,
        username=username,
        password=username,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def test1_auth(keycloak_config, ui_client) -> dict:
    """Authorization header for ``test1`` (group demo → ws-demo + direct ws-test1)."""
    return _auth_header(
        keycloak_config, ui_client,
        os.environ.get("OPTIN_USER1", "test1"),
    )


@pytest.fixture(scope="module")
def test2_auth(keycloak_config, ui_client) -> dict:
    """Authorization header for ``test2`` (group infra → ws-infra)."""
    return _auth_header(
        keycloak_config, ui_client,
        os.environ.get("OPTIN_USER2", "test2"),
    )


@pytest.fixture(scope="module")
def test3_auth(keycloak_config, ui_client) -> dict:
    """Authorization header for ``test3`` (group payment → ws-payment)."""
    return _auth_header(
        keycloak_config, ui_client,
        os.environ.get("OPTIN_USER3", "test3"),
    )


@pytest.fixture(scope="module", autouse=True)
def _require_demo_bootstrap(gateway_url, test1_auth):
    """Skip the entire module if the opt-in demo has not been bootstrapped.

    Checks that test1 gets HTTP 200 on the OCP reports endpoint, which
    confirms the demo workspace/group/binding tuples exist in SpiceDB.
    """
    with requests.Session() as session:
        session.verify = False
        resp = session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=test1_auth,
            timeout=30,
        )
    if resp.status_code == 403:
        pytest.skip(
            "Opt-in demo not bootstrapped — test1 has no OCP access. "
            "Run: kessel-admin.sh demo <org_id>"
        )


# =============================================================================
# Test Classes — Individual User Access
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
class TestTest1Access:
    """test1: member of group demo (ws-demo) + direct binding on ws-test1.

    Expected access:
        cluster-a   (ws-demo)    — read
        cluster-b   (ws-test1)   — read
        cluster-c                — DENIED
        demo-a      (ws-demo)    — read
        demo-b      (ws-test1)   — read
        payment-b   (ws-test1)   — read
        test-c                   — DENIED
        payment-c                — DENIED
    """

    def test_ocp_reports_allowed(
        self, gateway_url, test1_auth, http_session,
    ):
        """test1 has OCP read via ws-demo + ws-test1 → 200."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=test1_auth, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_aws_reports_denied(
        self, gateway_url, test1_auth, http_session,
    ):
        """test1 has no AWS roles → 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/aws/costs/",
            headers=test1_auth, timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_cost_models_denied(
        self, gateway_url, test1_auth, http_session,
    ):
        """test1 has no cost-model read → 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=test1_auth, timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_sources_empty(
        self, gateway_url, test1_auth, http_session,
    ):
        """test1 has no integration:read → 0 sources."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=test1_auth, timeout=30,
        )
        assert resp.status_code == 200
        assert resp.json()["meta"]["count"] == 0, (
            f"test1 should see 0 sources, got {resp.json()['meta']['count']}"
        )

    def test_user_access_has_ocp_permissions(
        self, gateway_url, test1_auth, http_session,
    ):
        """test1 /user-access/ reflects partial (OCP) permissions."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/user-access/?type=any",
            headers=test1_auth, timeout=30,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("data") is True or body.get("access") is True, (
            f"test1 should have some access, got {body}"
        )

    def test_recommendations_allowed(
        self, gateway_url, test1_auth, http_session,
    ):
        """test1 has OCP read → recommendations accessible."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=test1_auth, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )


@pytest.mark.e2e
@pytest.mark.kessel
class TestTest2Access:
    """test2: member of group infra (ws-infra).

    Expected access:
        cluster-a   (ws-infra)   — read
        cluster-b                — DENIED
        cluster-c   (ws-infra)   — read
        demo-a      (ws-infra)   — read
        demo-b                   — DENIED
        payment-b                — DENIED
        test-c      (ws-infra)   — read
        payment-c   (ws-infra)   — read
    """

    def test_ocp_reports_allowed(
        self, gateway_url, test2_auth, http_session,
    ):
        """test2 has OCP read via ws-infra → 200."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=test2_auth, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_aws_reports_denied(
        self, gateway_url, test2_auth, http_session,
    ):
        """test2 has no AWS roles → 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/aws/costs/",
            headers=test2_auth, timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_cost_models_denied(
        self, gateway_url, test2_auth, http_session,
    ):
        """test2 has no cost-model read → 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=test2_auth, timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_sources_empty(
        self, gateway_url, test2_auth, http_session,
    ):
        """test2 has no integration:read → 0 sources."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=test2_auth, timeout=30,
        )
        assert resp.status_code == 200
        assert resp.json()["meta"]["count"] == 0, (
            f"test2 should see 0 sources, got {resp.json()['meta']['count']}"
        )

    def test_user_access_has_ocp_permissions(
        self, gateway_url, test2_auth, http_session,
    ):
        """test2 /user-access/ reflects partial (OCP) permissions."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/user-access/?type=any",
            headers=test2_auth, timeout=30,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("data") is True or body.get("access") is True, (
            f"test2 should have some access, got {body}"
        )

    def test_recommendations_allowed(
        self, gateway_url, test2_auth, http_session,
    ):
        """test2 has OCP read → recommendations accessible."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=test2_auth, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )


@pytest.mark.e2e
@pytest.mark.kessel
class TestTest3Access:
    """test3: member of group payment (ws-payment).

    Expected access:
        cluster-a                      — DENIED
        cluster-b   (has_project)      — read  (via payment-b namespace)
        cluster-c   (has_project)      — read  (via payment-c namespace)
        demo-a                         — DENIED
        demo-b                         — DENIED
        payment-b   (ws-payment)       — read
        test-c                         — DENIED
        payment-c   (ws-payment)       — read
    """

    def test_ocp_reports_allowed(
        self, gateway_url, test3_auth, http_session,
    ):
        """test3 has OCP read via ws-payment (namespace-scoped) → 200."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            headers=test3_auth, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_aws_reports_denied(
        self, gateway_url, test3_auth, http_session,
    ):
        """test3 has no AWS roles → 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/reports/aws/costs/",
            headers=test3_auth, timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_cost_models_denied(
        self, gateway_url, test3_auth, http_session,
    ):
        """test3 has no cost-model read → 403."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/cost-models/",
            headers=test3_auth, timeout=30,
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text[:300]}"
        )

    def test_sources_empty(
        self, gateway_url, test3_auth, http_session,
    ):
        """test3 has no integration:read → 0 sources."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            headers=test3_auth, timeout=30,
        )
        assert resp.status_code == 200
        assert resp.json()["meta"]["count"] == 0, (
            f"test3 should see 0 sources, got {resp.json()['meta']['count']}"
        )

    def test_user_access_has_ocp_permissions(
        self, gateway_url, test3_auth, http_session,
    ):
        """test3 /user-access/ reflects partial (OCP, namespace-scoped) permissions."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/user-access/?type=any",
            headers=test3_auth, timeout=30,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("data") is True or body.get("access") is True, (
            f"test3 should have some access, got {body}"
        )

    def test_recommendations_allowed(
        self, gateway_url, test3_auth, http_session,
    ):
        """test3 has OCP read (namespace-scoped) → recommendations accessible."""
        resp = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers=test3_auth, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        )


# =============================================================================
# Cross-User Boundary Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.kessel
class TestCrossBoundaries:
    """Pairwise comparisons confirming opt-in isolation between users.

    All three users are cost-openshift-viewer bound to different workspaces.
    They share the same deny profile for non-OCP endpoints (AWS, cost-models,
    sources) but should each resolve a different set of OCP resources once
    data-level assertions are added.
    """

    def test_all_denied_aws(
        self, gateway_url, test1_auth, test2_auth, test3_auth, http_session,
    ):
        """All opt-in users are denied AWS reports (no aws_account roles)."""
        for label, auth in [
            ("test1", test1_auth), ("test2", test2_auth), ("test3", test3_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/reports/aws/costs/",
                headers=auth, timeout=30,
            )
            assert resp.status_code == 403, (
                f"{label}: expected 403 on AWS, got {resp.status_code}"
            )

    def test_all_denied_cost_models(
        self, gateway_url, test1_auth, test2_auth, test3_auth, http_session,
    ):
        """All opt-in users are denied cost-models (no cost_model roles)."""
        for label, auth in [
            ("test1", test1_auth), ("test2", test2_auth), ("test3", test3_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/cost-models/",
                headers=auth, timeout=30,
            )
            assert resp.status_code == 403, (
                f"{label}: expected 403 on cost-models, got {resp.status_code}"
            )

    def test_all_see_zero_sources(
        self, gateway_url, test1_auth, test2_auth, test3_auth, http_session,
    ):
        """All opt-in users see 0 sources (no integration:read)."""
        for label, auth in [
            ("test1", test1_auth), ("test2", test2_auth), ("test3", test3_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/sources/",
                headers=auth, timeout=30,
            )
            assert resp.status_code == 200
            assert resp.json()["meta"]["count"] == 0, (
                f"{label}: expected 0 sources, got {resp.json()['meta']['count']}"
            )

    def test_all_allowed_ocp_reports(
        self, gateway_url, test1_auth, test2_auth, test3_auth, http_session,
    ):
        """All opt-in users can access OCP reports (each via different workspace)."""
        for label, auth in [
            ("test1", test1_auth), ("test2", test2_auth), ("test3", test3_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
                headers=auth, timeout=30,
            )
            assert resp.status_code == 200, (
                f"{label}: expected 200 on OCP reports, got {resp.status_code}"
            )

    def test_all_have_user_access(
        self, gateway_url, test1_auth, test2_auth, test3_auth, http_session,
    ):
        """All opt-in users report having some access via /user-access/."""
        for label, auth in [
            ("test1", test1_auth), ("test2", test2_auth), ("test3", test3_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/user-access/?type=any",
                headers=auth, timeout=30,
            )
            assert resp.status_code == 200, (
                f"{label}: expected 200 on user-access, got {resp.status_code}"
            )
            body = resp.json()
            assert body.get("data") is True or body.get("access") is True, (
                f"{label}: expected access=true, got {body}"
            )

    def test_all_allowed_recommendations(
        self, gateway_url, test1_auth, test2_auth, test3_auth, http_session,
    ):
        """All opt-in users can access recommendations (all have OCP read)."""
        for label, auth in [
            ("test1", test1_auth), ("test2", test2_auth), ("test3", test3_auth),
        ]:
            resp = http_session.get(
                f"{gateway_url}/cost-management/v1/recommendations/openshift",
                headers=auth, timeout=30,
            )
            assert resp.status_code == 200, (
                f"{label}: expected 200 on recommendations, got {resp.status_code}"
            )
