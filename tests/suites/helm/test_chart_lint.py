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


SASL_SSL_MOCK_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "kafka.securityProtocol": "SASL_SSL",
    "kafka.sasl.mechanism": "SCRAM-SHA-512",
    "kafka.sasl.existingSecret": "kafka-sasl-credentials",
    "kafka.tls.enabled": "true",
    "kafka.tls.caCertSecret": "kafka-ca-cert",
}


@pytest.mark.helm
@pytest.mark.component
class TestKafkaSASLTLS:
    """Tests for Kafka SASL/TLS template rendering."""

    def test_lint_with_sasl_ssl_values(self, chart_path: str):
        """Verify chart passes helm lint with SASL_SSL configuration."""
        from utils import run_helm_command

        set_args = []
        for k, v in SASL_SSL_MOCK_VALUES.items():
            set_args.extend(["--set", f"{k}={v}"])
        result = run_helm_command(
            ["lint", chart_path] + set_args, check=False,
        )
        assert result.returncode == 0, f"Helm lint with SASL_SSL failed:\n{result.stderr}"

    def test_template_renders_with_sasl_ssl(self, chart_path: str):
        """Verify chart templates render with SASL_SSL configuration."""
        success, output = helm_template(chart_path, set_values=SASL_SSL_MOCK_VALUES)
        assert success, f"Helm template with SASL_SSL failed:\n{output}"

    def test_sasl_env_vars_present_when_configured(self, chart_path: str):
        """Verify SASL environment variables are rendered when kafka.sasl.mechanism is set."""
        success, output = helm_template(chart_path, set_values=SASL_SSL_MOCK_VALUES)
        assert success, f"Template rendering failed:\n{output}"

        assert "KAFKA_SASL_MECHANISM" in output, "Missing KAFKA_SASL_MECHANISM env var"
        assert "KAFKA_SASL_USERNAME" in output, "Missing KAFKA_SASL_USERNAME env var"
        assert "KAFKA_SASL_PASSWORD" in output, "Missing KAFKA_SASL_PASSWORD env var"
        assert "KAFKA_SSL_CA_LOCATION" in output, "Missing KAFKA_SSL_CA_LOCATION env var"
        assert "kafka-sasl-credentials" in output, "Missing secretKeyRef for SASL credentials"

    def test_ingress_sasl_env_vars_present(self, chart_path: str):
        """Verify Ingress-specific SASL env vars use INGRESS_ prefix."""
        success, output = helm_template(chart_path, set_values=SASL_SSL_MOCK_VALUES)
        assert success, f"Template rendering failed:\n{output}"

        assert "INGRESS_SASLMECHANISM" in output, "Missing INGRESS_SASLMECHANISM env var"
        assert "INGRESS_KAFKAUSERNAME" in output, "Missing INGRESS_KAFKAUSERNAME env var"
        assert "INGRESS_KAFKAPASSWORD" in output, "Missing INGRESS_KAFKAPASSWORD env var"

    def test_tls_volume_mount_present(self, chart_path: str):
        """Verify TLS CA certificate volume and mount are rendered when tls.enabled."""
        success, output = helm_template(chart_path, set_values=SASL_SSL_MOCK_VALUES)
        assert success, f"Template rendering failed:\n{output}"

        assert "kafka-ca-cert" in output, "Missing kafka-ca-cert volume/mount"
        assert "/etc/kafka/certs" in output, "Missing /etc/kafka/certs mount path"

    def test_sasl_absent_when_not_configured(self, chart_path: str):
        """Verify SASL env vars are NOT rendered with default (PLAINTEXT) config."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template rendering failed:\n{output}"

        assert "KAFKA_SASL_MECHANISM" not in output, "KAFKA_SASL_MECHANISM should not be present"
        assert "INGRESS_SASLMECHANISM" not in output, "INGRESS_SASLMECHANISM should not be present"
        assert "kafka-ca-cert" not in output, "kafka-ca-cert volume should not be present"


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
