"""
Sources API removal validation tests.

Tests to verify that the standalone sources-api service has been removed
and its functionality is now provided by Koku.

These tests validate the infrastructure state after the sources-api removal.
"""

import pytest

from utils import check_service_exists, check_deployment_exists, run_oc_command


@pytest.mark.infrastructure
@pytest.mark.component
class TestSourcesApiRemoved:
    """Tests to verify sources-api infrastructure has been removed.

    The sources-api was a standalone Go service that has been replaced
    by Koku's built-in sources endpoints. These tests verify that the
    old infrastructure components no longer exist.
    """

    def test_sources_api_service_not_exists(self, cluster_config):
        """Verify the sources-api Kubernetes service does not exist.

        The old sources-api service exposed the Go-based sources service
        on port 8000. This service has been removed.
        """
        service_name = f"{cluster_config.helm_release_name}-sources-api"

        exists = check_service_exists(cluster_config.namespace, service_name)

        assert not exists, (
            f"sources-api service '{service_name}' still exists in namespace "
            f"'{cluster_config.namespace}'. This service should be removed as "
            "sources functionality is now provided by Koku."
        )

    def test_sources_api_deployment_not_exists(self, cluster_config):
        """Verify the sources-api deployment does not exist.

        The old sources-api deployment ran the Go-based sources service.
        This deployment has been removed.
        """
        deployment_name = f"{cluster_config.helm_release_name}-sources-api"

        exists = check_deployment_exists(cluster_config.namespace, deployment_name)

        assert not exists, (
            f"sources-api deployment '{deployment_name}' still exists in namespace "
            f"'{cluster_config.namespace}'. This deployment should be removed."
        )

    def test_sources_listener_deployment_not_exists(self, cluster_config):
        """Verify the sources-listener deployment does not exist.

        The old sources-listener was a Kafka consumer that processed
        source events. This functionality is now handled by Koku's listener.
        """
        deployment_name = f"{cluster_config.helm_release_name}-sources-listener"

        exists = check_deployment_exists(cluster_config.namespace, deployment_name)

        assert not exists, (
            f"sources-listener deployment '{deployment_name}' still exists in namespace "
            f"'{cluster_config.namespace}'. This deployment should be removed."
        )

    def test_sources_database_not_exists(self, cluster_config):
        """Verify no separate sources database exists.

        The old sources-api used its own database (sources_db).
        This has been replaced by tables within the Koku database.
        """
        # Check for sources-specific database service
        service_patterns = [
            f"{cluster_config.helm_release_name}-sources-db",
            f"{cluster_config.helm_release_name}-sources-database",
        ]

        for service_name in service_patterns:
            exists = check_service_exists(cluster_config.namespace, service_name)
            assert not exists, (
                f"sources database service '{service_name}' still exists. "
                "Sources data should be stored in the main Koku database."
            )


@pytest.mark.infrastructure
@pytest.mark.component
class TestKokuSourcesIntegration:
    """Tests to verify Koku provides sources functionality."""

    def test_koku_api_provides_sources_endpoint(self, cluster_config):
        """Verify Koku API service exists and provides sources endpoints.

        After sources-api removal, Koku's API should serve all sources
        endpoints at /api/cost-management/v1/.
        """
        # Check that Koku API services exist
        for suffix in ["koku-api-reads", "koku-api-writes"]:
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


