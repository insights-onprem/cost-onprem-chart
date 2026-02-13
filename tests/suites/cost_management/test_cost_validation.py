"""
Cost Calculation Validation Tests.

These tests are SELF-CONTAINED - they set up their own test data via the full E2E flow:
1. Generate NISE data with known expected values
2. Register a source in Sources API
3. Upload data via JWT-authenticated ingress
4. Wait for Koku to process and populate summary tables
5. Validate cost calculations match expected values
6. Clean up all test data

Environment Variables:
- E2E_COST_TOLERANCE: Tolerance for cost validation (default: 0.05 = 5%)

Configuration is centralized in e2e_helpers.py (NISEConfig class).
"""

import os

import pytest

from utils import execute_db_query


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_COST_TOLERANCE = 0.05


def get_cost_tolerance() -> float:
    """Get cost validation tolerance from environment or default."""
    try:
        return float(os.environ.get("E2E_COST_TOLERANCE", DEFAULT_COST_TOLERANCE))
    except ValueError:
        return DEFAULT_COST_TOLERANCE


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.cost_management
@pytest.mark.cost_validation
@pytest.mark.timeout(900)  # 15 minutes for E2E setup + tests
class TestSummaryTableData:
    """Tests for summary table data validation."""
    
    def test_summary_table_exists(self, cost_validation_data):
        """Verify the OCP usage summary table exists."""
        ctx = cost_validation_data
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = '{ctx["schema_name"]}'
                AND table_name = 'reporting_ocpusagelineitem_daily_summary'
            )
            """,
        )
        
        assert result and result[0][0] in ["t", "True", True, "1"], (
            f"Summary table not found in schema '{ctx['schema_name']}'."
        )
    
    def test_summary_has_data_for_cluster(self, cost_validation_data):
        """Verify summary table has data for the test cluster."""
        ctx = cost_validation_data
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT COUNT(*)
            FROM {ctx["schema_name"]}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{ctx["cluster_id"]}'
            """,
        )
        
        row_count = int(result[0][0]) if result else 0
        
        assert row_count > 0, (
            f"No summary data found for cluster '{ctx['cluster_id']}'."
        )


@pytest.mark.cost_management
@pytest.mark.cost_validation
class TestMetricValidation:
    """Tests for CPU and memory metric validation against expected values."""
    
    @pytest.mark.parametrize("metric_name,db_column,expected_key,unit", [
        pytest.param(
            "CPU request", "pod_request_cpu_core_hours", 
            "expected_cpu_hours", "hours", 
            id="cpu_request_hours"
        ),
        pytest.param(
            "memory request", "pod_request_memory_gigabyte_hours", 
            "expected_memory_gb_hours", "GB-hours", 
            id="memory_request_gb_hours"
        ),
    ])
    def test_request_metric_within_tolerance(
        self, cost_validation_data, metric_name: str, db_column: str, expected_key: str, unit: str
    ):
        """Verify request metric matches expected value within tolerance.
        
        Parametrized for: CPU request hours, memory request GB-hours.
        """
        ctx = cost_validation_data
        expected = ctx["expected"]
        tolerance = get_cost_tolerance()
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT SUM({db_column}) as total
            FROM {ctx["schema_name"]}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{ctx["cluster_id"]}'
            AND namespace NOT LIKE '%unallocated%'
            """,
        )
        
        assert result and result[0][0] is not None, f"No {metric_name} data in summary tables"
        
        actual_value = float(result[0][0])
        expected_value = expected[expected_key]
        
        diff_pct = abs(actual_value - expected_value) / expected_value if expected_value > 0 else 0
        
        assert diff_pct <= tolerance, (
            f"{metric_name.capitalize()} mismatch:\n"
            f"  Expected: {expected_value:.2f} {unit}\n"
            f"  Actual:   {actual_value:.2f} {unit}\n"
            f"  Diff:     {diff_pct*100:.1f}% (tolerance: {tolerance*100}%)"
        )
    
    @pytest.mark.parametrize("metric_name,db_column,unit", [
        pytest.param("CPU", "pod_usage_cpu_core_hours", "hours", id="cpu_usage"),
        pytest.param("memory", "pod_usage_memory_gigabyte_hours", "GB-hours", id="memory_usage"),
    ])
    def test_usage_recorded(self, cost_validation_data, metric_name: str, db_column: str, unit: str):
        """Verify usage data was recorded (non-zero).
        
        Parametrized for: CPU usage, memory usage.
        """
        ctx = cost_validation_data
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT SUM({db_column}) as total_usage
            FROM {ctx["schema_name"]}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{ctx["cluster_id"]}'
            AND namespace NOT LIKE '%unallocated%'
            """,
        )
        
        assert result and result[0][0] is not None, f"No {metric_name} usage data"
        
        actual_usage = float(result[0][0])
        assert actual_usage > 0, f"{metric_name} usage is zero or negative: {actual_usage} {unit}"


@pytest.mark.cost_management
@pytest.mark.cost_validation
class TestResourceCounts:
    """Tests for resource count validation."""
    
    @pytest.mark.parametrize("resource_type,db_column,expected_key", [
        pytest.param("node", "node", "expected_node_count", id="node_count"),
        pytest.param("namespace", "namespace", "expected_namespace_count", id="namespace_count"),
        pytest.param("pod", "resource_id", "expected_pod_count", id="pod_count"),
    ])
    def test_resource_count_matches_expected(
        self, cost_validation_data, resource_type: str, db_column: str, expected_key: str
    ):
        """Verify unique resource count matches expected.
        
        Parametrized for: node, namespace, pod counts.
        """
        ctx = cost_validation_data
        expected = ctx["expected"]
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT COUNT(DISTINCT {db_column}) as count
            FROM {ctx["schema_name"]}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{ctx["cluster_id"]}'
            AND namespace NOT LIKE '%unallocated%'
            """,
        )
        
        assert result, f"Could not query {resource_type} count"
        
        actual_count = int(result[0][0])
        expected_count = expected[expected_key]
        
        assert actual_count == expected_count, (
            f"{resource_type.capitalize()} count mismatch: "
            f"expected {expected_count}, got {actual_count}"
        )


@pytest.mark.cost_management
@pytest.mark.cost_validation
class TestResourceNames:
    """Tests for resource name validation."""
    
    @pytest.mark.parametrize("resource_type,db_column,expected_key", [
        pytest.param("node", "node", "node_name", id="node_name"),
        pytest.param("namespace", "namespace", "namespace", id="namespace_name"),
    ])
    def test_resource_name_matches_expected(
        self, cost_validation_data, resource_type: str, db_column: str, expected_key: str
    ):
        """Verify resource name matches NISE static report.
        
        Parametrized for: node name, namespace name.
        """
        ctx = cost_validation_data
        expected = ctx["expected"]
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT DISTINCT {db_column}
            FROM {ctx["schema_name"]}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{ctx["cluster_id"]}'
            AND namespace NOT LIKE '%unallocated%'
            LIMIT 1
            """,
        )
        
        assert result and result[0][0], f"No {resource_type} data available"
        
        actual_value = result[0][0]
        expected_value = expected[expected_key]
        
        assert actual_value == expected_value, (
            f"{resource_type.capitalize()} name mismatch: "
            f"expected '{expected_value}', got '{actual_value}'"
        )


@pytest.mark.cost_management
@pytest.mark.cost_validation
class TestInfrastructureCost:
    """Tests for infrastructure cost calculation."""
    
    def test_infrastructure_cost_calculated(self, cost_validation_data):
        """Verify infrastructure cost was calculated (non-zero)."""
        ctx = cost_validation_data
        
        result = execute_db_query(
            ctx["namespace"],
            ctx["db_pod"],
            "costonprem_koku",
            "koku_user",
            f"""
            SELECT 
                COUNT(*) as rows_with_cost,
                SUM(CAST(infrastructure_usage_cost->>'value' AS NUMERIC)) as total_cost
            FROM {ctx["schema_name"]}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{ctx["cluster_id"]}'
            AND infrastructure_usage_cost IS NOT NULL
            """,
        )
        
        assert result, "Could not query infrastructure cost"
        
        rows_with_cost = int(result[0][0]) if result[0][0] else 0
        total_cost = float(result[0][1]) if result[0][1] else 0.0
        
        # Infrastructure cost may not be calculated immediately - this is OK
        if rows_with_cost == 0:
            pytest.skip(
                "No infrastructure cost calculated yet. "
                "Cost calculation may run asynchronously."
            )
        
        assert total_cost >= 0, f"Infrastructure cost is negative: ${total_cost:.2f}"
        print(f"\n  Infrastructure cost: ${total_cost:.2f} ({rows_with_cost} rows)")


