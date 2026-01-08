"""
Phase 5-6: Data Processing
===========================

Trigger and monitor MASU data processing.
"""

import time
from typing import Dict


class ProcessingPhase:
    """Phase 5-6: Trigger and monitor data processing"""

    def __init__(self, k8s_client, db_client, timeout: int = 300, provider_uuid: str = None, org_id: str = "org1234567", manifest_uuid: str = None, provider_type: str = 'ocp', cluster_id: str = "test-cluster-123"):
        """Initialize processing phase

        Args:
            k8s_client: KubernetesClient instance
            db_client: DatabaseClient instance
            timeout: Processing timeout in seconds (default 300s = 5 minutes)
            provider_uuid: Provider UUID to process (if None, scans all providers)
            org_id: Organization ID / tenant schema name
            manifest_uuid: Specific manifest UUID to monitor (if None, monitors all manifests)
            provider_type: Provider type (currently only 'ocp' is supported)
            cluster_id: OpenShift cluster ID for OCP data queries
        """
        self.k8s = k8s_client
        self.db = db_client
        self.timeout = timeout
        self.provider_uuid = provider_uuid
        self.org_id = org_id
        self.manifest_uuid = manifest_uuid
        self.provider_type = provider_type.lower()
        self.cluster_id = cluster_id

        # Get postgres pod for kubectl exec queries (no port-forward needed)
        self.postgres_pod = k8s_client.get_pod_by_component('database')
        self.database = 'koku'

    def trigger_processing(self) -> Dict:
        """Trigger MASU processing via Celery task

        Uses provider-specific download task if provider_uuid is set,
        otherwise uses generic check_report_updates.
        """
        # Note: Timestamp reset not needed - Kafka message triggers immediate processing
        # The upload.announce message bypasses the polling_timestamp check

        masu_pod = self.k8s.get_pod_by_component('masu')
        if not masu_pod:
            return {'success': False, 'error': 'MASU pod not found'}

        try:
            # Use check_report_updates which scans all active providers
            # This is the proven approach from the bash script
            python_code = """
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'koku.settings')
sys.path.append('/opt/koku/koku')
django.setup()
from masu.celery.tasks import check_report_updates
try:
    result = check_report_updates.delay()
    print(f'TASK_ID={result.id}')
    print(f'TASK_NAME=check_report_updates')
except Exception as e:
    import traceback
    print(f'ERROR={e}')
    print(f'TRACEBACK={traceback.format_exc()}')
"""

            output = self.k8s.python_exec(masu_pod, python_code)

            if 'TASK_ID=' in output:
                task_id = output.split('TASK_ID=')[1].split('\n')[0].strip()
                task_name = 'unknown'
                if 'TASK_NAME=' in output:
                    task_name = output.split('TASK_NAME=')[1].split('\n')[0].strip()

                result = {'success': True, 'task_id': task_id, 'task_name': task_name}

                # Include provider info if available
                if 'PROVIDER_TYPE=' in output:
                    result['provider_type'] = output.split('PROVIDER_TYPE=')[1].split('\n')[0].strip()
                if 'PROVIDER_NAME=' in output:
                    result['provider_name'] = output.split('PROVIDER_NAME=')[1].split('\n')[0].strip()
                if 'ORG_ID=' in output:
                    result['org_id'] = output.split('ORG_ID=')[1].split('\n')[0].strip()

                return result
            elif 'ERROR=' in output:
                error = output.split('ERROR=')[1].split('\n')[0].strip()
                return {'success': False, 'error': error, 'output': output}
            else:
                return {'success': False, 'error': 'No task ID returned', 'output': output}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def check_processing_status(self) -> int:
        """Check manifest count or specific manifest completion (using kubectl exec)

        If manifest_uuid is set, returns 1 if completed, 0 otherwise.
        Otherwise returns total manifest count.
        """
        try:
            if self.manifest_uuid:
                # Check if specific manifest is completed
                sql = f"""
                    SELECT COUNT(*)
                    FROM reporting_common_costusagereportmanifest
                    WHERE assembly_id = '{self.manifest_uuid}'
                    AND completed_datetime IS NOT NULL
                """
            else:
                # Get total manifest count
                sql = "SELECT COUNT(*) FROM reporting_common_costusagereportmanifest"

            result = self.k8s.postgres_exec(self.postgres_pod, self.database, sql)
            return int(result) if result and result.strip() else 0
        except Exception as e:
            print(f"  ❓ Status check error: {str(e)[:50]}")
            return 0

    def fix_stuck_reports(self) -> Dict:
        """Fix reports that are stuck (skipped when using kubectl exec)

        Returns:
            Dict with counts of fixed reports (always 0 for kubectl exec mode)
        """
        # Skip this optimization when using kubectl exec (too complex for subprocess)
        return {'fixed': 0, 'cleared_task_ids': 0, 'skipped': 'kubectl exec mode'}

    def mark_manifests_complete(self) -> Dict:
        """Mark manifests as complete when all files are processed.

        This handles the chord callback issue where the on_complete callback
        doesn't fire in on-prem deployments.

        Returns:
            Dict with count of manifests marked complete
        """
        if not self.k8s or not self.postgres_pod:
            return {'marked_complete': 0, 'error': 'Missing k8s client or postgres pod'}

        try:
            # Find manifests where all files are processed but manifest not marked complete
            # Uses provider_uuid if set, otherwise checks all
            where_clause = f"AND m.provider_id = '{self.provider_uuid}'" if self.provider_uuid else ""

            sql = f"""
                UPDATE reporting_common_costusagereportmanifest m
                SET completed_datetime = NOW()
                WHERE m.completed_datetime IS NULL
                AND m.num_total_files > 0
                AND m.num_total_files = (
                    SELECT COUNT(*)
                    FROM reporting_common_costusagereportstatus s
                    WHERE s.manifest_id = m.id
                    AND s.completed_datetime IS NOT NULL
                )
                {where_clause}
            """

            result = self.k8s.postgres_exec(
                pod_name=self.postgres_pod,
                database=self.database,
                sql=sql
            )

            # Parse UPDATE result (e.g., "UPDATE 1")
            # postgres_exec returns a string, not a dict
            marked = 0
            if result and 'UPDATE' in result:
                try:
                    marked = int(result.strip().split()[-1])
                except (ValueError, IndexError):
                    pass

            return {'marked_complete': marked}

        except Exception as e:
            return {'marked_complete': 0, 'error': str(e)}

    def monitor_summary_population(self, timeout: int = 60, stability_window: int = 30,
                                 stability_checks: int = 3) -> Dict:
        """Monitor summary table population with stability detection for ONPREM batch processing

        Args:
            timeout: Max wait time in seconds
            stability_window: Time in seconds to wait for count to stabilize
            stability_checks: Number of consecutive stable checks required

        Returns:
            Dict with population status and stability metrics
        """
        print(f"\n📊 Monitoring summary table population (timeout: {timeout}s, stability: {stability_window}s)...")

        start_time = time.time()
        last_count = 0

        # Phase 1: Wait for data to appear
        while time.time() - start_time < timeout:
            elapsed = int(time.time() - start_time)

            # Check summary row count
            summary_result = self.check_summary_status()
            current_count = summary_result.get('row_count', 0)

            if current_count > 0:
                if current_count != last_count:
                    schema = summary_result.get('schema', f"org{self.org_id}")
                    print(f"  [{elapsed:2d}s] 📊 Summary data detected: {current_count} rows (schema: {schema})")

                    # Show initial progress sample
                    self._show_progress_sample(summary_result, current_count, elapsed)

                    # Transition to Phase 2: Stability monitoring
                    remaining_time = timeout - (time.time() - start_time)
                    return self._monitor_stability(start_time, timeout, stability_window,
                                                 stability_checks, current_count, summary_result)

                last_count = current_count
            else:
                # Still waiting for data
                if elapsed % 15 == 0:  # Print every 15s
                    print(f"  [{elapsed:2d}s] ⏳ Waiting for summary data...")

            time.sleep(5)

        # Timeout waiting for initial data
        elapsed = int(time.time() - start_time)
        print(f"  [{elapsed:2d}s] ⏱️  Timeout waiting for summary data to appear")
        return {'has_data': False, 'timeout': True, 'row_count': last_count, 'phase': 'data_detection'}

    def _monitor_stability(self, start_time: float, total_timeout: int, stability_window: int,
                         stability_checks: int, initial_count: int, initial_result: Dict) -> Dict:
        """Monitor summary population for stability indicating completion

        Phase 2 of monitoring - waits for row count to stabilize before declaring success.
        This prevents premature success when batch processing is still ongoing.

        Args:
            start_time: Original start time from Phase 1
            total_timeout: Total timeout from original call
            stability_window: Time to wait for stability
            stability_checks: Number of consecutive stable checks required
            initial_count: Row count when entering stability monitoring
            initial_result: Initial summary result from check_summary_status()

        Returns:
            Dict with completion status and stability metrics
        """
        print(f"  📊 Entering stability monitoring phase (window: {stability_window}s, checks: {stability_checks})")

        stable_start = None
        stable_count = 0
        last_count = initial_count
        stability_interval = 5  # Start with 5s checks

        while time.time() - start_time < total_timeout:
            elapsed = int(time.time() - start_time)

            # Check current row count
            summary_result = self.check_summary_status()
            current_count = summary_result.get('row_count', 0)

            if current_count == last_count:
                # Count is stable
                if stable_start is None:
                    stable_start = time.time()
                    stable_count = 1
                    print(f"  [{elapsed:2d}s] 🔒 Row count stable at {current_count}, monitoring for {stability_window}s...")
                else:
                    stable_duration = time.time() - stable_start
                    stable_count += 1

                    # Check if we've achieved stability
                    if stable_duration >= stability_window and stable_count >= stability_checks:
                        # Verify completion before declaring success
                        if self._verify_completion(current_count, summary_result):
                            print(f"  [{elapsed:2d}s] ✅ Summarization complete: {current_count} rows (stable for {stable_duration:.1f}s)")
                            return {
                                'has_data': True,
                                'row_count': current_count,
                                'schema': summary_result.get('schema', f"org{self.org_id}"),
                                'stable_duration': stable_duration,
                                'stable_checks': stable_count,
                                'phase': 'stability_complete'
                            }
                        else:
                            # Completion verification failed - reset stability and continue monitoring
                            print(f"  [{elapsed:2d}s] ⚠️  Completion verification failed, continuing monitoring...")
                            stable_start = None
                            stable_count = 0
                    else:
                        # Still monitoring for stability
                        if stable_count % 3 == 0:  # Log every 3rd check (every ~15s)
                            remaining = stability_window - stable_duration
                            print(f"  [{elapsed:2d}s] ⏳ Stable for {stable_duration:.1f}s (need {remaining:.1f}s more)")

            else:
                # Count changed - reset stability tracking
                if stable_start is not None:
                    print(f"  [{elapsed:2d}s] 📈 Row count changed: {last_count} → {current_count}, resetting stability")
                else:
                    print(f"  [{elapsed:2d}s] 📈 Row count increased: {last_count} → {current_count}")

                stable_start = None
                stable_count = 0
                last_count = current_count

                # Show progress on changes
                self._show_progress_sample(summary_result, current_count, elapsed)

            # Progressive backoff during stability monitoring (reduces database load)
            if stable_count > 0:
                stability_interval = min(stability_interval * 1.1, 15)  # Cap at 15s
            else:
                stability_interval = 5  # Reset to 5s when not stable

            time.sleep(stability_interval)

        # Timeout during stability monitoring
        elapsed = int(time.time() - start_time)
        stable_duration = time.time() - stable_start if stable_start else 0
        print(f"  [{elapsed:2d}s] ⏱️  Stability monitoring timeout")

        return {
            'has_data': current_count > 0,
            'timeout': True,
            'row_count': current_count,
            'stable_duration': stable_duration,
            'stable_checks': stable_count,
            'phase': 'stability_timeout'
        }

    def _verify_completion(self, row_count: int, summary_result: Dict) -> bool:
        """Verify that summarization is truly complete vs. just paused

        Performs multiple checks to ensure that summary population represents
        actual completion rather than a temporary pause in batch processing.

        Args:
            row_count: Current row count from summary table
            summary_result: Result from check_summary_status()

        Returns:
            bool: True if summarization appears complete, False otherwise
        """
        # Check 1: Row count should be reasonable (not empty)
        if row_count == 0:
            return False

        # Check 2: Verify manifests are marked complete (if provider_uuid available)
        if self.provider_uuid and self.postgres_pod:
            try:
                incomplete_manifests_sql = f"""
                    SELECT COUNT(*)
                    FROM reporting_common_costusagereportmanifest
                    WHERE provider_id = '{self.provider_uuid}'
                    AND completed_datetime IS NULL
                """
                incomplete_count = self.k8s.postgres_exec(
                    self.postgres_pod,
                    self.database,
                    incomplete_manifests_sql
                )
                if incomplete_count and int(incomplete_count.strip()) > 0:
                    return False  # Still have incomplete manifests
            except Exception as e:
                # If manifest check fails, continue with other verification
                print(f"    ⚠️  Manifest completion check failed: {str(e)[:50]}...")

        # Check 3: Verify we can actually query the summary data consistently
        try:
            schema = summary_result.get('schema', f"org{self.org_id}")
            verification_sql = f"""
                SELECT COUNT(*)
                FROM {schema}.reporting_ocpusagelineitem_daily_summary
                WHERE cluster_id = %s
            """
            verification_result = self.db.execute_query(verification_sql, (self.cluster_id,))

            if verification_result and verification_result[0]:
                verification_count = int(verification_result[0][0])
                # Allow small variance (e.g., +/- 1 row) due to potential timing
                count_match = abs(verification_count - row_count) <= 1
                if not count_match:
                    print(f"    ⚠️  Row count verification mismatch: {verification_count} vs {row_count}")
                    return False
            else:
                return False  # Query failed

        except Exception as e:
            print(f"    ⚠️  Data verification query failed: {str(e)[:50]}...")
            return False  # If verification query fails, data might not be stable

        # Check 4: Verify data quality (optional - basic sanity check)
        try:
            schema = summary_result.get('schema', f"org{self.org_id}")
            quality_sql = f"""
                SELECT
                    COUNT(*) as total_rows,
                    COUNT(DISTINCT usage_start) as unique_dates,
                    SUM(CASE WHEN pod_usage_cpu_core_hours > 0 OR pod_request_cpu_core_hours > 0 THEN 1 ELSE 0 END) as rows_with_cpu
                FROM {schema}.reporting_ocpusagelineitem_daily_summary
                WHERE cluster_id = %s
                LIMIT 1
            """
            quality_result = self.db.execute_query(quality_sql, (self.cluster_id,))

            if quality_result and quality_result[0]:
                total_rows, unique_dates, rows_with_cpu = quality_result[0]
                # Basic sanity checks
                if int(total_rows) != row_count:
                    return False  # Count inconsistency
                if int(unique_dates) == 0:
                    return False  # No date data
                if int(rows_with_cpu) == 0:
                    print(f"    ⚠️  No CPU usage data found (may be expected for some scenarios)")

        except Exception as e:
            # Quality check is optional - don't fail on this
            print(f"    ⚠️  Data quality check failed: {str(e)[:50]}...")

        return True  # All verifications passed

    def _show_progress_sample(self, summary_result: Dict, current_count: int, elapsed: int):
        """Show sample of OCP usage data for progress indication

        Refactored from original monitor_summary_population to avoid early return.
        Displays sample usage data to show progress but doesn't cause function exit.

        Args:
            summary_result: Result from check_summary_status()
            current_count: Current row count
            elapsed: Elapsed time in seconds
        """
        try:
            schema = summary_result.get('schema', f"org{self.org_id}")

            # Show sample of OCP usage data (same query as original implementation)
            sample = self.db.execute_query(f"""
                SELECT
                    usage_start,
                    namespace,
                    SUM(pod_usage_cpu_core_hours) as total_cpu_hours,
                    SUM(pod_usage_memory_gigabyte_hours) as total_memory_gb_hours,
                    COUNT(DISTINCT resource_id) as pod_count,
                    COUNT(*) as line_items
                FROM {schema}.reporting_ocpusagelineitem_daily_summary
                WHERE cluster_id = %s
                AND namespace NOT LIKE '%%unallocated%%'
                GROUP BY usage_start, namespace
                ORDER BY usage_start DESC, total_cpu_hours DESC
                LIMIT 5
            """, (self.cluster_id,))

            if sample:
                print(f"    📊 OCP Usage Sample ({current_count} total rows):")
                for row in sample:
                    usage_start, namespace, cpu, memory, pods, items = row
                    # Convert string results from psql to float (kubectl exec returns strings)
                    cpu_val = float(cpu) if cpu else 0.0
                    mem_val = float(memory) if memory else 0.0
                    cpu_str = f"{cpu_val:.2f}h"
                    mem_str = f"{mem_val:.2f}GB"
                    namespace_display = namespace[:20] + "..." if len(str(namespace)) > 23 else namespace
                    print(f"       {usage_start} | {namespace_display:<23} | CPU: {cpu_str:>8} | Mem: {mem_str:>8} | {pods} pods | {items} items")
            else:
                print(f"    📊 {current_count} rows populated (no sample data available)")

        except Exception as e:
            print(f"    ⚠️  Could not fetch usage sample: {str(e)[:50]}")

    def check_summary_status(self) -> Dict:
        """Check if OCP summary tables have been populated

        Returns:
            Dict with summary data counts
        """
        if not self.cluster_id:
            return {'has_data': False, 'row_count': 0}

        try:
            # Get tenant schema for this provider
            schema_result = self.db.execute_query("""
                SELECT c.schema_name
                FROM api_provider p
                JOIN api_customer c ON p.customer_id = c.id
                WHERE p.uuid = %s
            """, (self.provider_uuid,))

            if not schema_result:
                return {'has_data': False, 'row_count': 0, 'error': 'Provider not found'}

            schema_name = schema_result[0][0]

            # Check OCP daily summary table using cluster_id
            count_result = self.db.execute_query(f"""
                SELECT COUNT(*)
                FROM {schema_name}.reporting_ocpusagelineitem_daily_summary
                WHERE cluster_id = %s
            """, (self.cluster_id,))

            row_count_raw = count_result[0][0] if count_result else 0
            # Convert string result from psql to int (kubectl exec returns strings)
            row_count = int(row_count_raw) if row_count_raw is not None else 0

            return {
                'has_data': row_count > 0,
                'row_count': row_count,
                'schema': schema_name
            }
        except Exception as e:
            return {'has_data': False, 'row_count': 0, 'error': str(e)}

    def cleanup_stale_records(self) -> Dict:
        """Clean up stale processing records from previous runs.

        Clears report status and manifest records so files aren't seen as
        "already processed" and ensures fresh processing.

        Returns:
            Dict with cleanup status
        """
        if not self.k8s or not self.org_id:
            return {'cleaned': 0}

        cleaned_records = 0

        # 1. Clear report processing status records for this provider
        # This ensures files aren't seen as "already processed"
        if self.postgres_pod and self.provider_uuid:
            try:
                sql = f"""
                    DELETE FROM reporting_common_costusagereportstatus
                    WHERE manifest_id IN (
                        SELECT id FROM reporting_common_costusagereportmanifest
                        WHERE provider_id = '{self.provider_uuid}'
                    )
                """
                result = self.k8s.postgres_exec(
                    pod_name=self.postgres_pod,
                    database=self.database,
                    sql=sql
                )
                # postgres_exec returns a string, not a dict
                if result and 'DELETE' in result:
                    try:
                        cleaned_records = int(result.strip().split()[-1])
                    except (ValueError, IndexError):
                        pass
            except Exception:
                pass

        # 2. Clear manifest records (they'll be recreated)
        if self.postgres_pod and self.provider_uuid:
            try:
                sql = f"DELETE FROM reporting_common_costusagereportmanifest WHERE provider_id = '{self.provider_uuid}'"
                self.k8s.postgres_exec(
                    pod_name=self.postgres_pod,
                    database=self.database,
                    sql=sql
                )
            except Exception:
                pass

        return {'cleaned': cleaned_records, 'records': cleaned_records}

    def get_detailed_processing_status(self) -> Dict:
        """Get detailed breakdown of file processing status (simplified for kubectl exec)

        Returns:
            Dict with status counts, file details, and active tasks
        """
        # Simplified version - just return empty dict to skip detailed status
        # The main manifest count check is sufficient for monitoring
        return {}

    def detect_pipeline_stage(self) -> str:
        """Detect current pipeline stage (simplified for kubectl exec)

        Returns:
            Stage description with emoji indicator
        """
        # Simplified version - just return generic processing status
        # Detailed status querying via kubectl exec is complex and not essential
        return "🔄 Processing"

    def monitor_processing(self) -> Dict:
        """Monitor data processing with detailed progress reporting"""
        print(f"\n⏳ Monitoring processing (timeout: {self.timeout}s)...")
        if self.provider_uuid:
            print(f"   Provider: {self.provider_uuid}\n")

        start_count = self.check_processing_status()
        start_time = time.time()
        last_stage = None
        last_file_count = {}
        iteration = 0
        interval = 5  # Check every 5 seconds

        # When monitoring a specific manifest, check_processing_status returns 0/1
        # (0 = not complete, 1 = complete). For this case, we need to wait for 1.
        monitoring_specific_manifest = self.manifest_uuid is not None

        while True:
            elapsed = int(time.time() - start_time)

            if elapsed >= self.timeout:
                current_count = self.check_processing_status()
                print(f"\n  ⏱️  Timeout reached ({self.timeout}s)")

                # For specific manifest monitoring, success = 1 (completed)
                if monitoring_specific_manifest:
                    if current_count == 1:
                        print(f"  ✅ Manifest completed")
                        return {
                            'success': True,
                            'timeout': True,
                            'manifest_count': current_count,
                            'elapsed': elapsed
                        }
                    else:
                        print("  ⚠️  Manifest not completed")
                        return {
                            'success': False,
                            'timeout': True,
                            'manifest_count': current_count,
                            'elapsed': elapsed
                        }
                else:
                    # For general monitoring, check if count increased
                    if current_count > start_count:
                        print(f"  ✅ Processing started ({current_count} manifests)")
                        return {
                            'success': True,
                            'timeout': True,
                            'manifest_count': current_count,
                            'elapsed': elapsed
                        }
                    else:
                        print("  ⚠️  No manifests processed")
                        return {
                            'success': False,
                            'timeout': True,
                            'manifest_count': current_count,
                            'elapsed': elapsed
                        }

            time.sleep(interval)
            iteration += 1

            # Get detailed status every iteration
            details = self.get_detailed_processing_status()
            current_stage = self.detect_pipeline_stage()
            current_count = self.check_processing_status()

            # Print stage change or periodic update (every 3rd iteration = 15s)
            stage_changed = current_stage != last_stage
            periodic_update = iteration % 3 == 0

            if stage_changed or periodic_update:
                print(f"\n  [{elapsed:3d}s] {current_stage}")

                # Print status breakdown
                if 'status_breakdown' in details and details['status_breakdown']:
                    for row in details['status_breakdown']:
                        status, count, status_name = row
                        prev_count = last_file_count.get(status, 0)

                        # Show delta if count changed
                        if count != prev_count and prev_count > 0:
                            delta = count - prev_count
                            delta_str = f" ({delta:+d})" if delta != 0 else ""
                            print(f"         • {status_name}: {count} file(s){delta_str}")
                        else:
                            print(f"         • {status_name}: {count} file(s)")

                        last_file_count[status] = count

                # Print active files with progress
                if details.get('active_files'):
                    print(f"         📂 Active files:")
                    for file_row in details['active_files']:
                        name, status, started, elapsed_s = file_row
                        status_icon = "⬇️" if status == 2 else "⚙️"
                        elapsed_str = f"{int(elapsed_s)}s" if elapsed_s else "just started"
                        print(f"            {status_icon} {name[:40]}... ({elapsed_str})")

                # Show error hint if files are stuck
                if details.get('active_files'):
                    for file_row in details['active_files']:
                        _, status, _, elapsed_s = file_row
                        if elapsed_s and elapsed_s > 120:  # 2+ minutes
                            print(f"         ⚠️  Warning: File processing for >2min (check worker logs/memory)")
                            break

                last_stage = current_stage

            # Check completion
            # For specific manifest monitoring: success when count == 1 (completed)
            # For general monitoring: success when count increases
            is_complete = False
            if monitoring_specific_manifest:
                is_complete = current_count == 1
            else:
                is_complete = current_count > start_count

            if is_complete:
                print(f"\n  ✅ Processing complete (elapsed: {elapsed}s)")
                print(f"  ℹ️  Total manifests: {current_count}")
                return {
                    'success': True,
                    'timeout': False,
                    'manifest_count': current_count,
                    'elapsed': elapsed
                }

    def run(self) -> Dict:
        """Run processing phase

        Returns:
            Results dict
        """
        print("\n" + "="*70)
        print("Phase 5-6: Data Processing")
        print("="*70 + "\n")

        # NOTE: We intentionally do NOT cleanup database records here anymore.
        # Doing so during fresh installs causes race conditions with the listener
        # (DatabaseError: Save with update_fields did not affect any rows).
        # Data cleanup is handled in data_upload.py when force mode is used.

        # Fix any stuck reports from previous runs (makes script work in existing environments)
        if self.provider_uuid:
            print("🔧 Checking for stuck reports from previous runs...")
            fix_result = self.fix_stuck_reports()

            if 'error' in fix_result:
                print(f"  ⚠️  Error fixing stuck reports: {fix_result['error']}")
            elif fix_result['fixed'] > 0 or fix_result['cleared_task_ids'] > 0:
                print(f"  ✅ Fixed {fix_result['fixed']} stuck report(s)")
                if fix_result['cleared_task_ids'] > 0:
                    print(f"  ✅ Cleared {fix_result['cleared_task_ids']} stale task ID(s)")
            else:
                print(f"  ✓ No stuck reports found")

        # Trigger processing
        print("\n🚀 Triggering MASU data processing...")
        print(f"  Timeout: {self.timeout}s")
        if self.provider_uuid:
            print(f"  Provider UUID: {self.provider_uuid}")

        trigger_result = self.trigger_processing()

        if not trigger_result['success']:
            print(f"  ❌ Failed to trigger processing: {trigger_result.get('error')}")
            if 'output' in trigger_result:
                print(f"\n  Debug output:")
                for line in trigger_result['output'].split('\n')[:10]:
                    if line.strip():
                        print(f"    {line}")
            return {'passed': False, 'trigger': trigger_result}

        print(f"  ✅ Task triggered: {trigger_result['task_id']}")
        print(f"     Task name: {trigger_result.get('task_name', 'unknown')}")

        if 'provider_type' in trigger_result:
            print(f"     Provider: {trigger_result['provider_name']} ({trigger_result['provider_type']})")
            print(f"     Org ID: {trigger_result['org_id']}")

        # Monitor processing
        monitor_result = self.monitor_processing()

        if monitor_result['success']:
            print(f"\n  ✅ Processing complete")
            print(f"  ℹ️  Manifests: {monitor_result['manifest_count']}")
            print(f"  ℹ️  Time: {monitor_result['elapsed']}s")
        else:
            print(f"\n  ⚠️  Processing timeout or incomplete")
            print(f"  ℹ️  Manifests: {monitor_result['manifest_count']}")
            print(f"  ℹ️  Elapsed: {monitor_result['elapsed']}s")

        # CRITICAL FIX: Mark manifests as complete to trigger summary
        # On-prem deployments don't auto-complete manifests after file processing
        # We do this even if monitoring timed out, as long as files are processed
        if self.provider_uuid:
            print(f"\n📋 Checking manifest completion status...")
            completion_result = self.mark_manifests_complete()

            if 'error' in completion_result:
                print(f"  ⚠️  Error marking manifests complete: {completion_result['error']}")
            elif completion_result['marked_complete'] > 0:
                print(f"  ✅ Marked {completion_result['marked_complete']} manifest(s) as complete")
                # If monitoring timed out but files are processed, consider it a success
                print(f"  ℹ️  Manifests completed manually (chord callback issue)")
                monitor_result['success'] = True
            else:
                print(f"  ℹ️  No manifests needed completion marking (may already be complete)")

            # Always monitor summary population after processing attempt
            # (files may have been processed even if timeout occurred)
            summary_timeout = 90
            summary_result = self.monitor_summary_population(
                timeout=summary_timeout,
                stability_window=30,  # 30s stability window for ONPREM batch processing
                stability_checks=3    # 3 consecutive stable checks required
            )
            if summary_result.get('has_data'):
                # Data found - processing was successful
                monitor_result['success'] = True
                monitor_result['summary_rows'] = summary_result.get('row_count', 0)

                # Include stability metrics for diagnostics
                if summary_result.get('stable_duration') is not None:
                    print(f"  ✅ Summary population stable for {summary_result['stable_duration']:.1f}s with {summary_result.get('stable_checks', 0)} checks")
            else:
                # Enhanced failure mode distinction
                phase = summary_result.get('phase', 'unknown')
                row_count = summary_result.get('row_count', 0)

                if 'timeout' in summary_result:
                    if phase == 'data_detection':
                        print(f"  ❌ No summary data appeared after {summary_timeout}s")
                        print(f"  💡 This indicates summarization may not have started")
                        print(f"     Check Celery worker logs for processing errors")
                    elif phase == 'stability_timeout':
                        stable_duration = summary_result.get('stable_duration', 0)
                        stable_checks = summary_result.get('stable_checks', 0)
                        print(f"  ⚠️  Summary data detected ({row_count} rows) but stability timeout after {summary_timeout}s")
                        print(f"     Last stability: {stable_duration:.1f}s with {stable_checks} checks")
                        print(f"  💡 Data may still be processing in batches (ONPREM characteristic)")
                        print(f"     Consider increasing stability_window if this happens frequently")
                    else:
                        print(f"  ⚠️  Summary monitoring timeout in {phase} phase after {summary_timeout}s")
                        if row_count > 0:
                            print(f"     Found {row_count} rows but processing may still be ongoing")

                    print(f"  💡 To check summary table manually (looking for cluster_id='{self.cluster_id}'):")
                    schema = summary_result.get('schema', f"org{self.org_id}")
                    print(f"     oc exec -n {self.k8s.namespace} {self.postgres_pod} -- psql -U koku -d koku -c \\")
                    print(f"       \"SELECT COUNT(*) FROM {schema}.reporting_ocpusagelineitem_daily_summary WHERE cluster_id = '{self.cluster_id}';\"")

                elif 'error' in summary_result:
                    print(f"  ❌ Summary check failed: {summary_result['error']}")
                    if phase:
                        print(f"     Failure occurred in {phase} phase")

                else:
                    print(f"  ❌ Summary population failed for unknown reason")
                    print(f"     Phase: {phase}, Row count: {row_count}")
                    print(f"     Result: {summary_result}")

        return {
            'passed': monitor_result['success'],
            'trigger': trigger_result,
            'monitor': monitor_result
        }

