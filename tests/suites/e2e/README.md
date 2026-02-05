# E2E Test Suite

End-to-end tests that validate the complete data flow pipeline.

## YAML-Driven Scenario Tests (`test_scenarios.py`)

Framework for testing different OCP workload patterns with known expected values.

| Test Class | Test | Description |
|------------|------|-------------|
| `TestScenarioDefinitions` | `test_scenario_yaml_valid[scenario]` | YAML is parseable for each scenario |
| | `test_scenario_has_expected_values[scenario]` | Expected values are defined |
| | `test_all_scenarios_have_descriptions` | All scenarios have documentation |
| `TestScenarioExpectedValues` | `test_pod_count_expected[scenario-count]` | Pod counts match expected |
| | `test_namespace_count_expected[scenario-count]` | Namespace counts match |
| | `test_cpu_hours_calculation[hours]` | CPU hours scale correctly (1h, 24h, 168h) |
| | `test_memory_gb_hours_calculation[hours]` | Memory GB-hours scale correctly |
| `TestScenarioYAMLStructure` | `test_yaml_has_ocp_generator[scenario]` | Uses OCPGenerator |
| | `test_yaml_has_required_fields[scenario]` | Has start_date, end_date, nodes |
| | `test_yaml_dates_are_dynamic[scenario]` | Dates are injected, not hardcoded |

**Available Scenarios**:

| Scenario | Description | Pods | Namespaces | CPU | Memory |
|----------|-------------|------|------------|-----|--------|
| `minimal_ocp_pod_only` | Single pod baseline | 1 | 1 | 0.5 cores | 1 GiB |
| `multi_pod_namespace` | Multiple pods, one namespace | 3 | 1 | 1.5 cores | 3 GiB |
| `multi_namespace` | Workloads across namespaces | 2 | 2 | 1.5 cores | 3 GiB |
| `high_utilization` | High resource usage | 1 | 1 | 2.0 cores | 4 GiB |

**Key Implementation Detail**: Dates are **dynamically injected** into YAML templates at runtime. This solves the "hardcoded dates" problem that causes Koku to reject data with "missing start or end dates" errors.

**Example YAML Template** (dates replaced at runtime):
```yaml
generators:
  - OCPGenerator:
      start_date: {start_date}  # Injected dynamically
      end_date: {end_date}      # Injected dynamically
      nodes:
        - node:
          node_name: test-node-1
          cpu_cores: 2
          memory_gig: 8
          namespaces:
            test-namespace:
              pods:
                - pod:
                  pod_name: test-pod-1
                  cpu_request: 0.5
                  mem_request_gig: 1
```

**Markers**: `@pytest.mark.e2e`, `@pytest.mark.scenario`

---

## Using Scenarios with E2E Tests

To use a custom scenario with the E2E data flow tests:

```python
from test_scenarios import generate_scenario_yaml, get_scenario_expected_values

# Generate YAML with current dates
yaml_path = generate_scenario_yaml(
    "multi_pod_namespace",
    start_date=datetime.utcnow() - timedelta(days=1),
    end_date=datetime.utcnow(),
    output_dir="/tmp/nise",
)

# Get expected values for validation
expected = get_scenario_expected_values("multi_pod_namespace", hours=24)
# expected = {
#     "pod_count": 3,
#     "namespace_count": 1,
#     "expected_cpu_hours": 36.0,  # 1.5 cores * 24 hours
#     "expected_memory_gb_hours": 72.0,  # 3 GiB * 24 hours
# }
```

Or via environment variable:
```bash
E2E_NISE_STATIC_REPORT=/path/to/scenario.yml ./scripts/run-pytest.sh --e2e
```

---

## Running E2E Tests

```bash
# Run scenario validation tests (fast, no cluster needed)
pytest tests/suites/e2e/test_scenarios.py -v -m scenario

# Run full E2E data flow tests
./scripts/run-pytest.sh --e2e

# Run E2E smoke tests only
./scripts/run-pytest.sh -- -m "e2e and smoke"
```

## Related Files

- `test_complete_flow.py` - Full E2E data flow tests (source → upload → processing → recommendations)
- `test_smoke.py` - Quick smoke tests for E2E validation
- `conftest.py` - Shared fixtures for E2E tests
- `../../e2e_helpers.py` - Centralized E2E helpers (NISE config, source registration, upload utilities)