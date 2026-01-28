"""
Sources API tests.

Tests for the Sources API endpoints now served by Koku.
Note: Sources API has been merged into Koku. All sources endpoints are
available via /api/cost-management/v1/ using X-Rh-Identity header.

Source registration flow is tested in suites/e2e/ as part of the complete pipeline.
"""

import json

import pytest

from utils import exec_in_pod, check_pod_ready


@pytest.mark.cost_management
@pytest.mark.component
class TestKokuSourcesHealth:
    """Tests for Koku API health and sources endpoint availability."""

    @pytest.mark.smoke
    def test_koku_api_pod_ready(self, cluster_config):
        """Verify Koku API pod is ready (serves sources endpoints)."""
        # Koku API has separate read/write pods - check the writes pod for sources
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-management-api-writes"
        ), "Koku API (writes) pod is not ready"

    def test_koku_sources_endpoint_responds(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify Koku sources endpoint responds to requests."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                f"{koku_api_reads_url}/source_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )
        
        assert result is not None, "Could not reach Koku sources endpoint"
        assert result.strip() == "200", f"Koku sources endpoint returned {result}"


@pytest.mark.cost_management
@pytest.mark.integration
class TestSourceTypes:
    """Tests for source type configuration in Koku."""

    def test_openshift_source_type_exists(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify OpenShift source type is configured in Koku."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_reads_url}/source_types",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )
        
        assert result is not None, "Could not get source types from Koku"
        
        data = json.loads(result)
        source_types = [st.get("name") for st in data.get("data", [])]
        
        assert "openshift" in source_types, "OpenShift source type not found"

    def test_cost_management_app_type_exists(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify Cost Management application type is configured in Koku."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_reads_url}/application_types",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )
        
        assert result is not None, "Could not get application types"
        
        data = json.loads(result)
        app_types = [at.get("name") for at in data.get("data", [])]
        
        assert "/insights/platform/cost-management" in app_types, (
            "Cost Management application type not found"
        )
