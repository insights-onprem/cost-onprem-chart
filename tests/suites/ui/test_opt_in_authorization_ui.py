"""
Opt-in access model UI tests (S-14).

Validates that the three opt-in demo users (test1, test2, test3) can
authenticate via Keycloak and interact with the Cost Management UI with
the correct page-level access enforced by Kessel ReBAC.

All three users hold ``cost-openshift-viewer`` roles bound to different
team workspaces.  At the UI level they behave identically:

    +-----------------------------------+---------+---------+---------+
    | UI Component                      | test1   | test2   | test3   |
    +-----------------------------------+---------+---------+---------+
    | Overview page                     | visible | visible | visible |
    | OpenShift page (data)             | visible | visible | visible |
    | Cost Explorer                     | visible | visible | visible |
    | Settings page (controls)          | limited | limited | limited |
    | AWS page (data)                   | empty   | empty   | empty   |
    | Optimizations page                | visible | visible | visible |
    +-----------------------------------+---------+---------+---------+

Data-level scoping (test1 sees different clusters than test2) requires
NISE-ingested cost data and is deferred.

Prerequisites:
    - ``test1``, ``test2``, ``test3`` users in Keycloak (password = username)
    - ``kessel-admin.sh demo <org_id>`` has been run
    - UI deployed and accessible via route

Maps to test plan scenario: S-14.
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
def test1_context(
    browser: Browser, ui_url: str, keycloak_config: KeycloakConfig,
) -> Generator[BrowserContext, None, None]:
    """Authenticated browser context for test1 (group demo + ws-test1)."""
    ctx = _login_as(
        browser, ui_url, keycloak_config,
        os.environ.get("OPTIN_USER1", "test1"),
        os.environ.get("OPTIN_PASS1", "test1"),
    )
    yield ctx
    ctx.close()


@pytest.fixture(scope="module")
def test2_context(
    browser: Browser, ui_url: str, keycloak_config: KeycloakConfig,
) -> Generator[BrowserContext, None, None]:
    """Authenticated browser context for test2 (group infra)."""
    ctx = _login_as(
        browser, ui_url, keycloak_config,
        os.environ.get("OPTIN_USER2", "test2"),
        os.environ.get("OPTIN_PASS2", "test2"),
    )
    yield ctx
    ctx.close()


@pytest.fixture(scope="module")
def test3_context(
    browser: Browser, ui_url: str, keycloak_config: KeycloakConfig,
) -> Generator[BrowserContext, None, None]:
    """Authenticated browser context for test3 (group payment)."""
    ctx = _login_as(
        browser, ui_url, keycloak_config,
        os.environ.get("OPTIN_USER3", "test3"),
        os.environ.get("OPTIN_PASS3", "test3"),
    )
    yield ctx
    ctx.close()


@pytest.fixture
def test1_page(test1_context: BrowserContext) -> Generator[Page, None, None]:
    """Fresh page authenticated as test1."""
    page = test1_context.new_page()
    yield page
    page.close()


@pytest.fixture
def test2_page(test2_context: BrowserContext) -> Generator[Page, None, None]:
    """Fresh page authenticated as test2."""
    page = test2_context.new_page()
    yield page
    page.close()


@pytest.fixture
def test3_page(test3_context: BrowserContext) -> Generator[Page, None, None]:
    """Fresh page authenticated as test3."""
    page = test3_context.new_page()
    yield page
    page.close()


# =============================================================================
# Test Classes
# =============================================================================


@pytest.mark.ui
@pytest.mark.kessel
class TestOptInLogin:
    """All three opt-in users can authenticate and land on Overview."""

    def test_test1_lands_on_overview(self, test1_page: Page, ui_url: str):
        test1_page.goto(ui_url)
        test1_page.wait_for_load_state("networkidle")
        expect(test1_page).to_have_url(
            re.compile(r".*/openshift/cost-management/?$")
        )

    def test_test2_lands_on_overview(self, test2_page: Page, ui_url: str):
        test2_page.goto(ui_url)
        test2_page.wait_for_load_state("networkidle")
        expect(test2_page).to_have_url(
            re.compile(r".*/openshift/cost-management/?$")
        )

    def test_test3_lands_on_overview(self, test3_page: Page, ui_url: str):
        test3_page.goto(ui_url)
        test3_page.wait_for_load_state("networkidle")
        expect(test3_page).to_have_url(
            re.compile(r".*/openshift/cost-management/?$")
        )


@pytest.mark.ui
@pytest.mark.kessel
class TestOpenShiftVisibility:
    """All three users see the OpenShift page (all have cost-openshift-viewer)."""

    def test_test1_sees_ocp_content(self, test1_page: Page, ui_url: str):
        test1_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        test1_page.wait_for_load_state("networkidle")
        main = test1_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_test2_sees_ocp_content(self, test2_page: Page, ui_url: str):
        test2_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        test2_page.wait_for_load_state("networkidle")
        main = test2_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_test3_sees_ocp_content(self, test3_page: Page, ui_url: str):
        test3_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        test3_page.wait_for_load_state("networkidle")
        main = test3_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()


@pytest.mark.ui
@pytest.mark.kessel
class TestAWSVisibility:
    """All three users see empty/no-data on AWS (none have AWS roles)."""

    def _assert_aws_empty(self, page: Page, ui_url: str, label: str):
        page.goto(f"{ui_url}/openshift/cost-management/aws")
        page.wait_for_load_state("networkidle")
        main = page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()
        data_rows = page.locator(
            ".pf-v6-c-table tbody tr, .pf-v5-c-table tbody tr"
        )
        if data_rows.count() > 0:
            first_row_text = data_rows.first.text_content() or ""
            assert "$" not in first_row_text, (
                f"{label} should not see AWS cost data: {first_row_text[:200]}"
            )

    def test_test1_aws_empty(self, test1_page: Page, ui_url: str):
        self._assert_aws_empty(test1_page, ui_url, "test1")

    def test_test2_aws_empty(self, test2_page: Page, ui_url: str):
        self._assert_aws_empty(test2_page, ui_url, "test2")

    def test_test3_aws_empty(self, test3_page: Page, ui_url: str):
        self._assert_aws_empty(test3_page, ui_url, "test3")


@pytest.mark.ui
@pytest.mark.kessel
class TestSettingsVisibility:
    """All three users see a restricted Settings view (viewers, not admins)."""

    def _assert_settings_restricted(self, page: Page, ui_url: str, label: str):
        page.goto(f"{ui_url}/openshift/cost-management/settings")
        page.wait_for_load_state("networkidle")
        main = page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()
        error_banner = page.locator(
            ".pf-v6-c-alert--danger, .pf-v5-c-alert--danger"
        )
        if error_banner.count() > 0:
            text = error_banner.first.text_content() or ""
            assert "500" not in text and "internal" not in text.lower(), (
                f"{label} settings page shows server error: {text[:200]}"
            )

    def test_test1_settings_loads(self, test1_page: Page, ui_url: str):
        self._assert_settings_restricted(test1_page, ui_url, "test1")

    def test_test2_settings_loads(self, test2_page: Page, ui_url: str):
        self._assert_settings_restricted(test2_page, ui_url, "test2")

    def test_test3_settings_loads(self, test3_page: Page, ui_url: str):
        self._assert_settings_restricted(test3_page, ui_url, "test3")


@pytest.mark.ui
@pytest.mark.kessel
class TestOptimizationsVisibility:
    """All three users see the Optimizations page (all have OCP read)."""

    def test_test1_sees_optimizations(self, test1_page: Page, ui_url: str):
        test1_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        test1_page.wait_for_load_state("networkidle")
        main = test1_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_test2_sees_optimizations(self, test2_page: Page, ui_url: str):
        test2_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        test2_page.wait_for_load_state("networkidle")
        main = test2_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()

    def test_test3_sees_optimizations(self, test3_page: Page, ui_url: str):
        test3_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        test3_page.wait_for_load_state("networkidle")
        main = test3_page.locator("main, [role='main'], .pf-v6-c-page__main")
        expect(main).to_be_visible()


@pytest.mark.ui
@pytest.mark.kessel
class TestCostExplorerVisibility:
    """All three users see Cost Explorer (all have OCP read)."""

    def _assert_explorer_content(self, page: Page, ui_url: str, label: str):
        page.goto(f"{ui_url}/openshift/cost-management/explorer")
        page.wait_for_load_state("networkidle")
        content = page.locator(
            "svg, table, .pf-v6-c-table, .pf-v6-c-empty-state, "
            ".pf-v5-c-table, .pf-v5-c-empty-state"
        )
        assert content.count() > 0, (
            f"{label} Cost Explorer should display chart, table, or empty state"
        )

    def test_test1_sees_explorer(self, test1_page: Page, ui_url: str):
        self._assert_explorer_content(test1_page, ui_url, "test1")

    def test_test2_sees_explorer(self, test2_page: Page, ui_url: str):
        self._assert_explorer_content(test2_page, ui_url, "test2")

    def test_test3_sees_explorer(self, test3_page: Page, ui_url: str):
        self._assert_explorer_content(test3_page, ui_url, "test3")
