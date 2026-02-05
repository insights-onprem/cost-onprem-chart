"""
Playwright fixtures for UI tests.

This module provides Playwright-specific fixtures that integrate with
the existing pytest infrastructure (cluster_config, keycloak_config, etc.).
"""

import os
from typing import Generator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from conftest import ClusterConfig, KeycloakConfig
from utils import get_route_url


# =============================================================================
# Playwright Browser Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def playwright_instance() -> Generator[Playwright, None, None]:
    """Create a Playwright instance for the test session."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Generator[Browser, None, None]:
    """Launch a browser for the test session.
    
    Set PLAYWRIGHT_BROWSER env var to change:
    - chromium (default)
    - firefox
    - webkit
    """
    browser_type = os.environ.get("PLAYWRIGHT_BROWSER", "chromium")
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    
    browser_launcher = getattr(playwright_instance, browser_type)
    browser = browser_launcher.launch(
        headless=headless,
        slow_mo=int(os.environ.get("PLAYWRIGHT_SLOW_MO", "0")),
    )
    yield browser
    browser.close()


@pytest.fixture(scope="function")
def browser_context(browser: Browser) -> Generator[BrowserContext, None, None]:
    """Create a fresh browser context for each test.
    
    Each test gets an isolated context with:
    - Fresh cookies/storage
    - Configured viewport
    - SSL certificate handling for self-signed certs
    """
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,  # Handle self-signed certs in test environments
    )
    yield context
    context.close()


@pytest.fixture(scope="function")
def page(browser_context: BrowserContext) -> Generator[Page, None, None]:
    """Create a new page for each test."""
    page = browser_context.new_page()
    yield page
    page.close()


# =============================================================================
# Application URL Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def ui_url(cluster_config: ClusterConfig) -> str:
    """Get the Cost Management UI URL."""
    route_name = f"{cluster_config.helm_release_name}-ui"
    url = get_route_url(cluster_config.namespace, route_name)
    if not url:
        pytest.skip(f"UI route '{route_name}' not found in namespace {cluster_config.namespace}")
    return url


@pytest.fixture(scope="session")
def keycloak_login_url(keycloak_config: KeycloakConfig) -> str:
    """Get the Keycloak login URL for the kubernetes realm."""
    return f"{keycloak_config.url}/realms/{keycloak_config.realm}/protocol/openid-connect/auth"


# =============================================================================
# Authentication Fixtures
# =============================================================================


@pytest.fixture(scope="function")
def authenticated_context(
    browser: Browser,
    ui_url: str,
    keycloak_config: KeycloakConfig,
) -> Generator[BrowserContext, None, None]:
    """Create a browser context with authenticated session.
    
    Performs Keycloak login and stores the session for the test.
    Uses test/test credentials by default (configurable via env vars).
    """
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,
    )
    
    page = context.new_page()
    
    # Navigate to UI (will redirect to Keycloak)
    page.goto(ui_url)
    
    # Wait for Keycloak login page
    page.wait_for_url(f"**/{keycloak_config.realm}/**", timeout=10000)
    
    # Fill login form
    username = os.environ.get("TEST_UI_USERNAME", "test")
    password = os.environ.get("TEST_UI_PASSWORD", "test")
    
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('input[type="submit"], button[type="submit"]')
    
    # Wait for redirect back to UI
    page.wait_for_url(f"{ui_url}**", timeout=15000)
    
    page.close()
    
    yield context
    context.close()


@pytest.fixture(scope="function")
def authenticated_page(authenticated_context: BrowserContext) -> Generator[Page, None, None]:
    """Create a page with authenticated session."""
    page = authenticated_context.new_page()
    yield page
    page.close()


# =============================================================================
# Screenshot/Trace Fixtures
# =============================================================================


@pytest.fixture(scope="function", autouse=True)
def capture_on_failure(request, page: Page):
    """Capture screenshot and trace on test failure.
    
    Automatically captures debugging artifacts when a test fails.
    Artifacts are saved to tests/reports/screenshots/
    """
    yield
    
    if request.node.rep_call.failed if hasattr(request.node, "rep_call") else False:
        # Create screenshots directory
        screenshots_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "reports",
            "screenshots"
        )
        os.makedirs(screenshots_dir, exist_ok=True)
        
        # Generate filename from test name
        test_name = request.node.name.replace("/", "_").replace("::", "_")
        
        # Capture screenshot
        screenshot_path = os.path.join(screenshots_dir, f"{test_name}.png")
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            print(f"\n📸 Screenshot saved: {screenshot_path}")
        except Exception as e:
            print(f"\n⚠️ Failed to capture screenshot: {e}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result for use in fixtures."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
