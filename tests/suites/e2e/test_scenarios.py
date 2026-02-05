"""
YAML-Driven Scenario Tests.

These tests validate different data scenarios using NISE static reports.
Each scenario represents a different workload pattern or configuration.

Scenarios are defined as YAML files in scripts/e2e_validator/static_reports/
or can be generated dynamically.

Source Reference: scripts/e2e_validator/static_reports/
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pytest
import yaml


# =============================================================================
# Scenario Definitions
# =============================================================================

# Built-in scenarios with expected values
SCENARIOS = {
    "minimal_ocp_pod_only": {
        "description": "Minimal OCP scenario with 1 pod, 1 node, 24 hours",
        "expected": {
            "node_count": 1,
            "namespace_count": 1,
            "pod_count": 1,
            "cpu_request": 0.5,
            "mem_request_gig": 1,
        },
        "yaml_template": """---
# Minimal OCP Static Report - Single Pod
generators:
  - OCPGenerator:
      start_date: {start_date}
      end_date: {end_date}
      nodes:
        - node:
          node_name: test-node-1
          cpu_cores: 2
          memory_gig: 8
          resource_id: test-resource-1
          namespaces:
            test-namespace:
              pods:
                - pod:
                  pod_name: test-pod-1
                  cpu_request: 0.5
                  mem_request_gig: 1
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.25
                  mem_usage_gig:
                    full_period: 0.5
                  labels: environment:test|app:e2e-minimal
""",
    },
    "multi_pod_namespace": {
        "description": "Multiple pods in a single namespace",
        "expected": {
            "node_count": 1,
            "namespace_count": 1,
            "pod_count": 3,
            "cpu_request": 1.5,  # 0.5 * 3
            "mem_request_gig": 3,  # 1 * 3
        },
        "yaml_template": """---
# Multi-Pod OCP Static Report
generators:
  - OCPGenerator:
      start_date: {start_date}
      end_date: {end_date}
      nodes:
        - node:
          node_name: test-node-1
          cpu_cores: 8
          memory_gig: 32
          resource_id: test-resource-1
          namespaces:
            test-namespace:
              pods:
                - pod:
                  pod_name: test-pod-1
                  cpu_request: 0.5
                  mem_request_gig: 1
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.25
                  mem_usage_gig:
                    full_period: 0.5
                  labels: environment:test|app:web
                - pod:
                  pod_name: test-pod-2
                  cpu_request: 0.5
                  mem_request_gig: 1
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.3
                  mem_usage_gig:
                    full_period: 0.6
                  labels: environment:test|app:api
                - pod:
                  pod_name: test-pod-3
                  cpu_request: 0.5
                  mem_request_gig: 1
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.2
                  mem_usage_gig:
                    full_period: 0.4
                  labels: environment:test|app:worker
""",
    },
    "multi_namespace": {
        "description": "Multiple namespaces with different workloads",
        "expected": {
            "node_count": 1,
            "namespace_count": 2,
            "pod_count": 2,
            "cpu_request": 1.5,  # 0.5 + 1.0
            "mem_request_gig": 3,  # 1 + 2
        },
        "yaml_template": """---
# Multi-Namespace OCP Static Report
generators:
  - OCPGenerator:
      start_date: {start_date}
      end_date: {end_date}
      nodes:
        - node:
          node_name: test-node-1
          cpu_cores: 8
          memory_gig: 32
          resource_id: test-resource-1
          namespaces:
            production:
              pods:
                - pod:
                  pod_name: prod-app
                  cpu_request: 1.0
                  mem_request_gig: 2
                  cpu_limit: 2
                  mem_limit_gig: 4
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.8
                  mem_usage_gig:
                    full_period: 1.5
                  labels: environment:production|app:main
            staging:
              pods:
                - pod:
                  pod_name: staging-app
                  cpu_request: 0.5
                  mem_request_gig: 1
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.3
                  mem_usage_gig:
                    full_period: 0.5
                  labels: environment:staging|app:main
""",
    },
    "high_utilization": {
        "description": "High CPU/memory utilization scenario",
        "expected": {
            "node_count": 1,
            "namespace_count": 1,
            "pod_count": 1,
            "cpu_request": 2.0,
            "mem_request_gig": 4,
        },
        "yaml_template": """---
# High Utilization OCP Static Report
generators:
  - OCPGenerator:
      start_date: {start_date}
      end_date: {end_date}
      nodes:
        - node:
          node_name: high-util-node
          cpu_cores: 4
          memory_gig: 16
          resource_id: high-util-resource
          namespaces:
            high-util-ns:
              pods:
                - pod:
                  pod_name: high-util-pod
                  cpu_request: 2.0
                  mem_request_gig: 4
                  cpu_limit: 4
                  mem_limit_gig: 8
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 1.8
                  mem_usage_gig:
                    full_period: 3.5
                  labels: environment:test|app:high-util
""",
    },
}


# =============================================================================
# Helper Functions
# =============================================================================

def generate_scenario_yaml(
    scenario_name: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: str,
) -> str:
    """Generate a NISE static report YAML for a scenario.
    
    Args:
        scenario_name: Name of the scenario from SCENARIOS dict
        start_date: Start date for data generation
        end_date: End date for data generation
        output_dir: Directory to write the YAML file
        
    Returns:
        Path to the generated YAML file
    """
    if scenario_name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    
    scenario = SCENARIOS[scenario_name]
    yaml_content = scenario["yaml_template"].format(
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )
    
    yaml_path = os.path.join(output_dir, f"{scenario_name}.yml")
    with open(yaml_path, "w") as f:
        f.write(yaml_content)
    
    return yaml_path


def get_scenario_expected_values(scenario_name: str, hours: int = 24) -> Dict:
    """Get expected values for a scenario.
    
    Args:
        scenario_name: Name of the scenario
        hours: Number of hours of data
        
    Returns:
        Dict with expected metric values
    """
    if scenario_name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    
    expected = SCENARIOS[scenario_name]["expected"].copy()
    
    # Calculate expected totals based on hours
    expected["expected_cpu_hours"] = expected["cpu_request"] * hours
    expected["expected_memory_gb_hours"] = expected["mem_request_gig"] * hours
    expected["hours"] = hours
    
    return expected


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.e2e
@pytest.mark.scenario
class TestScenarioDefinitions:
    """Tests for scenario YAML generation and validation."""
    
    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_scenario_yaml_valid(self, scenario_name: str):
        """Verify scenario YAML is valid and parseable."""
        now = datetime.utcnow()
        start_date = now - timedelta(days=1)
        end_date = now
        
        with tempfile.TemporaryDirectory() as temp_dir:
            yaml_path = generate_scenario_yaml(
                scenario_name,
                start_date,
                end_date,
                temp_dir,
            )
            
            # Verify file was created
            assert os.path.exists(yaml_path), f"YAML file not created for {scenario_name}"
            
            # Verify YAML is parseable
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            
            assert "generators" in data, "Missing 'generators' key in YAML"
            assert len(data["generators"]) > 0, "No generators defined"
    
    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_scenario_has_expected_values(self, scenario_name: str):
        """Verify each scenario has expected values defined."""
        expected = get_scenario_expected_values(scenario_name)
        
        # Required expected values
        required_keys = [
            "node_count",
            "namespace_count",
            "pod_count",
            "expected_cpu_hours",
            "expected_memory_gb_hours",
        ]
        
        for key in required_keys:
            assert key in expected, f"Missing expected value '{key}' for scenario '{scenario_name}'"
    
    def test_all_scenarios_have_descriptions(self):
        """Verify all scenarios have descriptions."""
        for name, scenario in SCENARIOS.items():
            assert "description" in scenario, f"Scenario '{name}' missing description"
            assert len(scenario["description"]) > 0, f"Scenario '{name}' has empty description"


@pytest.mark.e2e
@pytest.mark.scenario
class TestScenarioExpectedValues:
    """Tests for scenario expected value calculations."""
    
    @pytest.mark.parametrize("scenario_name,expected_pods", [
        ("minimal_ocp_pod_only", 1),
        ("multi_pod_namespace", 3),
        ("multi_namespace", 2),
        ("high_utilization", 1),
    ])
    def test_pod_count_expected(self, scenario_name: str, expected_pods: int):
        """Verify pod count matches expected for each scenario."""
        expected = get_scenario_expected_values(scenario_name)
        assert expected["pod_count"] == expected_pods, (
            f"Scenario '{scenario_name}' pod count mismatch: "
            f"expected {expected_pods}, got {expected['pod_count']}"
        )
    
    @pytest.mark.parametrize("scenario_name,expected_namespaces", [
        ("minimal_ocp_pod_only", 1),
        ("multi_pod_namespace", 1),
        ("multi_namespace", 2),
        ("high_utilization", 1),
    ])
    def test_namespace_count_expected(self, scenario_name: str, expected_namespaces: int):
        """Verify namespace count matches expected for each scenario."""
        expected = get_scenario_expected_values(scenario_name)
        assert expected["namespace_count"] == expected_namespaces, (
            f"Scenario '{scenario_name}' namespace count mismatch: "
            f"expected {expected_namespaces}, got {expected['namespace_count']}"
        )
    
    @pytest.mark.parametrize("hours", [1, 24, 168])  # 1 hour, 1 day, 1 week
    def test_cpu_hours_calculation(self, hours: int):
        """Verify CPU hours calculation scales with hours."""
        expected = get_scenario_expected_values("minimal_ocp_pod_only", hours=hours)
        
        # 0.5 cores * hours
        expected_cpu_hours = 0.5 * hours
        assert expected["expected_cpu_hours"] == expected_cpu_hours, (
            f"CPU hours mismatch for {hours} hours: "
            f"expected {expected_cpu_hours}, got {expected['expected_cpu_hours']}"
        )
    
    @pytest.mark.parametrize("hours", [1, 24, 168])
    def test_memory_gb_hours_calculation(self, hours: int):
        """Verify memory GB-hours calculation scales with hours."""
        expected = get_scenario_expected_values("minimal_ocp_pod_only", hours=hours)
        
        # 1 GiB * hours
        expected_mem_hours = 1 * hours
        assert expected["expected_memory_gb_hours"] == expected_mem_hours, (
            f"Memory GB-hours mismatch for {hours} hours: "
            f"expected {expected_mem_hours}, got {expected['expected_memory_gb_hours']}"
        )


@pytest.mark.e2e
@pytest.mark.scenario
class TestScenarioYAMLStructure:
    """Tests for NISE YAML structure validation."""
    
    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_yaml_has_ocp_generator(self, scenario_name: str):
        """Verify YAML uses OCPGenerator."""
        now = datetime.utcnow()
        
        with tempfile.TemporaryDirectory() as temp_dir:
            yaml_path = generate_scenario_yaml(
                scenario_name,
                now - timedelta(days=1),
                now,
                temp_dir,
            )
            
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            
            generators = data.get("generators", [])
            has_ocp = any("OCPGenerator" in g for g in generators)
            
            assert has_ocp, f"Scenario '{scenario_name}' missing OCPGenerator"
    
    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_yaml_has_required_fields(self, scenario_name: str):
        """Verify YAML has required NISE fields."""
        now = datetime.utcnow()
        
        with tempfile.TemporaryDirectory() as temp_dir:
            yaml_path = generate_scenario_yaml(
                scenario_name,
                now - timedelta(days=1),
                now,
                temp_dir,
            )
            
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            
            generator = data["generators"][0]["OCPGenerator"]
            
            required_fields = ["start_date", "end_date", "nodes"]
            for field in required_fields:
                assert field in generator, (
                    f"Scenario '{scenario_name}' missing required field '{field}'"
                )
    
    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_yaml_dates_are_dynamic(self, scenario_name: str):
        """Verify YAML dates are dynamically set (not hardcoded)."""
        now = datetime.utcnow()
        start_date = now - timedelta(days=7)
        end_date = now
        
        with tempfile.TemporaryDirectory() as temp_dir:
            yaml_path = generate_scenario_yaml(
                scenario_name,
                start_date,
                end_date,
                temp_dir,
            )
            
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            
            generator = data["generators"][0]["OCPGenerator"]
            
            # YAML may parse dates as datetime.date objects or strings
            # Convert to string for comparison
            yaml_start = str(generator["start_date"])
            yaml_end = str(generator["end_date"])
            
            # Verify dates match what we passed in
            assert yaml_start == start_date.strftime("%Y-%m-%d"), (
                f"Start date not dynamic for scenario '{scenario_name}': "
                f"expected {start_date.strftime('%Y-%m-%d')}, got {yaml_start}"
            )
            assert yaml_end == end_date.strftime("%Y-%m-%d"), (
                f"End date not dynamic for scenario '{scenario_name}': "
                f"expected {end_date.strftime('%Y-%m-%d')}, got {yaml_end}"
            )
