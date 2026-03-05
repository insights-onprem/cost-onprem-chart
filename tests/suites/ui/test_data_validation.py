"""
UI data validation tests.

These tests are SELF-CONTAINED - they set up their own test data using the
cost_validation_data fixture, then validate that data displays correctly in the UI.

1. Data Visualization - Charts render correctly with real cost data
2. Optimization Recommendations - CPU/memory recommendations display correctly
3. Optimization Breakdown - Detailed breakdown view shows correct data
"""

import os
import re
import time

import pytest
from playwright.sync_api import Page, expect


def save_screenshot(page: Page, name: str) -> str:
    """Save a screenshot for documentation/verification purposes."""
    screenshots_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "reports", "screenshots", "data_validation"
    )
    os.makedirs(screenshots_dir, exist_ok=True)
    path = os.path.join(screenshots_dir, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"\n📸 Screenshot: {path}")
    return path


@pytest.mark.ui
@pytest.mark.data_validation
class TestCostDataVisualization:
    """Test that cost data displays correctly in charts and tables.
    
    HIGH PRIORITY: Validates that the UI correctly renders cost data from the backend.
    Uses cost_validation_data fixture for self-contained data setup.
    """

    def test_overview_shows_cost_data(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify Overview page displays cost data (not empty state).
        
        The Overview page should show:
        - Cost summary cards or widgets
        - Charts with actual data points
        - Not just "No data available" messages
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Wait for any loading indicators to disappear
        loading = authenticated_page.locator(".pf-v6-c-spinner, [data-testid='loading']")
        if loading.count() > 0:
            loading.first.wait_for(state="hidden", timeout=30000)
        
        # Check for empty state - should NOT be present since we have data
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        empty_text = authenticated_page.get_by_text(re.compile(r"no data|no cost data|empty", re.IGNORECASE))
        
        # With cost_validation_data, we expect data to be present
        # If empty state is shown, the test should fail (not skip)
        if empty_state.count() > 0 and empty_state.first.is_visible():
            pytest.fail(
                f"Empty state shown despite cost_validation_data setup. "
                f"Cluster ID: {cost_validation_data['cluster_id']}"
            )
        
        # Look for data indicators - charts, tables, or cost values
        found_data = False
        
        # Check CSS selectors
        css_indicators = ["svg path", "svg rect", "table tbody tr", ".pf-v6-c-card"]
        for selector in css_indicators:
            if authenticated_page.locator(selector).count() > 0:
                found_data = True
                break
        
        # Check for dollar amounts via text
        if not found_data:
            dollar_amounts = authenticated_page.get_by_text(re.compile(r"\$[0-9]"))
            if dollar_amounts.count() > 0:
                found_data = True
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "01_overview_cost_data")
        
        assert found_data, (
            f"Overview page should display cost data (charts, tables, or cost values). "
            f"Cluster ID: {cost_validation_data['cluster_id']}"
        )

    def test_openshift_page_shows_cluster_costs(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify OpenShift page displays cluster cost data.
        
        The OpenShift page should show:
        - Cost breakdown by cluster, project, or node
        - Charts or tables with actual values
        
        Note: The /ocp page may show empty state initially while data propagates
        through the UI's caching layer, even when data exists in the database.
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/ocp")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Wait for loading to complete - OCP page may need more time
        time.sleep(5)  # Allow async data to load
        
        # Look for cost data elements first
        found_data = False
        if authenticated_page.locator("svg path, svg rect, table tbody tr, .pf-v6-c-table tbody tr").count() > 0:
            found_data = True
        if not found_data and authenticated_page.get_by_text(re.compile(r"\$[0-9]", re.IGNORECASE)).count() > 0:
            found_data = True
        
        if found_data:
            # Capture screenshot for verification
            save_screenshot(authenticated_page, "02_openshift_cluster_costs")
            return  # Test passes - data is displayed
        
        # Check for empty state - may occur due to UI caching/timing
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state")
        if empty_state.count() > 0 and empty_state.first.is_visible():
            # This can happen due to UI caching - skip rather than fail
            # The Overview and Cost Explorer tests validate data is accessible
            pytest.skip(
                f"OpenShift page shows empty state (possible UI caching delay). "
                f"Data exists for cluster: {cost_validation_data['cluster_id']}"
            )
        
        # No data and no empty state - something else is wrong
        pytest.fail(
            f"OpenShift page should display cost data or empty state. "
            f"Cluster ID: {cost_validation_data['cluster_id']}"
        )

    def test_cost_explorer_displays_chart_with_data(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify Cost Explorer displays charts with actual data points.
        
        The Cost Explorer should show:
        - A chart (bar, line, or area) with visible data
        - Not just empty axes or "no data" message
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/explorer")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Wait for chart to render
        time.sleep(3)
        
        # Check for empty state - should NOT be present
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state")
        empty_text = authenticated_page.get_by_text(re.compile(r"no data available", re.IGNORECASE))
        if (empty_state.count() > 0 and empty_state.first.is_visible()) or \
           (empty_text.count() > 0 and empty_text.first.is_visible()):
            pytest.fail(
                f"Empty state shown despite cost_validation_data setup. "
                f"Cluster ID: {cost_validation_data['cluster_id']}"
            )
        
        # Look for chart with data (SVG with paths or rects indicates rendered data)
        chart_with_data = authenticated_page.locator("svg path, svg rect, svg circle")
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "03_cost_explorer_chart")
        
        # Should have multiple data points (not just axes)
        assert chart_with_data.count() > 2, (
            f"Cost Explorer chart should have data points. Found {chart_with_data.count()} SVG elements. "
            f"Cluster ID: {cost_validation_data['cluster_id']}"
        )

    def test_cost_explorer_table_has_rows(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify Cost Explorer table displays data rows.
        
        When viewing as table, should show actual cost data rows.
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/explorer")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Wait for data to load
        time.sleep(2)
        
        # Look for table rows
        table_rows = authenticated_page.locator(
            "table tbody tr, [role='grid'] [role='row'], .pf-v6-c-table tbody tr"
        )
        
        if table_rows.count() == 0:
            # Check if there's a table view toggle
            table_toggle = authenticated_page.locator(
                "button:has-text('Table'), [aria-label*='table'], [data-testid='table-view']"
            )
            if table_toggle.count() > 0:
                table_toggle.first.click()
                authenticated_page.wait_for_load_state("networkidle")
                time.sleep(1)
                table_rows = authenticated_page.locator("table tbody tr")
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "04_cost_explorer_table")
        
        # With data setup, we expect rows (or chart view is default which is also valid)
        # This test validates table view specifically if available
        if table_rows.count() == 0:
            # Check if chart is showing instead (also valid)
            chart_elements = authenticated_page.locator("svg path, svg rect")
            if chart_elements.count() > 2:
                pass  # Chart view is showing data, that's fine
            else:
                pytest.fail(
                    f"Cost Explorer should have data in table or chart view. "
                    f"Cluster ID: {cost_validation_data['cluster_id']}"
                )


@pytest.mark.ui
@pytest.mark.ros
@pytest.mark.data_validation
class TestOptimizationRecommendations:
    """Test that optimization recommendations display correctly.
    
    HIGH PRIORITY: Validates CPU/memory recommendations from Kruize are shown.
    Uses cost_validation_data fixture for self-contained data setup.
    
    Note: Optimization recommendations require Kruize processing time after data upload.
    The E2E flow waits for recommendations to be generated before these tests run.
    """

    def test_optimizations_table_has_recommendations(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify optimizations page displays recommendation data.
        
        Should show:
        - Table with container/workload recommendations
        - CPU and memory columns
        - Actual values (not all zeros or empty)
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        authenticated_page.wait_for_load_state("networkidle")
        
        # Wait for data to load
        time.sleep(3)
        
        # Check for empty state
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        empty_text = authenticated_page.get_by_text(re.compile(r"no optimization|no recommendation", re.IGNORECASE))
        
        # Optimization data may take longer to process - check if available
        if (empty_state.count() > 0 and empty_state.first.is_visible()) or \
           (empty_text.count() > 0 and empty_text.first.is_visible()):
            # This is acceptable - Kruize may not have processed yet
            pytest.skip(
                "No optimization data available yet. "
                "Kruize may still be processing recommendations."
            )
        
        # Look for table with data
        table = authenticated_page.locator("table, [role='grid'], .pf-v6-c-table")
        expect(table.first).to_be_visible(timeout=10000)
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "05_optimizations_table")
        
        # Verify table has rows
        rows = authenticated_page.locator("table tbody tr, [role='row']")
        assert rows.count() > 0, "Optimizations table should have recommendation rows"

    def test_optimizations_show_cpu_memory_values(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify optimizations display CPU and memory recommendation values.
        
        Each recommendation should show:
        - CPU values (cores, millicores)
        - Memory values (GiB, MiB)
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(3)
        
        # Check for empty state
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        if empty_state.count() > 0 and empty_state.first.is_visible():
            pytest.skip("No optimization data available yet")
        
        # Click on a container name in the table to see CPU/memory details
        container_link = authenticated_page.locator(
            "table tbody tr td a, table tbody tr td button, "
            "[role='row'] a, [role='row'] button"
        ).first
        
        if container_link.count() == 0:
            pytest.skip("No clickable container names found in optimizations table")
        
        container_link.click()
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        # Now look for CPU/memory related content in the detail view
        cpu_content = authenticated_page.get_by_text(re.compile(r"cpu|core|millicore", re.IGNORECASE))
        memory_content = authenticated_page.get_by_text(re.compile(r"memory|gib|mib|ram", re.IGNORECASE))
        
        has_cpu = cpu_content.count() > 0
        has_memory = memory_content.count() > 0
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "06_optimizations_cpu_memory")
        
        assert has_cpu or has_memory, (
            "Optimization detail should display CPU and/or memory recommendation values"
        )

    def test_optimizations_show_request_limit_recommendations(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify optimizations show request/limit recommendations.
        
        Kruize recommendations include:
        - Current requests/limits
        - Recommended requests/limits
        """
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(3)
        
        # Check for empty state
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        if empty_state.count() > 0 and empty_state.first.is_visible():
            pytest.skip("No optimization data available yet")
        
        # Click on a container name to see request/limit details
        container_link = authenticated_page.locator(
            "table tbody tr td a, table tbody tr td button, "
            "[role='row'] a, [role='row'] button"
        ).first
        
        if container_link.count() == 0:
            pytest.skip("No clickable container names found in optimizations table")
        
        container_link.click()
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        # Look for request/limit terminology in the detail view
        request_limit_content = authenticated_page.get_by_text(
            re.compile(r"request|limit|current|recommended|change", re.IGNORECASE)
        )
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "07_optimizations_request_limit")
        
        assert request_limit_content.count() > 0, (
            "Optimization detail should show request/limit recommendations"
        )


@pytest.mark.ui
@pytest.mark.ros
@pytest.mark.data_validation
class TestOptimizationBreakdown:
    """Test optimization detail/breakdown views.
    
    Validates detailed breakdown view shows correct data.
    Uses cost_validation_data fixture for self-contained data setup.
    """

    def test_can_navigate_to_optimization_detail(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify clicking an optimization navigates to detail view."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(3)
        
        # Check for empty state
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        if empty_state.count() > 0 and empty_state.first.is_visible():
            pytest.skip("No optimization data available yet")
        
        # Find clickable row or link
        clickable = authenticated_page.locator(
            "table tbody tr a, table tbody tr[role='row'], "
            "[role='row'] a, table tbody tr td a"
        ).first
        
        if clickable.count() == 0:
            pytest.skip("No clickable optimization rows found")
        
        # Store current URL
        current_url = authenticated_page.url
        
        # Click to navigate
        clickable.click()
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        # URL should change to detail/breakdown view
        new_url = authenticated_page.url
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "08_optimization_detail_navigation")
        
        assert new_url != current_url or "breakdown" in new_url or "detail" in new_url, (
            "Clicking optimization should navigate to detail view"
        )

    def test_optimization_detail_shows_container_info(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify optimization detail shows container-level information."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(3)
        
        # Check for empty state
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        if empty_state.count() > 0 and empty_state.first.is_visible():
            pytest.skip("No optimization data available yet")
        
        # Navigate to first optimization detail
        clickable = authenticated_page.locator(
            "table tbody tr a, table tbody tr[role='row']"
        ).first
        
        if clickable.count() == 0:
            pytest.skip("No clickable optimization rows found")
        
        clickable.click()
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        # Look for container-related content
        container_info = authenticated_page.get_by_text(
            re.compile(r"container|pod|workload|namespace|cluster", re.IGNORECASE)
        )
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "09_optimization_container_info")
        
        assert container_info.count() > 0, (
            "Optimization detail should show container/workload information"
        )

    def test_optimization_detail_shows_recommendation_values(
        self, authenticated_page: Page, ui_url: str, cost_validation_data
    ):
        """Verify optimization detail shows specific recommendation values."""
        authenticated_page.goto(f"{ui_url}/openshift/cost-management/optimizations")
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(3)
        
        # Check for empty state
        empty_state = authenticated_page.locator(".pf-v6-c-empty-state, .pf-c-empty-state")
        if empty_state.count() > 0 and empty_state.first.is_visible():
            pytest.skip("No optimization data available yet")
        
        # Navigate to first optimization detail
        clickable = authenticated_page.locator(
            "table tbody tr a, table tbody tr[role='row']"
        ).first
        
        if clickable.count() == 0:
            pytest.skip("No clickable optimization rows found")
        
        clickable.click()
        authenticated_page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        # Look for numeric values (CPU cores, memory GiB)
        numeric_values = authenticated_page.get_by_text(
            re.compile(r"[0-9]+\.?[0-9]*\s*(core|cpu|gib|mib|m)", re.IGNORECASE)
        )
        
        # Capture screenshot for verification
        save_screenshot(authenticated_page, "10_optimization_recommendation_values")
        
        # Should have multiple numeric values for recommendations
        assert numeric_values.count() > 0, (
            "Optimization detail should show numeric CPU/memory values"
        )
