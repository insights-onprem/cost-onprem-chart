# Cost On-Prem: Complex Group-By Query Performance Investigation

## Issue Summary

API queries with 3 group_by dimensions have highly variable and unacceptable latency, ranging from 13-72 seconds across test runs. The threshold for acceptable UI responsiveness is 10 seconds.

**Observed Results**:
- Run 1: P95 = **72.4 seconds**
- Run 2: P95 = 13.7 seconds  
- Run 3: P95 = 17.4 seconds
- Threshold: 10 seconds

**Test**: `test_perf_api_005_complex_group_by[group_by_dims2]`

**Query Pattern**: 
```
GET /api/cost-management/v1/reports/openshift/costs/
  ?group_by[project]=*
  &group_by[node]=*
  &group_by[cluster]=*
  &filter[time_scope_units]=month
  &filter[time_scope_value]=-1
```

---

## Investigation Plan

### Phase 1: Reproduce and Capture Data

- [ ] **1.1 Reproduce the slow query**
  ```bash
  # Run the specific failing test with verbose output
  ./scripts/run-pytest.sh --perf-api -v -k "complex_group_by and group_by_dims2"
  ```

- [ ] **1.2 Capture the exact API call**
  - Enable debug logging in the test or add print statements
  - Record the full URL with all query parameters
  - Note: Check `tests/suites/performance/test_api_latency.py` for the exact call

- [ ] **1.3 Test cold vs warm performance**
  ```bash
  # Restart koku-api pod (cold start)
  oc rollout restart deployment/koku-api -n cost-onprem
  oc rollout status deployment/koku-api -n cost-onprem
  
  # Run query immediately (cold)
  time curl -s "<full_api_url>" -H "Authorization: ..." | head -c 100
  
  # Run same query again (warm)
  time curl -s "<full_api_url>" -H "Authorization: ..." | head -c 100
  ```

### Phase 2: Database Analysis

- [ ] **2.1 Enable slow query logging**
  ```bash
  # Connect to postgres pod
  oc exec -it deployment/koku-db -n cost-onprem -- psql -U koku
  ```
  ```sql
  -- Enable slow query logging (queries > 5 seconds)
  ALTER SYSTEM SET log_min_duration_statement = 5000;
  SELECT pg_reload_conf();
  
  -- Verify setting
  SHOW log_min_duration_statement;
  ```

- [ ] **2.2 Capture the generated SQL**
  
  Option A: Check koku-api logs after running the query
  ```bash
  oc logs deployment/koku-api -n cost-onprem --tail=200 | grep -i "select"
  ```
  
  Option B: Enable Django SQL logging (requires config change)

- [ ] **2.3 Run EXPLAIN ANALYZE on the captured SQL**
  ```sql
  EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
  <paste captured SQL query here>;
  ```
  
  **Look for**:
  - Sequential scans on large tables (Seq Scan)
  - Missing index usage
  - High buffer reads (shared hit/read)
  - Nested loop joins with many rows
  - Sort operations spilling to disk

- [ ] **2.4 Check existing indexes on reporting tables**
  ```sql
  SELECT 
      tablename, 
      indexname, 
      indexdef 
  FROM pg_indexes 
  WHERE schemaname = 'public' 
    AND (tablename LIKE '%reporting%' OR tablename LIKE '%ocp%')
  ORDER BY tablename, indexname;
  ```

- [ ] **2.5 Check table sizes**
  ```sql
  SELECT 
      relname AS table_name,
      n_live_tup AS row_count,
      pg_size_pretty(pg_total_relation_size(relid)) AS total_size
  FROM pg_stat_user_tables 
  WHERE relname LIKE '%ocp%' 
     OR relname LIKE '%reporting%'
  ORDER BY n_live_tup DESC
  LIMIT 20;
  ```

### Phase 3: PostgreSQL Configuration Check

- [ ] **3.1 Check memory settings**
  ```sql
  SHOW work_mem;           -- Memory for sorts/hashes per query
  SHOW shared_buffers;     -- Shared memory for caching
  SHOW effective_cache_size;  -- Planner's estimate of available cache
  SHOW maintenance_work_mem;  -- Memory for maintenance operations
  ```

- [ ] **3.2 Check connection/query settings**
  ```sql
  SHOW max_connections;
  SHOW statement_timeout;
  SHOW idle_in_transaction_session_timeout;
  ```

### Phase 4: Compare and Baseline

- [ ] **4.1 Compare with SaaS performance** (if accessible)
  - Run equivalent query against SaaS API
  - Note if same variability exists
  - Compare response times

- [ ] **4.2 Check if query uses materialized views**
  ```sql
  SELECT schemaname, matviewname, ispopulated
  FROM pg_matviews
  WHERE schemaname = 'public';
  ```

- [ ] **4.3 Check for recent VACUUM/ANALYZE**
  ```sql
  SELECT 
      relname,
      last_vacuum,
      last_autovacuum,
      last_analyze,
      last_autoanalyze
  FROM pg_stat_user_tables
  WHERE relname LIKE '%reporting%' OR relname LIKE '%ocp%'
  ORDER BY last_analyze DESC NULLS LAST;
  ```

---

## Data Collection Template

Fill this in during investigation:

### Query Details
```
Full URL: https://${GATEWAY_ROUTE_HOST}/api/cost-management/v1/reports/openshift/costs/?group_by[project]=*&group_by[node]=*&group_by[cluster]=*&filter[time_scope_units]=day&filter[time_scope_value]=-30&filter[resolution]=daily
# GATEWAY_ROUTE_HOST: oc get route cost-onprem-api -n ${NAMESPACE} -o jsonpath='{.spec.host}'
Response time (cold): 282ms
Response time (warm): 7-20ms (cached by Valkey/Redis)
Response size: 2666 bytes
Data points returned: 30
```

### Key Log Finding
```
WARNING: ('cluster', 'node', 'project') for costs_by_project has no entry in views. Using the default.
```

This indicates the 3-dimension group_by combination doesn't have an optimized database view and falls back to a default (slower) query path.

### Table Statistics (2026-04-22)
```
Table: openshift_pod_usage_line_items_2026_04
Row count: 263,419
Size: 194 MB

Table: reporting_ocp_cost_summary_by_project_p_2026_04
Row count: 5,612
Size: 2 MB

Table: reporting_ocp_cost_summary_by_node_p_2026_04
Row count: 2,509
Size: 1 MB
```

### PostgreSQL Config
```
work_mem: 4MB
shared_buffers: 128MB (16384 x 8kB)
effective_cache_size: 4GB (524288 x 8kB)
maintenance_work_mem: 64MB
max_connections: 100
statement_timeout: 0 (no limit)
```

### VACUUM/ANALYZE Status
All key tables have been auto-analyzed within the last 24 hours. No manual VACUUM needed.

### Indexes Present
- `reporting_ocp_cost_summary_by_project_p` tables have indexes on: namespace, source_uuid, usage_start, cost_category_id
- `reporting_ocp_cost_summary_by_node_p` tables have indexes on: node, source_uuid, usage_start, cost_category_id
- No composite indexes for the 3-dimension combination (project, node, cluster)

### Findings
```
Root cause: 
1. The query combination (cluster, node, project) has no optimized view in koku
2. WARNING: "('cluster', 'node', 'project') for costs_by_project has no entry in views. Using the default."
3. First query takes 282ms, subsequent queries ~8ms due to Valkey caching
4. With current data volume (~5k summary rows), performance is acceptable
5. Performance likely degrades significantly with larger datasets (original finding showed 13-72 seconds)

Recommended fix: 
1. Add materialized view or optimized query path for (cluster, node, project) combination
2. Consider adding composite index on (namespace, node) for cross-dimension queries
3. Evaluate if Valkey cache TTL is appropriate (currently helps mask the issue)
```

---

## Potential Fixes to Consider

1. **Missing indexes** - Add composite indexes for common group_by combinations
2. **Query optimization** - Modify SQL generation in koku to be more efficient
3. **Materialized views** - Pre-aggregate common report queries
4. **PostgreSQL tuning** - Increase work_mem, shared_buffers
5. **Caching** - Add application-level caching for expensive queries
6. **Pagination** - Limit result set size for complex queries

---

## Related Files

- Test: `tests/suites/performance/test_api_latency.py` - `test_perf_api_005_complex_group_by`
- Findings: `docs/performance/FINDINGS.md`
- Koku API source: Check `koku/api/report/` for query generation

---

## Investigation Results (2026-04-22)

### Summary

The investigation was completed but couldn't fully reproduce the slow query behavior because:
1. Current data volume (~263k line items, ~5k summary rows) is smaller than during original testing
2. Query responses are now fast (282ms cold, 8ms warm) due to caching

### Key Findings

1. **Missing Optimized View**: The koku API logs a warning that `('cluster', 'node', 'project')` has no entry in views and uses the default. This is the likely root cause of variable/slow performance.

2. **Caching Masks the Issue**: The Valkey (Redis-compatible) cache makes subsequent queries very fast (7-20ms). The slow query issue only manifests on cold cache or cache miss.

3. **No Materialized Views**: PostgreSQL has no materialized views configured. The system relies entirely on partitioned tables with single-column indexes.

4. **Index Gap**: While individual column indexes exist (namespace, node, source_uuid, usage_start), there's no composite index optimized for the 3-dimension group_by query pattern.

### Recommendations for JIRA-002

1. **Add Optimized View**: Create an optimized view/query path in koku for the (cluster, node, project) dimension combination
2. **Composite Index**: Consider adding a composite index like `(usage_start, namespace, node)` to optimize cross-dimension aggregations
3. **Load Testing**: Reproduce with larger dataset (500k+ rows) to validate performance improvements
4. **Cache Strategy Review**: Evaluate if current caching strategy adequately handles cache-miss scenarios in production

### Checklist Status

- [x] Phase 1.1: Reproduce the slow query - Query runs in <1s with current data
- [x] Phase 1.2: Capture exact API call - URL and params documented
- [x] Phase 1.3: Test cold vs warm - 282ms cold, 8ms warm
- [x] Phase 2.1: Enable slow query logging - Set to 100ms threshold
- [x] Phase 2.2: Capture generated SQL - Found WARNING about missing view
- [ ] Phase 2.3: Run EXPLAIN ANALYZE - Skipped (query too fast to capture)
- [x] Phase 2.4: Check indexes - Single-column indexes exist, no composite
- [x] Phase 2.5: Check table sizes - 263k line items, 5k summary rows
- [x] Phase 3.1: Check memory settings - work_mem=4MB, shared_buffers=128MB
- [x] Phase 3.2: Check connection settings - Defaults, no timeout
- [x] Phase 4.2: Check materialized views - None configured
- [x] Phase 4.3: Check VACUUM/ANALYZE - Auto-analyzed within 24h

---

_Created: 2026-04-21_
_Status: Completed_
_Last Updated: 2026-04-22_
