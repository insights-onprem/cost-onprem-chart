"""
Helm template tests for Kafka SASL/TLS helper gate conditions (COST-7893).

Validates that:
- TLS env vars render independently of SASL configuration
- KAFKA_SSL_CA_LOCATION gate matches tlsVolume/tlsVolumeMount gates
- No duplicate KAFKA_SECURITY_PROTOCOL in any deployment
- Migration job gets Kafka TLS volumes when TLS is configured
- Ingress gets INGRESS_* TLS env vars independently of SASL
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

KAFKA_SSL_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "kafka.securityProtocol": "SSL",
    "kafka.tls.enabled": "true",
    "kafka.tls.caCertSecret": "my-kafka-ca",
}

KAFKA_TLS_NO_CA_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "kafka.securityProtocol": "SSL",
    "kafka.tls.enabled": "true",
}

KAFKA_SASL_PLAINTEXT_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "kafka.securityProtocol": "SASL_PLAINTEXT",
    "kafka.sasl.mechanism": "SCRAM-SHA-512",
    "kafka.sasl.existingSecret": "kafka-auth",
}

KAFKA_SASL_SSL_VALUES = {
    **OFFLINE_MOCK_VALUES,
    "kafka.securityProtocol": "SASL_SSL",
    "kafka.sasl.mechanism": "SCRAM-SHA-512",
    "kafka.sasl.existingSecret": "kafka-auth",
    "kafka.tls.enabled": "true",
    "kafka.tls.caCertSecret": "my-kafka-ca",
}


def _parse_manifests(rendered: str) -> list[dict]:
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


def _count_env_var(container: dict, name: str) -> int:
    return sum(
        1 for env in container.get("env", [])
        if isinstance(env, dict) and env.get("name") == name
    )


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
    pod_spec = manifest["spec"]["template"]["spec"]
    return pod_spec["containers"][0]


def _get_pod_spec(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]


KOKU_DEPLOYMENTS = ["koku-api", "koku-masu", "listener"]
ROS_DEPLOYMENTS = ["ros-api", "ros-processor", "ros-housekeeper", "ros-recommendation"]
ALL_KAFKA_DEPLOYMENTS = KOKU_DEPLOYMENTS + ROS_DEPLOYMENTS


@pytest.mark.helm
@pytest.mark.component
class TestKafkaPlaintextMode:
    """Default PLAINTEXT mode - no SASL/TLS env vars."""

    def test_no_security_protocol_env_var(self, chart_path):
        """PLAINTEXT mode should not render KAFKA_SECURITY_PROTOCOL."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            env = _get_env_var(container, "KAFKA_SECURITY_PROTOCOL")
            assert env is None, (
                f"{name_sub}: KAFKA_SECURITY_PROTOCOL should not be present "
                f"in PLAINTEXT mode"
            )

    def test_no_sasl_env_vars(self, chart_path):
        """PLAINTEXT mode should not render any SASL env vars."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            for var in ("KAFKA_SASL_MECHANISM", "KAFKA_SASL_USERNAME",
                        "KAFKA_SASL_PASSWORD", "KAFKA_SSL_CA_LOCATION"):
                env = _get_env_var(container, var)
                assert env is None, (
                    f"{name_sub}: {var} should not be present in PLAINTEXT mode"
                )

    def test_no_tls_volumes(self, chart_path):
        """PLAINTEXT mode should not render Kafka TLS volumes."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            pod_spec = _get_pod_spec(dep)
            vol = _get_volume(pod_spec, "kafka-ca-cert")
            assert vol is None, (
                f"{name_sub}: kafka-ca-cert volume should not be present "
                f"in PLAINTEXT mode"
            )


@pytest.mark.helm
@pytest.mark.component
class TestKafkaSslOnly:
    """SSL mode - TLS without SASL. The core bug scenario from COST-7893."""

    def test_security_protocol_rendered(self, chart_path):
        """SSL mode renders KAFKA_SECURITY_PROTOCOL=SSL in all deployments."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            env = _get_env_var(container, "KAFKA_SECURITY_PROTOCOL")
            assert env is not None, (
                f"{name_sub}: KAFKA_SECURITY_PROTOCOL missing in SSL mode"
            )
            assert env["value"] == "SSL"

    def test_ca_location_rendered(self, chart_path):
        """SSL mode with caCertSecret renders KAFKA_SSL_CA_LOCATION."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            env = _get_env_var(container, "KAFKA_SSL_CA_LOCATION")
            assert env is not None, (
                f"{name_sub}: KAFKA_SSL_CA_LOCATION missing in SSL mode"
            )
            assert env["value"] == "/etc/kafka/certs/ca.crt"

    def test_no_sasl_vars_in_ssl_only(self, chart_path):
        """SSL-only mode should not render SASL mechanism or credentials."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            for var in ("KAFKA_SASL_MECHANISM", "KAFKA_SASL_USERNAME",
                        "KAFKA_SASL_PASSWORD"):
                env = _get_env_var(container, var)
                assert env is None, (
                    f"{name_sub}: {var} should not be present in SSL-only mode"
                )

    def test_tls_volume_and_mount_present(self, chart_path):
        """SSL mode renders kafka-ca-cert volume and volumeMount."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            pod_spec = _get_pod_spec(dep)

            mount = _get_volume_mount(container, "kafka-ca-cert")
            assert mount is not None, (
                f"{name_sub}: kafka-ca-cert volumeMount missing in SSL mode"
            )
            assert mount["mountPath"] == "/etc/kafka/certs"
            assert mount.get("readOnly") is True

            vol = _get_volume(pod_spec, "kafka-ca-cert")
            assert vol is not None, (
                f"{name_sub}: kafka-ca-cert volume missing in SSL mode"
            )
            assert vol["secret"]["secretName"] == "my-kafka-ca"


@pytest.mark.helm
@pytest.mark.component
class TestKafkaTlsWithoutCaCert:
    """TLS enabled but no caCertSecret - no phantom CA location env var."""

    def test_security_protocol_present(self, chart_path):
        """Security protocol still renders without CA cert."""
        success, output = helm_template(chart_path, set_values=KAFKA_TLS_NO_CA_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if dep is None:
            pytest.skip("koku-api deployment not found")
        container = _get_container(dep)
        env = _get_env_var(container, "KAFKA_SECURITY_PROTOCOL")
        assert env is not None, "KAFKA_SECURITY_PROTOCOL should be present"
        assert env["value"] == "SSL"

    def test_no_ca_location_without_secret(self, chart_path):
        """No KAFKA_SSL_CA_LOCATION when caCertSecret is not set."""
        success, output = helm_template(chart_path, set_values=KAFKA_TLS_NO_CA_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            env = _get_env_var(container, "KAFKA_SSL_CA_LOCATION")
            assert env is None, (
                f"{name_sub}: KAFKA_SSL_CA_LOCATION should not be present "
                f"without caCertSecret"
            )

    def test_no_tls_volumes_without_secret(self, chart_path):
        """No kafka-ca-cert volume without caCertSecret."""
        success, output = helm_template(chart_path, set_values=KAFKA_TLS_NO_CA_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            pod_spec = _get_pod_spec(dep)
            vol = _get_volume(pod_spec, "kafka-ca-cert")
            assert vol is None, (
                f"{name_sub}: kafka-ca-cert volume should not be present "
                f"without caCertSecret"
            )


@pytest.mark.helm
@pytest.mark.component
class TestKafkaSaslPlaintext:
    """SASL_PLAINTEXT mode - SASL without TLS."""

    def test_security_protocol_and_sasl_vars(self, chart_path):
        """SASL_PLAINTEXT renders security protocol and SASL vars on all deployments."""
        success, output = helm_template(
            chart_path, set_values=KAFKA_SASL_PLAINTEXT_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)

            proto = _get_env_var(container, "KAFKA_SECURITY_PROTOCOL")
            assert proto is not None, (
                f"{name_sub}: KAFKA_SECURITY_PROTOCOL missing"
            )
            assert proto["value"] == "SASL_PLAINTEXT"

            mech = _get_env_var(container, "KAFKA_SASL_MECHANISM")
            assert mech is not None, (
                f"{name_sub}: KAFKA_SASL_MECHANISM missing"
            )
            assert mech["value"] == "SCRAM-SHA-512"

            user = _get_env_var(container, "KAFKA_SASL_USERNAME")
            assert user is not None, f"{name_sub}: KAFKA_SASL_USERNAME missing"
            assert user["valueFrom"]["secretKeyRef"]["name"] == "kafka-auth"
            assert user["valueFrom"]["secretKeyRef"]["key"] == "username"

            password = _get_env_var(container, "KAFKA_SASL_PASSWORD")
            assert password is not None, (
                f"{name_sub}: KAFKA_SASL_PASSWORD missing"
            )
            assert password["valueFrom"]["secretKeyRef"]["name"] == "kafka-auth"
            assert password["valueFrom"]["secretKeyRef"]["key"] == "password"

    def test_no_tls_vars_in_sasl_plaintext(self, chart_path):
        """SASL_PLAINTEXT should not render CA location or TLS volumes."""
        success, output = helm_template(
            chart_path, set_values=KAFKA_SASL_PLAINTEXT_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)
            pod_spec = _get_pod_spec(dep)

            ca = _get_env_var(container, "KAFKA_SSL_CA_LOCATION")
            assert ca is None, (
                f"{name_sub}: KAFKA_SSL_CA_LOCATION should not be present"
            )

            vol = _get_volume(pod_spec, "kafka-ca-cert")
            assert vol is None, (
                f"{name_sub}: kafka-ca-cert volume should not be present"
            )


@pytest.mark.helm
@pytest.mark.component
class TestKafkaSaslSsl:
    """SASL_SSL mode - both SASL and TLS."""

    def test_all_vars_present(self, chart_path):
        """SASL_SSL renders security protocol, SASL vars, and CA location."""
        success, output = helm_template(chart_path, set_values=KAFKA_SASL_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            container = _get_container(dep)

            proto = _get_env_var(container, "KAFKA_SECURITY_PROTOCOL")
            assert proto is not None, (
                f"{name_sub}: KAFKA_SECURITY_PROTOCOL missing in SASL_SSL mode"
            )
            assert proto["value"] == "SASL_SSL"

            mech = _get_env_var(container, "KAFKA_SASL_MECHANISM")
            assert mech is not None, (
                f"{name_sub}: KAFKA_SASL_MECHANISM missing in SASL_SSL mode"
            )

            ca = _get_env_var(container, "KAFKA_SSL_CA_LOCATION")
            assert ca is not None, (
                f"{name_sub}: KAFKA_SSL_CA_LOCATION missing in SASL_SSL mode"
            )

    def test_tls_volumes_present(self, chart_path):
        """SASL_SSL renders TLS volumes on all deployments."""
        success, output = helm_template(chart_path, set_values=KAFKA_SASL_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for name_sub in ALL_KAFKA_DEPLOYMENTS:
            dep = _find_by_kind_and_name(manifests, "Deployment", name_sub)
            if dep is None:
                continue
            pod_spec = _get_pod_spec(dep)
            vol = _get_volume(pod_spec, "kafka-ca-cert")
            assert vol is not None, (
                f"{name_sub}: kafka-ca-cert volume missing in SASL_SSL mode"
            )


@pytest.mark.helm
@pytest.mark.component
class TestKafkaNoDuplicates:
    """No duplicate KAFKA_SECURITY_PROTOCOL in any mode."""

    @pytest.mark.parametrize("values,mode", [
        (KAFKA_SSL_VALUES, "SSL"),
        (KAFKA_SASL_PLAINTEXT_VALUES, "SASL_PLAINTEXT"),
        (KAFKA_SASL_SSL_VALUES, "SASL_SSL"),
    ])
    def test_no_duplicate_security_protocol(self, chart_path, values, mode):
        """Each container has at most one KAFKA_SECURITY_PROTOCOL."""
        success, output = helm_template(chart_path, set_values=values)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        for m in manifests:
            kind = m.get("kind", "")
            if kind not in ("Deployment", "Job", "CronJob"):
                continue
            name = m.get("metadata", {}).get("name", "")
            spec = m.get("spec", {})
            if kind == "CronJob":
                containers = (
                    spec.get("jobTemplate", {})
                    .get("spec", {})
                    .get("template", {})
                    .get("spec", {})
                    .get("containers", [])
                )
            else:
                containers = (
                    spec.get("template", {})
                    .get("spec", {})
                    .get("containers", [])
                )
            for c in containers:
                count = _count_env_var(c, "KAFKA_SECURITY_PROTOCOL")
                assert count <= 1, (
                    f"{kind}/{name}: KAFKA_SECURITY_PROTOCOL appears {count} "
                    f"times in {mode} mode (expected at most 1)"
                )


@pytest.mark.helm
@pytest.mark.component
class TestKafkaMigrationJob:
    """Migration job gets Kafka TLS volumes when TLS is configured."""

    def test_migration_job_has_tls_volumes(self, chart_path):
        """Migration job renders kafka-ca-cert volume and mount in SSL mode."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        job = _find_by_kind_and_name(manifests, "Job", "koku-migrate")
        if job is None:
            pytest.skip("koku-migrate job not in rendered output")

        container = _get_container(job)
        pod_spec = _get_pod_spec(job)

        mount = _get_volume_mount(container, "kafka-ca-cert")
        assert mount is not None, "kafka-ca-cert volumeMount missing on migration job"
        assert mount["mountPath"] == "/etc/kafka/certs"

        vol = _get_volume(pod_spec, "kafka-ca-cert")
        assert vol is not None, "kafka-ca-cert volume missing on migration job"
        assert vol["secret"]["secretName"] == "my-kafka-ca"

    def test_migration_job_no_tls_in_plaintext(self, chart_path):
        """Migration job has no Kafka TLS volumes in PLAINTEXT mode."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        job = _find_by_kind_and_name(manifests, "Job", "koku-migrate")
        if job is None:
            pytest.skip("koku-migrate job not in rendered output")

        pod_spec = _get_pod_spec(job)
        vol = _get_volume(pod_spec, "kafka-ca-cert")
        assert vol is None, "kafka-ca-cert volume should not be present in PLAINTEXT"


@pytest.mark.helm
@pytest.mark.component
class TestKafkaIngressTls:
    """Ingress gets INGRESS_* TLS env vars independently of SASL."""

    def test_ingress_ssl_only_env_vars(self, chart_path):
        """SSL-only mode renders INGRESS_KAFKASECURITYPROTOCOL and INGRESS_KAFKACA."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "ingress")
        if dep is None:
            pytest.skip("ingress deployment not found")
        container = _get_container(dep)

        proto = _get_env_var(container, "INGRESS_KAFKASECURITYPROTOCOL")
        assert proto is not None, (
            "INGRESS_KAFKASECURITYPROTOCOL missing in SSL mode"
        )
        assert proto["value"] == "SSL"

        ca = _get_env_var(container, "INGRESS_KAFKACA")
        assert ca is not None, "INGRESS_KAFKACA missing in SSL mode"
        assert ca["value"] == "/etc/kafka/certs/ca.crt"

    def test_ingress_no_sasl_vars_in_ssl_only(self, chart_path):
        """SSL-only mode should not render SASL vars on ingress."""
        success, output = helm_template(chart_path, set_values=KAFKA_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "ingress")
        if dep is None:
            pytest.skip("ingress deployment not found")
        container = _get_container(dep)

        for var in ("INGRESS_SASLMECHANISM", "INGRESS_KAFKAUSERNAME",
                     "INGRESS_KAFKAPASSWORD"):
            env = _get_env_var(container, var)
            assert env is None, (
                f"ingress: {var} should not be present in SSL-only mode"
            )

    def test_ingress_sasl_ssl_all_vars(self, chart_path):
        """SASL_SSL mode renders all INGRESS_* env vars on ingress."""
        success, output = helm_template(chart_path, set_values=KAFKA_SASL_SSL_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "ingress")
        if dep is None:
            pytest.skip("ingress deployment not found")
        container = _get_container(dep)

        proto = _get_env_var(container, "INGRESS_KAFKASECURITYPROTOCOL")
        assert proto is not None, "INGRESS_KAFKASECURITYPROTOCOL missing in SASL_SSL"
        assert proto["value"] == "SASL_SSL"

        mech = _get_env_var(container, "INGRESS_SASLMECHANISM")
        assert mech is not None, "INGRESS_SASLMECHANISM missing in SASL_SSL"
        assert mech["value"] == "SCRAM-SHA-512"

        user = _get_env_var(container, "INGRESS_KAFKAUSERNAME")
        assert user is not None, "INGRESS_KAFKAUSERNAME missing in SASL_SSL"

        password = _get_env_var(container, "INGRESS_KAFKAPASSWORD")
        assert password is not None, "INGRESS_KAFKAPASSWORD missing in SASL_SSL"

        ca = _get_env_var(container, "INGRESS_KAFKACA")
        assert ca is not None, "INGRESS_KAFKACA missing in SASL_SSL"
        assert ca["value"] == "/etc/kafka/certs/ca.crt"

    def test_ingress_plaintext_no_tls_vars(self, chart_path):
        """PLAINTEXT mode has no INGRESS_KAFKASECURITYPROTOCOL or INGRESS_KAFKACA."""
        success, output = helm_template(chart_path, set_values=OFFLINE_MOCK_VALUES)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "ingress")
        if dep is None:
            pytest.skip("ingress deployment not found")
        container = _get_container(dep)

        proto = _get_env_var(container, "INGRESS_KAFKASECURITYPROTOCOL")
        assert proto is None, (
            "INGRESS_KAFKASECURITYPROTOCOL should not be present in PLAINTEXT"
        )

        ca = _get_env_var(container, "INGRESS_KAFKACA")
        assert ca is None, "INGRESS_KAFKACA should not be present in PLAINTEXT"


@pytest.mark.helm
@pytest.mark.component
class TestKafkaSaslWithoutSecret:
    """SASL mechanism set but no existingSecret - credentials should be omitted."""

    SASL_NO_SECRET_VALUES = {
        **OFFLINE_MOCK_VALUES,
        "kafka.securityProtocol": "SASL_PLAINTEXT",
        "kafka.sasl.mechanism": "SCRAM-SHA-512",
    }

    def test_mechanism_present_without_secret(self, chart_path):
        """SASL mechanism renders even without existingSecret."""
        success, output = helm_template(
            chart_path, set_values=self.SASL_NO_SECRET_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if dep is None:
            pytest.skip("koku-api deployment not found")
        container = _get_container(dep)

        mech = _get_env_var(container, "KAFKA_SASL_MECHANISM")
        assert mech is not None, "KAFKA_SASL_MECHANISM should be present"
        assert mech["value"] == "SCRAM-SHA-512"

    def test_no_credentials_without_secret(self, chart_path):
        """Username/password should not render without existingSecret."""
        success, output = helm_template(
            chart_path, set_values=self.SASL_NO_SECRET_VALUES
        )
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if dep is None:
            pytest.skip("koku-api deployment not found")
        container = _get_container(dep)

        for var in ("KAFKA_SASL_USERNAME", "KAFKA_SASL_PASSWORD"):
            env = _get_env_var(container, var)
            assert env is None, (
                f"{var} should not be present without existingSecret"
            )


@pytest.mark.helm
@pytest.mark.component
class TestKafkaSaslMechanismVariants:
    """Different SASL mechanisms render correctly."""

    @pytest.mark.parametrize("mechanism", ["PLAIN", "SCRAM-SHA-512"])
    def test_mechanism_value_rendered(self, chart_path, mechanism):
        """KAFKA_SASL_MECHANISM reflects the configured mechanism."""
        values = {
            **OFFLINE_MOCK_VALUES,
            "kafka.securityProtocol": "SASL_PLAINTEXT",
            "kafka.sasl.mechanism": mechanism,
            "kafka.sasl.existingSecret": "kafka-auth",
        }
        success, output = helm_template(chart_path, set_values=values)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if dep is None:
            pytest.skip("koku-api deployment not found")
        container = _get_container(dep)

        mech = _get_env_var(container, "KAFKA_SASL_MECHANISM")
        assert mech is not None, "KAFKA_SASL_MECHANISM missing"
        assert mech["value"] == mechanism


@pytest.mark.helm
@pytest.mark.component
class TestKafkaCaCertSecretReference:
    """TLS volume references the correct secret name and key."""

    @pytest.mark.parametrize("secret_name", ["my-kafka-ca", "custom-ca-bundle"])
    def test_volume_references_configured_secret(self, chart_path, secret_name):
        """kafka-ca-cert volume secretName matches kafka.tls.caCertSecret."""
        values = {
            **OFFLINE_MOCK_VALUES,
            "kafka.securityProtocol": "SSL",
            "kafka.tls.enabled": "true",
            "kafka.tls.caCertSecret": secret_name,
        }
        success, output = helm_template(chart_path, set_values=values)
        assert success, f"Template failed:\n{output}"

        manifests = _parse_manifests(output)
        dep = _find_by_kind_and_name(manifests, "Deployment", "koku-api")
        if dep is None:
            pytest.skip("koku-api deployment not found")
        pod_spec = _get_pod_spec(dep)

        vol = _get_volume(pod_spec, "kafka-ca-cert")
        assert vol is not None, "kafka-ca-cert volume missing"
        assert vol["secret"]["secretName"] == secret_name
