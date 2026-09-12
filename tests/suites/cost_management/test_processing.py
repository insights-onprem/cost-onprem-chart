"""
Koku processing component tests.

Tests for Koku listener and MASU worker health.
Note: Processing validation is tested in suites/e2e/ as part of the complete pipeline.
"""

import pytest

from utils import check_pod_ready, run_oc_command


@pytest.mark.cost_management
@pytest.mark.component
@pytest.mark.operator
class TestKokuListenerHealth:
    """Tests for Koku listener health."""

    @pytest.mark.smoke
    def test_listener_pod_ready(self, cluster_config):
        """Verify Koku listener pod is ready."""
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=listener"
        ), "Koku listener pod is not ready"

    def test_listener_no_critical_errors(self, cluster_config):
        """Verify listener logs don't show critical errors."""
        result = run_oc_command([
            "logs", "-n", cluster_config.namespace,
            "-l", "app.kubernetes.io/component=listener",
            "--tail=50"
        ], check=False)
        
        if result.returncode != 0:
            pytest.skip("Could not get listener logs")
        
        logs = result.stdout.lower()
        
        # Check for critical errors only
        critical_errors = ["fatal", "panic", "cannot connect to database"]
        for error in critical_errors:
            if error in logs:
                pytest.fail(f"Critical error '{error}' found in listener logs")


@pytest.mark.cost_management
@pytest.mark.component
@pytest.mark.operator
class TestMASUHealth:
    """Tests for MASU worker health."""

    @pytest.mark.smoke
    def test_masu_pod_ready(self, cluster_config):
        """Verify MASU pod is ready."""
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-processor"
        ), "MASU pod is not ready"

    def test_celery_workers_ready(self, cluster_config):
        """Verify Celery worker pods are ready."""
        # Check for any worker pods
        result = run_oc_command([
            "get", "pods", "-n", cluster_config.namespace,
            "-l", "app.kubernetes.io/component=cost-worker",
            "-o", "jsonpath={.items[*].status.phase}"
        ], check=False)
        
        if result.returncode != 0 or not result.stdout.strip():
            pytest.skip("No Celery worker pods found")
        
        phases = result.stdout.strip().split()
        running_count = sum(1 for p in phases if p == "Running")
        
        assert running_count > 0, "No Celery workers are running"
