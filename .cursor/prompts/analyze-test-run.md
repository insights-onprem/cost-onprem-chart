# Analyze IQE Test Run

Analyze an IQE test run log to understand performance characteristics.
Disambiguate between **product performance** (Koku backend) and **test
performance** (IQE framework overhead).

## Input

Provide one of:
1. **Log file path**: e.g., `/tmp/iqe-extended-run.log`
2. **CI artifact URL**: Prow job log URL
3. **Active run**: "analyze the current run" (check `/tmp/iqe-extended-run*.log`)

## Analysis Steps

### 1. Run Summary

Extract high-level results from the pytest summary line:

```bash
# Final summary (appears at end of run)
grep -a "passed\|failed\|error\|skipped\|deselected" "$LOG" | tail -1

# Wall clock time
grep -a "in [0-9]" "$LOG" | tail -1
```

Report: total tests, passed, failed, errors, skipped, deselected, wall time.

### 2. Product Performance (Koku Backend)

These metrics reflect how fast the deployed Koku instance processes data.
They are independent of the test framework.

#### 2a. Listener Throughput

The listener processes uploaded report files serially via Kafka.

```bash
# From cluster (live):
LISTENER=$(kubectl get pods -l app.kubernetes.io/component=listener \
    -o jsonpath='{.items[0].metadata.name}')
kubectl logs "$LISTENER" | grep -c "Processing.*complete"

# Listener resource allocation:
kubectl get pods -l app.kubernetes.io/component=listener \
    -o jsonpath='{.items[0].spec.containers[0].resources}'
```

Key questions:
- How many files did the listener process?
- Were there processing failures? (`grep "failed to convert"`)
- What was the listener CPU limit? (4 cores = max boost)
- Were there `mig_instance_uuid` / `UndefinedColumn` errors? (schema mismatch)

#### 2b. Source Processing Time

Time from source creation to all files processed + summary complete.

```bash
# From test log — look for ingestion polling patterns:
grep -a "Files processing\|Summary processing\|Ingest complete\|No stats" "$LOG"

# Per-source timing (look for source UUID patterns):
grep -a "files processed\|process_complete_date\|summary_complete_date" "$LOG"
```

Key metric: **time from first "No stats" to "Ingest complete"** per source.
This is pure product processing time. Typical ranges:
- Small static source (1 month, no GPU): 30-60s
- Dynamic daily source (4 months, 6 CSVs/month): 2-5 min
- Source with GPU/MIG data on mismatched schema: **never completes** (fail-fast
  should catch within ~10s)

#### 2c. Processing Failures

```bash
# Fail-fast triggers (from IQE plugin changes):
grep -a "manifest stalled\|processing failed\|SourceProcessingFailed" "$LOG"

# Backend errors (from listener pod):
kubectl logs "$LISTENER" | grep "failed to convert\|UndefinedColumn\|ReportProcessorError"
```

A processing failure is a **product issue**, not a test issue. The fail-fast
code surfaces these immediately instead of timing out after 30+ minutes.

#### 2d. Summary/Worker Performance

After file processing, Celery workers run summary tasks.

```bash
# From cluster:
kubectl logs -l app.kubernetes.io/component=cost-worker --tail=50
kubectl logs -l app.kubernetes.io/component=cost-processor --tail=50
```

### 3. Test Performance (IQE Framework)

These metrics reflect overhead from the test framework itself.
They are independent of the Koku backend.

#### 3a. Fixture Setup Time

Source fixtures are the dominant cost. Each one:
1. Generates nise data (local CPU, 1-5s)
2. Uploads to ingress (network, <1s per file)
3. Polls for processing completion (blocked on product, 30s-5min)
4. Polls for summary completion (blocked on product, 10-30s)

```bash
# Count distinct source fixtures created:
grep -ac "nise.*report ocp\|nise.*report aws" "$LOG"

# Upload volume:
grep -ac "Upload File" "$LOG"
echo "total uploads"
grep -ao "filesize is [0-9.]* KB" "$LOG" | awk -F'is ' '{sum+=$2} END {printf "%.1f KB\n", sum}'
echo "total size"

# Polling overhead (time spent waiting for backend):
grep -ac "No stats\|Not all report files\|Summary processing not complete" "$LOG"
echo "poll cycles (×10s each)"
```

Key insight: most "test time" in the extended profile is actually **waiting
for the product** (ingestion polling). The actual test assertions are sub-second.

#### 3b. Test Execution Time

Pure API test time (excluding fixture setup):

```bash
# Tests that don't need source fixtures (cost model CRUD, etc.) run fast:
# Look for rapid PASSED sequences without interleaved polling
grep -a "PASSED\|ERROR\|FAILED" "$LOG" | head -50
```

API-only tests typically complete in <1s each. The extended profile has
~2000 of these — they'd run in ~15 min if fixtures were pre-provisioned.

#### 3c. Fixture Reuse

Multiple tests share the same source fixture (pytest session/module scope).
If a fixture fails, all dependent tests error immediately.

```bash
# Count cascading errors from a single fixture failure:
grep -aB5 "^ERROR" "$LOG" | grep -a "test_" | sort | uniq -c | sort -rn | head -10
```

A block of 10-20 ERRORs at the same percentage likely means one fixture failed
and all its dependent tests cascaded.

#### 3d. Skip/Filter Efficiency

```bash
# How many tests were deselected by filters:
grep -a "deselected" "$LOG" | tail -1

# Which skip groups are active:
grep -a "SKIP_.*=true" "$LOG" | head -20
```

### 4. Data Volume Analysis

Understanding what nise generates helps predict processing time.

```bash
# All nise invocations:
grep -a "nise.*report" "$LOG" | sed 's/.*nise/nise/' | sed 's/--insights-upload [^ ]*//'

# Dynamic vs static sources:
grep -ac "daily-reports" "$LOG"
echo "dynamic (daily) sources"
grep -a "nise.*report" "$LOG" | grep -vc "daily-reports"
echo "static sources"

# Date range (months of data):
grep -a "\-\-start-date" "$LOG"
```

Config reference: `nise_data_months: 4` means Dec→Mar for a March run.
Each dynamic source generates 6 CSV types × N months = 6N upload files.

### 5. Comparison Template

When comparing two runs, report:

```
                          Run A         Run B
Wall time:                ___           ___
Tests selected:           ___           ___
Passed:                   ___           ___
Failed:                   ___           ___
Errors:                   ___           ___
Source fixtures created:  ___           ___
Upload files:             ___           ___
Upload volume:            ___           ___
Poll cycles:              ___           ___
Listener CPU:             ___           ___
Processing failures:      ___           ___
Fail-fast triggers:       ___           ___
```

### 6. Common Patterns

#### "Stuck at N% for minutes"
- **Product issue**: Source ingestion blocked. Check listener logs for errors.
- Pre fail-fast: would spin for 30+ min then timeout.
- Post fail-fast: should error within ~10s if processing failed.

#### "Block of ERRORs at same percentage"
- **Test issue**: One fixture failed, cascading to all dependent tests.
- Check the first ERROR in the block for the root cause.
- If it's `SourceProcessingFailed` → product issue caught by fail-fast.
- If it's `ConnectionRefused` → infrastructure issue (route/port-forward).

#### "High error count, low failure count"
- ERRORs are typically fixture failures (setup/teardown).
- FAILEDs are assertion failures in the test body itself.
- A run with 1700 errors and 0 failures usually means masu connectivity
  was lost (port-forward died) — not real test failures.

#### "Tests pass but wall time is long"
- All time is in fixture setup (nise + ingestion polling).
- Optimization levers: fewer nise months, pre-provisioned data, listener
  CPU boost, bulk source setup.

### 7. Quick One-Liner Analysis

For a fast summary of any log file:

```bash
LOG="/tmp/iqe-extended-run.log"
echo "=== Summary ===" && grep -a "passed\|failed\|error" "$LOG" | tail -1
echo "=== Uploads ===" && grep -ac "Upload File" "$LOG" && echo "files"
echo "=== Poll Cycles ===" && grep -ac "No stats\|Not all report files" "$LOG"
echo "=== Fail-Fast ===" && grep -ac "manifest stalled\|processing failed" "$LOG"
echo "=== Errors by test ===" && grep -a "^ERROR.*test_\|^FAILED.*test_" "$LOG" \
    | sed 's/.*test_/test_/' | sed 's/ -.*//' | sort | uniq -c | sort -rn | head -5
```
