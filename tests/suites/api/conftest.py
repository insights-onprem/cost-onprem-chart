"""
Fixtures for external API tests.

These fixtures provide authenticated access to the API gateway for testing
the external API contract.
"""

import pytest
import requests

from conftest import ClusterConfig, JWTToken, obtain_jwt_token
from utils import run_oc_command


@pytest.fixture(scope="session")
def ocp_source_type_id(
    gateway_url: str,
    keycloak_config,
) -> int:
    """Get the OpenShift source type ID from the API.
    
    This fixture retrieves the source type ID for OpenShift Container Platform
    sources, which is needed when creating test sources.
    
    Returns:
        int: The source type ID for OCP sources
        
    Skips:
        If the source types endpoint is not accessible or OCP type not found
    """
    # Get a fresh token for this session-scoped fixture
    token = obtain_jwt_token(keycloak_config)
    
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token.access_token}",
    })
    session.verify = False
    
    try:
        response = session.get(
            f"{gateway_url}/cost-management/v1/source_types/",
            timeout=30,
        )
        
        if response.status_code != 200:
            pytest.skip(f"Could not fetch source types: {response.status_code}")
        
        data = response.json()
        for source_type in data.get("data", []):
            if source_type.get("name") == "OCP":
                return source_type["id"]
        
        pytest.fail("OCP source type not found in source-types response")
        
    except requests.RequestException as e:
        pytest.skip(f"Failed to connect to gateway: {e}")
