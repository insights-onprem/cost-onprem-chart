"""
Helm template tests for the Keycloak-to-RBAC sync CronJob feature.

Validates that keycloakSync resources render correctly when enabled,
are omitted when disabled, and that guard clauses reject invalid configs.
"""

import pytest
import yaml

from utils import helm_template


OFFLINE_MOCK_VALUES = {
    "global.clusterDomain": "apps.example.com",
    "objectStorage.endpoint": "https://s3.example.com",
    "objectStorage.credentials.accessKey": "mock-access-key",
    "objectStorage.credentials.secretKey": "mock-secret-key",
    "jwtAuth.keycloak.url": "https://keycloak.example.com",
}

SYNC_ENABLED_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "rbac.keycloakSync.enabled": "true",
    "rbac.keycloakSync.clientSecretRef.name": "keycloak-client-secret-rbac-sync",
}


def _parse_manifests(rendered: str) -> list[dict]:
    """Split multi-document YAML into a list of parsed dicts."""
    docs = []
    for doc in rendered.split("---"):
        stripped = doc.strip()
        if not stripped:
            continue
        parsed = yaml.safe_load(stripped)
        if parsed and isinstance(parsed, dict):
            docs.append(parsed)
    return docs


def _find_by_kind_and_name(manifests: list[dict], kind: str, name_substring: str) -> dict | None:
    for m in manifests:
        if m.get("kind") == kind and name_substring in m.get("metadata", {}).get("name", ""):
            return m
    return None


@pytest.mark.helm
@pytest.mark.component
class TestKeycloakSyncEnabled:
    """Tests with keycloakSync.enabled=true."""

    def test_template_renders_with_sync_enabled(self, chart_path: str):
        """Chart renders successfully with keycloakSync enabled."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success, f"Helm template failed with keycloakSync enabled:\n{output}"

    def test_cronjob_rendered(self, chart_path: str):
        """CronJob manifest is present when keycloakSync is enabled."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        assert cronjob is not None, "CronJob for rbac-keycloak-sync not found in rendered output"

    def test_configmap_rendered(self, chart_path: str):
        """ConfigMap with sync script is present when keycloakSync is enabled."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        cm = _find_by_kind_and_name(manifests, "ConfigMap", "rbac-sync-script")
        assert cm is not None, "ConfigMap for rbac-sync-script not found"
        assert "sync_keycloak_principals.py" in cm.get("data", {}), \
            "ConfigMap missing sync_keycloak_principals.py data key"

    def test_cronjob_spec_fields(self, chart_path: str):
        """CronJob has correct schedule, concurrency, and deadline settings."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        spec = cronjob["spec"]

        assert spec["schedule"] == "*/15 * * * *"
        assert spec["concurrencyPolicy"] == "Forbid"
        assert spec["startingDeadlineSeconds"] == 900

    def test_cronjob_job_template(self, chart_path: str):
        """CronJob job template has correct security and operational settings."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        job_spec = cronjob["spec"]["jobTemplate"]["spec"]
        pod_spec = job_spec["template"]["spec"]

        assert job_spec["activeDeadlineSeconds"] == 300
        assert job_spec["backoffLimit"] == 3
        assert pod_spec["restartPolicy"] == "OnFailure"
        assert pod_spec["automountServiceAccountToken"] is False

    def test_cronjob_container_env(self, chart_path: str):
        """CronJob container has the required environment variables."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        sync_container = next(c for c in containers if c["name"] == "keycloak-sync")
        env_names = {e["name"] for e in sync_container["env"] if isinstance(e, dict)}

        required_envs = {
            "KEYCLOAK_URL", "KEYCLOAK_REALM", "KEYCLOAK_CLIENT_ID",
            "KEYCLOAK_CLIENT_SECRET", "KEYCLOAK_TLS_VERIFY",
            "SYNC_ORG_ID", "SYNC_ACCOUNT_NUMBER",
            "SYNC_ORG_ADMIN_ROLE", "SYNC_PRUNE_ORPHANS",
        }
        missing = required_envs - env_names
        assert not missing, f"Missing env vars in CronJob container: {missing}"

    def test_cronjob_secret_ref(self, chart_path: str):
        """KEYCLOAK_CLIENT_SECRET env var uses secretKeyRef."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        sync_container = next(c for c in containers if c["name"] == "keycloak-sync")

        secret_env = next(e for e in sync_container["env"] if e["name"] == "KEYCLOAK_CLIENT_SECRET")
        ref = secret_env["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "keycloak-client-secret-rbac-sync"
        assert ref["key"] == "CLIENT_SECRET"

    def test_cronjob_volume_mount(self, chart_path: str):
        """Sync script is mounted read-only via ConfigMap volume."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        sync_container = next(c for c in containers if c["name"] == "keycloak-sync")

        mount = next(vm for vm in sync_container["volumeMounts"] if vm["name"] == "sync-script")
        assert mount["readOnly"] is True
        assert mount["subPath"] == "sync_keycloak_principals.py"

    def test_cronjob_component_label(self, chart_path: str):
        """CronJob and pod template have the rbac-keycloak-sync component label."""
        success, output = helm_template(chart_path, set_values=SYNC_ENABLED_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        assert cronjob["metadata"]["labels"]["app.kubernetes.io/component"] == "rbac-keycloak-sync"

        pod_labels = cronjob["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
        assert pod_labels["app.kubernetes.io/component"] == "rbac-keycloak-sync"

    def test_custom_schedule(self, chart_path: str):
        """Custom schedule overrides the default."""
        values = {**SYNC_ENABLED_VALUES, "rbac.keycloakSync.schedule": "*/5 * * * *"}
        success, output = helm_template(chart_path, set_values=values)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        assert cronjob["spec"]["schedule"] == "*/5 * * * *"


@pytest.mark.helm
@pytest.mark.component
class TestKeycloakSyncDisabled:
    """Tests with keycloakSync.enabled=false (default)."""

    def test_no_cronjob_by_default(self, chart_path: str):
        """CronJob is NOT rendered with default values."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cronjob = _find_by_kind_and_name(manifests, "CronJob", "rbac-keycloak-sync")
        assert cronjob is None, "CronJob should not be rendered when keycloakSync is disabled"

    def test_no_sync_configmap_by_default(self, chart_path: str):
        """Sync ConfigMap is NOT rendered with default values."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success

        manifests = _parse_manifests(output)
        cm = _find_by_kind_and_name(manifests, "ConfigMap", "rbac-sync-script")
        assert cm is None, "Sync ConfigMap should not be rendered when keycloakSync is disabled"


@pytest.mark.helm
@pytest.mark.component
class TestKeycloakSyncGuardClauses:
    """Tests for Helm guard clauses that reject invalid configurations."""

    def test_fails_without_client_secret_ref(self, chart_path: str):
        """Render fails when keycloakSync is enabled but clientSecretRef.name is empty."""
        values = {
            **OFFLINE_MOCK_VALUES,
            "rbac.keycloakSync.enabled": "true",
        }
        success, output = helm_template(chart_path, set_values=values)
        assert not success, "Template should fail when clientSecretRef.name is empty"
        assert "clientSecretRef.name" in output.lower() or "required" in output.lower(), \
            f"Error should mention clientSecretRef.name, got:\n{output}"

    def test_fails_without_org_admin_user(self, chart_path: str):
        """Render fails when keycloakSync is enabled but no orgAdmin user exists."""
        values = {
            **OFFLINE_MOCK_VALUES,
            "rbac.keycloakSync.enabled": "true",
            "rbac.keycloakSync.clientSecretRef.name": "some-secret",
            "jwtAuth.realmUsers[0].username": "testuser",
            "jwtAuth.realmUsers[0].orgId": "org123",
            "jwtAuth.realmUsers[0].accountNumber": "123",
            "jwtAuth.realmUsers[0].orgAdmin": "false",
        }
        success, output = helm_template(chart_path, set_values=values)
        assert not success, "Template should fail when no orgAdmin:true user exists"
        assert "orgAdmin" in output or "org_id" in output or "realmUsers" in output, \
            f"Error should mention orgAdmin, got:\n{output}"
