"""
Playwright fixtures for UI tests.

This module provides Playwright-specific fixtures that integrate with
the existing pytest infrastructure (cluster_config, keycloak_config, etc.).

Rich Reporting Configuration:
    Environment variables control what artifacts are captured:
    
    PLAYWRIGHT_VIDEO: "off", "on", "retain-on-failure" (default)
        Video recording - only failures are kept by default (videos are large)
    
    PLAYWRIGHT_TRACE: "off", "on" (default), "retain-on-failure"
        Rich trace with DOM snapshots, network requests, action log
        (produces .zip files viewable at trace.playwright.dev)
        Always captured by default - traces are small (~3-5MB) and very useful
    
    PLAYWRIGHT_SCREENSHOT: "off", "on" (default), "only-on-failure"
        Screenshot capture - always captured by default (~50-100KB)
    
    Artifacts are saved to:
    - tests/reports/videos/      - Video recordings (.webm)
    - tests/reports/screenshots/ - Screenshots (.png)
    - tests/reports/traces/      - Trace files (.zip)
    
    For CI, set ARTIFACT_DIR to copy reports to the artifact collection location.
    Orphaned video files (from passing tests) are cleaned up before copying.
"""

import os
import shutil
from typing import Generator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from conftest import ClusterConfig, KeycloakConfig
from utils import get_route_url


# =============================================================================
# Reporting Configuration
# =============================================================================

# Video recording mode: "off", "on", "retain-on-failure"
# Default to "retain-on-failure" - videos are large, only keep for failures
VIDEO_MODE = os.environ.get("PLAYWRIGHT_VIDEO", "retain-on-failure")

# Trace recording mode: "off", "on", "retain-on-failure"
# Traces provide the richest debugging: DOM snapshots, network, action log
# Default to "on" - traces are small (~3-5MB) and very useful for debugging
TRACE_MODE = os.environ.get("PLAYWRIGHT_TRACE", "on")

# Screenshot mode: "off", "on", "only-on-failure"
# Default to "on" - screenshots are small (~50-100KB) and useful for all tests
SCREENSHOT_MODE = os.environ.get("PLAYWRIGHT_SCREENSHOT", "on")


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


def _get_reports_base_dir() -> str:
    """Get the base reports directory."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "reports"
    )


def _get_videos_dir() -> str:
    """Get the directory for storing video recordings."""
    return os.path.join(_get_reports_base_dir(), "videos")


def _get_traces_dir() -> str:
    """Get the directory for storing trace files."""
    return os.path.join(_get_reports_base_dir(), "traces")


def _get_screenshots_dir() -> str:
    """Get the directory for storing screenshots."""
    return os.path.join(_get_reports_base_dir(), "screenshots")


@pytest.fixture(scope="function")
def authenticated_context(
    browser: Browser,
    ui_url: str,
    keycloak_config: KeycloakConfig,
    request,
) -> Generator[BrowserContext, None, None]:
    """Create a browser context with authenticated session.
    
    Performs Keycloak login and stores the session for the test.
    Uses test/test credentials by default (configurable via env vars).
    
    Artifact recording is controlled by environment variables:
    - PLAYWRIGHT_VIDEO: "off" (default), "on", "retain-on-failure"
    - PLAYWRIGHT_TRACE: "off" (default), "on", "retain-on-failure"
    - PLAYWRIGHT_SCREENSHOT: "off", "on", "only-on-failure" (default)
    """
    # Ensure output directories exist
    videos_dir = _get_videos_dir()
    traces_dir = _get_traces_dir()
    screenshots_dir = _get_screenshots_dir()
    for d in [videos_dir, traces_dir, screenshots_dir]:
        os.makedirs(d, exist_ok=True)
    
    # Configure video recording
    context_options = {
        "viewport": {"width": 1920, "height": 1080},
        "ignore_https_errors": True,
    }
    
    if VIDEO_MODE != "off":
        context_options["record_video_dir"] = videos_dir
        context_options["record_video_size"] = {"width": 1280, "height": 720}
    
    context = browser.new_context(**context_options)
    
    # Start tracing if enabled
    if TRACE_MODE != "off":
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    
    page = context.new_page()
    
    # Navigate to UI (will redirect to Keycloak)
    page.goto(ui_url)
    
    # Wait for Keycloak login page
    page.wait_for_url(f"**/{keycloak_config.realm}/**", timeout=10000)
    
    # Fill login form
    username = os.environ.get("TEST_UI_USERNAME", "admin")
    password = os.environ.get("TEST_UI_PASSWORD", "admin")
    
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('input[type="submit"], button[type="submit"]')
    
    # Wait for redirect back to UI - use wildcard pattern that matches any path
    # After login, UI may redirect to /openshift/cost-management or other paths
    from urllib.parse import urlparse
    parsed = urlparse(ui_url)
    ui_host_pattern = f"**{parsed.netloc}**"
    page.wait_for_url(ui_host_pattern, timeout=15000)
    
    # Wait for page to fully load
    page.wait_for_load_state("networkidle")
    
    page.close()
    
    yield context
    
    # Note: Context cleanup is handled in authenticated_page fixture
    # which has access to test results for retain-on-failure logic.
    # Do NOT close context here - it will be closed after trace/video handling.


# =============================================================================
# Screenshot/Trace Fixtures
# =============================================================================


@pytest.fixture(scope="function")
def authenticated_page(authenticated_context: BrowserContext, request) -> Generator[Page, None, None]:
    """Create a page with authenticated session.
    
    Automatically captures artifacts based on configuration:
    - Screenshots: PLAYWRIGHT_SCREENSHOT (default: only-on-failure)
    - Videos: PLAYWRIGHT_VIDEO (default: off)
    - Traces: PLAYWRIGHT_TRACE (default: off)
    
    Traces provide the richest debugging experience with:
    - Step-by-step action log
    - DOM snapshots at each step
    - Network requests
    - Console logs
    
    View traces at: https://trace.playwright.dev
    """
    page = authenticated_context.new_page()
    yield page
    
    test_name = request.node.name.replace("/", "_").replace("::", "_").replace("[", "_").replace("]", "_")
    test_failed = hasattr(request.node, "rep_call") and request.node.rep_call.failed
    
    # Determine if we should save artifacts
    should_save_screenshot = (
        SCREENSHOT_MODE == "on" or 
        (SCREENSHOT_MODE == "only-on-failure" and test_failed)
    )
    should_save_video = (
        VIDEO_MODE == "on" or 
        (VIDEO_MODE == "retain-on-failure" and test_failed)
    )
    should_save_trace = (
        TRACE_MODE == "on" or 
        (TRACE_MODE == "retain-on-failure" and test_failed)
    )
    
    # Capture screenshot
    if should_save_screenshot:
        screenshots_dir = _get_screenshots_dir()
        os.makedirs(screenshots_dir, exist_ok=True)
        screenshot_path = os.path.join(screenshots_dir, f"{test_name}.png")
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            status = "FAILED" if test_failed else "passed"
            print(f"\n📸 Screenshot saved ({status}): {screenshot_path}")
        except Exception as e:
            print(f"\n⚠️ Failed to capture screenshot: {e}")
    
    # Get video path before closing page (must be done before context closes)
    video_path = None
    if VIDEO_MODE != "off" and page.video:
        try:
            video_path = page.video.path()
        except Exception:
            pass
    
    page.close()
    
    # Save trace if enabled
    if TRACE_MODE != "off":
        traces_dir = _get_traces_dir()
        trace_path = os.path.join(traces_dir, f"{test_name}.zip")
        try:
            if should_save_trace:
                authenticated_context.tracing.stop(path=trace_path)
                status = "FAILED" if test_failed else "passed"
                print(f"\n🔍 Trace saved ({status}): {trace_path}")
                print(f"   View at: https://trace.playwright.dev (upload {test_name}.zip)")
            else:
                # Stop tracing without saving
                authenticated_context.tracing.stop()
        except Exception as e:
            print(f"\n⚠️ Failed to save trace: {e}")
    
    # Handle video retention
    if video_path and os.path.exists(video_path):
        videos_dir = _get_videos_dir()
        if should_save_video:
            new_video_path = os.path.join(videos_dir, f"{test_name}.webm")
            try:
                shutil.move(video_path, new_video_path)
                status = "FAILED" if test_failed else "passed"
                print(f"\n🎬 Video saved ({status}): {new_video_path}")
            except Exception as e:
                print(f"\n⚠️ Failed to rename video: {e}")
        else:
            # Delete video for passing tests in retain-on-failure mode
            try:
                os.remove(video_path)
            except Exception:
                pass
    
    # Close context (must happen after trace/video handling)
    authenticated_context.close()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result for use in fixtures."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Embed artifacts in HTML report after test teardown (when artifacts exist)."""
    yield
    
    # Only process UI tests
    if "ui" not in [m.name for m in item.iter_markers()]:
        return
    
    # Get the call report to add extras to
    rep = getattr(item, "rep_call", None)
    if rep is None:
        return
    
    test_name = item.name.replace("/", "_").replace("::", "_").replace("[", "_").replace("]", "_")
    extras = getattr(rep, "extras", [])
    
    try:
        from pytest_html import extras as html_extras
        import base64
        
        test_failed = rep.failed
        
        # Add screenshot if exists (thumbnail that expands on click)
        screenshot_path = os.path.join(_get_screenshots_dir(), f"{test_name}.png")
        if os.path.exists(screenshot_path):
            with open(screenshot_path, "rb") as f:
                screenshot_data = base64.b64encode(f.read()).decode()
            extras.append(html_extras.html(
                f'<details><summary>📸 Screenshot (click to expand)</summary>'
                f'<div class="image" style="margin-top:8px;">'
                f'<img src="data:image/png;base64,{screenshot_data}" '
                f'alt="Screenshot" style="max-width:100%; border:1px solid #ccc;"/>'
                f'</div></details>'
            ))
        
        # Add video if exists (only for failed tests - passing tests shouldn't have videos)
        video_path = os.path.join(_get_videos_dir(), f"{test_name}.webm")
        if os.path.exists(video_path):
            with open(video_path, "rb") as f:
                video_data = base64.b64encode(f.read()).decode()
            extras.append(html_extras.html(
                f'<details><summary>🎬 Video Recording (click to expand)</summary>'
                f'<div class="video" style="margin-top:8px;">'
                f'<video controls style="max-width:100%; border:1px solid #ccc;">'
                f'<source src="data:video/webm;base64,{video_data}" type="video/webm">'
                f'Your browser does not support the video tag.'
                f'</video></div></details>'
            ))
        
        # Add trace link if exists
        trace_path = os.path.join(_get_traces_dir(), f"{test_name}.zip")
        if os.path.exists(trace_path):
            # Get file size for display
            trace_size = os.path.getsize(trace_path)
            size_str = f"{trace_size / 1024 / 1024:.1f} MB" if trace_size > 1024*1024 else f"{trace_size / 1024:.0f} KB"
            # Link to trace.playwright.dev with the trace file
            trace_viewer_url = f"https://trace.playwright.dev/?trace=traces/{test_name}.zip"
            extras.append(html_extras.html(
                f'<details open><summary>🔍 Trace ({size_str})</summary>'
                f'<div style="margin:8px 0; padding:8px; background:#f5f5f5; border-radius:4px; font-size:0.9em;">'
                f'<p style="margin:0 0 8px 0;"><strong>View trace:</strong></p>'
                f'<ul style="margin:0; padding-left:20px;">'
                f'<li><a href="https://trace.playwright.dev" target="_blank">trace.playwright.dev</a> '
                f'(upload <code>traces/{test_name}.zip</code>)</li>'
                f'<li>Local: <code>playwright show-trace reports/traces/{test_name}.zip</code></li>'
                f'</ul>'
                f'<p style="margin:8px 0 0 0;"><a href="traces/{test_name}.zip" download>📥 Download trace</a></p>'
                f'</div></details>'
            ))
        
        rep.extras = extras
    except ImportError:
        pass


def _cleanup_orphaned_videos(videos_dir: str) -> None:
    """Remove orphaned video files (those with hash names instead of test names).
    
    Playwright creates videos with hash names during recording. We rename them
    to test names when we want to keep them. Any remaining hash-named files
    are orphans from the authentication context or tests that passed.
    """
    if not os.path.exists(videos_dir):
        return
    
    for filename in os.listdir(videos_dir):
        if filename.endswith(".webm"):
            # Keep files that start with "test_" (properly named test videos)
            # Remove files that are just hash names (32 hex chars + .webm)
            name_without_ext = filename[:-5]  # Remove .webm
            if len(name_without_ext) == 32 and all(c in "0123456789abcdef" for c in name_without_ext):
                filepath = os.path.join(videos_dir, filename)
                try:
                    os.remove(filepath)
                except Exception:
                    pass


def pytest_sessionfinish(session, exitstatus):
    """Copy UI test artifacts to ARTIFACT_DIR if set (for CI).
    
    This enables CI systems to collect Playwright artifacts alongside
    other test outputs like JUnit XML.
    
    Before copying, cleans up orphaned video files that weren't renamed
    (i.e., videos from passing tests in retain-on-failure mode).
    """
    reports_dir = _get_reports_base_dir()
    
    # Clean up orphaned videos before copying (or just for local cleanup)
    videos_dir = os.path.join(reports_dir, "videos")
    _cleanup_orphaned_videos(videos_dir)
    
    artifact_dir = os.environ.get("ARTIFACT_DIR")
    if not artifact_dir:
        return
    
    if not os.path.exists(reports_dir):
        return
    
    # Create playwright subdirectory in artifact dir
    pw_artifact_dir = os.path.join(artifact_dir, "playwright")
    os.makedirs(pw_artifact_dir, exist_ok=True)
    
    # Copy HTML report if it exists
    html_report = os.path.join(reports_dir, "report.html")
    if os.path.exists(html_report):
        try:
            shutil.copy2(html_report, os.path.join(pw_artifact_dir, "report.html"))
            print(f"\n📄 Copied HTML report to {pw_artifact_dir}/report.html")
        except Exception as e:
            print(f"\n⚠️ Failed to copy HTML report: {e}")
    
    # Copy each artifact type if it has meaningful content
    artifact_types = [
        ("screenshots", "screenshots", ".png"),
        ("videos", "videos", ".webm"),
        ("traces", "traces", ".zip"),
    ]
    
    for src_name, dst_name, ext in artifact_types:
        src_dir = os.path.join(reports_dir, src_name)
        if os.path.exists(src_dir):
            # Only copy if there are actual artifact files (not just .DS_Store etc)
            artifact_files = [f for f in os.listdir(src_dir) if f.endswith(ext)]
            if artifact_files:
                dst_dir = os.path.join(pw_artifact_dir, dst_name)
                try:
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"\n📦 Copied {len(artifact_files)} {src_name} to {dst_dir}")
                except Exception as e:
                    print(f"\n⚠️ Failed to copy {src_name}: {e}")
    
    # Generate an index.html for easy navigation
    _generate_artifact_index(pw_artifact_dir)


def _generate_artifact_index(artifact_dir: str) -> None:
    """Generate a simple HTML index for browsing artifacts."""
    screenshots = []
    videos = []
    traces = []
    
    screenshots_dir = os.path.join(artifact_dir, "screenshots")
    videos_dir = os.path.join(artifact_dir, "videos")
    traces_dir = os.path.join(artifact_dir, "traces")
    
    if os.path.exists(screenshots_dir):
        screenshots = [f for f in os.listdir(screenshots_dir) if f.endswith(".png")]
    if os.path.exists(videos_dir):
        videos = [f for f in os.listdir(videos_dir) if f.endswith(".webm")]
    if os.path.exists(traces_dir):
        traces = [f for f in os.listdir(traces_dir) if f.endswith(".zip")]
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Playwright Test Artifacts</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 2rem; }}
        h1 {{ color: #1a1a1a; }}
        h2 {{ color: #444; margin-top: 2rem; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ padding: 0.5rem 0; }}
        a {{ color: #0066cc; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .empty {{ color: #888; font-style: italic; }}
        .section {{ background: #f5f5f5; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
        .trace-info {{ font-size: 0.9em; color: #666; margin-left: 1rem; }}
    </style>
</head>
<body>
    <h1>🎭 Playwright Test Artifacts</h1>
    
    <div class="section">
        <h2>📸 Screenshots ({len(screenshots)})</h2>
        {"<ul>" + "".join(f'<li><a href="screenshots/{f}">{f}</a></li>' for f in sorted(screenshots)) + "</ul>" if screenshots else '<p class="empty">No screenshots captured</p>'}
    </div>
    
    <div class="section">
        <h2>🎬 Videos ({len(videos)})</h2>
        {"<ul>" + "".join(f'<li><a href="videos/{f}">{f}</a></li>' for f in sorted(videos)) + "</ul>" if videos else '<p class="empty">No videos captured</p>'}
    </div>
    
    <div class="section">
        <h2>🔍 Traces ({len(traces)})</h2>
        {"<ul>" + "".join(f'<li><a href="traces/{f}">{f}</a> <span class="trace-info">→ Upload to <a href="https://trace.playwright.dev" target="_blank">trace.playwright.dev</a></span></li>' for f in sorted(traces)) + "</ul>" if traces else '<p class="empty">No traces captured</p>'}
    </div>
    
    <p style="margin-top: 2rem; color: #888; font-size: 0.9em;">
        Generated by cost-onprem-chart UI tests
    </p>
</body>
</html>"""
    
    index_path = os.path.join(artifact_dir, "index.html")
    try:
        with open(index_path, "w") as f:
            f.write(html)
        print(f"\n📄 Artifact index: {index_path}")
    except Exception as e:
        print(f"\n⚠️ Failed to generate index: {e}")
