"""
Common locator helpers for PatternFly-based UI components.

These utilities provide semantic locator factories that work across
different PatternFly versions (PF5/PF6) and follow accessibility best practices.
"""

from playwright.sync_api import Locator, Page


class CommonLocators:
    """Factory methods for common PatternFly component locators."""

    @staticmethod
    def button_with_text(page: Page, text: str) -> Locator:
        """Get button by visible text using role selector."""
        return page.get_by_role("button", name=text)

    @staticmethod
    def link_with_text(page: Page, text: str) -> Locator:
        """Get link by visible text using role selector."""
        return page.get_by_role("link", name=text)

    @staticmethod
    def tab_with_text(page: Page, text: str) -> Locator:
        """Get tab button by visible text using role selector."""
        return page.get_by_role("tab", name=text)

    @staticmethod
    def input_by_name(page: Page, name: str) -> Locator:
        """Get input element by name attribute."""
        return page.locator(f"input[name='{name}']")

    @staticmethod
    def input_by_aria_label(page: Page, label: str) -> Locator:
        """Get input element by aria-label attribute."""
        return page.locator(f"input[aria-label='{label}']")

    @staticmethod
    def input_by_label_text(page: Page, label_text: str) -> Locator:
        """Get input associated with a label containing specific text."""
        return page.locator(f".pf-v6-c-form__group:has-text('{label_text}') input")

    @staticmethod
    def ouia_component(page: Page, component_id: str) -> Locator:
        """Get element by OUIA component ID (preferred for PatternFly)."""
        return page.locator(f"[data-ouia-component-id='{component_id}']")

    @staticmethod
    def table_row_containing(page: Page, text: str) -> Locator:
        """Get table row containing specific text."""
        return page.locator(f"tr:has-text('{text}')")

    @staticmethod
    def menu_item_with_text(page: Page, text: str) -> Locator:
        """Get menu item by visible text using role selector."""
        return page.get_by_role("menuitem", name=text)

    @staticmethod
    def modal(page: Page) -> Locator:
        """Get the currently visible modal/dialog."""
        return page.locator(".pf-v6-c-modal-box, .pf-c-modal-box, [role='dialog']")

    @staticmethod
    def wizard(page: Page) -> Locator:
        """Get the currently visible wizard component."""
        return page.locator(".pf-v6-c-wizard, .pf-c-wizard")

    @staticmethod
    def tabs_container(page: Page) -> Locator:
        """Get PatternFly tabs container (PF5 or PF6)."""
        return page.locator(".pf-v6-c-tabs, .pf-c-tabs")

    @staticmethod
    def dropdown_menu(page: Page) -> Locator:
        """Get the currently visible dropdown/kebab menu."""
        return page.locator(".pf-v6-c-menu, .pf-v6-c-dropdown__menu, [role='menu']")

    @staticmethod
    def kebab_toggle_in_row(row: Locator) -> Locator:
        """Get the kebab/actions toggle button within a table row."""
        return row.locator(
            ".pf-v6-c-menu-toggle, "
            ".pf-c-dropdown__toggle, "
            "button[aria-label='Actions']"
        )

    @staticmethod
    def checkbox(page: Page) -> Locator:
        """Get checkbox input element."""
        return page.locator("input[type='checkbox']")

    @staticmethod
    def spinner(page: Page) -> Locator:
        """Get loading spinner element."""
        return page.locator(".pf-v6-c-spinner, .pf-c-spinner")
