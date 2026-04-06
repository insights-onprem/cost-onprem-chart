"""
UI tests for Keycloak logout flow.

These tests validate the OAuth/OIDC logout flow through the browser,
ensuring users are properly logged out via Keycloak and the SSO session
is fully terminated.

Verifies: FLPATH-2959 - Implement logout for the UI via Keycloak
"""

import re

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.ui
@pytest.mark.auth
class TestLogoutFlow:
    """Test the Keycloak OAuth logout flow."""

    @pytest.mark.smoke
    def test_logout_redirects_to_login(
        self, authenticated_page: Page, ui_url: str, keycloak_config
    ):
        """Verify /logout redirects through oauth2-proxy sign_out to Keycloak login."""
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")

        # Navigate to logout endpoint
        authenticated_page.goto(f"{ui_url}/logout")

        # Should ultimately land on the Keycloak login page
        authenticated_page.wait_for_url(
            f"**/{keycloak_config.realm}/**", timeout=15000
        )
        expect(authenticated_page).to_have_url(
            re.compile(f".*{keycloak_config.realm}.*")
        )

        # Login form should be visible (session fully terminated)
        expect(authenticated_page.locator('input[name="username"]')).to_be_visible()

    def test_session_invalidated_after_logout(
        self, authenticated_page: Page, ui_url: str, keycloak_config
    ):
        """Verify accessing a protected page after logout requires re-authentication.

        This confirms the Keycloak SSO session is destroyed (via id_token_hint),
        not just the local oauth2-proxy cookie.
        """
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")

        # Logout
        authenticated_page.goto(f"{ui_url}/logout")
        authenticated_page.wait_for_url(
            f"**/{keycloak_config.realm}/**", timeout=15000
        )

        # Now try accessing a protected page directly
        authenticated_page.goto(ui_url)

        # Must redirect to Keycloak login — NOT auto-authenticate
        authenticated_page.wait_for_url(
            f"**/{keycloak_config.realm}/**", timeout=15000
        )
        expect(authenticated_page.locator('input[name="username"]')).to_be_visible()

    def test_logout_from_subpage(
        self, authenticated_page: Page, ui_url: str, keycloak_config
    ):
        """Verify logout works when initiated from a subpage (not just root)."""
        # Navigate to a subpage first
        authenticated_page.goto(f"{ui_url}/recommendations")
        authenticated_page.wait_for_load_state("networkidle")

        # Logout
        authenticated_page.goto(f"{ui_url}/logout")

        # Should land on Keycloak login
        authenticated_page.wait_for_url(
            f"**/{keycloak_config.realm}/**", timeout=15000
        )
        expect(authenticated_page.locator('input[name="username"]')).to_be_visible()


@pytest.mark.ui
@pytest.mark.auth
class TestLogoutBoundary:
    """Test logout edge cases and boundary conditions."""

    def test_unauthenticated_logout_no_error(
        self, page: Page, ui_url: str
    ):
        """Verify /logout from an unauthenticated session does not produce a 500."""
        response = page.goto(f"{ui_url}/logout")

        # Should not be a server error
        assert response is not None
        assert response.status < 500, (
            f"Unauthenticated /logout returned server error: {response.status}"
        )
