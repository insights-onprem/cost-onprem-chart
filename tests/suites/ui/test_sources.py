"""
UI tests for Sources/Integrations management (FLPATH-2976).

These tests validate the Sources tab in Settings page for creating and
managing OpenShift cost data sources directly from the Cost Management UI.

Jira: https://redhat.atlassian.net/browse/FLPATH-2976
"""

import uuid
from dataclasses import dataclass
from typing import Optional

import pytest
import requests
from playwright.sync_api import Page, expect

from conftest import obtain_jwt_token


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SourceData:
    """Test source data container."""
    id: str
    name: str
    cluster_id: str


# =============================================================================
# Helper Functions
# =============================================================================


def wait_for_integrations_load(page: Page, timeout_ms: int = 15000) -> None:
    """Wait for the Integrations page to finish loading.
    
    The Integrations page shows a loading state for 10-15 seconds while
    searching for integrations.
    """
    page.wait_for_timeout(timeout_ms)


def dismiss_any_modal(page: Page) -> None:
    """Dismiss any open modal/backdrop by trying multiple close methods.
    
    Useful for cleaning up state between tests or after wizard completion.
    """
    # First check for any backdrop (modal overlay)
    backdrop = page.locator(".pf-v6-c-backdrop")
    modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")
    
    attempts = 0
    max_attempts = 3
    
    while (backdrop.count() > 0 or modal.count() > 0) and attempts < max_attempts:
        attempts += 1
        
        # Try to find a close button
        close_buttons = page.locator(
            ".pf-v6-c-modal-box button:has-text('Close'), "
            ".pf-v6-c-modal-box button:has-text('Cancel'), "
            ".pf-v6-c-modal-box button[aria-label='Close'], "
            ".pf-v6-c-wizard button:has-text('Close'), "
            "button.pf-v6-c-wizard__close"
        )
        
        if close_buttons.count() > 0:
            try:
                close_buttons.first.click()
                page.wait_for_timeout(1000)
            except Exception:
                pass
        else:
            # Try pressing Escape
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
        
        # Re-check modals
        backdrop = page.locator(".pf-v6-c-backdrop")
        modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")


def navigate_to_integrations(page: Page, ui_url: str) -> None:
    """Navigate to the Integrations tab in Settings page.
    
    The tab may be called "Sources" or "Integrations" depending on UI version.
    Dismisses any open modals first to ensure clean state.
    """
    # First dismiss any open modals
    dismiss_any_modal(page)
    
    page.goto(f"{ui_url}/openshift/cost-management/settings")
    page.wait_for_load_state("networkidle")
    
    # Click Sources/Integrations tab
    sources_tab = page.locator(
        "button:has-text('Sources'), "
        "a:has-text('Sources'), "
        "button:has-text('Integrations'), "
        "a:has-text('Integrations'), "
        "[data-ouia-component-id='Sources'], "
        "[data-ouia-component-id='Integrations']"
    )
    
    if sources_tab.count() > 0:
        sources_tab.first.click()
        page.wait_for_load_state("networkidle")


def open_add_integration_wizard(page: Page) -> None:
    """Open the Add Integration wizard.
    
    Works in both:
    - Empty state: clicks OpenShift card which opens the wizard
    - Populated state: clicks "Add integration" button which directly opens the wizard
    """
    # Wait for page to stabilize after loading
    page.wait_for_timeout(3000)
    
    # Check for "Add integration" button first (populated state)
    add_button = page.locator("button:has-text('Add integration')")
    
    # Empty state - OpenShift card (use the specific OUIA ID)
    ocp_card = page.locator("[data-ouia-component-id='sources-empty-add-openshift-card']")
    
    clicked = False
    
    # Try Add button first (populated state)
    if add_button.count() > 0 and add_button.first.is_visible():
        add_button.first.click()
        clicked = True
    # Then try OpenShift card (empty state)
    elif ocp_card.count() > 0 and ocp_card.first.is_visible():
        ocp_card.first.click()
        clicked = True
    
    if not clicked:
        page.screenshot(path="/tmp/wizard-debug.png")
        raise AssertionError(
            f"Could not find Add integration button ({add_button.count()}) "
            f"or OpenShift card ({ocp_card.count()})"
        )
    
    # Wait longer for wizard to appear
    page.wait_for_timeout(2000)
    
    # Verify wizard opened
    wizard = page.locator(".pf-v6-c-wizard, .pf-v6-c-modal-box")
    expect(wizard.first).to_be_visible(timeout=10000)


def fill_wizard_step1_name(page: Page, name: str) -> None:
    """Fill in the integration name on wizard step 1."""
    name_input = page.locator(".pf-v6-c-modal-box input[type='text']")
    expect(name_input.first).to_be_visible(timeout=5000)
    name_input.first.fill(name)


def fill_wizard_step2_cluster_id(page: Page, cluster_id: str) -> None:
    """Fill in the cluster ID on wizard step 2."""
    cluster_input = page.locator(
        "input[name='credentials.cluster_id'], "
        ".pf-v6-c-modal-box input[type='text']"
    )
    expect(cluster_input.first).to_be_visible(timeout=5000)
    cluster_input.first.fill(cluster_id)


def click_wizard_next(page: Page) -> None:
    """Click the Next button in the wizard."""
    next_button = page.locator("button:has-text('Next')")
    next_button.click()
    page.wait_for_timeout(1000)


def click_wizard_submit(page: Page) -> None:
    """Click the Submit button to create the integration."""
    submit_button = page.locator("button:has-text('Submit')")
    expect(submit_button.first).to_be_visible(timeout=5000)
    submit_button.first.click()
    page.wait_for_load_state("networkidle")


def click_wizard_cancel(page: Page) -> None:
    """Click Cancel to close the wizard without saving."""
    cancel_button = page.locator(
        "button:has-text('Cancel'), "
        "button[aria-label='Close']"
    )
    cancel_button.first.click()
    page.wait_for_timeout(1000)


def create_integration_via_wizard(
    page: Page,
    name: str,
    cluster_id: str,
) -> None:
    """Complete the full wizard flow to create an integration.
    
    Args:
        page: Playwright page (should already be on Integrations tab)
        name: Integration name
        cluster_id: Cluster identifier
    """
    open_add_integration_wizard(page)
    
    # Step 1: Name
    fill_wizard_step1_name(page, name)
    click_wizard_next(page)
    
    # Step 2: Cluster ID
    fill_wizard_step2_cluster_id(page, cluster_id)
    click_wizard_next(page)
    
    # Step 3: Submit
    click_wizard_submit(page)
    page.wait_for_timeout(3000)
    
    # Dismiss any success/confirmation modal that appears after creation
    dismiss_any_modal(page)


def open_source_actions_menu(page: Page, source_name: str) -> None:
    """Open the actions (kebab) menu for a specific source.
    
    Finds the table row containing the source and clicks its kebab menu.
    """
    # Wait for the source to appear in the page
    source_locator = page.locator(f":text('{source_name}')")
    expect(source_locator.first).to_be_visible(timeout=15000)
    
    # Find the table row containing our source
    # PatternFly tables use tr elements
    row = page.locator(f"tr:has-text('{source_name}')")
    if row.count() == 0:
        # Try alternate row structure
        row = page.locator(f"[role='row']:has-text('{source_name}')")
    
    if row.count() == 0:
        raise AssertionError(f"Could not find table row containing '{source_name}'")
    
    # Look for the kebab menu button within the row
    # PatternFly v6 uses pf-v6-c-menu-toggle for kebab menus
    actions_button = row.locator(
        ".pf-v6-c-menu-toggle, "
        ".pf-v6-c-dropdown__toggle, "
        "button[aria-label='Kebab toggle'], "
        "button[aria-label='Actions']"
    )
    
    if actions_button.count() == 0:
        # Debug: print what buttons are in the row
        row_buttons = row.locator("button")
        button_count = row_buttons.count()
        raise AssertionError(
            f"Could not find actions menu in row for '{source_name}'. "
            f"Row has {button_count} buttons."
        )
    
    actions_button.first.click()
    page.wait_for_timeout(1000)


def delete_source_via_ui(page: Page, source_name: str) -> None:
    """Delete/remove a source using the UI actions menu.
    
    The remove modal has a checkbox confirmation, not a text input.
    """
    open_source_actions_menu(page, source_name)
    
    # Click Remove option in menu (UI uses "Remove" not "Delete")
    remove_option = page.locator(
        "[role='menuitem']:has-text('Remove'), "
        ".pf-v6-c-menu__item:has-text('Remove'), "
        "button:has-text('Remove')"
    )
    remove_option.first.click()
    page.wait_for_timeout(1500)
    
    # Modal appears
    modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")
    expect(modal.first).to_be_visible(timeout=5000)
    
    # Check the acknowledgement checkbox
    checkbox = modal.locator("input[type='checkbox']")
    if checkbox.count() > 0:
        checkbox.first.check()
        page.wait_for_timeout(500)
    
    # Click the "Remove integration and its data" button
    remove_button = modal.locator(
        "button:has-text('Remove integration'), "
        "button.pf-m-danger"
    )
    expect(remove_button.first).to_be_enabled(timeout=5000)
    remove_button.first.click()
    
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(5000)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def sources_api_session(keycloak_config, gateway_url) -> requests.Session:
    """Authenticated requests session for API operations in UI tests.
    
    Module-scoped to be usable by class-scoped fixtures like ensure_no_sources.
    Token expiration may be an issue for very long test runs.
    """
    token = obtain_jwt_token(keycloak_config)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token.access_token}"
    session.headers["Content-Type"] = "application/json"
    session.verify = False
    return session


def refresh_session_token(session: requests.Session, keycloak_config) -> None:
    """Refresh the token in an existing session if needed."""
    token = obtain_jwt_token(keycloak_config)
    session.headers["Authorization"] = f"Bearer {token.access_token}"


@pytest.fixture(scope="function")
def test_source(sources_api_session, gateway_url) -> SourceData:
    """Create a test source via API with automatic cleanup.
    
    Use this fixture when a test needs an existing source to work with.
    """
    source_name = f"ui-test-{uuid.uuid4().hex[:8]}"
    cluster_id = f"ui-cluster-{uuid.uuid4().hex[:8]}"
    
    response = sources_api_session.post(
        f"{gateway_url}/cost-management/v1/sources",
        json={
            "name": source_name,
            "source_type_id": 1,  # OpenShift
            "source_ref": cluster_id,
        },
    )
    
    if response.status_code != 201:
        pytest.skip(f"Could not create test source: {response.status_code}")
    
    source_data = response.json()
    source = SourceData(
        id=source_data.get("id"),
        name=source_name,
        cluster_id=cluster_id,
    )
    
    yield source
    
    # Cleanup
    sources_api_session.delete(f"{gateway_url}/cost-management/v1/sources/{source.id}")


def cleanup_source_by_name(session: requests.Session, gateway_url: str, name: str) -> None:
    """Helper to cleanup a source by name via API."""
    response = session.get(
        f"{gateway_url}/cost-management/v1/sources",
        params={"name": name},
    )
    if response.ok:
        for source in response.json().get("data", []):
            if source.get("name") == name:
                session.delete(f"{gateway_url}/cost-management/v1/sources/{source['id']}")


# =============================================================================
# Navigation Tests
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
class TestIntegrationsNavigation:
    """Test Integrations tab visibility and navigation."""

    @pytest.mark.smoke
    def test_integrations_tab_visible(self, authenticated_page: Page, ui_url: str):
        """Verify Integrations tab is visible in Settings page."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/settings")
        authenticated_page.wait_for_load_state("networkidle")
        
        sources_tab = authenticated_page.locator(
            "button:has-text('Sources'), "
            "a:has-text('Sources'), "
            "button:has-text('Integrations'), "
            "a:has-text('Integrations')"
        )
        expect(sources_tab.first).to_be_visible(timeout=10000)

    def test_can_navigate_to_integrations(self, authenticated_page: Page, ui_url: str):
        """Verify clicking Integrations tab displays content."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        
        # Should show either table, empty state, or add card
        content = authenticated_page.locator(
            ".pf-v6-c-table, "
            ".pf-v6-c-card, "
            "[data-ouia-component-id='sources-empty-add-openshift-card']"
        )
        expect(content.first).to_be_visible(timeout=10000)


# =============================================================================
# Empty State Tests
# =============================================================================


@pytest.fixture(scope="function")
def ensure_no_sources(keycloak_config, gateway_url):
    """Delete all sources before running empty state tests.
    
    WARNING: This deletes all sources in the namespace!
    Only use for tests that require a clean slate.
    
    Uses its own fresh token to avoid expiration issues.
    """
    # Create fresh session with new token
    token = obtain_jwt_token(keycloak_config)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token.access_token}"
    session.headers["Content-Type"] = "application/json"
    session.verify = False
    
    response = session.get(f"{gateway_url}/cost-management/v1/sources")
    if response.ok:
        for source in response.json().get("data", []):
            session.delete(
                f"{gateway_url}/cost-management/v1/sources/{source['id']}"
            )
    yield
    # No cleanup needed - tests will handle their own sources


@pytest.mark.ui
@pytest.mark.sources
class TestIntegrationsEmptyState:
    """Test Integrations empty state when no sources are configured.
    
    Note: These tests require no existing sources. They use ensure_no_sources
    fixture to clean up before running.
    """

    def test_empty_state_shows_openshift_card(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify empty state shows clickable OpenShift card."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        
        ocp_card = authenticated_page.locator(
            "[data-ouia-component-id='sources-empty-add-openshift-card'], "
            ".pf-v6-c-card.pf-m-clickable[aria-label*='OpenShift']"
        )
        expect(ocp_card.first).to_be_visible(timeout=10000)

    def test_openshift_card_opens_wizard(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify clicking OpenShift card opens the add wizard."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        
        open_add_integration_wizard(authenticated_page)
        
        wizard = authenticated_page.locator(
            ".pf-v6-c-wizard, .pf-v6-c-modal-box, [role='dialog']"
        )
        expect(wizard.first).to_be_visible(timeout=5000)


# =============================================================================
# Wizard Tests
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
class TestAddIntegrationWizard:
    """Test the Add Integration wizard.
    
    These tests use ensure_no_sources to guarantee the wizard opens
    from the empty state card.
    """

    def test_wizard_has_name_input(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify wizard step 1 has name input field."""
        # Start fresh by going to base URL first
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        open_add_integration_wizard(authenticated_page)
        
        try:
            name_input = authenticated_page.locator(
                ".pf-v6-c-modal-box input[type='text']"
            )
            expect(name_input.first).to_be_visible(timeout=5000)
        finally:
            # Always close the wizard
            click_wizard_cancel(authenticated_page)

    def test_wizard_has_cluster_id_input(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify wizard step 2 has cluster ID input field."""
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        open_add_integration_wizard(authenticated_page)
        
        try:
            # Fill step 1 and advance
            fill_wizard_step1_name(authenticated_page, "test-name")
            click_wizard_next(authenticated_page)
            
            cluster_input = authenticated_page.locator(
                "input[name='credentials.cluster_id'], "
                ".pf-v6-c-modal-box input[type='text']"
            )
            expect(cluster_input.first).to_be_visible(timeout=5000)
        finally:
            # Always close the wizard
            click_wizard_cancel(authenticated_page)

    def test_wizard_can_be_cancelled(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify wizard can be cancelled without creating a source."""
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        open_add_integration_wizard(authenticated_page)
        
        click_wizard_cancel(authenticated_page)
        
        wizard = authenticated_page.locator(".pf-v6-c-modal-box")
        expect(wizard).to_have_count(0)

    def test_wizard_shows_review_step(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify wizard has a review step with Submit button."""
        authenticated_page.goto(ui_url)
        authenticated_page.wait_for_load_state("networkidle")
        
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        open_add_integration_wizard(authenticated_page)
        
        try:
            # Complete steps 1 and 2
            fill_wizard_step1_name(authenticated_page, "review-test")
            click_wizard_next(authenticated_page)
            fill_wizard_step2_cluster_id(authenticated_page, "test-cluster-id")
            click_wizard_next(authenticated_page)
            
            # Verify we're on review step with Submit button
            submit_button = authenticated_page.locator("button:has-text('Submit')")
            expect(submit_button.first).to_be_visible(timeout=5000)
        finally:
            # Always close the wizard
            click_wizard_cancel(authenticated_page)


# =============================================================================
# Table Tests (with existing source)
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
class TestIntegrationsTable:
    """Test Integrations table when sources exist."""

    def test_table_displays_source(
        self, authenticated_page: Page, ui_url: str, test_source: SourceData
    ):
        """Verify table displays existing sources."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page, 20000)
        
        source_text = authenticated_page.locator(f":text('{test_source.name}')")
        expect(source_text.first).to_be_visible(timeout=15000)

    def test_table_shows_openshift_type(
        self, authenticated_page: Page, ui_url: str, test_source: SourceData
    ):
        """Verify source type (OpenShift) is displayed."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page, 20000)
        
        # Find our source first
        expect(authenticated_page.locator(f":text('{test_source.name}')").first).to_be_visible()
        
        # OpenShift type indicator should be visible
        openshift_type = authenticated_page.locator(":text('OpenShift'), :text('OCP')")
        expect(openshift_type.first).to_be_visible()

    def test_source_has_actions_menu(
        self, authenticated_page: Page, ui_url: str, test_source: SourceData
    ):
        """Verify source row has an actions menu."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page, 20000)
        
        expect(authenticated_page.locator(f":text('{test_source.name}')").first).to_be_visible()
        
        actions_button = authenticated_page.locator(
            ".pf-v6-c-dropdown__toggle, "
            ".pf-v6-c-menu-toggle, "
            "button[aria-label='Actions']"
        )
        expect(actions_button.first).to_be_visible()


# =============================================================================
# Functional Workflow Tests
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
@pytest.mark.slow
class TestIntegrationWorkflows:
    """End-to-end workflow tests for source management."""

    def test_create_integration_workflow(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url,
        ensure_no_sources
    ):
        """Create a new integration through the full wizard workflow."""
        source_name = f"workflow-create-{uuid.uuid4().hex[:8]}"
        cluster_id = f"workflow-cluster-{uuid.uuid4().hex[:8]}"
        
        try:
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            create_integration_via_wizard(authenticated_page, source_name, cluster_id)
            
            # Reload page to verify source was created
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            source_text = authenticated_page.locator(f":text('{source_name}')")
            expect(source_text.first).to_be_visible(timeout=30000)
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_delete_integration_workflow(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url
    ):
        """Delete an existing integration through the UI."""
        # Setup: create source via API
        source_name = f"workflow-delete-{uuid.uuid4().hex[:8]}"
        cluster_id = f"delete-cluster-{uuid.uuid4().hex[:8]}"
        
        response = sources_api_session.post(
            f"{gateway_url}/cost-management/v1/sources",
            json={
                "name": source_name,
                "source_type_id": 1,
                "source_ref": cluster_id,
            },
        )
        assert response.status_code == 201
        
        try:
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page, 20000)
            
            # Verify source is visible
            expect(authenticated_page.locator(f":text('{source_name}')").first).to_be_visible()
            
            # Delete via UI
            delete_source_via_ui(authenticated_page, source_name)
            
            # Reload page to ensure fresh data
            authenticated_page.reload()
            wait_for_integrations_load(authenticated_page, 20000)
            
            # Verify source is gone
            deleted_source = authenticated_page.locator(f":text('{source_name}')")
            expect(deleted_source).to_have_count(0)
            
            # Also verify via API
            api_response = sources_api_session.get(
                f"{gateway_url}/cost-management/v1/sources",
                params={"name": source_name},
            )
            if api_response.ok:
                sources = api_response.json().get("data", [])
                assert len(sources) == 0, f"Source still exists in API: {sources}"
            
        finally:
            # Cleanup if delete failed
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_create_multiple_integrations(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url,
        ensure_no_sources
    ):
        """Create multiple integrations via UI wizard and verify all appear in table.
        
        This test validates that users can create multiple sources in sequence,
        which requires proper wizard state management between creations.
        """
        sources = [
            (f"multi-test-1-{uuid.uuid4().hex[:6]}", f"cluster-1-{uuid.uuid4().hex[:6]}"),
            (f"multi-test-2-{uuid.uuid4().hex[:6]}", f"cluster-2-{uuid.uuid4().hex[:6]}"),
        ]
        created_sources = []
        
        try:
            # Navigate to integrations page
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            # Create first source via wizard (from empty state)
            create_integration_via_wizard(authenticated_page, sources[0][0], sources[0][1])
            created_sources.append(sources[0][0])
            
            # Reload page to clear wizard state and verify first source
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            expect(authenticated_page.locator(f":text('{sources[0][0]}')").first).to_be_visible(timeout=30000)
            
            # Now create second source via wizard (from populated state)
            # The wizard opens via "Add" button when sources exist
            create_integration_via_wizard(authenticated_page, sources[1][0], sources[1][1])
            created_sources.append(sources[1][0])
            
            # Reload and verify both sources appear in the table
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            for name, _ in sources:
                source_locator = authenticated_page.locator(f":text('{name}')")
                expect(source_locator.first).to_be_visible(timeout=30000)
            
        finally:
            # Cleanup all created sources
            for name in created_sources:
                cleanup_source_by_name(sources_api_session, gateway_url, name)

    def test_create_and_delete_workflow(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url,
        ensure_no_sources
    ):
        """Full lifecycle: create an integration via wizard, then delete it via UI."""
        source_name = f"lifecycle-{uuid.uuid4().hex[:8]}"
        cluster_id = f"lifecycle-cluster-{uuid.uuid4().hex[:8]}"
        
        try:
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            # Create
            create_integration_via_wizard(authenticated_page, source_name, cluster_id)
            
            # Reload to verify and prepare for delete
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            expect(authenticated_page.locator(f":text('{source_name}')").first).to_be_visible(timeout=30000)
            
            # Delete
            delete_source_via_ui(authenticated_page, source_name)
            
            # Reload to verify deletion
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            # Verify deleted
            deleted = authenticated_page.locator(f":text('{source_name}')")
            expect(deleted).to_have_count(0)
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_cancel_wizard_preserves_empty_state(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify cancelling wizard returns to empty state without creating source."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        
        # Open wizard and fill some data
        open_add_integration_wizard(authenticated_page)
        fill_wizard_step1_name(authenticated_page, "should-not-exist")
        
        # Cancel
        click_wizard_cancel(authenticated_page)
        
        # Reload to verify empty state
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        
        # Verify we're back to empty state with the OpenShift card
        ocp_card = authenticated_page.locator(
            "[data-ouia-component-id='sources-empty-add-openshift-card']"
        )
        expect(ocp_card.first).to_be_visible(timeout=10000)
        
        # Verify no source was created
        no_source = authenticated_page.locator(":text('should-not-exist')")
        expect(no_source).to_have_count(0)


# =============================================================================
# Actions Menu Tests
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
class TestSourceActionsMenu:
    """Test the source actions (kebab) menu."""

    def test_actions_menu_has_remove_option(
        self, authenticated_page: Page, ui_url: str, test_source: SourceData
    ):
        """Verify actions menu contains Remove option."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page, 20000)
        
        open_source_actions_menu(authenticated_page, test_source.name)
        
        remove_option = authenticated_page.locator(
            "[role='menuitem']:has-text('Remove'), "
            ".pf-v6-c-menu__item:has-text('Remove')"
        )
        expect(remove_option.first).to_be_visible()

    def test_remove_shows_confirmation(
        self, authenticated_page: Page, ui_url: str, test_source: SourceData
    ):
        """Verify remove action shows confirmation dialog with checkbox."""
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page, 20000)
        
        open_source_actions_menu(authenticated_page, test_source.name)
        
        # Click remove
        remove_option = authenticated_page.locator(
            "[role='menuitem']:has-text('Remove'), "
            ".pf-v6-c-menu__item:has-text('Remove')"
        )
        remove_option.first.click()
        authenticated_page.wait_for_timeout(1000)
        
        # Check for confirmation modal
        confirm_modal = authenticated_page.locator(".pf-v6-c-modal-box")
        expect(confirm_modal.first).to_be_visible()
        
        # Verify it has an acknowledgement checkbox
        checkbox = confirm_modal.locator("input[type='checkbox']")
        expect(checkbox.first).to_be_visible()
        
        # Cancel to not actually delete
        cancel = authenticated_page.locator("button:has-text('Cancel')")
        if cancel.count() > 0:
            cancel.first.click()
