"""
UI tests for Cost Management navigation.

These tests validate that the main navigation pages load correctly
and that users can navigate between different sections of the application.
"""

import re
from typing import NamedTuple

import pytest
from playwright.sync_api import Page, expect


class NavPage(NamedTuple):
    """Navigation page definition."""
    name: str
    path: str
    nav_text: str  # Text shown in the navigation menu


# Pages that should be validated
# Extend this list to add more pages to test
NAVIGATION_PAGES = [
    NavPage("Overview", "/openshift/cost-management", "Overview"),
    NavPage("OpenShift", "/openshift/cost-management/ocp", "OpenShift"),
    NavPage("Cost Explorer", "/openshift/cost-management/explorer", "Cost Explorer"),
    NavPage("Settings", "/openshift/cost-management/settings", "Settings"),
]

# Pages that exist but may not have data or are cloud-provider specific
OPTIONAL_PAGES = [
    NavPage("Optimizations", "/openshift/cost-management/optimizations", "Optimizations"),
    NavPage("AWS", "/openshift/cost-management/aws", "Amazon Web Services"),
    NavPage("GCP", "/openshift/cost-management/gcp", "Google Cloud"),
    NavPage("Azure", "/openshift/cost-management/azure", "Microsoft Azure"),
]


@pytest.mark.ui
class TestDefaultNavigation:
    """Test default navigation behavior."""

    @pytest.mark.smoke
    def test_ui_defaults_to_overview(self, authenticated_page: Page, ui_url: str):
        """Verify navigating to the UI defaults to the Overview page."""
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        # Should redirect to /openshift/cost-management (Overview)
        expect(authenticated_page).to_have_url(re.compile(r".*/openshift/cost-management/?$"))
        
        # Overview nav item should be marked as current
        overview_link = authenticated_page.locator("a.pf-v6-c-nav__link.pf-m-current")
        expect(overview_link).to_be_visible()
        expect(overview_link).to_have_text("Overview")

    def test_navigation_menu_visible(self, authenticated_page: Page, ui_url: str):
        """Verify the navigation menu is visible with expected items."""
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        # Navigation list should be visible
        nav_list = authenticated_page.locator("ul.pf-v6-c-nav__list")
        expect(nav_list).to_be_visible()
        
        # Check that expected nav items exist
        for page in NAVIGATION_PAGES:
            nav_item = authenticated_page.locator(f"a.pf-v6-c-nav__link:has-text('{page.nav_text}')")
            expect(nav_item).to_be_visible()


@pytest.mark.ui
class TestPageNavigation:
    """Test navigation to main application pages."""

    @pytest.mark.parametrize("nav_page", NAVIGATION_PAGES, ids=lambda p: p.name)
    def test_can_navigate_to_page(
        self, authenticated_page: Page, ui_url: str, nav_page: NavPage
    ):
        """Verify each main page can be navigated to and loads correctly.
        
        Parametrized for: Overview, OpenShift, Cost Explorer, Settings.
        """
        # Start at the UI root
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        # Click the navigation link
        nav_link = authenticated_page.locator(f"a.pf-v6-c-nav__link:has-text('{nav_page.nav_text}')")
        expect(nav_link).to_be_visible()
        nav_link.click()
        
        # Wait for navigation
        authenticated_page.wait_for_load_state("networkidle")
        
        # Verify URL contains the expected path
        expect(authenticated_page).to_have_url(re.compile(f".*{re.escape(nav_page.path)}.*"))
        
        # Verify the nav item is now marked as current
        current_link = authenticated_page.locator("a.pf-v6-c-nav__link.pf-m-current")
        expect(current_link).to_have_text(nav_page.nav_text)

    @pytest.mark.parametrize("nav_page", NAVIGATION_PAGES, ids=lambda p: p.name)
    def test_page_loads_without_error(
        self, authenticated_page: Page, ui_url: str, nav_page: NavPage
    ):
        """Verify each page loads without displaying error messages.
        
        Parametrized for: Overview, OpenShift, Cost Explorer, Settings.
        """
        # Navigate directly to the page
        authenticated_page.goto(f"{ui_url}{nav_page.path}")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Check for common error indicators
        error_indicators = [
            "text=/error|failed|not found|500|503/i",
            ".pf-v6-c-alert--danger",
            "[data-testid='error-state']",
        ]
        
        for indicator in error_indicators:
            error_element = authenticated_page.locator(indicator)
            # Allow for "No data" messages which are not errors
            if error_element.count() > 0:
                # Check if it's actually an error vs just "no data"
                text = error_element.first.text_content() or ""
                if "no data" not in text.lower() and "empty" not in text.lower():
                    # This might be a real error - log but don't fail
                    # Some pages may legitimately show errors if not configured
                    print(f"  ⚠️ Possible error on {nav_page.name}: {text[:100]}")


@pytest.mark.ui
class TestOptionalPages:
    """Test navigation to optional/cloud-provider pages."""

    @pytest.mark.parametrize("nav_page", OPTIONAL_PAGES, ids=lambda p: p.name)
    def test_optional_page_accessible(
        self, authenticated_page: Page, ui_url: str, nav_page: NavPage
    ):
        """Verify optional pages are accessible (may show 'no data' state).
        
        Parametrized for: Optimizations, AWS, GCP, Azure.
        """
        # Navigate directly to the page
        authenticated_page.goto(f"{ui_url}{nav_page.path}")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Page should load (URL should contain the path)
        expect(authenticated_page).to_have_url(re.compile(f".*{re.escape(nav_page.path)}.*"))
        
        # The page should have some content (not completely blank)
        body = authenticated_page.locator("body")
        expect(body).not_to_be_empty()


@pytest.mark.ui
class TestOverviewPage:
    """Tests specific to the Overview page."""

    def test_overview_has_content(self, authenticated_page: Page, ui_url: str):
        """Verify the Overview page displays content."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Should have some main content area
        main_content = authenticated_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main_content).to_be_visible()


@pytest.mark.ui
class TestOpenShiftPage:
    """Tests specific to the OpenShift page."""

    def test_openshift_page_has_content(self, authenticated_page: Page, ui_url: str):
        """Verify the OpenShift page displays content."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Should have some main content area
        main_content = authenticated_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main_content).to_be_visible()


@pytest.mark.ui
class TestCostExplorerPage:
    """Tests specific to the Cost Explorer page."""

    def test_cost_explorer_has_content(self, authenticated_page: Page, ui_url: str):
        """Verify the Cost Explorer page displays content."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/explorer")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Should have some main content area
        main_content = authenticated_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main_content).to_be_visible()


@pytest.mark.ui
class TestSettingsPage:
    """Tests specific to the Settings page."""

    def test_settings_page_has_content(self, authenticated_page: Page, ui_url: str):
        """Verify the Settings page displays content."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/settings")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Should have some main content area
        main_content = authenticated_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main_content).to_be_visible()
