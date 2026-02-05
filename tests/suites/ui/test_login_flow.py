"""
UI tests for Keycloak login flow.

These tests validate the OAuth/OIDC login flow through the browser,
ensuring users can authenticate via Keycloak and access the UI.
"""

import re

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.ui
@pytest.mark.auth
class TestLoginFlow:
    """Test the Keycloak OAuth login flow."""

    @pytest.mark.smoke
    def test_ui_redirects_to_keycloak(self, page: Page, ui_url: str, keycloak_config):
        """Verify unauthenticated access redirects to Keycloak login."""
        # Navigate to UI
        page.goto(ui_url)
        
        # Should redirect to Keycloak
        expect(page).to_have_url(re.compile(f".*{keycloak_config.realm}.*"))
        
        # Login form should be visible
        expect(page.locator('input[name="username"]')).to_be_visible()
        expect(page.locator('input[name="password"]')).to_be_visible()

    def test_successful_login(self, page: Page, ui_url: str, keycloak_config):
        """Verify successful login redirects back to UI."""
        import os
        
        # Navigate to UI (redirects to Keycloak)
        page.goto(ui_url)
        page.wait_for_url(f"**/{keycloak_config.realm}/**", timeout=10000)
        
        # Fill login form
        username = os.environ.get("TEST_UI_USERNAME", "test")
        password = os.environ.get("TEST_UI_PASSWORD", "test")
        
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('input[type="submit"], button[type="submit"]')
        
        # Should redirect back to UI
        page.wait_for_url(f"{ui_url}**", timeout=15000)
        
        # Verify we're on the UI (not Keycloak)
        expect(page).not_to_have_url(re.compile(f".*{keycloak_config.realm}.*"))

    def test_invalid_credentials_shows_error(self, page: Page, ui_url: str, keycloak_config):
        """Verify invalid credentials show an error message."""
        # Navigate to UI (redirects to Keycloak)
        page.goto(ui_url)
        page.wait_for_url(f"**/{keycloak_config.realm}/**", timeout=10000)
        
        # Fill with invalid credentials
        page.fill('input[name="username"]', "invalid_user")
        page.fill('input[name="password"]', "invalid_password")
        page.click('input[type="submit"], button[type="submit"]')
        
        # Should show error message (stay on Keycloak)
        expect(page).to_have_url(re.compile(f".*{keycloak_config.realm}.*"))
        
        # Error message should be visible
        error_locator = page.locator(".alert-error, .kc-feedback-text, #input-error")
        expect(error_locator).to_be_visible(timeout=5000)


@pytest.mark.ui
@pytest.mark.auth
class TestAuthenticatedSession:
    """Test authenticated session behavior."""

    def test_session_persists_across_navigation(
        self, authenticated_page: Page, ui_url: str
    ):
        """Verify session persists when navigating within the app."""
        # Navigate to UI
        authenticated_page.goto(ui_url)
        
        # Should not redirect to Keycloak
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(f"{re.escape(ui_url)}.*"))

    def test_can_access_protected_routes(
        self, authenticated_page: Page, ui_url: str
    ):
        """Verify authenticated user can access protected routes."""
        # Navigate to a protected route (e.g., recommendations)
        authenticated_page.goto(f"{ui_url}/recommendations")
        
        # Should load without redirect to Keycloak
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).not_to_have_url(re.compile(".*keycloak.*"))
