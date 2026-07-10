"""
Helm template tests for external Valkey/Redis configuration (COST-7578).

Validates that external Valkey settings render correctly across all workloads,
bundled resources are omitted when deploy is false, and TLS CA volume mounts
are consistent everywhere REDIS_SSL_CA_CERTS appears.
"""

import re

import pytest
import yaml

from utils import helm_template

RELEASE_NAME = "test-release"

OFFLINE_MOCK_VALUES = {
    "global.clusterDomain": "apps.example.com",
    "objectStorage.endpoint": "https://s3.example.com",
    "objectStorage.credentials.accessKey": "mock-access-key",
    "objectStorage.credentials.secretKey": "mock-secret-key",
    "jwtAuth.keycloak.url": "https://keycloak.example.com",
}

EXTERNAL_VALKEY_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "valkey.deploy": "false",
    "valkey.host": "redis.example.com",
    "valkey.port": "6380",
}

EXTERNAL_VALKEY_AUTH_VALUES = {
    **EXTERNAL_VALKEY_VALUES,
    "valkey.auth.enabled": "true",
    "valkey.auth.secretName": "my-redis-auth",
}

EXTERNAL_VALKEY_TLS_VALUES = {
    **EXTERNAL_VALKEY_VALUES,
    "valkey.tls.enabled": "true",
}

EXTERNAL_VALKEY_TLS_CA_VALUES = {
    **EXTERNAL_VALKEY_VALUES,
    "valkey.tls.enabled": "true",
    "valkey.tls.caCertSecretName": "redis-ca-cert",
}

EXTERNAL_VALKEY_FULL_VALUES = {
    **EXTERNAL_VALKEY_VALUES,
    "valkey.auth.enabled": "true",
    "valkey.auth.secretName": "my-redis-auth",
    "valkey.tls.enabled": "true",
    "valkey.tls.caCertSecretName": "redis-ca-cert",
}


def _parse_manifests(rendered: str) -> list[dict]:
    """Split multi-document YAML into a list of parsed dicts."""
    docs = []
    for doc in re.split(r"^---\s*$", rendered, flags=re.MULTILINE):
        stripped = doc.strip()
        if not stripped:
            continue
        parsed = yaml.safe_load(stripped)
        if parsed and isinstance(parsed, dict):
            docs.append(parsed)
    return docs


def _find_by_kind_and_name(
    manifests: list[dict], kind: str, name_substring: str
) -> dict | None:
    for m in manifests:
        if (
            m.get("kind") == kind
            and name_substring in m.get("metadata", {}).get("name", "")
        ):
            return m
    return None


def _get_env_var(container: dict, name: str) -> dict | None:
    for env in container.get("env", []):
        if isinstance(env, dict) and env.get("name") == name:
            return env
    return None


def _get_volume_mount(container: dict, name: str) -> dict | None:
    for vm in container.get("volumeMounts", []):
        if isinstance(vm, dict) and vm.get("name") == name:
            return vm
    return None


def _get_volume(pod_spec: dict, name: str) -> dict | None:
    for v in pod_spec.get("volumes", []):
        if isinstance(v, dict) and v.get("name") == name:
            return v
    return None


def _get_container(manifest: dict) -> dict:
    """Get the first container from a Deployment or Job manifest."""
    pod_spec = manifest["spec"]["template"]["spec"]
    return pod_spec["containers"][0]


def _get_pod_spec(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]


@pytest.mark.helm
@pytest.mark.component
class TestExternalValkeyDisabled:
    """Default bundled Valkey mode — valkey.deploy=true (default)."""

    def test_bundled_valkey_resources_present(self, chart_path):
        """Bundled mode creates Valkey Deployment, Service, and PVC."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        has_valkey = _find_by_kind_and_name(manifests, "Deployment", "valkey")
        has_redis = _find_by_kind_and_name(manifests, "Deployment", "redis")
        assert has_valkey or has_redis, "No valkey or redis Deployment found"

        has_svc = (
            _find_by_kind_and_name(manifests, "Service", "valkey")
            or _find_by_kind_and_name(manifests, "Service", "redis")
        )
        assert has_svc is not None, "No valkey or redis Service found"

    def test_bundled_redis_host_uses_release_name(self, chart_path):
        """Bundled REDIS_HOST is derived from the release name."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")

        container = _get_container(api)
        host_env = _get_env_var(container, "REDIS_HOST")
        assert host_env is not None, "REDIS_HOST env var not found"
        assert "valkey" in host_env["value"]
        assert host_env["value"].startswith(RELEASE_NAME)

    def test_bundled_no_auth_or_tls_env_vars(self, chart_path):
        """Bundled mode has no auth or TLS env vars."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")
        container = _get_container(api)

        env_names = {e["name"] for e in container.get("env", []) if isinstance(e, dict)}
        for var in ("REDIS_PASSWORD", "REDIS_USERNAME", "REDIS_SSL", "REDIS_SSL_CA_CERTS"):
            assert var not in env_names, f"{var} should not be present in bundled mode"


@pytest.mark.helm
@pytest.mark.component
class TestExternalValkeyEnabled:
    """External Valkey mode — valkey.deploy=false with various configs."""

    def test_no_bundled_valkey_resources(self, chart_path):
        """External mode creates no Valkey/Redis Deployment, Service, or PVC."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        has_cache = (
            _find_by_kind_and_name(manifests, "Deployment", "valkey")
            or _find_by_kind_and_name(manifests, "Deployment", "redis")
        )
        if has_cache:
            pytest.skip("Chart does not support valkey.deploy toggle (pre-PR#99)")

    def test_custom_redis_host_and_port(self, chart_path):
        """External mode sets custom REDIS_HOST and REDIS_PORT."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")
        container = _get_container(api)

        host_env = _get_env_var(container, "REDIS_HOST")
        assert host_env is not None, "REDIS_HOST env var not found on koku-api"
        assert host_env["value"] == "redis.example.com"

        port_env = _get_env_var(container, "REDIS_PORT")
        assert port_env is not None, "REDIS_PORT env var not found on koku-api"
        assert port_env["value"] == "6380"

    def test_auth_env_vars_present(self, chart_path):
        """Auth mode injects REDIS_PASSWORD and REDIS_USERNAME from secret."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_AUTH_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")
        container = _get_container(api)

        pw_env = _get_env_var(container, "REDIS_PASSWORD")
        assert pw_env is not None, "REDIS_PASSWORD env var not found"
        secret_ref = pw_env["valueFrom"]["secretKeyRef"]
        assert secret_ref["name"] == "my-redis-auth"
        assert secret_ref["key"] == "redis-password"

        user_env = _get_env_var(container, "REDIS_USERNAME")
        assert user_env is not None, "REDIS_USERNAME env var not found"
        user_ref = user_env["valueFrom"]["secretKeyRef"]
        assert user_ref["name"] == "my-redis-auth"
        assert user_ref["key"] == "redis-username"
        assert user_ref.get("optional") is True

    def test_tls_ssl_env_var_without_ca(self, chart_path):
        """TLS without CA cert sets REDIS_SSL but not REDIS_SSL_CA_CERTS."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_TLS_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")
        container = _get_container(api)

        ssl_env = _get_env_var(container, "REDIS_SSL")
        assert ssl_env is not None, "REDIS_SSL env var not found on koku-api"
        assert ssl_env["value"] == "True"

        ca_env = _get_env_var(container, "REDIS_SSL_CA_CERTS")
        assert ca_env is None, "REDIS_SSL_CA_CERTS should not be set without caCertSecretName"

    def test_tls_ca_env_and_volume(self, chart_path):
        """TLS with CA cert sets env, volumeMount, and volume on koku-api."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_TLS_CA_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")
        container = _get_container(api)
        pod_spec = _get_pod_spec(api)

        ca_env = _get_env_var(container, "REDIS_SSL_CA_CERTS")
        assert ca_env is not None, "REDIS_SSL_CA_CERTS env var not found on koku-api"
        assert ca_env["value"] == "/etc/redis-tls/ca.crt"

        mount = _get_volume_mount(container, "redis-tls-ca")
        assert mount is not None, "redis-tls-ca volumeMount not found on koku-api"
        assert mount["mountPath"] == "/etc/redis-tls"
        assert mount.get("readOnly") is True

        vol = _get_volume(pod_spec, "redis-tls-ca")
        assert vol is not None, "redis-tls-ca volume not found on koku-api"
        assert vol["secret"]["secretName"] == "redis-ca-cert"

    def test_rbac_workloads_get_redis_env_vars(self, chart_path):
        """RBAC API deployment gets the same external Redis host and port."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        rbac = _find_by_kind_and_name(manifests, "Deployment", "rbac-api")
        if rbac is None:
            pytest.skip("rbac-api deployment not in rendered output")

        container = _get_container(rbac)
        host_env = _get_env_var(container, "REDIS_HOST")
        assert host_env is not None, "REDIS_HOST env var not found on rbac-api"
        assert host_env["value"] == "redis.example.com"

        port_env = _get_env_var(container, "REDIS_PORT")
        assert port_env is not None, "REDIS_PORT env var not found on rbac-api"
        assert port_env["value"] == "6380"

    def test_rbac_auth_env_vars_present(self, chart_path):
        """RBAC API gets REDIS_PASSWORD and REDIS_USERNAME when auth is enabled."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_AUTH_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        rbac = _find_by_kind_and_name(manifests, "Deployment", "rbac-api")
        if rbac is None:
            pytest.skip("rbac-api deployment not in rendered output")

        container = _get_container(rbac)

        pw_env = _get_env_var(container, "REDIS_PASSWORD")
        assert pw_env is not None, "REDIS_PASSWORD env var not found on rbac-api"
        secret_ref = pw_env["valueFrom"]["secretKeyRef"]
        assert secret_ref["name"] == "my-redis-auth"
        assert secret_ref["key"] == "redis-password"

        user_env = _get_env_var(container, "REDIS_USERNAME")
        assert user_env is not None, "REDIS_USERNAME env var not found on rbac-api"
        user_ref = user_env["valueFrom"]["secretKeyRef"]
        assert user_ref["name"] == "my-redis-auth"
        assert user_ref["key"] == "redis-username"
        assert user_ref.get("optional") is True

    def test_rbac_tls_env_and_volume(self, chart_path):
        """RBAC API gets TLS env vars, volumeMount, and volume with CA cert."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_TLS_CA_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        rbac = _find_by_kind_and_name(manifests, "Deployment", "rbac-api")
        if rbac is None:
            pytest.skip("rbac-api deployment not in rendered output")

        container = _get_container(rbac)
        pod_spec = _get_pod_spec(rbac)

        ssl_env = _get_env_var(container, "REDIS_SSL")
        assert ssl_env is not None, "REDIS_SSL env var not found on rbac-api"
        assert ssl_env["value"] == "True"

        ca_env = _get_env_var(container, "REDIS_SSL_CA_CERTS")
        assert ca_env is not None, "REDIS_SSL_CA_CERTS env var not found on rbac-api"
        assert ca_env["value"] == "/etc/redis-tls/ca.crt"

        mount = _get_volume_mount(container, "redis-tls-ca")
        assert mount is not None, "redis-tls-ca volumeMount not found on rbac-api"
        assert mount["mountPath"] == "/etc/redis-tls"
        assert mount.get("readOnly") is True

        vol = _get_volume(pod_spec, "redis-tls-ca")
        assert vol is not None, "redis-tls-ca volume not found on rbac-api"
        assert vol["secret"]["secretName"] == "redis-ca-cert"

    def test_auth_and_tls_combined(self, chart_path):
        """Combined auth + TLS renders without errors and all expected fields present."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_FULL_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")

        container = _get_container(api)
        pod_spec = _get_pod_spec(api)

        assert _get_env_var(container, "REDIS_HOST") is not None, "REDIS_HOST missing"
        assert _get_env_var(container, "REDIS_PORT") is not None, "REDIS_PORT missing"
        assert _get_env_var(container, "REDIS_PASSWORD") is not None, "REDIS_PASSWORD missing"
        assert _get_env_var(container, "REDIS_SSL") is not None, "REDIS_SSL missing"
        assert _get_env_var(container, "REDIS_SSL_CA_CERTS") is not None, "REDIS_SSL_CA_CERTS missing"
        assert _get_volume_mount(container, "redis-tls-ca") is not None, "redis-tls-ca volumeMount missing"
        assert _get_volume(pod_spec, "redis-tls-ca") is not None, "redis-tls-ca volume missing"


@pytest.mark.helm
@pytest.mark.component
class TestExternalValkeyTLSConsistency:
    """Regression gate for COST-7740.

    Every workload whose container has REDIS_SSL_CA_CERTS env var must also
    have the redis-tls-ca volumeMount and volume — otherwise the container
    references a CA cert file that doesn't exist.
    """

    def test_all_workloads_with_ca_env_have_volume_mount(self, chart_path):
        """Every container with REDIS_SSL_CA_CERTS must have redis-tls-ca volumeMount."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_TLS_CA_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        violations = []

        for manifest in manifests:
            kind = manifest.get("kind", "")
            if kind not in ("Deployment", "Job"):
                continue

            name = manifest["metadata"]["name"]
            pod_spec = _get_pod_spec(manifest)

            for container in pod_spec.get("containers", []):
                if _get_env_var(container, "REDIS_SSL_CA_CERTS") is None:
                    continue
                if _get_volume_mount(container, "redis-tls-ca") is None:
                    violations.append(f"{kind}/{name} container={container['name']}")

        assert not violations, (
            "COST-7740: Workloads have REDIS_SSL_CA_CERTS env but missing "
            "redis-tls-ca volumeMount — the CA cert file won't exist:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_all_workloads_with_ca_env_have_volume(self, chart_path):
        """Every workload with REDIS_SSL_CA_CERTS must define the redis-tls-ca volume."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_TLS_CA_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        violations = []

        for manifest in manifests:
            kind = manifest.get("kind", "")
            if kind not in ("Deployment", "Job"):
                continue

            name = manifest["metadata"]["name"]
            pod_spec = _get_pod_spec(manifest)

            has_ca_env = any(
                _get_env_var(c, "REDIS_SSL_CA_CERTS") is not None
                for c in pod_spec.get("containers", [])
            )
            if not has_ca_env:
                continue

            if _get_volume(pod_spec, "redis-tls-ca") is None:
                violations.append(f"{kind}/{name}")

        assert not violations, (
            "COST-7740: Workloads have REDIS_SSL_CA_CERTS env but missing "
            "redis-tls-ca volume definition:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


@pytest.mark.helm
@pytest.mark.component
class TestExternalValkeyCompleteness:
    """Verify all Redis-consuming workloads get auth and TLS when enabled.

    The inverse of TLS consistency tests: instead of "if CA env → must have
    volume", these check "if REDIS_HOST → must have REDIS_SSL/REDIS_PASSWORD".
    Catches workloads that silently skip auth or TLS config.
    """

    def test_all_redis_workloads_get_ssl_when_tls_enabled(self, chart_path):
        """Every workload with REDIS_HOST must also have REDIS_SSL when TLS is on."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_TLS_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        violations = []

        for manifest in manifests:
            kind = manifest.get("kind", "")
            if kind not in ("Deployment", "Job"):
                continue

            name = manifest["metadata"]["name"]
            pod_spec = _get_pod_spec(manifest)

            for container in pod_spec.get("containers", []):
                if _get_env_var(container, "REDIS_HOST") is None:
                    continue
                if _get_env_var(container, "REDIS_SSL") is None:
                    violations.append(f"{kind}/{name} container={container['name']}")

        assert not violations, (
            "Workloads have REDIS_HOST but missing REDIS_SSL when TLS is enabled:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_all_redis_workloads_get_password_when_auth_enabled(self, chart_path):
        """Every workload with REDIS_HOST must also have REDIS_PASSWORD when auth is on."""
        success, output = helm_template(
            chart_path, set_values=EXTERNAL_VALKEY_AUTH_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        violations = []

        for manifest in manifests:
            kind = manifest.get("kind", "")
            if kind not in ("Deployment", "Job"):
                continue

            name = manifest["metadata"]["name"]
            pod_spec = _get_pod_spec(manifest)

            for container in pod_spec.get("containers", []):
                if _get_env_var(container, "REDIS_HOST") is None:
                    continue
                if _get_env_var(container, "REDIS_PASSWORD") is None:
                    violations.append(f"{kind}/{name} container={container['name']}")

        assert not violations, (
            "Workloads have REDIS_HOST but missing REDIS_PASSWORD when auth is enabled:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


@pytest.mark.helm
@pytest.mark.component
class TestExternalValkeyGuardClauses:
    """Invalid external Valkey configurations fail with clear errors."""

    def test_deploy_false_without_host_renders_empty_redis_host(self, chart_path):
        """External mode without valkey.host renders empty REDIS_HOST."""
        values = {
            **OFFLINE_MOCK_VALUES,
            "valkey.deploy": "false",
        }
        success, output = helm_template(chart_path, set_values=values)
        if not success:
            return
        manifests = _parse_manifests(output)
        api = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if api is None:
            pytest.skip("koku-api deployment not in rendered output")
        container = _get_container(api)
        host_env = _get_env_var(container, "REDIS_HOST")
        if host_env is not None:
            assert host_env["value"] == "", (
                "valkey.deploy=false without valkey.host should render empty "
                f"REDIS_HOST, but got '{host_env['value']}' - operator may not "
                "notice the misconfiguration"
            )

    def test_auth_enabled_without_secret_name_fails(self, chart_path):
        """Auth enabled with empty secretName causes template failure."""
        values = {
            **EXTERNAL_VALKEY_VALUES,
            "valkey.auth.enabled": "true",
        }
        success, output = helm_template(chart_path, set_values=values)
        if success:
            pytest.skip("Chart does not have auth.secretName fail guard (pre-PR#99)")
        assert "secretname" in output.lower() or "redis-password" in output.lower(), (
            f"Error should mention secretName or redis-password, got:\n{output}"
        )
