"""
Pytest fixtures and configuration for cost-onprem-chart tests.

This is the root conftest.py that provides shared fixtures used across all test suites.
Suite-specific fixtures are defined in each suite's conftest.py.
"""

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
import requests
import urllib3

from utils import (
    check_pod_exists,
    exec_in_pod,
    get_pod_by_label,
    get_route_url,
    get_secret_value,
    run_oc_command,
)

# Import shared fixtures from test suites
# These fixtures are available to all test suites
pytest_plugins = ["suites.cost_management.conftest", "suites.sources.conftest"]

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ClusterConfig:
    """Configuration for the target cluster."""

    namespace: str
    helm_release_name: str
    keycloak_namespace: str
    platform: str = "openshift"
    project_root: str = field(default_factory=lambda: os.path.dirname(os.path.dirname(__file__)))


@dataclass
class KeycloakConfig:
    """Keycloak authentication configuration."""

    url: str
    client_id: str
    client_secret: str
    realm: str = "kubernetes"

    @property
    def token_url(self) -> str:
        """Get the token endpoint URL."""
        return f"{self.url}/realms/{self.realm}/protocol/openid-connect/token"


@dataclass
class JWTToken:
    """JWT token with expiry tracking."""

    access_token: str
    expires_at: datetime
    token_type: str = "Bearer"

    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def authorization_header(self) -> dict:
        """Get the Authorization header dict."""
        return {"Authorization": f"{self.token_type} {self.access_token}"}


@dataclass
class DatabaseConfig:
    """Database connection configuration."""

    pod_name: str
    namespace: str
    database: str = "costonprem_koku"  # Chart default from values.yaml
    user: str = "koku_user"  # Chart default from values.yaml
    password: Optional[str] = None


@dataclass
class S3Config:
    """S3/Object storage configuration."""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str = "koku-bucket"
    verify_ssl: bool = False


# =============================================================================
# Session-Scoped Fixtures (shared across all tests)
# =============================================================================


@pytest.fixture(scope="session")
def cluster_config() -> ClusterConfig:
    """Get cluster configuration from environment variables."""
    return ClusterConfig(
        namespace=os.environ.get("NAMESPACE", "cost-onprem"),
        helm_release_name=os.environ.get("HELM_RELEASE_NAME", "cost-onprem"),
        keycloak_namespace=os.environ.get("KEYCLOAK_NAMESPACE", "keycloak"),
        platform=os.environ.get("PLATFORM", "openshift"),
    )


@pytest.fixture(scope="session")
def keycloak_config(cluster_config: ClusterConfig) -> KeycloakConfig:
    """Detect and return Keycloak configuration."""
    # Try to find Keycloak route
    keycloak_url = get_route_url(cluster_config.keycloak_namespace, "keycloak")
    if not keycloak_url:
        pytest.skip(
            f"Keycloak route not found in namespace {cluster_config.keycloak_namespace}"
        )

    # Get client credentials from secret
    client_id = "cost-management-operator"
    client_secret = None

    # Try different secret name patterns
    secret_patterns = [
        "keycloak-client-secret-cost-management-operator",
        "keycloak-client-secret-cost-management-service-account",
        f"credential-{client_id}",
        f"keycloak-client-{client_id}",
        f"{client_id}-secret",
    ]

    for secret_name in secret_patterns:
        client_secret = get_secret_value(
            cluster_config.keycloak_namespace, secret_name, "CLIENT_SECRET"
        )
        if client_secret:
            break

    if not client_secret:
        pytest.skip(
            f"Client secret not found in namespace {cluster_config.keycloak_namespace}"
        )

    return KeycloakConfig(
        url=keycloak_url,
        client_id=client_id,
        client_secret=client_secret,
    )


def obtain_jwt_token(keycloak_config: KeycloakConfig) -> JWTToken:
    """Obtain a fresh JWT token from Keycloak using client credentials flow.
    
    This is a helper function that can be called by fixtures or tests that need
    to generate their own tokens. Use this directly when you need control over
    token lifecycle (e.g., module-scoped fixtures that run longer than 5 minutes).
    
    Args:
        keycloak_config: Keycloak configuration with URL and credentials
        
    Returns:
        JWTToken object with access token and expiration time
        
    Raises:
        pytest.fail: If token request fails
    """
    response = requests.post(
        keycloak_config.token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": keycloak_config.client_id,
            "client_secret": keycloak_config.client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=False,
        timeout=30,
    )

    if response.status_code != 200:
        pytest.fail(f"Failed to obtain JWT token: {response.status_code} - {response.text}")

    token_data = response.json()
    expires_in = token_data.get("expires_in", 300)

    return JWTToken(
        access_token=token_data["access_token"],
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )


@pytest.fixture(scope="function")
def jwt_token(keycloak_config: KeycloakConfig) -> JWTToken:
    """Obtain a JWT token from Keycloak using client credentials flow.
    
    Scope: function - Each test gets a fresh token to prevent expiration.
    Keycloak tokens expire after 5 minutes. Using function scope ensures each
    test gets a fresh token, eliminating any possibility of expiration during
    test execution.
    
    For fixtures that need tokens and run longer than 5 minutes, call
    obtain_jwt_token(keycloak_config) directly instead of depending on this fixture.
    """
    return obtain_jwt_token(keycloak_config)


@pytest.fixture(scope="session")
def gateway_url(cluster_config: ClusterConfig) -> str:
    """Get the API gateway URL.

    The centralized Envoy gateway handles all API traffic with JWT authentication.
    Routes: /api/* -> gateway -> backend services
    """
    route_name = f"{cluster_config.helm_release_name}-api"
    url = get_route_url(cluster_config.namespace, route_name)
    if not url:
        pytest.skip(f"Gateway route '{route_name}' not found")

    # Get the route path (e.g., /api)
    result = run_oc_command([
        "get", "route", route_name, "-n", cluster_config.namespace,
        "-o", "jsonpath={.spec.path}"
    ], check=False)
    route_path = result.stdout.strip().rstrip("/")

    # Return URL with path
    return f"{url}{route_path}" if route_path else url


@pytest.fixture(scope="session")
def ingress_url(gateway_url: str) -> str:
    """Get the ingress upload URL via the gateway.

    All API traffic now routes through the centralized gateway.
    Ingress upload endpoint: /api/ingress/v1/upload
    """
    # The gateway route already includes /api prefix
    # Ingress endpoint is at /api/ingress/*
    base = gateway_url.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/ingress"
    return f"{base}/api/ingress"


# =============================================================================
# Database Discovery Helpers
# =============================================================================

@dataclass
class DbHostLookup:
    """Maps a Kubernetes pod label to the environment variable that holds the resolved DB host."""

    label: str
    env_var: str


_DB_HOST_LOOKUPS = [
    DbHostLookup(label="app.kubernetes.io/component=ros-api", env_var="DB_HOST"),
    DbHostLookup(label="app.kubernetes.io/component=cost-management-api", env_var="DATABASE_SERVICE_HOST"),
    DbHostLookup(label="app.kubernetes.io/component=cost-processor", env_var="DATABASE_SERVICE_HOST"),
]


def _get_db_host_from_app_pod(cluster_config: ClusterConfig) -> Optional[str]:
    """Read the database host from a running app pod's environment.

    The Helm templates resolve the database host (bundled or external) and inject
    it as an environment variable into every app pod.  Reading it back gives us
    the concrete hostname without needing to parse Helm values or sentinels.
    """
    for lookup in _DB_HOST_LOOKUPS:
        pod = get_pod_by_label(cluster_config.namespace, lookup.label)
        if pod:
            result = exec_in_pod(
                cluster_config.namespace, pod, ["printenv", lookup.env_var]
            )
            if result and result.strip():
                return result.strip()
    return None


@dataclass
class ParsedService:
    """A Kubernetes service parsed from its FQDN."""

    namespace: str
    name: str


def _parse_k8s_service(hostname: str) -> Optional[ParsedService]:
    """Parse a Kubernetes service FQDN into a ParsedService.

    Handles forms like:
      postgresql.byoi-infra.svc.cluster.local  -> ParsedService("byoi-infra", "postgresql")
      postgresql.byoi-infra.svc                -> ParsedService("byoi-infra", "postgresql")
    Returns None for non-FQDN hostnames.
    """
    parts = hostname.split(".")
    if len(parts) >= 3 and "svc" in parts:
        svc_idx = parts.index("svc")
        if svc_idx >= 2:
            return ParsedService(
                namespace=parts[svc_idx - 1],
                name=parts[svc_idx - 2],
            )
    return None


def _find_db_pod(namespace: str, service_name: str) -> Optional[str]:
    """Find the database pod backing a Kubernetes service.

    Tries service endpoints first, then falls back to common PostgreSQL labels.
    """
    # Try endpoints (most reliable – works for any service type)
    result = run_oc_command([
        "get", "endpoints", service_name, "-n", namespace,
        "-o", "jsonpath={.subsets[0].addresses[0].targetRef.name}"
    ], check=False)
    if result.stdout.strip():
        return result.stdout.strip()

    # Fallback: common PostgreSQL label selectors
    for label in [
        "app.kubernetes.io/component=database",
        "app=postgresql",
        "app.kubernetes.io/name=postgresql",
    ]:
        pod = get_pod_by_label(namespace, label)
        if pod:
            return pod
    return None


@pytest.fixture(scope="session")
def database_deployed(cluster_config: ClusterConfig) -> bool:
    """Detect whether the chart deployed a bundled database pod.

    Returns True for default (bundled) deployments, False for BYOI.
    Used only by tests that verify chart-created resources (pod exists, service exists).
    """
    return check_pod_exists(
        cluster_config.namespace, "app.kubernetes.io/component=database"
    )


@pytest.fixture(scope="session")
def database_config(cluster_config: ClusterConfig) -> DatabaseConfig:
    """Discover the database pod and return Koku database configuration.

    Single code path for both bundled and BYOI deployments:
    1. Read the resolved DB_HOST from a running app pod
    2. Parse the hostname to determine namespace and service
    3. Find the actual database pod via service endpoints
    4. Detect the actual database name from the Koku deployment
    """
    # Step 1: Get the resolved DB host from any running app pod
    db_host = _get_db_host_from_app_pod(cluster_config)
    if not db_host:
        pytest.skip(
            "Cannot determine database host (no app pod with DB_HOST found)"
        )

    # Step 2: Resolve hostname to namespace + service name
    if ".svc" in db_host:
        # FQDN: "postgresql.byoi-infra.svc.cluster.local"
        parsed = _parse_k8s_service(db_host)
        if not parsed:
            pytest.skip(f"Cannot parse k8s service from DB host: {db_host}")
        db_namespace = parsed.namespace
        service_name = parsed.name
    else:
        # Simple name: "cost-onprem-database" (same namespace)
        db_namespace = cluster_config.namespace
        service_name = db_host

    # Step 3: Find the actual database pod
    db_pod = _find_db_pod(db_namespace, service_name)
    if not db_pod:
        pytest.skip(
            f"Cannot locate database pod for host: {db_host} "
            f"(namespace: {db_namespace})"
        )

    # Step 4: Get credentials from chart secret (always in chart namespace)
    secret_name = f"{cluster_config.helm_release_name}-db-credentials"
    db_user = get_secret_value(cluster_config.namespace, secret_name, "koku-user")
    db_password = get_secret_value(cluster_config.namespace, secret_name, "koku-password")

    if not db_user:
        db_user = "koku_user"  # Chart default from values.yaml

    # Detect actual database name from Koku deployment (unified koku-api)
    db_name_result = run_oc_command([
        "get", "deployment", f"{cluster_config.helm_release_name}-koku-api",
        "-n", cluster_config.namespace,
        "-o", "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='DATABASE_NAME')].value}"
    ], check=False)

    db_name = db_name_result.stdout.strip() if db_name_result.returncode == 0 else ""
    if not db_name:
        db_name = "costonprem_koku"  # Chart default from values.yaml

    return DatabaseConfig(
        pod_name=db_pod,
        namespace=db_namespace,
        database=db_name,
        user=db_user,
        password=db_password,
    )


@pytest.fixture(scope="session")
def kruize_database_config(
    cluster_config: ClusterConfig, database_config: DatabaseConfig
) -> DatabaseConfig:
    """Get database configuration for Kruize.

    Reuses the database pod discovered by database_config (same unified server)
    and detects the Kruize database name from the Kruize deployment.
    """
    # Get Kruize credentials from secret (always in chart namespace)
    secret_name = f"{cluster_config.helm_release_name}-db-credentials"
    db_user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
    db_password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")

    if not db_user:
        db_user = "kruize_user"

    # Detect actual database name from Kruize deployment
    db_name_result = run_oc_command([
        "get", "deployment", f"{cluster_config.helm_release_name}-kruize",
        "-n", cluster_config.namespace,
        "-o", "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='database_name')].value}"
    ], check=False)

    db_name = db_name_result.stdout.strip() if db_name_result.returncode == 0 else ""
    if not db_name:
        db_name = "costonprem_kruize"  # Default from values.yaml

    return DatabaseConfig(
        pod_name=database_config.pod_name,
        namespace=database_config.namespace,
        database=db_name,
        user=db_user,
        password=db_password,
    )


@pytest.fixture(scope="session")
def s3_config(cluster_config: ClusterConfig) -> Optional[S3Config]:
    """Get S3/Object storage configuration."""
    # Try to get S3 route (OpenShift ODF)
    s3_endpoint = None
    
    # Try external route first
    result = run_oc_command([
        "get", "route", "-n", "openshift-storage", "s3",
        "-o", "jsonpath={.spec.host}"
    ], check=False)
    
    if result.stdout.strip():
        s3_endpoint = f"https://{result.stdout.strip()}"
    else:
        # Fallback to internal service
        s3_endpoint = "https://s3.openshift-storage.svc:443"
    
    # Get credentials - try multiple secret name patterns
    # The helm chart uses 'cost-onprem-storage-credentials' (release name prefix)
    # but the namespace might be different from the helm release name
    storage_secret_patterns = [
        f"{cluster_config.helm_release_name}-storage-credentials",  # Helm release name
        f"{cluster_config.namespace}-storage-credentials",  # Namespace-based
        "cost-onprem-storage-credentials",  # Default helm release name
        "koku-storage-credentials",  # Legacy name
        f"{cluster_config.helm_release_name}-object-storage-credentials",  # Object storage credentials
    ]
    
    access_key = None
    secret_key = None
    
    for secret_name in storage_secret_patterns:
        access_key = get_secret_value(cluster_config.namespace, secret_name, "access-key")
        secret_key = get_secret_value(cluster_config.namespace, secret_name, "secret-key")
        if access_key and secret_key:
            break
    
    if not access_key or not secret_key:
        return None
    
    return S3Config(
        endpoint=s3_endpoint,
        access_key=access_key,
        secret_key=secret_key,
    )


@pytest.fixture(scope="session", autouse=True)
def s3_bucket_preflight(cluster_config: ClusterConfig, s3_config: Optional[S3Config]) -> None:
    """Pre-flight check: Ensure required S3 buckets exist before running tests.

    This fixture runs automatically at the start of the test session and creates
    any missing S3 buckets. This prevents test failures due to missing buckets
    when the install script's bucket creation fails (e.g., network issues
    downloading the MinIO client).

    Required buckets (from values.yaml):
    - koku-bucket: Main cost data storage
    - ros-data: ROS processor data
    - insights-upload-perma: Ingress upload storage
    """
    if s3_config is None:
        # No S3 config available - skip bucket check
        # Tests that need S3 will fail with appropriate errors
        return

    required_buckets = ["koku-bucket", "ros-data", "insights-upload-perma"]

    # Execute bucket check/creation inside the koku-api pod which has boto3 and credentials
    bucket_check_script = f'''
import boto3
import os
import sys

s3 = boto3.client('s3',
    endpoint_url=os.environ.get('S3_ENDPOINT', '{s3_config.endpoint}'),
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    verify=False
)

required_buckets = {required_buckets!r}
created = []
existing = []
failed = []

for bucket in required_buckets:
    try:
        s3.head_bucket(Bucket=bucket)
        existing.append(bucket)
    except s3.exceptions.ClientError as e:
        error_code = e.response.get('Error', {{}}).get('Code', '')
        if error_code in ('404', 'NoSuchBucket'):
            try:
                s3.create_bucket(Bucket=bucket)
                created.append(bucket)
            except Exception as create_err:
                failed.append((bucket, str(create_err)))
        else:
            failed.append((bucket, str(e)))
    except Exception as e:
        failed.append((bucket, str(e)))

if existing:
    print(f"Existing buckets: {{', '.join(existing)}}")
if created:
    print(f"Created buckets: {{', '.join(created)}}")
if failed:
    print(f"Failed buckets: {{failed}}", file=sys.stderr)
    sys.exit(1)
'''

    # Run the script inside the koku-api pod
    result = run_oc_command(
        [
            "exec", "-n", cluster_config.namespace,
            "deployment/cost-onprem-koku-api", "--",
            "python3", "-c", bucket_check_script
        ],
        check=False,
    )

    if result.returncode != 0:
        pytest.fail(
            f"S3 bucket pre-flight check failed: {result.stderr}\n"
            "Required buckets could not be created: koku-bucket, ros-data, insights-upload-perma\n"
            "Check S3/object storage configuration and connectivity."
        )
    elif result.stdout.strip():
        # Log bucket status for visibility
        print(f"\n[S3 Pre-flight] {result.stdout.strip()}")


@pytest.fixture(scope="session")
def org_id(cluster_config: ClusterConfig, keycloak_config: KeycloakConfig) -> str:
    """Get org_id from Keycloak test user or use default."""
    import base64
    
    try:
        # Get admin credentials
        admin_pass_result = run_oc_command([
            "get", "secret", "-n", cluster_config.keycloak_namespace,
            "keycloak-initial-admin", "-o", "jsonpath={.data.password}"
        ], check=False)
        
        if not admin_pass_result.stdout.strip():
            return "org1234567"
        
        admin_password = base64.b64decode(admin_pass_result.stdout.strip()).decode("utf-8")
        
        # Get admin token
        token_response = requests.post(
            f"{keycloak_config.url}/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "admin",
                "password": admin_password,
            },
            verify=False,
            timeout=30,
        )
        
        if token_response.status_code != 200:
            return "org1234567"
        
        admin_token = token_response.json().get("access_token")
        
        # Get test user's org_id
        users_response = requests.get(
            f"{keycloak_config.url}/admin/realms/kubernetes/users",
            params={"username": "test", "exact": "true"},
            headers={"Authorization": f"Bearer {admin_token}"},
            verify=False,
            timeout=30,
        )
        
        if users_response.status_code == 200:
            users = users_response.json()
            if users:
                org_id_value = users[0].get("attributes", {}).get("org_id", [None])[0]
                if org_id_value:
                    return org_id_value
        
        return "org1234567"
    except Exception:
        return "org1234567"


# =============================================================================
# Function-Scoped Fixtures (fresh for each test)
# =============================================================================


@pytest.fixture
def unique_cluster_id() -> str:
    """Generate a unique cluster ID for test uploads."""
    return f"test-cluster-{int(time.time())}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_csv_data() -> str:
    """Generate test CSV data with current timestamps."""
    now = datetime.now(timezone.utc)
    now_date = now.strftime("%Y-%m-%d")

    def format_timestamp(minutes_ago: int) -> str:
        ts = now - timedelta(minutes=minutes_ago)
        return ts.strftime("%Y-%m-%d %H:%M:%S -0000 UTC")

    intervals = [
        (75, 60),
        (60, 45),
        (45, 30),
        (30, 15),
    ]

    header = (
        "report_period_start,report_period_end,interval_start,interval_end,"
        "container_name,pod,owner_name,owner_kind,workload,workload_type,"
        "namespace,image_name,node,resource_id,"
        "cpu_request_container_avg,cpu_request_container_sum,"
        "cpu_limit_container_avg,cpu_limit_container_sum,"
        "cpu_usage_container_avg,cpu_usage_container_min,cpu_usage_container_max,cpu_usage_container_sum,"
        "cpu_throttle_container_avg,cpu_throttle_container_max,cpu_throttle_container_sum,"
        "memory_request_container_avg,memory_request_container_sum,"
        "memory_limit_container_avg,memory_limit_container_sum,"
        "memory_usage_container_avg,memory_usage_container_min,memory_usage_container_max,memory_usage_container_sum,"
        "memory_rss_usage_container_avg,memory_rss_usage_container_min,memory_rss_usage_container_max,memory_rss_usage_container_sum"
    )

    rows = [header]
    cpu_usages = [0.247832, 0.265423, 0.289567, 0.234567]
    memory_usages = [413587266, 427891456, 445678901, 398765432]

    for i, (start_ago, end_ago) in enumerate(intervals):
        row = (
            f"{now_date},{now_date},"
            f"{format_timestamp(start_ago)},{format_timestamp(end_ago)},"
            "test-container,test-pod-123,test-deployment,Deployment,test-workload,deployment,"
            "test-namespace,quay.io/test/image:latest,worker-node-1,resource-123,"
            f"0.5,0.5,1.0,1.0,{cpu_usages[i]},0.185671,0.324131,{cpu_usages[i]},"
            "0.001,0.002,0.001,"
            f"536870912,536870912,1073741824,1073741824,"
            f"{memory_usages[i]},410009344,420900544,{memory_usages[i]},"
            f"{memory_usages[i] - 20000000},390293568,396371392,{memory_usages[i] - 20000000}"
        )
        rows.append(row)

    return "\n".join(rows)


@pytest.fixture
def http_session() -> requests.Session:
    """Create a requests session with SSL verification disabled."""
    session = requests.Session()
    session.verify = False
    return session


# =============================================================================
# External API Access Fixtures (for api/ tests)
# =============================================================================


@pytest.fixture(scope="function")
def authenticated_session(jwt_token: JWTToken) -> requests.Session:
    """Pre-configured requests session with JWT authentication.
    
    Scope: function - Matches jwt_token scope to ensure fresh auth per test.
    
    Use this fixture for external API tests that go through the gateway.
    The session includes:
    - Authorization header with Bearer token
    - SSL verification disabled (for self-signed certs)
    
    Note: Content-Type is NOT set by default to allow multipart/form-data
    uploads to work correctly. Set it explicitly in tests that need JSON.
    """
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {jwt_token.access_token}",
    })
    session.verify = False
    return session


# =============================================================================
# Internal Cluster Access Fixtures (for internal/ tests)
# =============================================================================


@pytest.fixture(scope="session")
def test_runner_pod(cluster_config: ClusterConfig):
    """Dedicated test runner pod for internal cluster commands.
    
    Provides a consistent environment for executing commands inside the cluster
    without depending on application pod availability.
    
    Benefits:
    - Isolation: Tests don't interfere with application pods
    - Consistent tooling: Same tools available regardless of app pods
    - No container guessing: Don't need to find "a pod that has curl"
    - Cleaner logs: Test output doesn't pollute application logs
    
    The pod is created at session start and cleaned up at session end
    (unless E2E_CLEANUP_AFTER=false).
    """
    import json
    
    namespace = cluster_config.namespace
    pod_name = "cost-onprem-test-runner"
    
    # Check if pod already exists and is ready
    check_result = run_oc_command([
        "get", "pod", pod_name, "-n", namespace,
        "-o", "jsonpath={.status.phase}"
    ], check=False)
    
    if check_result.returncode == 0 and check_result.stdout.strip() == "Running":
        # Pod already exists and is running
        yield pod_name
        # Don't delete if we didn't create it
        return
    
    # Delete any existing pod that's not running
    run_oc_command([
        "delete", "pod", pod_name, "-n", namespace, "--ignore-not-found"
    ], check=False)
    
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "test-runner",
                "app.kubernetes.io/component": "testing",
                "app.kubernetes.io/part-of": "cost-onprem-tests",
            }
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [{
                "name": "runner",
                "image": "registry.access.redhat.com/ubi9/ubi:latest",
                "command": ["sleep", "infinity"],
                "resources": {
                    "requests": {"memory": "64Mi", "cpu": "100m"},
                    "limits": {"memory": "256Mi", "cpu": "500m"}
                }
            }]
        }
    }
    
    # Create pod using kubectl apply with stdin
    import subprocess
    result = subprocess.run(
        ["oc", "apply", "-n", namespace, "-f", "-"],
        input=json.dumps(pod_manifest),
        capture_output=True,
        text=True,
        check=False,
    )
    
    if result.returncode != 0:
        pytest.skip(f"Failed to create test runner pod: {result.stderr}")
    
    # Wait for pod to be ready
    wait_result = run_oc_command([
        "wait", "pod", pod_name, "-n", namespace,
        "--for=condition=Ready", "--timeout=120s"
    ], check=False)
    
    if wait_result.returncode != 0:
        # Clean up failed pod
        run_oc_command(["delete", "pod", pod_name, "-n", namespace, "--ignore-not-found"], check=False)
        pytest.skip(f"Test runner pod failed to become ready: {wait_result.stderr}")
    
    yield pod_name
    
    # Cleanup (unless E2E_CLEANUP_AFTER=false)
    if os.environ.get("E2E_CLEANUP_AFTER", "true").lower() == "true":
        run_oc_command([
            "delete", "pod", pod_name, "-n", namespace, "--ignore-not-found"
        ], check=False)


@pytest.fixture(scope="session")
def internal_api_url(cluster_config: ClusterConfig) -> str:
    """Internal Koku API URL (ClusterIP service).
    
    Use this for tests that need to bypass the gateway and test
    Koku API directly via internal service networking.
    
    Format: http://{release}-koku-api.{namespace}.svc:8000
    """
    return f"http://{cluster_config.helm_release_name}-koku-api.{cluster_config.namespace}.svc:8000"


@pytest.fixture(scope="session")
def internal_ros_api_url(cluster_config: ClusterConfig) -> str:
    """Internal ROS API URL (ClusterIP service).
    
    Use this for tests that need to bypass the gateway and test
    ROS API directly via internal service networking.
    
    Format: http://{release}-ros-api.{namespace}.svc:8000
    """
    return f"http://{cluster_config.helm_release_name}-ros-api.{cluster_config.namespace}.svc:8000"
