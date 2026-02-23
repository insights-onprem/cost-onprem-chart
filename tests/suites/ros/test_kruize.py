"""
Kruize service tests.

Tests for the Kruize recommendation engine health and database connectivity.
Note: Recommendation generation is tested in suites/e2e/ as part of the complete pipeline.
"""

import pytest

from utils import check_pod_ready, exec_in_pod, execute_db_query


@pytest.mark.ros
@pytest.mark.component
class TestKruizeHealth:
    """Tests for Kruize service health."""

    @pytest.mark.smoke
    def test_kruize_pod_ready(self, cluster_config):
        """Verify Kruize pod is ready."""
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=ros-optimization"
        ), "Kruize pod is not ready"

    def test_kruize_health_endpoint(self, cluster_config, kruize_pod: str):
        """Verify Kruize health endpoint responds."""
        result = exec_in_pod(
            cluster_config.namespace,
            kruize_pod,
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8080/health"],
        )
        
        assert result is not None, "Could not reach Kruize health endpoint"
        assert result.strip() == "200", f"Kruize health check failed: {result}"


@pytest.mark.ros
@pytest.mark.component
class TestKruizeDatabase:
    """Tests for Kruize database connectivity."""

    def test_kruize_can_connect_to_database(
        self, cluster_config, database_config, kruize_credentials
    ):
        """Verify Kruize can connect to its database."""
        result = execute_db_query(
            database_config.namespace,
            database_config.pod_name,
            kruize_credentials["database"],
            kruize_credentials["user"],
            "SELECT 1",
            password=kruize_credentials["password"],
        )
        
        assert result is not None, "Kruize database connection failed"

    def test_kruize_tables_exist(
        self, cluster_config, database_config, kruize_credentials
    ):
        """Verify Kruize tables exist in database."""
        result = execute_db_query(
            database_config.namespace,
            database_config.pod_name,
            kruize_credentials["database"],
            kruize_credentials["user"],
            """
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name LIKE 'kruize_%'
            """,
            password=kruize_credentials["password"],
        )
        
        assert result is not None, "Could not query Kruize tables"
        tables = [row[0] for row in result]
        
        # Check for essential tables
        assert any("experiment" in t for t in tables), "Kruize experiments table not found"
        assert any("recommendation" in t for t in tables), "Kruize recommendations table not found"
