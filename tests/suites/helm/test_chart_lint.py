"""
Helm chart linting and validation tests.

These tests verify the Helm chart is syntactically correct and follows best practices.
"""

import pytest

from utils import helm_lint, helm_template


# Mock values for offline template rendering (no cluster context)
OFFLINE_MOCK_VALUES = {
    # Provide mock cluster domain for route generation
    "global.clusterDomain": "apps.example.com",
    # Provide mock S3 endpoint and credentials to avoid lookup failures
    "objectStorage.endpoint": "https://s3.example.com",
    "objectStorage.credentials.accessKey": "mock-access-key",
    "objectStorage.credentials.secretKey": "mock-secret-key",
    # Provide mock Keycloak URL for JWT tests
    "jwtAuth.keycloak.url": "https://keycloak.example.com",
}


@pytest.mark.helm
@pytest.mark.component
class TestChartLint:
    """Tests for Helm chart linting."""

    @pytest.mark.smoke
    def test_chart_lint_default_values(self, chart_path: str):
        """Verify chart passes helm lint with default values."""
        success, output = helm_lint(chart_path)
        assert success, f"Helm lint failed:\n{output}"

    def test_chart_lint_openshift_values(
        self, chart_path: str, openshift_values_file: str
    ):
        """Verify chart passes helm lint with OpenShift values."""
        from utils import run_helm_command
        
        result = run_helm_command(
            ["lint", chart_path, "-f", openshift_values_file],
            check=False,
        )
        assert result.returncode == 0, f"Helm lint failed:\n{result.stderr}"


@pytest.mark.helm
@pytest.mark.component
class TestChartTemplate:
    """Tests for Helm chart template rendering (offline with mock values)."""

    @pytest.mark.smoke
    def test_template_renders_successfully(self, chart_path: str):
        """Verify chart templates render without errors (with mock credentials)."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Helm template failed:\n{output}"

    def test_template_with_openshift_values(
        self, chart_path: str, openshift_values_file: str
    ):
        """Verify chart templates render with OpenShift values (with mock credentials)."""
        # OpenShift values enables JWT which requires Keycloak URL and cluster domain
        openshift_mock_values = {
            **OFFLINE_MOCK_VALUES,
            "jwtAuth.keycloak.url": "https://keycloak.apps.example.com",
            "global.clusterDomain": "apps.example.com",
        }
        success, output = helm_template(
            chart_path,
            values_file=openshift_values_file,
            set_values=openshift_mock_values,
        )
        assert success, f"Helm template failed:\n{output}"

    def test_template_contains_required_resources(self, chart_path: str):
        """Verify rendered templates contain required Kubernetes resources."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, "Template rendering failed"

        # Check for essential resources
        required_kinds = [
            "Deployment",
            "Service",
            "ConfigMap",
        ]

        for kind in required_kinds:
            assert f"kind: {kind}" in output, f"Missing {kind} in rendered templates"

    def test_template_with_jwt_auth(self, chart_path: str):
        """Verify chart templates render with JWT authentication configuration."""
        # JWT auth is always enabled; this test verifies the chart renders correctly
        # with the standard mock values that include Keycloak URL
        success, output = helm_template(
            chart_path,
            set_values=OFFLINE_MOCK_VALUES,
        )
        assert success, f"Helm template with JWT auth failed:\n{output}"


@pytest.mark.helm
@pytest.mark.component
class TestChartMetadata:
    """Tests for Helm chart metadata."""

    def test_chart_yaml_exists(self, chart_path: str):
        """Verify Chart.yaml exists and is valid."""
        from pathlib import Path
        import yaml

        chart_yaml = Path(chart_path) / "Chart.yaml"
        assert chart_yaml.exists(), "Chart.yaml not found"

        with open(chart_yaml) as f:
            chart = yaml.safe_load(f)

        assert "name" in chart, "Chart.yaml missing 'name'"
        assert "version" in chart, "Chart.yaml missing 'version'"
        assert "apiVersion" in chart, "Chart.yaml missing 'apiVersion'"

    def test_values_yaml_exists(self, values_file: str):
        """Verify values.yaml exists and is valid YAML."""
        from pathlib import Path
        import yaml

        assert Path(values_file).exists(), "values.yaml not found"

        with open(values_file) as f:
            values = yaml.safe_load(f)

        assert isinstance(values, dict), "values.yaml should be a dictionary"
