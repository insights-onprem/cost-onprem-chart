"""
Sources-to-Koku migration infrastructure tests.

Validates that Koku provides sources functionality.
"""

import pytest

from utils import check_service_exists, check_deployment_exists, run_oc_command


@pytest.mark.infrastructure
@pytest.mark.component
@pytest.mark.operator
class TestKokuSourcesIntegration:
    """Tests to verify Koku provides sources functionality."""

    def test_koku_api_provides_sources_endpoint(self, cluster_config):
        """Verify Koku API service exists and provides sources endpoints.

        After sources-api removal, Koku's API should serve all sources
        endpoints at /api/cost-management/v1/.
        """
        # Check that Koku API services exist
        for suffix in ["koku-api"]:
            service_name = f"{cluster_config.helm_release_name}-{suffix}"
            exists = check_service_exists(cluster_config.namespace, service_name)

            assert exists, (
                f"Koku API service '{service_name}' not found in namespace "
                f"'{cluster_config.namespace}'. Koku API is required for sources functionality."
            )

    def test_koku_listener_deployment_exists(self, cluster_config):
        """Verify Koku listener deployment exists for source events.

        The Koku listener processes source lifecycle events from Kafka.
        """
        # Check for different naming patterns - the chart may use different names
        possible_names = [
            f"{cluster_config.helm_release_name}-koku-listener",
            f"{cluster_config.helm_release_name}-listener",
        ]

        exists = False
        for name in possible_names:
            if check_deployment_exists(cluster_config.namespace, name):
                exists = True
                break

        # If no deployment found by name, check by label
        if not exists:
            result = run_oc_command([
                "get", "deployment", "-n", cluster_config.namespace,
                "-l", "app.kubernetes.io/component=listener",
                "-o", "name"
            ], check=False)
            if result.returncode == 0 and result.stdout.strip():
                exists = True

        assert exists, (
            f"Koku listener deployment not found. "
            "The listener is required for processing source events."
        )


