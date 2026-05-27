"""
Unit tests for the Keycloak-to-RBAC sync Python script.

These tests exercise the KeycloakClient and sync logic from
sync_keycloak_principals.py by importing the script as a module and
mocking the Keycloak API responses and Django ORM.
"""

import importlib.util
import json
import os
import sys
import types
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(scope="module")
def sync_module() -> types.ModuleType:
    """Import sync_keycloak_principals.py as a module without executing main()."""
    script_path = Path(__file__).parents[3] / "cost-onprem" / "scripts" / "sync_keycloak_principals.py"
    if not script_path.exists():
        pytest.skip(f"Sync script not found at {script_path}")

    spec = importlib.util.spec_from_file_location("sync_keycloak_principals", script_path)
    module = importlib.util.module_from_spec(spec)

    source = script_path.read_text()
    source = source.replace("\nmain()\n", "\n# main() removed for testing\n")
    source = source.replace("\nmain()", "\n# main() removed for testing")
    code = compile(source, str(script_path), "exec")
    exec(code, module.__dict__)

    return module


def _make_http_response(data, status=200):
    """Create a mock HTTP response object."""
    body = json.dumps(data).encode()
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


@pytest.mark.helm
@pytest.mark.component
class TestKeycloakClient:
    """Tests for the KeycloakClient class."""

    def test_authenticate_stores_token(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        token_response = _make_http_response({
            "access_token": "test-token-abc",
            "expires_in": 300,
        })
        with mock.patch("urllib.request.urlopen", return_value=token_response):
            kc.authenticate()

        assert kc._access_token == "test-token-abc"
        assert kc._token_expires_in == 300

    def test_token_freshness_check(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        assert not kc._token_is_fresh(), "Token should not be fresh before authentication"

        token_response = _make_http_response({
            "access_token": "fresh-token",
            "expires_in": 300,
        })
        with mock.patch("urllib.request.urlopen", return_value=token_response):
            kc.authenticate()

        assert kc._token_is_fresh(), "Token should be fresh right after authentication"

    def test_ensure_authenticated_lazy(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        token_response = _make_http_response({
            "access_token": "lazy-token",
            "expires_in": 300,
        })
        with mock.patch("urllib.request.urlopen", return_value=token_response) as mock_open:
            kc.ensure_authenticated()
            kc.ensure_authenticated()

        assert mock_open.call_count == 1, "Should only authenticate once when token is fresh"

    def test_list_all_users_pagination(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        kc._access_token = "test-token"
        kc._token_acquired_at = __import__("time").monotonic()
        kc._token_expires_in = 300

        page1 = [{"username": f"user{i}"} for i in range(100)]
        page2 = [{"username": f"user{i}"} for i in range(100, 130)]

        responses = [_make_http_response(page1), _make_http_response(page2)]
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            users = kc.list_all_users()

        assert len(users) == 130

    def test_list_all_users_single_page(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        kc._access_token = "test-token"
        kc._token_acquired_at = __import__("time").monotonic()
        kc._token_expires_in = 300

        page = [{"username": f"user{i}"} for i in range(5)]

        with mock.patch("urllib.request.urlopen", return_value=_make_http_response(page)):
            users = kc.list_all_users()

        assert len(users) == 5

    def test_get_role_members(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        kc._access_token = "test-token"
        kc._token_acquired_at = __import__("time").monotonic()
        kc._token_expires_in = 300

        members = [{"username": "admin1"}, {"username": "admin2"}]
        with mock.patch("urllib.request.urlopen", return_value=_make_http_response(members)):
            result = kc.get_role_members("org-admin")

        assert len(result) == 2
        assert result[0]["username"] == "admin1"

    def test_get_role_members_404(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret",
        )
        kc._access_token = "test-token"
        kc._token_acquired_at = __import__("time").monotonic()
        kc._token_expires_in = 300

        error = urllib.error.HTTPError(
            "https://keycloak.example.com/admin/realms/testrealm/roles/missing/users",
            404, "Not Found", {}, BytesIO(b""),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                kc.get_role_members("missing-role")
            assert exc_info.value.code == 404

    def test_ssl_verification_disabled(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret", verify_tls=False,
        )
        assert kc._ssl_ctx.check_hostname is False

    def test_ssl_verification_enabled(self, sync_module):
        kc = sync_module.KeycloakClient(
            "https://keycloak.example.com", "testrealm",
            "client-id", "client-secret", verify_tls=True,
        )
        assert kc._ssl_ctx.check_hostname is True


@pytest.mark.helm
@pytest.mark.component
class TestMainValidation:
    """Tests for main() input validation."""

    def test_missing_env_vars_exits(self, sync_module):
        env = {
            "KEYCLOAK_URL": "",
            "KEYCLOAK_CLIENT_ID": "",
            "KEYCLOAK_CLIENT_SECRET": "",
            "SYNC_ORG_ID": "",
            "SYNC_ACCOUNT_NUMBER": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                sync_module.main()
            assert exc_info.value.code == 1

    def test_partial_env_exits(self, sync_module):
        env = {
            "KEYCLOAK_URL": "https://keycloak.example.com",
            "KEYCLOAK_CLIENT_ID": "my-client",
            "KEYCLOAK_CLIENT_SECRET": "",
            "SYNC_ORG_ID": "org123",
            "SYNC_ACCOUNT_NUMBER": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                sync_module.main()
            assert exc_info.value.code == 1

    def test_auth_failure_exits(self, sync_module):
        env = {
            "KEYCLOAK_URL": "https://keycloak.example.com",
            "KEYCLOAK_REALM": "testrealm",
            "KEYCLOAK_CLIENT_ID": "my-client",
            "KEYCLOAK_CLIENT_SECRET": "my-secret",
            "SYNC_ORG_ID": "org123",
            "SYNC_ACCOUNT_NUMBER": "456",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
                with pytest.raises(SystemExit) as exc_info:
                    sync_module.main()
                assert exc_info.value.code == 1


@pytest.mark.helm
@pytest.mark.component
class TestSyncConstants:
    """Tests for module-level constants."""

    def test_page_size(self, sync_module):
        assert sync_module.PAGE_SIZE == 100

    def test_request_timeout(self, sync_module):
        assert sync_module.REQUEST_TIMEOUT == 30

    def test_token_refresh_margin(self, sync_module):
        assert sync_module.TOKEN_REFRESH_MARGIN == 0.8
