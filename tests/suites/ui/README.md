# UI Tests (Playwright)

Browser-based UI tests using [Playwright](https://playwright.dev/) for end-to-end validation of the Cost Management UI.

## Overview

These tests validate:
- **Login Flow**: OAuth/OIDC authentication via Keycloak
- **Navigation**: Main application pages (Overview, OpenShift, Cost Explorer, Settings)
- **Session Persistence**: Protected routes and session handling

## Prerequisites

### Platform-Specific Setup

Playwright requires both the Python package and browser binaries with system dependencies.

#### macOS

macOS includes most required dependencies. Just install the browsers:

```bash
# Activate the test virtual environment
cd tests && source .venv/bin/activate

# Install Playwright browsers
playwright install chromium

# Or install all browsers
playwright install
```

#### Linux (Fedora/RHEL)

Linux requires additional system libraries for Chromium:

```bash
# Install system dependencies
sudo dnf install -y \
    nspr \
    nss \
    nss-util \
    atk \
    at-spi2-atk \
    cups-libs \
    libdrm \
    libXcomposite \
    libXdamage \
    libXrandr \
    mesa-libgbm \
    pango \
    alsa-lib

# Activate the test virtual environment
cd tests && source .venv/bin/activate

# Install Playwright browsers
playwright install chromium
```

#### Linux (Ubuntu/Debian)

```bash
# Install system dependencies (requires sudo)
sudo apt-get update && sudo apt-get install -y \
    libnspr4 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libasound2

# Activate the test virtual environment
cd tests && source .venv/bin/activate

# Install Playwright browsers
playwright install chromium
```

#### Alternative: Install All Dependencies via Playwright

Playwright can attempt to install system dependencies automatically (requires root):

```bash
# This tries to install system deps AND browsers
playwright install --with-deps chromium
```

### Verify Installation

```bash
# Check that Playwright can launch the browser
python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(); b.close(); p.stop(); print('Playwright OK')"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAYWRIGHT_BROWSER` | `chromium` | Browser to use (`chromium`, `firefox`, `webkit`) |
| `PLAYWRIGHT_HEADLESS` | `true` | Run in headless mode |
| `PLAYWRIGHT_SLOW_MO` | `0` | Slow down actions by N milliseconds (for debugging) |
| `TEST_UI_USERNAME` | `test` | Keycloak test user username |
| `TEST_UI_PASSWORD` | `test` | Keycloak test user password |

## Running Tests

```bash
# Run all UI tests
pytest -m ui

# Run UI smoke tests only
pytest -m "ui and smoke"

# Run with visible browser (for debugging)
PLAYWRIGHT_HEADLESS=false pytest -m ui

# Run with slow motion (see what's happening)
PLAYWRIGHT_HEADLESS=false PLAYWRIGHT_SLOW_MO=500 pytest -m ui

# Run specific test file
pytest suites/ui/test_login_flow.py
pytest suites/ui/test_navigation.py

# Run with different browser
PLAYWRIGHT_BROWSER=firefox pytest -m ui
```

## Test Structure

```
suites/ui/
├── __init__.py
├── conftest.py           # Playwright fixtures
├── README.md             # This file
├── test_login_flow.py    # Keycloak OAuth login tests
└── test_navigation.py    # Main page navigation tests
```

## Test Coverage

### Login Flow Tests (`test_login_flow.py`)

| Test Class | Test | Description |
|------------|------|-------------|
| `TestLoginFlow` | `test_ui_redirects_to_keycloak` | UI redirects to Keycloak for auth |
| | `test_successful_login` | Valid credentials complete login |
| | `test_invalid_credentials_shows_error` | Invalid credentials show error |
| `TestAuthenticatedSession` | `test_session_persists_across_navigation` | Session persists after navigation |
| | `test_can_access_protected_routes` | Authenticated user can access routes |

### Navigation Tests (`test_navigation.py`)

| Test Class | Test | Description |
|------------|------|-------------|
| `TestDefaultNavigation` | `test_ui_defaults_to_overview` | Root URL defaults to Overview page |
| | `test_navigation_menu_visible` | Nav menu shows expected items |
| `TestPageNavigation` | `test_can_navigate_to_page[page]` | Can click nav to each main page |
| | `test_page_loads_without_error[page]` | Each page loads without errors |
| `TestOptionalPages` | `test_optional_page_accessible[page]` | Cloud provider pages accessible |
| `TestOverviewPage` | `test_overview_has_content` | Overview page has content |
| `TestOpenShiftPage` | `test_openshift_page_has_content` | OpenShift page has content |
| `TestCostExplorerPage` | `test_cost_explorer_has_content` | Cost Explorer page has content |
| `TestSettingsPage` | `test_settings_page_has_content` | Settings page has content |

**Validated Pages** (parametrized tests):
- Overview (`/openshift/cost-management`)
- OpenShift (`/openshift/cost-management/ocp`)
- Cost Explorer (`/openshift/cost-management/explorer`)
- Settings (`/openshift/cost-management/settings`)

**Optional Pages** (may show "no data"):
- Optimizations (`/openshift/cost-management/optimizations`)
- AWS (`/openshift/cost-management/aws`)
- GCP (`/openshift/cost-management/gcp`)
- Azure (`/openshift/cost-management/azure`)

### Extending Navigation Tests

To add more pages to validate, edit `test_navigation.py`:

```python
# Add to NAVIGATION_PAGES for required pages
NAVIGATION_PAGES = [
    NavPage("Overview", "/openshift/cost-management", "Overview"),
    NavPage("OpenShift", "/openshift/cost-management/ocp", "OpenShift"),
    # Add new pages here...
]

# Add to OPTIONAL_PAGES for pages that may not have data
OPTIONAL_PAGES = [
    NavPage("Optimizations", "/openshift/cost-management/optimizations", "Optimizations"),
    # Add new optional pages here...
]
```

## Fixtures

### Browser Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `playwright_instance` | session | Playwright instance |
| `browser` | session | Browser instance (Chromium by default) |
| `browser_context` | function | Fresh context per test (isolated cookies/storage) |
| `page` | function | Fresh page per test |

### Authentication Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `authenticated_context` | function | Browser context with logged-in session |
| `authenticated_page` | function | Page with logged-in session |

### URL Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `ui_url` | session | Cost Management UI URL |
| `keycloak_login_url` | session | Keycloak login page URL |

## Debugging

### Screenshots on Failure

Screenshots are automatically captured when tests fail and saved to:
```
tests/reports/screenshots/<test_name>.png
```

### Running with Visible Browser

```bash
PLAYWRIGHT_HEADLESS=false pytest -m ui -k test_login
```

### Using Playwright Inspector

```bash
PWDEBUG=1 pytest -m ui -k test_login
```

### Generating Tests with Codegen

```bash
# Record actions and generate test code
playwright codegen https://your-ui-url.example.com
```

## Writing New Tests

### Basic Test Structure

```python
import pytest
from playwright.sync_api import Page, expect

@pytest.mark.ui
class TestMyFeature:
    def test_something(self, page: Page, ui_url: str):
        page.goto(ui_url)
        expect(page.locator("h1")).to_have_text("Expected Title")
```

### Using Authenticated Session

```python
@pytest.mark.ui
class TestProtectedFeature:
    def test_requires_auth(self, authenticated_page: Page, ui_url: str):
        # Already logged in
        authenticated_page.goto(f"{ui_url}/protected-route")
        # Verify not redirected to Keycloak
        expect(authenticated_page).to_have_url(re.compile(f"{ui_url}.*"))
```

### Best Practices

1. **Use `expect()` assertions** - They auto-wait and provide better error messages
2. **Use semantic locators** - Prefer `role`, `text`, `label` over CSS selectors
3. **Wait for network idle** - Use `page.wait_for_load_state("networkidle")` after navigation
4. **Handle dynamic content** - Use `expect().to_be_visible(timeout=N)` for async content
5. **Skip when no data** - Use `pytest.skip()` when tests require data that may not exist

## CI Integration

**UI tests are excluded by default** because they require Playwright system dependencies that may not be available in all CI environments.

```bash
# Default: runs all tests EXCEPT UI
./scripts/run-pytest.sh

# Run ONLY UI tests (when Playwright deps are available)
./scripts/run-pytest.sh --ui

# Run all tests INCLUDING UI
pytest -m ""  # Empty marker expression overrides default exclusion
```

### Enabling UI Tests in CI

To enable UI tests in CI, the CI environment must:

1. **Install system dependencies** before running tests:
   ```bash
   # Fedora/RHEL
   dnf install -y nspr nss nss-util atk at-spi2-atk cups-libs libdrm \
       libXcomposite libXdamage libXrandr mesa-libgbm pango alsa-lib
   ```

2. **Install Playwright browsers** (handled by `run-pytest.sh`):
   ```bash
   playwright install chromium
   ```

3. **Set headless mode** (default):
   ```bash
   export PLAYWRIGHT_HEADLESS=true
   ```

4. **Preserve screenshots** as artifacts on failure
