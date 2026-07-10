"""
Helm chart linting and validation tests.

These tests verify the Helm chart is syntactically correct and follows best practices.
Includes content assertions for S3 configuration rendering.
"""

import yaml

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


@pytest.mark.helm
@pytest.mark.component
class TestS3TemplateContent:
    """Tests for S3-related template content correctness (offline, no cluster)."""

    def _get_rendered_docs(self, chart_path, extra_values=None, release_name="test-release"):
        values = {**OFFLINE_MOCK_VALUES}
        if extra_values:
            values.update(extra_values)
        success, output = helm_template(chart_path, release_name=release_name, set_values=values)
        assert success, f"Template rendering failed:\n{output}"
        return list(yaml.safe_load_all(output))

    def _find_resource(self, docs, kind, name_suffix):
        for doc in docs:
            if (
                doc
                and doc.get("kind") == kind
                and doc.get("metadata", {}).get("name", "").endswith(name_suffix)
            ):
                return doc
        return None

    def test_default_addressing_style_is_path(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        cm = self._find_resource(docs, "ConfigMap", "-aws-config")
        assert cm, "aws-config ConfigMap not found"
        assert "addressing_style = path" in cm["data"]["config"]

    def test_addressing_style_override_to_auto(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"objectStorage.s3.addressingStyle": "auto"}
        )
        cm = self._find_resource(docs, "ConfigMap", "-aws-config")
        assert cm, "aws-config ConfigMap not found"
        config = cm["data"]["config"]
        assert "addressing_style = auto" in config
        assert "addressing_style = path" not in config

    def test_default_region_is_onprem(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        cm = self._find_resource(docs, "ConfigMap", "-aws-config")
        assert cm, "aws-config ConfigMap not found"
        assert "region = onprem" in cm["data"]["config"]

    def test_region_override(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"objectStorage.s3.region": "us-east-1"}
        )
        cm = self._find_resource(docs, "ConfigMap", "-aws-config")
        assert cm, "aws-config ConfigMap not found"
        assert "region = us-east-1" in cm["data"]["config"]

    def test_signature_version_always_s3v4(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        cm = self._find_resource(docs, "ConfigMap", "-aws-config")
        assert cm, "aws-config ConfigMap not found"
        assert "signature_version = s3v4" in cm["data"]["config"]

    def test_endpoint_url_https_port_443_omits_port(self, chart_path):
        docs = self._get_rendered_docs(chart_path, {
            "objectStorage.endpoint": "s3.us-east-1.amazonaws.com",
            "objectStorage.port": "443",
            "objectStorage.useSSL": "true",
        })
        rendered = yaml.dump_all(docs)
        assert "https://s3.us-east-1.amazonaws.com" in rendered
        assert "https://s3.us-east-1.amazonaws.com:443" not in rendered

    def test_endpoint_url_http_port_80_omits_port(self, chart_path):
        docs = self._get_rendered_docs(chart_path, {
            "objectStorage.endpoint": "s4.s4-test.svc.cluster.local",
            "objectStorage.port": "80",
            "objectStorage.useSSL": "false",
        })
        rendered = yaml.dump_all(docs)
        assert "http://s4.s4-test.svc.cluster.local" in rendered
        assert "http://s4.s4-test.svc.cluster.local:80" not in rendered

    def test_endpoint_url_http_non_standard_port(self, chart_path):
        docs = self._get_rendered_docs(chart_path, {
            "objectStorage.endpoint": "s4.s4-test.svc.cluster.local",
            "objectStorage.port": "7480",
            "objectStorage.useSSL": "false",
        })
        rendered = yaml.dump_all(docs)
        assert "http://s4.s4-test.svc.cluster.local:7480" in rendered

    def test_secret_name_override(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"objectStorage.secretName": "my-custom-secret"}
        )
        rendered = yaml.dump_all(docs)
        assert "my-custom-secret" in rendered

    # -- S3 endpoint/port/SSL rendering tests --

    def test_endpoint_port_ssl_in_ingress_env(self, chart_path):
        docs = self._get_rendered_docs(chart_path, {
            "objectStorage.endpoint": "s4.cost-onprem.svc.cluster.local",
            "objectStorage.port": "7480",
            "objectStorage.useSSL": "false",
        })
        env = self._get_container_env(docs, "-ingress")
        assert env["INGRESS_MINIOENDPOINT"]["value"] == "s4.cost-onprem.svc.cluster.local:7480"
        assert env["INGRESS_USESSL"]["value"] == "false"

    def test_aws_endpoint_defaults_port_443_ssl_true(self, chart_path):
        docs = self._get_rendered_docs(chart_path, {
            "objectStorage.endpoint": "s3.us-east-1.amazonaws.com",
            "objectStorage.port": "443",
            "objectStorage.useSSL": "true",
            "objectStorage.s3.addressingStyle": "auto",
            "objectStorage.s3.region": "us-east-1",
        })
        env = self._get_container_env(docs, "-ingress")
        assert env["INGRESS_MINIOENDPOINT"]["value"] == "s3.us-east-1.amazonaws.com:443"
        assert env["INGRESS_USESSL"]["value"] == "true"

    def test_aws_full_stack_rendering(self, chart_path):
        docs = self._get_rendered_docs(chart_path, {
            "objectStorage.endpoint": "s3.eu-west-1.amazonaws.com",
            "objectStorage.port": "443",
            "objectStorage.useSSL": "true",
            "objectStorage.s3.addressingStyle": "auto",
            "objectStorage.s3.region": "eu-west-1",
            "objectStorage.secretName": "aws-creds",
            "ingress.storage.bucket": "org-ingress",
            "costManagement.storage.bucketName": "org-koku",
            "costManagement.storage.rosBucketName": "org-ros",
        })
        env = self._get_container_env(docs, "-ingress")
        assert env["INGRESS_MINIOENDPOINT"]["value"] == "s3.eu-west-1.amazonaws.com:443"
        assert env["INGRESS_USESSL"]["value"] == "true"
        assert env["INGRESS_STAGEBUCKET"]["value"] == "org-ingress"
        assert env["INGRESS_MINIOACCESSKEY"]["valueFrom"]["secretKeyRef"]["name"] == "aws-creds"
        cm = self._find_resource(docs, "ConfigMap", "-aws-config")
        assert cm, "aws-config ConfigMap not found"
        config = cm["data"]["config"]
        assert "addressing_style = auto" in config
        assert "region = eu-west-1" in config
        assert "signature_version = s3v4" in config
        rendered = yaml.dump_all(docs)
        assert "org-koku" in rendered
        assert "org-ros" in rendered

    def test_ros_bucket_name_override(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"costManagement.storage.rosBucketName": "my-custom-ros"}
        )
        rendered = yaml.dump_all(docs)
        assert "my-custom-ros" in rendered

    # -- Credential secret template tests --

    def test_credential_secret_has_required_keys(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        secret = self._find_resource(docs, "Secret", "-storage-credentials")
        assert secret, "storage-credentials Secret not found"
        assert "access-key" in secret["data"]
        assert "secret-key" in secret["data"]

    def test_credential_secret_name_follows_release(self, chart_path):
        docs = self._get_rendered_docs(chart_path, release_name="myrelease")
        secret = self._find_resource(docs, "Secret", "-storage-credentials")
        assert secret, "storage-credentials Secret not found"
        assert secret["metadata"]["name"].startswith("myrelease-")

    def test_custom_secret_name_skips_placeholder(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"objectStorage.secretName": "my-custom-secret"}
        )
        secret = self._find_resource(docs, "Secret", "-storage-credentials")
        assert secret is None, "placeholder secret should not render when secretName is set"

    # -- Pod credential security tests --

    def _get_container_env(self, docs, deploy_suffix, container_index=0):
        deploy = self._find_resource(docs, "Deployment", deploy_suffix)
        assert deploy, f"Deployment ending with '{deploy_suffix}' not found"
        containers = deploy["spec"]["template"]["spec"]["containers"]
        return {
            e["name"]: e for e in containers[container_index].get("env", [])
        }

    def test_ingress_credentials_use_secret_ref(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        env = self._get_container_env(docs, "-ingress")
        for var_name in ("INGRESS_MINIOACCESSKEY", "INGRESS_MINIOSECRETKEY"):
            assert var_name in env, f"{var_name} not found in ingress env"
            assert "valueFrom" in env[var_name], f"{var_name} should use valueFrom"
            assert "secretKeyRef" in env[var_name]["valueFrom"], f"{var_name} should use secretKeyRef"

    def test_ingress_credential_secret_keys_correct(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        env = self._get_container_env(docs, "-ingress")
        assert env["INGRESS_MINIOACCESSKEY"]["valueFrom"]["secretKeyRef"]["key"] == "access-key"
        assert env["INGRESS_MINIOSECRETKEY"]["valueFrom"]["secretKeyRef"]["key"] == "secret-key"

    def test_no_inline_credential_values(self, chart_path):
        docs = self._get_rendered_docs(chart_path)
        env = self._get_container_env(docs, "-ingress")
        for var_name in ("INGRESS_MINIOACCESSKEY", "INGRESS_MINIOSECRETKEY"):
            assert "value" not in env[var_name], f"{var_name} must not have inline value"

    # -- Bucket name rendering tests --

    def test_ingress_bucket_name_override(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"ingress.storage.bucket": "my-custom-ingress"}
        )
        env = self._get_container_env(docs, "-ingress")
        assert env["INGRESS_STAGEBUCKET"]["value"] == "my-custom-ingress"

    def test_koku_bucket_name_override(self, chart_path):
        docs = self._get_rendered_docs(
            chart_path, {"costManagement.storage.bucketName": "my-custom-koku"}
        )
        deploy = self._find_resource(docs, "Deployment", "-listener")
        assert deploy, "listener Deployment not found"
        rendered = yaml.dump(deploy)
        assert "my-custom-koku" in rendered
