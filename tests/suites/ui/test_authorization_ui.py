"""
UI authorization tests: verify role-based visibility in the browser.

Validates that admin (cost-administrator) and viewer (cost-openshift-viewer)
see different content in the Cost Management UI, reflecting Kessel ReBAC
enforcement at the API layer.

Test matrix:
    +-----------------------------------+---------+---------+
    | UI Component                      | admin   | viewer  |
    +-----------------------------------+---------+---------+
    | Overview page                     | visible | visible |
    | OpenShift page (data)             | visible | visible |
    | Cost Explorer                     | visible | visible |
    | Settings page (controls)          | visible | limited |
    | AWS page (data)                   | visible | empty   |
    | Optimizations page                | visible | visible |
    +-----------------------------------+---------+---------+

Prerequisites:
    - ``admin`` user in Keycloak with ``cost-administrator`` SpiceDB role
    - ``test`` user in Keycloak with ``cost-openshift-viewer`` SpiceDB role
    - UI deployed and accessible via route
"""

import os
import re
from typing import Generator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

from conftest import KeycloakConfig


# =============================================================================
# Helpers
# =============================================================================


def _login_as(
    browser: Browser,
    ui_url: str,
    keycloak_config: KeycloakConfig,
    username: str,
    password: str,
) -> BrowserContext:
    """Create a browser context authenticated as the given user."""
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,
    )
    page = context.new_page()
    page.goto(ui_url)
    page.wait_for_url(f"**/{keycloak_config.realm}/**", timeout=15000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('input[type="submit"], button[type="submit"]')
    page.wait_for_url(f"{ui_url}**", timeout=15000)
    page.close()
    return context


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def admin_context(
    browser: Browser, ui_url: str, keycloak_config: KeycloakConfig,
) -> Generator[BrowserContext, None, None]:
    """Authenticated browser context for the admin user."""
    ctx = _login_as(
        browser,
        ui_url,
        keycloak_config,
        os.environ.get("AUTHZ_ADMIN_USER", "admin"),
        os.environ.get("AUTHZ_ADMIN_PASS", "admin"),
    )
    yield ctx
    ctx.close()


@pytest.fixture(scope="module")
def viewer_context(
    browser: Browser, ui_url: str, keycloak_config: KeycloakConfig,
) -> Generator[BrowserContext, None, None]:
    """Authenticated browser context for the viewer user."""
    ctx = _login_as(
        browser,
        ui_url,
        keycloak_config,
        os.environ.get("AUTHZ_VIEWER_USER", "test"),
        os.environ.get("AUTHZ_VIEWER_PASS", "test"),
    )
    yield ctx
    ctx.close()


@pytest.fixture
def admin_page(admin_context: BrowserContext) -> Generator[Page, None, None]:
    """Fresh page authenticated as admin."""
    page = admin_context.new_page()
    yield page
    page.close()


@pytest.fixture
def viewer_page(viewer_context: BrowserContext) -> Generator[Page, None, None]:
    """Fresh page authenticated as viewer."""
    page = viewer_context.new_page()
    yield page
    page.close()


# =============================================================================
# Test Classes
# =============================================================================


@pytest.mark.ui
@pytest.mark.kessel
class TestAdminVsViewerLogin:
    """Verify both roles can log in and land on the Overview page."""

    def test_admin_lands_on_overview(self, admin_page: Page, ui_url: str):
        """Admin login redirects to Overview."""
        admin_page.goto(ui_url)
        admin_page.wait_for_load_state("networkidle")
        expect(admin_page).to_have_url(
            re.compile(r".*/openshift/cost-management/?$")
        )

    def test_viewer_lands_on_overview(self, viewer_page: Page, ui_url: str):
        """Viewer login redirects to Overview."""
        viewer_page.goto(ui_url)
        viewer_page.wait_for_load_state("networkidle")
        expect(viewer_page).to_have_url(
            re.compile(r".*/openshift/cost-management/?$")
        )


@pytest.mark.ui
@pytest.mark.kessel
class TestOpenShiftPageBothRoles:
    """Both admin and viewer should see OpenShift page content."""

    def test_admin_sees_ocp_content(self, admin_page: Page, ui_url: str):
        """Admin can navigate to OpenShift page and see content."""
        admin_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        admin_page.wait_for_load_state("networkidle")
        main = admin_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_viewer_sees_ocp_content(self, viewer_page: Page, ui_url: str):
        """Viewer (OCP read) can navigate to OpenShift page and see content."""
        viewer_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        viewer_page.wait_for_load_state("networkidle")
        main = viewer_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()


@pytest.mark.ui
@pytest.mark.kessel
class TestSettingsVisibility:
    """Admin sees full Settings controls; viewer may see a restricted view."""

    def test_admin_sees_settings_controls(self, admin_page: Page, ui_url: str):
        """Admin should see tabs, forms, or table controls on Settings."""
        admin_page.goto(f"{ui_url}/openshift/cost-management/settings")
        admin_page.wait_for_load_state("networkidle")

        controls = admin_page.locator(
            ".pf-v6-c-tabs, .pf-v6-c-form, .pf-v6-c-card, "
            "table, .pf-v6-c-table, .pf-v5-c-tabs, .pf-v5-c-card"
        )
        assert controls.count() > 0, (
            "Admin should see settings controls (tabs, forms, cards, or tables)"
        )

    def test_viewer_settings_page_loads(self, viewer_page: Page, ui_url: str):
        """Viewer can reach Settings without a server error.

        The viewer (cost-openshift-viewer) has no settings:write, so the UI
        may show a read-only or restricted view.  We verify it loads without
        a 500-level error page.
        """
        viewer_page.goto(f"{ui_url}/openshift/cost-management/settings")
        viewer_page.wait_for_load_state("networkidle")
        main = viewer_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

        # Check no crash-level error is displayed
        error_banner = viewer_page.locator(
            ".pf-v6-c-alert--danger, .pf-v5-c-alert--danger"
        )
        if error_banner.count() > 0:
            text = error_banner.first.text_content() or ""
            assert "500" not in text and "internal" not in text.lower(), (
                f"Viewer settings page shows server error: {text[:200]}"
            )


@pytest.mark.ui
@pytest.mark.kessel
class TestCloudProviderVisibility:
    """Admin can access cloud provider pages; viewer (OCP-only) sees empty/no-data."""

    def test_admin_aws_page_loads(self, admin_page: Page, ui_url: str):
        """Admin can reach the AWS page."""
        admin_page.goto(f"{ui_url}/openshift/cost-management/aws")
        admin_page.wait_for_load_state("networkidle")
        expect(admin_page).to_have_url(re.compile(r".*aws.*"))
        main = admin_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_viewer_aws_page_restricted(self, viewer_page: Page, ui_url: str):
        """Viewer (no aws.account:read) sees empty state or no-data on AWS page.

        The UI may either show an empty-state component, redirect to Overview,
        or display a "no data" message.  We verify the page does not show
        actual cost data.
        """
        viewer_page.goto(f"{ui_url}/openshift/cost-management/aws")
        viewer_page.wait_for_load_state("networkidle")
        main = viewer_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

        # Viewer should NOT see AWS cost data tables with rows
        data_rows = viewer_page.locator(
            ".pf-v6-c-table tbody tr, .pf-v5-c-table tbody tr"
        )
        # If there are data rows, check they don't contain cost data
        if data_rows.count() > 0:
            first_row_text = data_rows.first.text_content() or ""
            assert "$" not in first_row_text, (
                "Viewer should not see AWS cost data, but found dollar amounts "
                f"in table: {first_row_text[:200]}"
            )


@pytest.mark.ui
@pytest.mark.kessel
class TestOptimizationsVisibility:
    """Both roles should see the Optimizations page (both have OCP read)."""

    def test_admin_sees_optimizations(self, admin_page: Page, ui_url: str):
        """Admin can access the Optimizations page."""
        admin_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        admin_page.wait_for_load_state("networkidle")
        main = admin_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_viewer_sees_optimizations(self, viewer_page: Page, ui_url: str):
        """Viewer (OCP read) can access the Optimizations page."""
        viewer_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        viewer_page.wait_for_load_state("networkidle")
        main = viewer_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()


@pytest.mark.ui
@pytest.mark.kessel
class TestCostExplorerVisibility:
    """Both roles see Cost Explorer, but data scope may differ."""

    def test_admin_sees_explorer_content(self, admin_page: Page, ui_url: str):
        """Admin sees Cost Explorer with data or empty state."""
        admin_page.goto(f"{ui_url}/openshift/cost-management/explorer")
        admin_page.wait_for_load_state("networkidle")
        content = admin_page.locator(
            "svg, table, .pf-v6-c-table, .pf-v6-c-empty-state, "
            ".pf-v5-c-table, .pf-v5-c-empty-state"
        )
        assert content.count() > 0, (
            "Admin Cost Explorer should display chart, table, or empty state"
        )

    def test_viewer_sees_explorer_content(self, viewer_page: Page, ui_url: str):
        """Viewer sees Cost Explorer (OCP data) with data or empty state."""
        viewer_page.goto(f"{ui_url}/openshift/cost-management/explorer")
        viewer_page.wait_for_load_state("networkidle")
        content = viewer_page.locator(
            "svg, table, .pf-v6-c-table, .pf-v6-c-empty-state, "
            ".pf-v5-c-table, .pf-v5-c-empty-state"
        )
        assert content.count() > 0, (
            "Viewer Cost Explorer should display chart, table, or empty state"
        )
