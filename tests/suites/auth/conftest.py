"""
Auth suite fixtures.

Fixtures specific to JWT authentication testing.
"""

import pytest
import requests


@pytest.fixture
def ui_client_config(cluster_config, keycloak_config):
    """Get UI client configuration for OAuth testing."""
    from utils import get_secret_value
    
    client_id = "cost-management-ui"
    client_secret = get_secret_value(
        cluster_config.keycloak_namespace,
        f"keycloak-client-secret-{client_id}",
        "CLIENT_SECRET"
    )
    
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "keycloak_url": keycloak_config.url,
        "realm": keycloak_config.realm,
    }


@pytest.fixture
def test_user_credentials():
    """Get test user credentials for password grant flow."""
    return {
        "username": "admin",
        "password": "admin",
    }


@pytest.fixture
def non_admin_user_credentials():
    """Credentials for the non-admin viewer user (no org-admin realm role)."""
    return {
        "username": "viewer",
        "password": "viewer",
    }


@pytest.fixture
def http_session() -> requests.Session:
    """Shared requests session with SSL verification disabled."""
    session = requests.Session()
    session.verify = False
    return session
