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

from utils import get_route_url, get_secret_value, run_oc_command

# Import shared fixtures from cost_management suite
# These fixtures are available to all test suites
pytest_plugins = ["suites.cost_management.conftest"]

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
    database: str = "koku"
    user: str = "koku"
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


@pytest.fixture(scope="session")
def jwt_token(keycloak_config: KeycloakConfig) -> JWTToken:
    """Obtain a JWT token from Keycloak using client credentials flow."""
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


@pytest.fixture(scope="session")
def ingress_url(cluster_config: ClusterConfig) -> str:
    """Get the ingress service URL including the route path."""
    route_name = f"{cluster_config.helm_release_name}-ingress"
    url = get_route_url(cluster_config.namespace, route_name)
    if not url:
        pytest.skip(f"Ingress route '{route_name}' not found")
    
    # Get the route path (e.g., /api/ingress)
    result = run_oc_command([
        "get", "route", route_name, "-n", cluster_config.namespace,
        "-o", "jsonpath={.spec.path}"
    ], check=False)
    route_path = result.stdout.strip().rstrip("/")
    
    # Return URL with path
    return f"{url}{route_path}" if route_path else url


@pytest.fixture(scope="session")
def backend_api_url(cluster_config: ClusterConfig) -> str:
    """Get the backend API URL."""
    # Try different route name patterns
    for route_suffix in ["main", "ros-api"]:
        route_name = f"{cluster_config.helm_release_name}-{route_suffix}"
        url = get_route_url(cluster_config.namespace, route_name)
        if url:
            return url

    pytest.skip("Backend API route not found")


@pytest.fixture(scope="session")
def database_config(cluster_config: ClusterConfig) -> DatabaseConfig:
    """Get database configuration for Koku."""
    # Find database pod
    result = run_oc_command([
        "get", "pods", "-n", cluster_config.namespace,
        "-l", "app.kubernetes.io/component=database",
        "-o", "jsonpath={.items[0].metadata.name}"
    ], check=False)
    
    db_pod = result.stdout.strip()
    if not db_pod:
        # Try fallback pod name
        db_pod = f"{cluster_config.helm_release_name}-database-0"
    
    # Get credentials from secret
    secret_name = f"{cluster_config.helm_release_name}-db-credentials"
    db_user = get_secret_value(cluster_config.namespace, secret_name, "koku-user")
    db_password = get_secret_value(cluster_config.namespace, secret_name, "koku-password")
    
    if not db_user:
        db_user = "koku"
    
    return DatabaseConfig(
        pod_name=db_pod,
        namespace=cluster_config.namespace,
        database="koku",
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
        f"{cluster_config.helm_release_name}-odf-credentials",  # ODF credentials
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
