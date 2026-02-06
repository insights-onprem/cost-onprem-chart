"""
External API tests for cost report endpoints.

These tests validate the cost report API contract by making direct HTTP calls
through the gateway with JWT authentication.

Jira Test Cases:
- FLPATH-3167: Verify OCP cost reports endpoint returns valid data
- FLPATH-3168: Verify OCP compute metrics endpoint returns valid data
- FLPATH-3169: Verify OCP memory metrics endpoint returns valid data
- FLPATH-3170: Verify OCP volume metrics endpoint returns valid data

Note: The gateway_url fixture already includes the /api path prefix from the route,
so endpoint paths should NOT include /api/ prefix.
"""

import pytest
import requests


@pytest.mark.api
@pytest.mark.component
class TestCostReportsAPI:
    """Test cost report endpoints via external gateway route."""

    def test_ocp_costs_report(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify OCP cost reports endpoint returns valid response.
        
        FLPATH-3167: Verify OCP cost reports endpoint returns valid data
        
        Tests:
        - Endpoint is accessible via gateway
        - Response has expected structure (meta, links, data)
        - Status code is 200
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "meta" in data, "Response missing 'meta' field"
        assert "data" in data, "Response missing 'data' field"
        
        # Meta should contain count
        assert "count" in data["meta"], "Meta missing 'count' field"

    def test_ocp_compute_report(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify OCP compute metrics endpoint returns valid response.
        
        FLPATH-3168: Verify OCP compute metrics endpoint returns valid data
        
        Tests:
        - Endpoint is accessible via gateway
        - Response has expected structure
        - Status code is 200
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/compute/",
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "meta" in data, "Response missing 'meta' field"
        assert "data" in data, "Response missing 'data' field"

    def test_ocp_memory_report(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify OCP memory metrics endpoint returns valid response.
        
        FLPATH-3169: Verify OCP memory metrics endpoint returns valid data
        
        Tests:
        - Endpoint is accessible via gateway
        - Response has expected structure
        - Status code is 200
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/memory/",
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "meta" in data, "Response missing 'meta' field"
        assert "data" in data, "Response missing 'data' field"

    def test_ocp_volume_report(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify OCP volume metrics endpoint returns valid response.
        
        FLPATH-3170: Verify OCP volume metrics endpoint returns valid data
        
        Tests:
        - Endpoint is accessible via gateway
        - Response has expected structure
        - Status code is 200
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/volumes/",
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "meta" in data, "Response missing 'meta' field"
        assert "data" in data, "Response missing 'data' field"


@pytest.mark.api
@pytest.mark.component
class TestReportFiltering:
    """Test report filtering and grouping functionality."""

    def test_report_with_project_filter(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify reports can be filtered by project/namespace.
        
        Tests:
        - Filter parameter is accepted
        - Response structure is valid
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            params={"filter[project]": "openshift-monitoring"},
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "data" in data

    def test_report_with_group_by(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify reports can be grouped by project.
        
        Tests:
        - Group by parameter is accepted
        - Response structure is valid
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            params={"group_by[project]": "*"},
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "data" in data

    def test_report_with_time_scope(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify reports can be filtered by time scope.
        
        Tests:
        - Time scope parameter is accepted
        - Response structure is valid
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/reports/openshift/costs/",
            params={"filter[time_scope_value]": "-1", "filter[time_scope_units]": "month"},
            timeout=30,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "data" in data


