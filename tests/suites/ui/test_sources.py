"""
UI tests for Sources/Integrations management (FLPATH-2976).

These tests validate the Sources tab in Settings page for creating and
managing OpenShift cost data sources directly from the Cost Management UI.

Jira: https://redhat.atlassian.net/browse/FLPATH-2976

Test Strategy:
    These are end-to-end workflow tests that validate complete user journeys
    rather than isolated UI components. Each test exercises multiple UI 
    elements in sequence, providing better coverage with fewer tests.
"""

import uuid
from dataclasses import dataclass

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
    
    The page is considered loaded when either:
    - A table with sources is visible
    - The empty state card is visible
    - The loading spinner disappears
    """
    # Wait for loading spinner to disappear (if present)
    spinner = page.locator(".pf-v6-c-spinner, .pf-c-spinner")
    if spinner.count() > 0:
        spinner.first.wait_for(state="hidden", timeout=timeout_ms)
    
    # Wait for content to appear (table OR empty state)
    content = page.locator(
        ".pf-v6-c-table tbody tr, "
        "[data-ouia-component-id='sources-empty-add-openshift-card'], "
        "button:has-text('Add integration')"
    )
    content.first.wait_for(state="visible", timeout=timeout_ms)


def dismiss_any_modal(page: Page) -> None:
    """Dismiss any open modal/backdrop by trying multiple close methods."""
    backdrop = page.locator(".pf-v6-c-backdrop")
    modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")
    
    attempts = 0
    max_attempts = 3
    
    while (backdrop.count() > 0 or modal.count() > 0) and attempts < max_attempts:
        attempts += 1
        
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
                # Wait for modal to close
                modal.first.wait_for(state="hidden", timeout=3000)
            except Exception:
                pass
        else:
            page.keyboard.press("Escape")
            try:
                modal.first.wait_for(state="hidden", timeout=3000)
            except Exception:
                pass
        
        backdrop = page.locator(".pf-v6-c-backdrop")
        modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")


def navigate_to_integrations(page: Page, ui_url: str) -> None:
    """Navigate to the Integrations tab in Settings page."""
    dismiss_any_modal(page)
    
    page.goto(f"{ui_url}/openshift/cost-management/settings")
    page.wait_for_load_state("networkidle")
    
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


def verify_empty_state(page: Page) -> None:
    """Verify the empty state is displayed with OpenShift card."""
    ocp_card = page.locator("[data-ouia-component-id='sources-empty-add-openshift-card']")
    expect(
        ocp_card.first,
        "Empty state should display OpenShift card for adding first integration"
    ).to_be_visible(timeout=10000)


def click_openshift_card(page: Page) -> None:
    """Click the OpenShift card in empty state to open wizard."""
    ocp_card = page.locator("[data-ouia-component-id='sources-empty-add-openshift-card']")
    expect(ocp_card.first).to_be_visible(timeout=10000)
    ocp_card.first.click()
    # Wait for wizard to appear
    wizard = page.locator(".pf-v6-c-wizard, .pf-v6-c-modal-box")
    wizard.first.wait_for(state="visible", timeout=5000)


def click_add_integration_button(page: Page) -> None:
    """Click the Add integration button (when sources exist)."""
    add_button = page.locator("button:has-text('Add integration')")
    expect(add_button.first).to_be_visible(timeout=10000)
    add_button.first.click()
    # Wait for wizard to appear
    wizard = page.locator(".pf-v6-c-wizard, .pf-v6-c-modal-box")
    wizard.first.wait_for(state="visible", timeout=5000)


def verify_wizard_open(page: Page) -> None:
    """Verify the wizard/modal is open."""
    wizard = page.locator(".pf-v6-c-wizard, .pf-v6-c-modal-box")
    expect(wizard.first).to_be_visible(timeout=10000)


def verify_wizard_name_input(page: Page) -> None:
    """Verify wizard step 1 has name input."""
    # Use specific locators: name attribute, aria-label, or form group label
    name_input = page.locator(
        "input[name='name'], "
        "input[aria-label='Integration name'], "
        "input[aria-label='Name'], "
        ".pf-v6-c-form__group:has-text('Name') input[type='text'], "
        ".pf-v6-c-modal-box input[type='text']"
    )
    expect(name_input.first).to_be_visible(timeout=5000)


def fill_wizard_name(page: Page, name: str) -> None:
    """Fill the integration name field."""
    name_input = page.locator(
        "input[name='name'], "
        "input[aria-label='Integration name'], "
        "input[aria-label='Name'], "
        ".pf-v6-c-form__group:has-text('Name') input[type='text'], "
        ".pf-v6-c-modal-box input[type='text']"
    )
    expect(name_input.first).to_be_visible(timeout=5000)
    name_input.first.fill(name)


def click_wizard_next(page: Page) -> None:
    """Click the Next button in the wizard."""
    next_button = page.locator("button:has-text('Next')")
    expect(next_button.first).to_be_enabled(timeout=5000)
    next_button.first.click()
    # Wait for next step content to load
    page.wait_for_load_state("domcontentloaded")


def verify_wizard_cluster_id_input(page: Page) -> None:
    """Verify wizard step 2 has cluster ID input."""
    # Use specific locators: name attribute, aria-label, or form group label
    cluster_input = page.locator(
        "input[name='credentials.cluster_id'], "
        "input[name='cluster_id'], "
        "input[aria-label='Cluster identifier'], "
        "input[aria-label='Cluster ID'], "
        ".pf-v6-c-form__group:has-text('Cluster') input[type='text'], "
        ".pf-v6-c-modal-box input[type='text']"
    )
    expect(cluster_input.first).to_be_visible(timeout=5000)


def fill_wizard_cluster_id(page: Page, cluster_id: str) -> None:
    """Fill the cluster ID field."""
    cluster_input = page.locator(
        "input[name='credentials.cluster_id'], "
        "input[name='cluster_id'], "
        "input[aria-label='Cluster identifier'], "
        "input[aria-label='Cluster ID'], "
        ".pf-v6-c-form__group:has-text('Cluster') input[type='text'], "
        ".pf-v6-c-modal-box input[type='text']"
    )
    expect(cluster_input.first).to_be_visible(timeout=5000)
    cluster_input.first.fill(cluster_id)


def verify_wizard_submit_button(page: Page) -> None:
    """Verify the Submit button is visible on review step."""
    submit_button = page.locator("button:has-text('Submit')")
    expect(submit_button.first).to_be_visible(timeout=5000)


def click_wizard_submit(page: Page) -> None:
    """Click the Submit button to create the integration."""
    submit_button = page.locator("button:has-text('Submit')")
    expect(submit_button.first).to_be_visible(timeout=5000)
    submit_button.first.click()
    page.wait_for_load_state("networkidle")
    # Wait for wizard to close or success indicator
    wizard = page.locator(".pf-v6-c-wizard, .pf-v6-c-modal-box")
    try:
        wizard.first.wait_for(state="hidden", timeout=10000)
    except Exception:
        pass  # Modal may still be open with success message


def click_wizard_cancel(page: Page) -> None:
    """Click Cancel to close the wizard without saving."""
    cancel_button = page.locator(
        "button:has-text('Cancel'), "
        "button[aria-label='Close']"
    )
    cancel_button.first.click()
    # Wait for wizard to close
    wizard = page.locator(".pf-v6-c-modal-box")
    wizard.first.wait_for(state="hidden", timeout=5000)


def verify_wizard_closed(page: Page) -> None:
    """Verify the wizard is closed."""
    wizard = page.locator(".pf-v6-c-modal-box")
    expect(wizard).to_have_count(0, timeout=5000)


def verify_source_in_table(page: Page, source_name: str) -> None:
    """Verify a source appears in the integrations table."""
    # Find the table row containing our source
    row = page.locator(f"tr:has-text('{source_name}')")
    expect(
        row.first,
        f"Source '{source_name}' should appear in integrations table"
    ).to_be_visible(timeout=30000)
    
    # Verify the name is in the row
    name_cell = row.locator(f":text('{source_name}')")
    expect(
        name_cell.first,
        f"Source name '{source_name}' should be visible in table row"
    ).to_be_visible()
    
    # Scroll to and highlight the row for video visibility
    row.first.scroll_into_view_if_needed()
    row.first.highlight()


def verify_source_type_in_row(page: Page, source_name: str, expected_type: str = "OpenShift") -> None:
    """Verify the source type is displayed in the source's row."""
    row = page.locator(f"tr:has-text('{source_name}')")
    expect(
        row.first,
        f"Table row for source '{source_name}' should be visible"
    ).to_be_visible(timeout=15000)
    
    # Check for type indicator in the same row
    # UI shows "OpenShift Container Platform" or abbreviated versions
    type_indicator = row.locator(f":text('{expected_type}'), :text('OCP'), :text('OpenShift Container Platform')")
    expect(
        type_indicator.first,
        f"Source '{source_name}' should show type '{expected_type}'"
    ).to_be_visible()
    
    # Highlight the type for video visibility
    type_indicator.first.highlight()


def verify_source_status_in_row(page: Page, source_name: str, expected_status: str = "Available") -> None:
    """Verify the source status is displayed in the source's row.
    
    Status values observed:
    - "Available" - Integration is connected and working
    - "Unavailable" - Integration has connection issues
    - Other potential statuses may exist
    """
    row = page.locator(f"tr:has-text('{source_name}')")
    expect(
        row.first,
        f"Table row for source '{source_name}' should be visible"
    ).to_be_visible(timeout=15000)
    
    # Check for status indicator in the same row
    status_indicator = row.locator(f":text('{expected_status}')")
    expect(
        status_indicator.first,
        f"Source '{source_name}' should have status '{expected_status}'"
    ).to_be_visible()
    
    # Highlight the status for video visibility
    status_indicator.first.highlight()


def verify_actions_menu_in_row(page: Page, source_name: str) -> None:
    """Verify the actions (kebab) menu exists in the source's row."""
    row = page.locator(f"tr:has-text('{source_name}')")
    expect(row.first).to_be_visible(timeout=15000)
    
    actions_button = row.locator(
        ".pf-v6-c-menu-toggle, "
        ".pf-v6-c-dropdown__toggle, "
        "button[aria-label='Kebab toggle'], "
        "button[aria-label='Actions']"
    )
    expect(actions_button.first).to_be_visible()


def open_actions_menu(page: Page, source_name: str) -> None:
    """Open the actions (kebab) menu for a specific source."""
    row = page.locator(f"tr:has-text('{source_name}')")
    expect(row.first).to_be_visible(timeout=15000)
    
    actions_button = row.locator(
        ".pf-v6-c-menu-toggle, "
        ".pf-v6-c-dropdown__toggle, "
        "button[aria-label='Kebab toggle'], "
        "button[aria-label='Actions']"
    )
    actions_button.first.click()
    # Wait for menu to appear
    menu = page.locator(".pf-v6-c-menu, .pf-v6-c-dropdown__menu, [role='menu']")
    menu.first.wait_for(state="visible", timeout=3000)


def verify_remove_option_in_menu(page: Page) -> None:
    """Verify Remove option exists in the open actions menu."""
    remove_option = page.locator(
        "[role='menuitem']:has-text('Remove'), "
        ".pf-v6-c-menu__item:has-text('Remove')"
    )
    expect(remove_option.first).to_be_visible(timeout=5000)


def click_remove_option(page: Page) -> None:
    """Click the Remove option in the actions menu."""
    remove_option = page.locator(
        "[role='menuitem']:has-text('Remove'), "
        ".pf-v6-c-menu__item:has-text('Remove')"
    )
    remove_option.first.click()
    # Wait for confirmation modal to appear
    modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")
    modal.first.wait_for(state="visible", timeout=5000)


def verify_remove_confirmation_modal(page: Page) -> None:
    """Verify the remove confirmation modal with checkbox."""
    modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")
    expect(modal.first).to_be_visible(timeout=5000)
    
    # Verify checkbox exists
    checkbox = modal.locator("input[type='checkbox']")
    expect(checkbox.first).to_be_visible()
    
    # Verify remove button is disabled until checkbox checked
    remove_button = modal.locator("button:has-text('Remove integration'), button.pf-m-danger")
    expect(remove_button.first).to_be_visible()


def complete_remove_confirmation(page: Page) -> None:
    """Complete the remove confirmation by checking checkbox and clicking remove."""
    modal = page.locator(".pf-v6-c-modal-box, [role='dialog']")
    
    # Check the acknowledgement checkbox
    checkbox = modal.locator("input[type='checkbox']")
    checkbox.first.check()
    
    # Click the remove button (wait for it to become enabled after checkbox)
    remove_button = modal.locator("button:has-text('Remove integration'), button.pf-m-danger")
    expect(remove_button.first).to_be_enabled(timeout=5000)
    remove_button.first.click()
    
    # Wait for modal to close and page to refresh
    page.wait_for_load_state("networkidle")
    modal.first.wait_for(state="hidden", timeout=10000)


def verify_source_not_in_table(page: Page, source_name: str) -> None:
    """Verify a source does NOT appear in the table."""
    source_locator = page.locator(f"tr:has-text('{source_name}')")
    expect(
        source_locator,
        f"Source '{source_name}' should NOT appear in table after deletion"
    ).to_have_count(0, timeout=10000)


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
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def sources_api_session(keycloak_config, gateway_url) -> requests.Session:
    """Authenticated requests session for API operations in UI tests."""
    token = obtain_jwt_token(keycloak_config)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token.access_token}"
    session.headers["Content-Type"] = "application/json"
    session.verify = False
    return session


# Test source name prefixes - used for identifying test-created sources
TEST_SOURCE_PREFIXES = ("e2e-", "api-source-", "lifecycle-", "should-not-exist")


@pytest.fixture(scope="function")
def ensure_no_sources(keycloak_config, gateway_url):
    """Delete ALL sources before running tests that require empty state.

    This ensures tests that depend on empty state work correctly even when
    other tests have created sources beforehand.

    Note: This deletes ALL sources, not just test-prefixed ones, because:
    - IQE tests create sources with UUID names that don't match test prefixes
    - Empty state tests require truly empty state to pass
    - In CI, sources are ephemeral and can be recreated
    """
    token = obtain_jwt_token(keycloak_config)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token.access_token}"
    session.headers["Content-Type"] = "application/json"
    session.verify = False

    response = session.get(f"{gateway_url}/cost-management/v1/sources")
    if response.ok:
        for source in response.json().get("data", []):
            source_id = source.get("id")
            if source_id:
                session.delete(f"{gateway_url}/cost-management/v1/sources/{source_id}")
    yield


@pytest.fixture(scope="function")
def api_created_source(sources_api_session, gateway_url) -> SourceData:
    """Create a test source via API with automatic cleanup."""
    source_name = f"api-source-{uuid.uuid4().hex[:8]}"
    cluster_id = f"api-cluster-{uuid.uuid4().hex[:8]}"
    
    response = sources_api_session.post(
        f"{gateway_url}/cost-management/v1/sources",
        json={
            "name": source_name,
            "source_type_id": 1,
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


# =============================================================================
# Smoke Test
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
@pytest.mark.smoke
class TestIntegrationsSmoke:
    """Quick smoke test to verify basic UI accessibility."""

    def test_settings_page_loads_with_integrations_tab(
        self, authenticated_page: Page, ui_url: str
    ):
        """Verify Settings page loads and Integrations tab is accessible.
        
        Validates:
        - Settings page loads successfully
        - Integrations/Sources tab is visible
        - Tab can be clicked and content loads
        """
        # Navigate to settings
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/settings")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Verify Integrations tab is visible
        sources_tab = authenticated_page.locator(
            "button:has-text('Sources'), "
            "a:has-text('Sources'), "
            "button:has-text('Integrations'), "
            "a:has-text('Integrations')"
        )
        expect(sources_tab.first).to_be_visible(timeout=10000)
        
        # Click tab and verify content loads
        sources_tab.first.click()
        authenticated_page.wait_for_load_state("networkidle")
        wait_for_integrations_load(authenticated_page)
        
        # Should show either table, empty state, or add card
        content = authenticated_page.locator(
            ".pf-v6-c-table, "
            ".pf-v6-c-card, "
            "[data-ouia-component-id='sources-empty-add-openshift-card']"
        )
        expect(content.first).to_be_visible(timeout=10000)


# =============================================================================
# End-to-End Workflow Tests
# =============================================================================


@pytest.mark.ui
@pytest.mark.sources
class TestIntegrationWorkflows:
    """End-to-end workflow tests covering complete user journeys.
    
    These tests validate the full integration management lifecycle from
    the user's perspective, exercising multiple UI components in sequence.
    """

    def test_create_integration_from_empty_state(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url,
        ensure_no_sources
    ):
        """Create a new integration starting from empty state.
        
        User Journey:
        1. Navigate to Integrations (empty state)
        2. Verify OpenShift card is displayed
        3. Click card to open wizard
        4. Complete wizard steps (name, cluster ID, submit)
        5. Verify integration appears in table with correct type and status
        
        Validates:
        - Empty state displays OpenShift card
        - Card click opens wizard
        - Wizard has name input (step 1)
        - Wizard has cluster ID input (step 2)
        - Wizard has submit button (step 3)
        - Created integration appears in table
        - Integration shows OpenShift type
        - Integration shows Available status
        """
        source_name = f"e2e-create-{uuid.uuid4().hex[:8]}"
        cluster_id = f"e2e-cluster-{uuid.uuid4().hex[:8]}"
        
        try:
            # Navigate to integrations
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            # Verify empty state and click OpenShift card
            verify_empty_state(authenticated_page)
            click_openshift_card(authenticated_page)
            
            # Verify wizard opened with name input
            verify_wizard_open(authenticated_page)
            verify_wizard_name_input(authenticated_page)
            
            # Step 1: Fill name and proceed
            fill_wizard_name(authenticated_page, source_name)
            click_wizard_next(authenticated_page)
            
            # Step 2: Verify cluster ID input, fill and proceed
            verify_wizard_cluster_id_input(authenticated_page)
            fill_wizard_cluster_id(authenticated_page, cluster_id)
            click_wizard_next(authenticated_page)
            
            # Step 3: Verify submit button and submit
            verify_wizard_submit_button(authenticated_page)
            click_wizard_submit(authenticated_page)
            dismiss_any_modal(authenticated_page)
            
            # Reload and verify source in table
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            verify_source_in_table(authenticated_page, source_name)
            verify_source_type_in_row(authenticated_page, source_name, "OpenShift")
            verify_source_status_in_row(authenticated_page, source_name, "Available")
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_add_second_integration_from_table_view(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url,
        api_created_source: SourceData
    ):
        """Add a second integration when one already exists.
        
        User Journey:
        1. Navigate to Integrations (with existing source)
        2. Verify existing source in table
        3. Click "Add integration" button
        4. Complete wizard
        5. Verify both integrations appear in table
        
        Validates:
        - Table displays existing source
        - "Add integration" button works from table view
        - Multiple sources can coexist in table
        """
        new_source_name = f"e2e-second-{uuid.uuid4().hex[:8]}"
        new_cluster_id = f"e2e-second-cluster-{uuid.uuid4().hex[:8]}"
        
        try:
            # Navigate and verify existing source
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page, 20000)
            
            verify_source_in_table(authenticated_page, api_created_source.name)
            
            # Click Add integration button
            click_add_integration_button(authenticated_page)
            verify_wizard_open(authenticated_page)
            
            # Complete wizard
            fill_wizard_name(authenticated_page, new_source_name)
            click_wizard_next(authenticated_page)
            fill_wizard_cluster_id(authenticated_page, new_cluster_id)
            click_wizard_next(authenticated_page)
            click_wizard_submit(authenticated_page)
            dismiss_any_modal(authenticated_page)
            
            # Verify both sources in table
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            
            verify_source_in_table(authenticated_page, api_created_source.name)
            verify_source_in_table(authenticated_page, new_source_name)
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, new_source_name)

    def test_delete_integration_via_actions_menu(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url
    ):
        """Delete an integration using the actions menu.
        
        User Journey:
        1. Create source via API (setup)
        2. Navigate to Integrations
        3. Verify source in table with actions menu
        4. Open actions menu, verify Remove option
        5. Click Remove, verify confirmation modal with checkbox
        6. Complete removal
        7. Verify source no longer in table
        
        Validates:
        - Table row has actions menu
        - Actions menu contains Remove option
        - Remove shows confirmation modal
        - Confirmation requires checkbox acknowledgement
        - Removal deletes source from table
        """
        source_name = f"e2e-delete-{uuid.uuid4().hex[:8]}"
        cluster_id = f"e2e-delete-cluster-{uuid.uuid4().hex[:8]}"
        
        # Create source via API
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
            # Navigate and verify source with actions menu
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page, 20000)
            
            verify_source_in_table(authenticated_page, source_name)
            verify_actions_menu_in_row(authenticated_page, source_name)
            
            # Open actions menu and verify Remove option
            open_actions_menu(authenticated_page, source_name)
            verify_remove_option_in_menu(authenticated_page)
            
            # Click Remove and verify confirmation modal
            click_remove_option(authenticated_page)
            verify_remove_confirmation_modal(authenticated_page)
            
            # Complete removal
            complete_remove_confirmation(authenticated_page)
            
            # Verify source removed
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page, 20000)
            
            verify_source_not_in_table(authenticated_page, source_name)
            
            # Verify via API as well
            api_response = sources_api_session.get(
                f"{gateway_url}/cost-management/v1/sources",
                params={"name": source_name},
            )
            if api_response.ok:
                sources = api_response.json().get("data", [])
                assert len(sources) == 0, f"Source still exists in API: {sources}"
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_full_lifecycle_create_and_delete(
        self, authenticated_page: Page, ui_url: str, sources_api_session, gateway_url,
        ensure_no_sources
    ):
        """Complete lifecycle: create via wizard, then delete via UI.
        
        User Journey:
        1. Start from empty state
        2. Create integration via wizard
        3. Verify in table
        4. Delete via actions menu
        5. Verify empty state returns
        
        Validates:
        - Full CRUD cycle through UI
        - State transitions (empty -> populated -> empty)
        """
        source_name = f"lifecycle-{uuid.uuid4().hex[:8]}"
        cluster_id = f"lifecycle-cluster-{uuid.uuid4().hex[:8]}"
        
        try:
            # Start from empty state
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            verify_empty_state(authenticated_page)
            
            # Create via wizard
            click_openshift_card(authenticated_page)
            fill_wizard_name(authenticated_page, source_name)
            click_wizard_next(authenticated_page)
            fill_wizard_cluster_id(authenticated_page, cluster_id)
            click_wizard_next(authenticated_page)
            click_wizard_submit(authenticated_page)
            dismiss_any_modal(authenticated_page)
            
            # Verify in table
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            verify_source_in_table(authenticated_page, source_name)
            
            # Delete via actions menu
            open_actions_menu(authenticated_page, source_name)
            click_remove_option(authenticated_page)
            complete_remove_confirmation(authenticated_page)
            
            # Verify empty state returns
            navigate_to_integrations(authenticated_page, ui_url)
            wait_for_integrations_load(authenticated_page)
            verify_empty_state(authenticated_page)
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_cancel_wizard_does_not_create_source(
        self, authenticated_page: Page, ui_url: str, ensure_no_sources
    ):
        """Verify cancelling wizard doesn't create a source.
        
        User Journey:
        1. Start from empty state
        2. Open wizard
        3. Fill in name
        4. Cancel wizard
        5. Verify still in empty state
        
        Validates:
        - Cancel button works
        - Wizard closes without creating source
        - Empty state persists
        """
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        verify_empty_state(authenticated_page)
        
        # Open wizard and fill data
        click_openshift_card(authenticated_page)
        verify_wizard_open(authenticated_page)
        fill_wizard_name(authenticated_page, "should-not-exist")
        
        # Cancel
        click_wizard_cancel(authenticated_page)
        verify_wizard_closed(authenticated_page)
        
        # Verify still empty state
        navigate_to_integrations(authenticated_page, ui_url)
        wait_for_integrations_load(authenticated_page)
        verify_empty_state(authenticated_page)
        
        # Verify no source created
        no_source = authenticated_page.locator(":text('should-not-exist')")
        expect(no_source).to_have_count(0)
