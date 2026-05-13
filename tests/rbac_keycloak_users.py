"""Keycloak Admin API helpers for RBAC-related tests.

Creates ephemeral realm users that match RBAC principal names so JWTs
obtained via password grant exercise the same Envoy → Koku/ROS path as real
clients. Intended for CI / lab clusters only (see deploy-rhbk.sh).
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from utils import get_secret_value

logger = logging.getLogger(__name__)


def fetch_keycloak_master_admin_token(
    keycloak_base_url: str,
    keycloak_namespace: str,
) -> Optional[str]:
    """Return a bearer token for the Keycloak master realm admin-cli user."""
    admin_password = get_secret_value(
        keycloak_namespace, "keycloak-initial-admin", "password"
    )
    admin_username = (
        get_secret_value(keycloak_namespace, "keycloak-initial-admin", "username")
        or "admin"
    )
    if not admin_password:
        logger.warning("Keycloak initial-admin password secret not found")
        return None

    base = keycloak_base_url.rstrip("/")
    resp = requests.post(
        f"{base}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": admin_username,
            "password": admin_password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=False,
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning(
            "Master realm token failed: %s %s",
            resp.status_code,
            resp.text[:200],
        )
        return None
    return resp.json().get("access_token")


def _realm_user_id(
    base: str,
    realm: str,
    admin_token: str,
    username: str,
) -> Optional[str]:
    r = requests.get(
        f"{base}/admin/realms/{realm}/users",
        params={"username": username, "exact": "true"},
        headers={"Authorization": f"Bearer {admin_token}"},
        verify=False,
        timeout=30,
    )
    if r.status_code != 200:
        return None
    users = r.json()
    return users[0]["id"] if users else None


def ensure_realm_user_with_password(
    *,
    keycloak_base_url: str,
    realm: str,
    admin_token: str,
    username: str,
    password: str,
    org_id: str,
    account_number: str,
    email: str,
) -> None:
    """Create or update a realm user with org_id / account_number attributes."""
    base = keycloak_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }

    user_id = _realm_user_id(base, realm, admin_token, username)
    body = {
        "username": username,
        "enabled": True,
        "email": email,
        "emailVerified": True,
        "attributes": {
            "org_id": [org_id],
            "account_number": [account_number],
        },
        "credentials": [
            {"type": "password", "value": password, "temporary": False},
        ],
    }

    if user_id:
        put_body = {k: v for k, v in body.items() if k != "credentials"}
        r = requests.put(
            f"{base}/admin/realms/{realm}/users/{user_id}",
            json=put_body,
            headers=headers,
            verify=False,
            timeout=30,
        )
        if r.status_code not in (204, 200):
            raise RuntimeError(
                f"Keycloak PUT user {username} failed: {r.status_code} {r.text[:300]}"
            )
    else:
        r = requests.post(
            f"{base}/admin/realms/{realm}/users",
            json=body,
            headers=headers,
            verify=False,
            timeout=30,
        )
        if r.status_code not in (201, 204) and r.status_code != 409:
            raise RuntimeError(
                f"Keycloak POST user {username} failed: {r.status_code} {r.text[:300]}"
            )

    if not user_id:
        user_id = _realm_user_id(base, realm, admin_token, username)
    if not user_id:
        raise RuntimeError(f"Could not resolve Keycloak user id for {username}")

    pr = requests.put(
        f"{base}/admin/realms/{realm}/users/{user_id}/reset-password",
        json={"type": "password", "value": password, "temporary": False},
        headers=headers,
        verify=False,
        timeout=30,
    )
    if pr.status_code not in (204, 200):
        raise RuntimeError(
            f"Keycloak reset-password for {username} failed: "
            f"{pr.status_code} {pr.text[:300]}"
        )


def ensure_rbac_gateway_persona_users(
    keycloak_base_url: str,
    keycloak_namespace: str,
    realm: str,
    org_id: str,
    account_number: str,
    *,
    password: str,
    extra_users: Optional[list[str]] = None,
) -> None:
    """Provision Keycloak users whose usernames match RBAC E2E principals."""
    token = fetch_keycloak_master_admin_token(keycloak_base_url, keycloak_namespace)
    if not token:
        raise RuntimeError("Could not obtain Keycloak master admin token")

    users = ["alice", "bob", "carol", "nobody-unassigned"]
    if extra_users:
        users = list(dict.fromkeys(users + list(extra_users)))

    for uname in users:
        ensure_realm_user_with_password(
            keycloak_base_url=keycloak_base_url,
            realm=realm,
            admin_token=token,
            username=uname,
            password=password,
            org_id=org_id,
            account_number=account_number,
            email=f"{uname}@rbac-gateway.test",
        )
