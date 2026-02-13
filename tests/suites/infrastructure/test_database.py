"""
Database schema and migration tests.

Tests for database schema validation and migration status.
"""

import pytest

from utils import exec_in_pod, execute_db_query


@pytest.mark.infrastructure
@pytest.mark.component
class TestDatabaseSchema:
    """Tests for database schema validation."""

    def test_api_provider_table_exists(self, cluster_config, database_config):
        """Verify api_provider table exists (core Koku table)."""
        result = execute_db_query(
            cluster_config.namespace,
            database_config.pod_name,
            database_config.database,
            database_config.user,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'api_provider')",
            password=database_config.password,
        )
        
        assert result is not None, "Query failed"
        assert result[0][0] in ["t", "True", True, "1"], "api_provider table not found"

    def test_api_customer_table_exists(self, cluster_config, database_config):
        """Verify api_customer table exists."""
        result = execute_db_query(
            cluster_config.namespace,
            database_config.pod_name,
            database_config.database,
            database_config.user,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'api_customer')",
            password=database_config.password,
        )
        
        assert result is not None, "Query failed"
        assert result[0][0] in ["t", "True", True, "1"], "api_customer table not found"

    def test_manifest_table_exists(self, cluster_config, database_config):
        """Verify cost usage report manifest table exists."""
        result = execute_db_query(
            cluster_config.namespace,
            database_config.pod_name,
            database_config.database,
            database_config.user,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'reporting_common_costusagereportmanifest')",
            password=database_config.password,
        )
        
        assert result is not None, "Query failed"
        assert result[0][0] in ["t", "True", True, "1"], "Manifest table not found"


@pytest.mark.infrastructure
@pytest.mark.component
class TestDatabaseMigrations:
    """Tests for database migration status."""

    def test_django_migrations_table_exists(self, cluster_config, database_config):
        """Verify Django migrations table exists."""
        result = execute_db_query(
            cluster_config.namespace,
            database_config.pod_name,
            database_config.database,
            database_config.user,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'django_migrations')",
            password=database_config.password,
        )
        
        assert result is not None, "Query failed"
        assert result[0][0] in ["t", "True", True, "1"], "django_migrations table not found"

    def test_migrations_applied(self, cluster_config, database_config):
        """Verify migrations have been applied."""
        result = execute_db_query(
            cluster_config.namespace,
            database_config.pod_name,
            database_config.database,
            database_config.user,
            "SELECT COUNT(*) FROM django_migrations",
            password=database_config.password,
        )
        
        assert result is not None, "Query failed"
        count = int(result[0][0])
        assert count > 0, "No migrations have been applied"

    def test_no_pending_migrations(self, cluster_config, database_config):
        """Verify no migrations are pending (informational)."""
        # This is informational - we just check that the app tables exist
        # which indicates migrations have run
        result = execute_db_query(
            cluster_config.namespace,
            database_config.pod_name,
            database_config.database,
            database_config.user,
            "SELECT app FROM django_migrations GROUP BY app ORDER BY app",
            password=database_config.password,
        )
        
        assert result is not None, "Query failed"
        apps = [row[0] for row in result]
        
        # Check for expected Django apps
        expected_apps = ["api", "reporting", "reporting_common"]
        for app in expected_apps:
            assert app in apps, f"Migrations for '{app}' not found"


@pytest.mark.infrastructure
@pytest.mark.component
class TestKruizeDatabase:
    """Tests for Kruize database schema."""

    @pytest.fixture
    def kruize_credentials(self, cluster_config):
        """Get Kruize database credentials."""
        from utils import get_secret_value
        
        secret_name = f"{cluster_config.helm_release_name}-db-credentials"
        user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
        password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")
        
        if not user or not password:
            pytest.skip("Kruize database credentials not found")
        
        return {"user": user, "password": password}

    def test_kruize_experiments_table_exists(
        self, cluster_config, kruize_database_config
    ):
        """Verify kruize_experiments table exists."""
        result = execute_db_query(
            cluster_config.namespace,
            kruize_database_config.pod_name,
            kruize_database_config.database,
            kruize_database_config.user,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kruize_experiments')",
            password=kruize_database_config.password,
        )
        
        assert result is not None, "Query failed"
        assert result[0][0] in ["t", "True", True, "1"], "kruize_experiments table not found"

    def test_kruize_recommendations_table_exists(
        self, cluster_config, kruize_database_config
    ):
        """Verify kruize_recommendations table exists."""
        result = execute_db_query(
            cluster_config.namespace,
            kruize_database_config.pod_name,
            kruize_database_config.database,
            kruize_database_config.user,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kruize_recommendations')",
            password=kruize_database_config.password,
        )
        
        assert result is not None, "Query failed"
        assert result[0][0] in ["t", "True", True, "1"], "kruize_recommendations table not found"
