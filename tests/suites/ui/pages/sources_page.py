"""
Page Object for Sources/Integrations management.

Encapsulates all UI interactions for the Sources tab in Settings,
including navigation, wizard operations, and table interactions.
"""

from dataclasses import dataclass

from playwright.sync_api import Locator, Page, expect

from .common import CommonLocators


@dataclass
class SourceData:
    """Test source data container."""
    id: str
    name: str
    cluster_id: str


class SourcesPage:
    """Page object for Sources/Integrations management UI.
    
    Provides methods for navigating to the sources page, creating integrations
    via the wizard, and managing existing integrations in the table.
    
    Example:
        sources = SourcesPage(page, ui_url)
        sources.navigate()
        sources.wait_for_content_load()
        sources.create_integration_from_empty_state("my-source", "my-cluster")
        sources.verify_source_in_table("my-source")
    """

    # OUIA Component IDs
    OUIA_SOURCES_TAB = "sources-tab"
    OUIA_EMPTY_STATE_CARD = "sources-empty-add-openshift-card"

    # Default timeouts (ms)
    DEFAULT_TIMEOUT = 30000
    SHORT_TIMEOUT = 5000
    MEDIUM_TIMEOUT = 15000

    def __init__(self, page: Page, ui_url: str):
        """Initialize the SourcesPage.
        
        Args:
            page: Playwright page instance
            ui_url: Base URL of the Cost Management UI
        """
        self.page = page
        self.ui_url = ui_url
        self.locators = CommonLocators

    # =========================================================================
    # Navigation
    # =========================================================================

    def navigate(self) -> None:
        """Navigate to the Integrations tab in Settings page."""
        self.page.goto(f"{self.ui_url}/openshift/cost-management/settings")
        self.page.wait_for_load_state("domcontentloaded")
        
        # Wait for tabs container to be visible
        self.locators.tabs_container(self.page).first.wait_for(
            state="visible", timeout=self.DEFAULT_TIMEOUT
        )
        
        # Click the Sources/Integrations tab
        sources_tab = self._get_sources_tab()
        if sources_tab.count() > 0:
            sources_tab.first.click()
            self.wait_for_content_load()

    def _get_sources_tab(self) -> Locator:
        """Get the Sources/Integrations tab locator."""
        # Prefer OUIA ID
        tab = self.locators.ouia_component(self.page, self.OUIA_SOURCES_TAB)
        if tab.count() > 0:
            return tab
        
        # Fallback: scoped text search within tabs
        return self.page.locator(
            ".pf-v6-c-tabs__item button:has-text('Sources'), "
            ".pf-v6-c-tabs__item button:has-text('Integrations')"
        )

    def wait_for_content_load(self, timeout_ms: int = None) -> None:
        """Wait for the Integrations page content to load.
        
        The page is considered loaded when either:
        - A table with sources is visible
        - The empty state card is visible
        - The Add integration button is visible
        """
        timeout = timeout_ms or self.DEFAULT_TIMEOUT
        content = self.page.locator(
            ".pf-v6-c-table tbody tr, "
            f"[data-ouia-component-id='{self.OUIA_EMPTY_STATE_CARD}'], "
            "button:has-text('Add integration')"
        )
        content.first.wait_for(state="visible", timeout=timeout)

    # =========================================================================
    # Empty State
    # =========================================================================

    def verify_empty_state(self) -> None:
        """Verify the empty state is displayed with OpenShift card."""
        card = self.locators.ouia_component(self.page, self.OUIA_EMPTY_STATE_CARD)
        expect(
            card.first,
            "Empty state should display OpenShift card for adding first integration"
        ).to_be_visible(timeout=self.MEDIUM_TIMEOUT)

    def click_openshift_card(self) -> None:
        """Click the OpenShift card in empty state to open wizard."""
        card = self.locators.ouia_component(self.page, self.OUIA_EMPTY_STATE_CARD)
        expect(card.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)
        card.first.click()
        self._wait_for_wizard_open()

    # =========================================================================
    # Add Integration Button (Table View)
    # =========================================================================

    def click_add_integration_button(self) -> None:
        """Click the Add integration button (when sources exist)."""
        button = self.locators.button_with_text(self.page, "Add integration")
        expect(button.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)
        button.first.click()
        self._wait_for_wizard_open()

    # =========================================================================
    # Wizard Operations
    # =========================================================================

    def _wait_for_wizard_open(self) -> None:
        """Wait for wizard/modal to become visible."""
        wizard = self.locators.wizard(self.page).or_(self.locators.modal(self.page))
        wizard.first.wait_for(state="visible", timeout=self.SHORT_TIMEOUT)

    def verify_wizard_open(self) -> None:
        """Verify the wizard/modal is open."""
        wizard = self.locators.wizard(self.page).or_(self.locators.modal(self.page))
        expect(wizard.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)

    def _get_name_input(self) -> Locator:
        """Get the integration name input field."""
        # Try specific selectors in order of preference
        by_name = self.locators.input_by_name(self.page, "name")
        if by_name.count() > 0:
            return by_name
        
        by_aria = self.locators.input_by_aria_label(self.page, "Integration name")
        if by_aria.count() > 0:
            return by_aria
        
        by_aria_alt = self.locators.input_by_aria_label(self.page, "Name")
        if by_aria_alt.count() > 0:
            return by_aria_alt
        
        # Fallback: input within Name form group
        return self.locators.input_by_label_text(self.page, "Name")

    def verify_name_input_visible(self) -> None:
        """Verify wizard step 1 has name input."""
        expect(self._get_name_input().first).to_be_visible(timeout=self.SHORT_TIMEOUT)

    def fill_name(self, name: str) -> None:
        """Fill the integration name field."""
        name_input = self._get_name_input()
        expect(name_input.first).to_be_visible(timeout=self.SHORT_TIMEOUT)
        name_input.first.fill(name)

    def _get_cluster_id_input(self) -> Locator:
        """Get the cluster ID input field."""
        # Try specific selectors in order of preference
        by_name = self.locators.input_by_name(self.page, "credentials.cluster_id")
        if by_name.count() > 0:
            return by_name
        
        by_name_alt = self.locators.input_by_name(self.page, "cluster_id")
        if by_name_alt.count() > 0:
            return by_name_alt
        
        by_aria = self.locators.input_by_aria_label(self.page, "Cluster identifier")
        if by_aria.count() > 0:
            return by_aria
        
        by_aria_alt = self.locators.input_by_aria_label(self.page, "Cluster ID")
        if by_aria_alt.count() > 0:
            return by_aria_alt
        
        # Fallback: input within Cluster form group
        return self.locators.input_by_label_text(self.page, "Cluster")

    def verify_cluster_id_input_visible(self) -> None:
        """Verify wizard step 2 has cluster ID input."""
        expect(self._get_cluster_id_input().first).to_be_visible(timeout=self.SHORT_TIMEOUT)

    def fill_cluster_id(self, cluster_id: str) -> None:
        """Fill the cluster ID field."""
        cluster_input = self._get_cluster_id_input()
        expect(cluster_input.first).to_be_visible(timeout=self.SHORT_TIMEOUT)
        cluster_input.first.fill(cluster_id)

    def click_next(self) -> None:
        """Click the Next button in the wizard."""
        next_button = self.locators.button_with_text(self.page, "Next")
        expect(next_button.first).to_be_enabled(timeout=self.SHORT_TIMEOUT)
        next_button.first.click()
        self.page.wait_for_load_state("domcontentloaded")

    def verify_submit_button_visible(self) -> None:
        """Verify the Submit button is visible on review step."""
        submit_button = self.locators.button_with_text(self.page, "Submit")
        expect(submit_button.first).to_be_visible(timeout=self.SHORT_TIMEOUT)

    def click_submit(self) -> None:
        """Click Submit and wait for wizard to close.
        
        Waits for wizard to close after submission. If wizard stays open,
        it indicates a submission failure which should cause test failure.
        """
        submit_button = self.locators.button_with_text(self.page, "Submit")
        expect(submit_button.first).to_be_visible(timeout=self.SHORT_TIMEOUT)
        submit_button.first.click()
        
        # Wait for wizard to close - this is the success indicator
        wizard = self.locators.wizard(self.page).or_(self.locators.modal(self.page))
        wizard.first.wait_for(state="hidden", timeout=self.MEDIUM_TIMEOUT)

    def click_cancel(self) -> None:
        """Click Cancel to close the wizard without saving."""
        cancel_button = self.page.locator(
            "button:has-text('Cancel'), "
            "button[aria-label='Close']"
        )
        cancel_button.first.click()
        
        # Wait for wizard to close
        modal = self.locators.modal(self.page)
        modal.first.wait_for(state="hidden", timeout=self.SHORT_TIMEOUT)

    def verify_wizard_closed(self) -> None:
        """Verify the wizard is closed."""
        modal = self.locators.modal(self.page)
        expect(modal).to_have_count(0, timeout=self.SHORT_TIMEOUT)

    # =========================================================================
    # High-Level Wizard Flows
    # =========================================================================

    def create_integration_via_wizard(self, name: str, cluster_id: str) -> None:
        """Complete the full wizard flow to create an integration.
        
        Args:
            name: Integration name
            cluster_id: Cluster identifier
        """
        self.fill_name(name)
        self.click_next()
        self.fill_cluster_id(cluster_id)
        self.click_next()
        self.click_submit()

    # =========================================================================
    # Table Operations
    # =========================================================================

    def _get_source_row(self, source_name: str) -> Locator:
        """Get the table row for a specific source."""
        return self.locators.table_row_containing(self.page, source_name)

    def verify_source_in_table(self, source_name: str) -> None:
        """Verify a source appears in the integrations table."""
        row = self._get_source_row(source_name)
        expect(
            row.first,
            f"Source '{source_name}' should appear in integrations table"
        ).to_be_visible(timeout=self.DEFAULT_TIMEOUT)
        
        # Scroll to and highlight for video visibility
        row.first.scroll_into_view_if_needed()
        row.first.highlight()

    def verify_source_type(self, source_name: str, expected_type: str = "OpenShift") -> None:
        """Verify the source type is displayed in the source's row."""
        row = self._get_source_row(source_name)
        expect(row.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)
        
        # Check for type indicator in the same row
        type_indicator = row.locator(
            f":text('{expected_type}'), "
            ":text('OCP'), "
            ":text('OpenShift Container Platform')"
        )
        expect(
            type_indicator.first,
            f"Source '{source_name}' should show type '{expected_type}'"
        ).to_be_visible()
        type_indicator.first.highlight()

    def verify_source_status(self, source_name: str, expected_status: str = "Available") -> None:
        """Verify the source status is displayed in the source's row."""
        row = self._get_source_row(source_name)
        expect(row.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)
        
        status_indicator = row.locator(f":text('{expected_status}')")
        expect(
            status_indicator.first,
            f"Source '{source_name}' should have status '{expected_status}'"
        ).to_be_visible()
        status_indicator.first.highlight()

    def verify_source_not_in_table(self, source_name: str) -> None:
        """Verify a source does NOT appear in the table."""
        row = self._get_source_row(source_name)
        expect(
            row,
            f"Source '{source_name}' should NOT appear in table after deletion"
        ).to_have_count(0, timeout=self.MEDIUM_TIMEOUT)

    # =========================================================================
    # Actions Menu Operations
    # =========================================================================

    def verify_actions_menu_exists(self, source_name: str) -> None:
        """Verify the actions (kebab) menu exists in the source's row."""
        row = self._get_source_row(source_name)
        expect(row.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)
        
        actions_button = self.locators.kebab_toggle_in_row(row)
        expect(actions_button.first).to_be_visible()

    def open_actions_menu(self, source_name: str) -> None:
        """Open the actions (kebab) menu for a specific source."""
        row = self._get_source_row(source_name)
        expect(row.first).to_be_visible(timeout=self.MEDIUM_TIMEOUT)
        
        actions_button = self.locators.kebab_toggle_in_row(row)
        actions_button.first.click()
        
        # Wait for menu to appear
        menu = self.locators.dropdown_menu(self.page)
        menu.first.wait_for(state="visible", timeout=3000)

    def verify_remove_option_visible(self) -> None:
        """Verify Remove option exists in the open actions menu."""
        remove_option = self.locators.menu_item_with_text(self.page, "Remove")
        if remove_option.count() == 0:
            # Fallback for different menu structures
            remove_option = self.page.locator(".pf-v6-c-menu__item:has-text('Remove')")
        expect(remove_option.first).to_be_visible(timeout=self.SHORT_TIMEOUT)

    def click_remove_option(self) -> None:
        """Click the Remove option in the actions menu."""
        remove_option = self.locators.menu_item_with_text(self.page, "Remove")
        if remove_option.count() == 0:
            remove_option = self.page.locator(".pf-v6-c-menu__item:has-text('Remove')")
        remove_option.first.click()
        
        # Wait for confirmation modal to appear
        modal = self.locators.modal(self.page)
        modal.first.wait_for(state="visible", timeout=self.SHORT_TIMEOUT)

    # =========================================================================
    # Remove Confirmation Modal
    # =========================================================================

    def verify_remove_confirmation_modal(self) -> None:
        """Verify the remove confirmation modal with checkbox."""
        modal = self.locators.modal(self.page)
        expect(modal.first).to_be_visible(timeout=self.SHORT_TIMEOUT)
        
        # Verify checkbox exists
        checkbox = modal.locator("input[type='checkbox']")
        expect(checkbox.first).to_be_visible()
        
        # Verify remove button is visible
        remove_button = modal.locator(
            "button:has-text('Remove integration'), "
            "button.pf-m-danger"
        )
        expect(remove_button.first).to_be_visible()

    def complete_remove_confirmation(self) -> None:
        """Complete the remove confirmation by checking checkbox and clicking remove."""
        modal = self.locators.modal(self.page)
        
        # Check the acknowledgement checkbox
        checkbox = modal.locator("input[type='checkbox']")
        checkbox.first.check()
        
        # Click the remove button
        remove_button = modal.locator(
            "button:has-text('Remove integration'), "
            "button.pf-m-danger"
        )
        expect(remove_button.first).to_be_enabled(timeout=self.SHORT_TIMEOUT)
        remove_button.first.click()
        
        # Wait for modal to close
        self.page.wait_for_load_state("networkidle")
        modal.first.wait_for(state="hidden", timeout=self.MEDIUM_TIMEOUT)

    # =========================================================================
    # High-Level Delete Flow
    # =========================================================================

    def delete_source_via_ui(self, source_name: str) -> None:
        """Delete a source using the actions menu.
        
        Args:
            source_name: Name of the source to delete
        """
        self.open_actions_menu(source_name)
        self.click_remove_option()
        self.complete_remove_confirmation()
