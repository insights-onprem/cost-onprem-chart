"""
Auth suite fixtures.

Fixtures specific to JWT authentication testing.
"""

import os

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
    """Get test user credentials for password grant flow.
    
    Configurable via environment variables:
    - TEST_USERNAME: Keycloak username (default: "test")
    - TEST_PASSWORD: Keycloak password (default: "test")
    
    SECURITY NOTE: These credentials are ONLY valid in ephemeral CI test
    environments. The test Keycloak user is provisioned by the test harness
    bootstrap (see scripts/deploy-rhbk.sh). These credentials must never
    match any staging or production credentials.
    """
    return {
        "username": os.environ.get("TEST_USERNAME", "test"),
        "password": os.environ.get("TEST_PASSWORD", "test"),
    }
