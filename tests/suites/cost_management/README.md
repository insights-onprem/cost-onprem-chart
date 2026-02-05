# Cost Management Test Suite

Tests for validating Koku cost processing pipeline and data integrity.

## Cost Calculation Validation (`test_cost_validation.py`)

Validates that cost calculations in Koku match expected values from NISE-generated data.

| Test Class | Test | Description |
|------------|------|-------------|
| `TestSummaryTableData` | `test_summary_table_exists` | Verifies OCP usage summary table exists |
| | `test_summary_has_data_for_cluster` | Verifies summary table has data for test cluster |
| `TestCPUMetrics` | `test_cpu_request_hours_within_tolerance` | CPU request hours match expected (±5%) |
| | `test_cpu_usage_recorded` | CPU usage data was recorded (non-zero) |
| `TestMemoryMetrics` | `test_memory_request_gb_hours_within_tolerance` | Memory GB-hours match expected (±5%) |
| | `test_memory_usage_recorded` | Memory usage data was recorded (non-zero) |
| `TestResourceCounts` | `test_node_count_matches_expected` | Unique node count matches NISE config |
| | `test_namespace_count_matches_expected` | Unique namespace count matches |
| | `test_pod_count_matches_expected` | Unique pod/resource count matches |
| `TestResourceNames` | `test_node_name_matches_expected` | Node name matches NISE static report |
| | `test_namespace_name_matches_expected` | Namespace name matches |
| `TestInfrastructureCost` | `test_infrastructure_cost_calculated` | Infrastructure cost was calculated |
| `TestMetricTolerance` | `test_metric_within_tolerance[cpu]` | Parametrized CPU tolerance check |
| | `test_metric_within_tolerance[memory]` | Parametrized memory tolerance check |

**Expected Values** (from NISE static report):
- Node: `test-node-1`
- Namespace: `test-namespace`
- Pod: `test-pod-1`
- CPU request: 0.5 cores → 12 CPU-hours/day
- Memory request: 1 GiB → 24 GB-hours/day

**Tolerance**: 5% (matches IQE validation pattern)

**Markers**: `@pytest.mark.cost_management`, `@pytest.mark.cost_validation`

**Self-Contained Setup**: These tests are fully self-contained via the `cost_validation_data` fixture. Each test run:
1. Generates NISE data with known expected values (from `e2e_helpers.NISEConfig`)
2. Registers a source in Sources API (with `source_ref` for cluster matching)
3. Uploads data via JWT-authenticated ingress
4. Waits for Koku to process and populate summary tables
5. Runs validation tests
6. Cleans up all test data (source, database records, temp files)

**Note**: The expected values come from `e2e_helpers.NISEConfig.get_expected_values()` to ensure consistency with the NISE static report template.

**Environment Variables**:
| Variable | Default | Description |
|----------|---------|-------------|
| `E2E_COST_TOLERANCE` | `0.05` | Tolerance for cost validation (5% = 0.05) |

---

### Processing State Validation

Processing state validation (manifest fields, file status, stuck manifests, summary failures) has been **merged into the E2E tests** in `tests/suites/e2e/test_complete_flow.py`:

| E2E Test | Processing Validation Added |
|----------|----------------------------|
| `test_04_manifest_created_in_koku` | Manifest required fields (id, assembly_id, cluster_id, num_total_files, creation_datetime) |
| `test_05_files_processed_by_masu` | File status validation (no failures, completion timestamps) |
| `test_06_summary_tables_populated` | Stuck manifest detection, summary failure detection, processing stats |

This ensures processing state is validated as part of the E2E flow with the actual test data, rather than depending on leftover data from previous runs.

---

## Running Cost Management Tests

```bash
# Run all cost management tests
./scripts/run-pytest.sh --cost-management

# Run only cost validation tests
pytest tests/suites/cost_management/test_cost_validation.py -v

# Run cost validation with custom tolerance (10%)
E2E_COST_TOLERANCE=0.10 pytest -m cost_validation -v
```

## Related Files

- `test_processing.py` - Koku listener and MASU worker health tests
- `test_sources_api.py` - Sources API health and configuration tests
- `conftest.py` - Suite fixtures including `cost_validation_data` for self-contained E2E setup
- `../../e2e_helpers.py` - Centralized E2E helpers (NISE config, source registration, upload utilities)
