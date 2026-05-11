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

import pytest
import requests
from playwright.sync_api import Page, expect

from conftest import create_authenticated_session

from .pages import SourcesPage, SourceData


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def sources_api_session(keycloak_config, gateway_url) -> requests.Session:
    """Authenticated requests session for API operations in UI tests."""
    return create_authenticated_session(keycloak_config, content_type="application/json")


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
    session = create_authenticated_session(keycloak_config, content_type="application/json")

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


@pytest.fixture
def sources_page(authenticated_page: Page, ui_url: str) -> SourcesPage:
    """Provide a SourcesPage instance for tests."""
    return SourcesPage(authenticated_page, ui_url)


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
        
        # Use page object for content verification
        sources = SourcesPage(authenticated_page, ui_url)
        sources.wait_for_content_load()
        
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
        self, sources_page: SourcesPage, sources_api_session, gateway_url,
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
            sources_page.navigate()
            sources_page.wait_for_content_load()
            
            # Verify empty state and click OpenShift card
            sources_page.verify_empty_state()
            sources_page.click_openshift_card()
            
            # Verify wizard opened with name input
            sources_page.verify_wizard_open()
            sources_page.verify_name_input_visible()
            
            # Step 1: Fill name and proceed
            sources_page.fill_name(source_name)
            sources_page.click_next()
            
            # Step 2: Verify cluster ID input, fill and proceed
            sources_page.verify_cluster_id_input_visible()
            sources_page.fill_cluster_id(cluster_id)
            sources_page.click_next()
            
            # Step 3: Verify submit button and submit
            sources_page.verify_submit_button_visible()
            sources_page.click_submit()
            
            # Reload and verify source in table
            sources_page.navigate()
            sources_page.wait_for_content_load()
            
            sources_page.verify_source_in_table(source_name)
            sources_page.verify_source_type(source_name, "OpenShift")
            sources_page.verify_source_status(source_name, "Available")
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_add_second_integration_from_table_view(
        self, sources_page: SourcesPage, sources_api_session, gateway_url,
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
            sources_page.navigate()
            sources_page.wait_for_content_load(20000)
            
            sources_page.verify_source_in_table(api_created_source.name)
            
            # Click Add integration button
            sources_page.click_add_integration_button()
            sources_page.verify_wizard_open()
            
            # Complete wizard
            sources_page.create_integration_via_wizard(new_source_name, new_cluster_id)
            
            # Verify both sources in table
            sources_page.navigate()
            sources_page.wait_for_content_load()
            
            sources_page.verify_source_in_table(api_created_source.name)
            sources_page.verify_source_in_table(new_source_name)
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, new_source_name)

    def test_delete_integration_via_actions_menu(
        self, sources_page: SourcesPage, sources_api_session, gateway_url
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
            sources_page.navigate()
            sources_page.wait_for_content_load(20000)
            
            sources_page.verify_source_in_table(source_name)
            sources_page.verify_actions_menu_exists(source_name)
            
            # Open actions menu and verify Remove option
            sources_page.open_actions_menu(source_name)
            sources_page.verify_remove_option_visible()
            
            # Click Remove and verify confirmation modal
            sources_page.click_remove_option()
            sources_page.verify_remove_confirmation_modal()
            
            # Complete removal
            sources_page.complete_remove_confirmation()
            
            # Verify source removed
            sources_page.navigate()
            sources_page.wait_for_content_load(20000)
            
            sources_page.verify_source_not_in_table(source_name)
            
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
        self, sources_page: SourcesPage, sources_api_session, gateway_url,
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
            sources_page.navigate()
            sources_page.wait_for_content_load()
            sources_page.verify_empty_state()
            
            # Create via wizard
            sources_page.click_openshift_card()
            sources_page.create_integration_via_wizard(source_name, cluster_id)
            
            # Verify in table
            sources_page.navigate()
            sources_page.wait_for_content_load()
            sources_page.verify_source_in_table(source_name)
            
            # Delete via actions menu
            sources_page.delete_source_via_ui(source_name)
            
            # Verify empty state returns
            sources_page.navigate()
            sources_page.wait_for_content_load()
            sources_page.verify_empty_state()
            
        finally:
            cleanup_source_by_name(sources_api_session, gateway_url, source_name)

    def test_cancel_wizard_does_not_create_source(
        self, sources_page: SourcesPage, ensure_no_sources
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
        sources_page.navigate()
        sources_page.wait_for_content_load()
        sources_page.verify_empty_state()
        
        # Open wizard and fill data
        sources_page.click_openshift_card()
        sources_page.verify_wizard_open()
        sources_page.fill_name("should-not-exist")
        
        # Cancel
        sources_page.click_cancel()
        sources_page.verify_wizard_closed()
        
        # Verify still empty state
        sources_page.navigate()
        sources_page.wait_for_content_load()
        sources_page.verify_empty_state()
        
        # Verify no source created
        no_source = sources_page.page.locator(":text('should-not-exist')")
        expect(no_source).to_have_count(0)
