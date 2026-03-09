"""
Fixtures for external API tests.

These fixtures provide authenticated access to the API gateway for testing
the external API contract.

The ``authenticated_session`` fixture is overridden here to authenticate as
the ``admin`` Keycloak user via resource-owner password grant instead of
the service-account client-credentials flow defined in root conftest.  This
gives the session a real user identity with the ``cost-administrator`` Kessel
role, which is required for endpoints that enforce ReBAC authorization.
"""

import os

import pytest
import requests

from conftest import obtain_user_token


@pytest.fixture(scope="function")
def authenticated_session(keycloak_config, ui_client, gateway_url) -> requests.Session:
    """Requests session authenticated as the ``admin`` Keycloak user.

    Overrides the root conftest fixture so that all API-suite tests go
    through the gateway with a real user identity that has full
    ``cost-administrator`` permissions in Kessel.
    """
    client_id, client_secret = ui_client
    token = obtain_user_token(
        keycloak_config.url,
        keycloak_config.realm,
        client_id,
        client_secret,
        username=os.environ.get("AUTHZ_ADMIN_USER", "admin"),
        password=os.environ.get("AUTHZ_ADMIN_PASS", "admin"),
    )
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    session.verify = False
    return session


@pytest.fixture(scope="session")
def ocp_source_type_id(
    gateway_url: str,
    keycloak_config,
    ui_client,
) -> int:
    """Get the OpenShift source type ID from the API.

    Returns:
        int: The source type ID for OCP sources

    Skips:
        If the source types endpoint is not accessible or OCP type not found
    """
    client_id, client_secret = ui_client
    token = obtain_user_token(
        keycloak_config.url,
        keycloak_config.realm,
        client_id,
        client_secret,
        username=os.environ.get("AUTHZ_ADMIN_USER", "admin"),
        password=os.environ.get("AUTHZ_ADMIN_PASS", "admin"),
    )

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    session.verify = False

    try:
        response = session.get(
            f"{gateway_url}/cost-management/v1/source_types",
            timeout=30,
        )

        if response.status_code != 200:
            pytest.skip(f"Could not fetch source types: {response.status_code}")

        data = response.json()
        for source_type in data.get("data", []):
            if source_type.get("name") == "openshift":
                return source_type["id"]

        pytest.fail("openshift source type not found in source-types response")

    except requests.RequestException as e:
        pytest.skip(f"Failed to connect to gateway: {e}")
