"""
E2E Validator CLI
=================

Main command-line interface for E2E validation.
"""

import click
import time
import os
from datetime import datetime, timedelta

# Suppress urllib3 SSL warnings (expected in local/test environments with self-signed certs)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .clients.kubernetes import KubernetesClient
from .clients.kubectl_database import KubectlDatabaseClient
from .clients.nise import NiseClient
from .clients.kafka_producer import KafkaProducerClient
from .clients.sources_api import SourcesAPIClient
from .phases.preflight import PreflightPhase
from .phases.provider import ProviderPhase
from .phases.data_upload import DataUploadPhase
from .phases.processing import ProcessingPhase
from .phases.smoke_validation import SmokeValidationPhase
from .phases.deployment_validation import DeploymentValidationPhase
from .phases.kafka_validation import KafkaValidationPhase


def discover_sources_api_info(namespace: str) -> tuple:
    """Auto-discover Sources API service and route info

    Args:
        namespace: Kubernetes namespace

    Returns:
        Tuple of (route_url, service_ip, route_hostname) or (None, None, None)
    """
    try:
        from kubernetes import client

        # Get route info
        custom_api = client.CustomObjectsApi()
        route = custom_api.get_namespaced_custom_object(
            group="route.openshift.io",
            version="v1",
            namespace=namespace,
            plural="routes",
            name="sources-api"
        )
        route_host = route['spec']['host']
        tls = route['spec'].get('tls')
        scheme = 'https' if tls else 'http'
        route_url = f"{scheme}://{route_host}"

        # Get service ClusterIP (for --resolve fallback)
        v1 = client.CoreV1Api()
        service = v1.read_namespaced_service(
            name="cost-onprem-sources-api",
            namespace=namespace
        )
        service_ip = service.spec.cluster_ip

        print(f"  ✓ Auto-discovered Sources API:")
        print(f"    Route: {route_url}")
        print(f"    Service IP: {service_ip} (for --resolve if needed)")

        return (route_url, service_ip, route_host)
    except Exception as e:
        print(f"  ℹ️  Sources API not found: {e}")
        return (None, None, None)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option('--namespace', default='cost-onprem', help='Kubernetes namespace')
@click.option('--org-id', default='org1234567', help='Organization ID')
@click.option('--provider-type', type=click.Choice(['aws', 'azure', 'gcp', 'ocp'], case_sensitive=False), default='aws', help='Cloud provider type to test')
@click.option('--skip-migrations', is_flag=True, help='Skip database migrations')
@click.option('--skip-provider', is_flag=True, help='Skip provider setup')
@click.option('--infrastructure-only', is_flag=True, help='Infrastructure testing only (skip data upload/processing/validation) - WARNING: incomplete E2E test')
@click.option('--skip-tests', is_flag=True, help='Skip IQE tests (only allowed in smoke-test mode)')
@click.option('--diagnose', is_flag=True, help='Run infrastructure diagnostics (auto-runs on failure)')
@click.option('--quick', is_flag=True, help='Quick mode (skip all setup)')
@click.option('--smoke-test', is_flag=True, help='Smoke test mode: fast validation with minimal data')
@click.option('--force', is_flag=True, help='Force data regeneration even if data exists')
@click.option('--timeout', default=300, help='Processing timeout (seconds)')
@click.option('--scenarios', default='all', help='Comma-separated scenario list or "all"')
@click.pass_context
def main(ctx, namespace, org_id, provider_type, skip_migrations, skip_provider, infrastructure_only,
         skip_tests, diagnose, quick, smoke_test, force, timeout, scenarios):
    """
    E2E Validation Suite for Cost Management

    Database-agnostic testing with standalone validation
    """

    # Track exit code
    exit_code = 0

    # Quick mode skips slow operations but RUNS critical setup and data upload
    if quick:
        skip_migrations = True
        # NOTE: We don't skip provider because billing source must be configured
        # for MASU to process data. Provider setup is fast (~2 seconds).

    # Smoke test optimizations: skip expensive operations but KEEP validation
    if smoke_test:
        timeout = 180  # Use 180s for smoke test (processing + summarization)
        print("\n🚀 Smoke test mode enabled:")
        print("   - Running standalone validation (YAML-driven)")
        print("   - Processing timeout: 180s")
        print("   - Minimal data (1 pod, 1 node, 1 cluster)")
        print("   - Estimated completion: ~90 seconds")
        print("")

    # Infrastructure-only mode warning
    if infrastructure_only:
        print("\n⚠️  WARNING: Infrastructure-only mode enabled")
        print("   - Data upload, processing, and validation will be SKIPPED")
        print("   - This is NOT a complete E2E test")
        print("   - Use only for quick infrastructure checks (preflight, Kafka, Sources API)")
        print("")

    # Normalize provider type to uppercase for internal use
    provider_type = provider_type.upper()

    # Parse scenarios
    if scenarios == 'all':
        scenario_list = list(NiseClient.SCENARIOS.keys())
    else:
        scenario_list = [s.strip() for s in scenarios.split(',')]

    # Print header
    print("\n" + "="*70)
    print("  Cost Management E2E Validation Suite")
    print("  Database-Agnostic Testing (Nise + IQE)")
    print("="*70)
    print(f"\nConfiguration:")
    print(f"  Namespace:  {namespace}")
    print(f"  Org ID:     {org_id}")
    print(f"  Provider:   {provider_type}")
    print(f"  Mode:       {'SMOKE TEST (fast 4-row CSV)' if smoke_test else 'FULL VALIDATION (nise)'}")
    print(f"  Scenarios:  {len(scenario_list)}")
    print(f"  Timeout:    {timeout}s")
    print()

    start_time = time.time()
    results = {}

    try:
        # Initialize clients
        print("🔧 Initializing clients...")
        k8s = KubernetesClient(namespace=namespace)
        print("  ✓ Kubernetes client")

        # Dynamically discover PostgreSQL pod by label
        print("  🔍 Discovering PostgreSQL pod...")
        postgres_pod = k8s.discover_postgresql_pod()
        if not postgres_pod:
            print("  ❌ Failed to discover PostgreSQL pod with label app.kubernetes.io/component=database")
            ctx.exit(1)

        print(f"  ✓ Discovered PostgreSQL pod: {postgres_pod}")

        # Dynamically discover database secret from postgres pod
        print("  🔍 Discovering database secret...")
        db_secret_info = k8s.discover_database_secret(pod_name=postgres_pod)
        if not db_secret_info:
            print(f"  ❌ Failed to discover database secret from {postgres_pod} pod")
            ctx.exit(1)

        print(f"  ✓ Discovered secret: {db_secret_info['secret_name']}")

        # Get DB credentials from discovered secret
        db_password = k8s.get_secret(db_secret_info['secret_name'], 'koku-password')
        if not db_password:
            print(f"  ❌ Failed to get password from secret {db_secret_info['secret_name']} (key: koku-password)")
            ctx.exit(1)

        # Get database user from secret
        db_user = k8s.get_secret(db_secret_info['secret_name'], 'koku-user')
        if not db_user:
            print(f"  ❌ Failed to get username from secret {db_secret_info['secret_name']} (key: koku-user)")
            ctx.exit(1)

        # Use kubectl exec for database queries - no port-forward needed!
        # This is more resilient to network issues than direct TCP connections
        db = KubectlDatabaseClient(
            k8s=k8s,
            pod_name=postgres_pod,
            database='koku',
            user=db_user
        )
        print("  ✓ Database client (kubectl exec - no port-forward needed)")

        nise = NiseClient()
        print("  ✓ Nise client")

        # Initialize Sources API client (production flow)
        print("\n📡 Discovering Sources API...")
        route_url, service_ip, route_hostname = discover_sources_api_info(namespace)
        sources_api = None

        if service_ip:
            try:
                # Use internal service URL (curl will be executed from within the cluster)
                service_url = f"http://cost-onprem-sources-api:8000"
                print(f"    Using internal service: {service_url}")
                print(f"    Requests will be executed from within the cluster")

                sources_api = SourcesAPIClient(
                    base_url=service_url,
                    org_id=org_id,
                    k8s_client=k8s,
                    namespace=namespace
                )
                # Test connection
                source_types = sources_api.get_source_types()
                if source_types:
                    print(f"  ✓ Sources API client initialized")
                    print(f"  ℹ️  Available source types: {len(source_types)}")
                    print(f"  🎯 Will use Production Flow (HTTP → Kafka → Listener)")
                else:
                    print(f"  ⚠️  Sources API responded but returned no source types")
                    sources_api = None
            except Exception as e:
                print(f"  ⚠️  Sources API connection failed: {e}")
                import traceback
                traceback.print_exc()
                sources_api = None
        else:
            print(f"  ℹ️  Sources API not available")
            print(f"  Will use Development Flow (kubectl exec)")

        # Database queries will be done via kubectl exec (no port-forward needed!)
        print("\n✓ Database queries will use kubectl exec from within cluster")

        # Get S3 configuration for preflight checks
        # Try external route first for local execution
        s3_endpoint = None
        try:
            route_output = k8s.exec_in_pod('', ['oc', 'get', 'route', '-n', 'openshift-storage', 's3', '-o', 'jsonpath={.spec.host}'])
            if route_output and route_output.strip():
                s3_endpoint = f"https://{route_output.strip()}"
        except:
            # Fallback to internal endpoint
            s3_endpoint = "https://s3.openshift-storage.svc:443"

        bucket = "koku-bucket"

        # Fail-fast: skip remaining phases if a critical phase fails
        should_skip_remaining = False
        failed_phase = None

        # Phase 1: Preflight
        preflight = PreflightPhase(db, namespace, s3_endpoint, bucket, k8s_client=k8s, postgres_pod=postgres_pod)
        results['preflight'] = preflight.run()
        if not results['preflight']['passed'] and not results['preflight'].get('skipped'):
            print("\n❌ Pre-flight checks failed - skipping remaining phases")
            should_skip_remaining = True
            failed_phase = 'preflight'

        # Phase 2: Migrations
        from .phases.migrations import MigrationsPhase

        if should_skip_remaining:
            results['migrations'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        else:
            migrations = MigrationsPhase(k8s, db)
            results['migrations'] = migrations.run(skip=skip_migrations)
            if not results['migrations']['passed'] and not results['migrations'].get('skipped'):
                print("\n❌ Migrations failed - skipping remaining phases")
                should_skip_remaining = True
                failed_phase = 'migrations'

        # Phase 2.5: Kafka Validation (Event-Driven Processing)
        if should_skip_remaining:
            results['kafka_validation'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        else:
            kafka_validation = KafkaValidationPhase(k8s, namespace=namespace)
            kafka_results = kafka_validation.run()
            results['kafka_validation'] = {
                'passed': kafka_results.get('overall_success', False),
                'skipped': False,
                **kafka_results
            }
            # Kafka validation is informational - don't fail the pipeline if it fails
            # (This allows graceful degradation if Kafka listener isn't deployed yet)
            if not results['kafka_validation']['passed']:
                print("\n⚠️  Kafka validation failed - continuing with remaining phases")
                print("   (Kafka listener is optional - orchestrator will use chords as fallback)")

        # Phase 3: Provider (with k8s client for Django ORM operations)
        if should_skip_remaining:
            results['provider'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
            provider_uuid = None
        else:
            provider_phase = ProviderPhase(
                db,
                org_id=org_id,
                k8s_client=k8s,
                sources_api_client=sources_api,
                postgres_pod=postgres_pod
            )
            results['provider'] = provider_phase.run(skip=skip_provider, provider_type=provider_type, cluster_id='test-cluster-123')
            if not results['provider']['passed'] and not results['provider'].get('skipped'):
                print("\n❌ Provider setup failed - skipping remaining phases")
                should_skip_remaining = True
                failed_phase = 'provider'
            provider_uuid = results['provider'].get('provider_uuid')

        # Phase 4: Data Upload
        if should_skip_remaining:
            results['data_upload'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        elif infrastructure_only:
            results['data_upload'] = {'passed': False, 'skipped': True, 'reason': '--infrastructure-only'}
        else:
            from .clients.s3 import S3Client

            # Get S3 credentials (try helm chart secret name first, then fallback)
            storage_secret_name = f'{namespace}-storage-credentials'
            s3_access_key = k8s.get_secret(storage_secret_name, 'access-key')
            s3_secret_key = k8s.get_secret(storage_secret_name, 'secret-key')

            # Fallback to old secret name for backwards compatibility
            if not s3_access_key or not s3_secret_key:
                s3_access_key = k8s.get_secret('koku-storage-credentials', 'access-key')
                s3_secret_key = k8s.get_secret('koku-storage-credentials', 'secret-key')

            if not s3_access_key or not s3_secret_key:
                print("\n❌ Failed to get S3 credentials")
                should_skip_remaining = True
                failed_phase = 'data_upload'
                results['data_upload'] = {'passed': False, 'error': 'Missing S3 credentials'}
                s3_endpoint = None  # Set to None to skip the rest of data upload logic

            # Get S3 endpoint - try external route first for local execution
            # Check for external S3 route (OpenShift)
            try:
                route_output = k8s.exec_in_pod('', ['oc', 'get', 'route', '-n', 'openshift-storage', 's3', '-o', 'jsonpath={.spec.host}'])
                if route_output and route_output.strip():
                    s3_endpoint = f"https://{route_output.strip()}"
                    print(f"  ℹ️  Using external S3 route: {s3_endpoint}")
                else:
                    raise Exception("No route found")
            except:
                # Fallback: Get from MASU pod (internal endpoint - may not work locally)
                masu_pod = k8s.get_pod_by_component('masu')
                if not masu_pod:
                    print("\n❌ MASU pod not found")
                    ctx.exit(1)

                s3_endpoint_output = k8s.exec_in_pod(masu_pod, ['env'])
                s3_endpoint = None
                for line in s3_endpoint_output.split('\n'):
                    if line.startswith('S3_ENDPOINT='):
                        s3_endpoint = line.split('=', 1)[1].strip()
                        break

                if not s3_endpoint:
                    # Try common ODF S3 endpoint
                    s3_endpoint = "https://s3.openshift-storage.svc:443"
                    print(f"  ⚠️  S3_ENDPOINT not in env, using default: {s3_endpoint}")

                # Convert internal endpoints to external for local execution
                if '.svc' in s3_endpoint:
                    print(f"  ⚠️  Internal endpoint detected: {s3_endpoint}")
                    print(f"  ⚠️  Attempting to find external route...")
                    # Try to get the OpenShift cluster domain
                    try:
                        import subprocess
                        result = subprocess.run(['oc', 'whoami', '--show-server'], capture_output=True, text=True)
                        if result.returncode == 0:
                            # Extract domain from API server URL (e.g., api.stress.parodos.dev:6443 -> stress.parodos.dev)
                            server = result.stdout.strip()
                            domain = server.split('//')[1].split(':')[0].replace('api.', 'apps.')
                            s3_endpoint = f"https://s3-openshift-storage.{domain}"
                            print(f"  ✓ Using external route: {s3_endpoint}")
                    except Exception as e:
                        print(f"  ❌ Could not determine external route: {e}")

            # Create S3 client using wrapper
            s3_client = S3Client(
                endpoint_url=s3_endpoint,
                access_key=s3_access_key,
                secret_key=s3_secret_key,
                verify=False
            )

            # Create Kafka client for OCP report announcements (OCP uses push-based processing)
            kafka_client = None
            if provider_type == 'OCP':
                try:
                    kafka_client = KafkaProducerClient()
                    print(f"  ✓ Kafka producer client initialized for OCP processing")
                except Exception as e:
                    print(f"  ⚠️  Kafka client initialization failed: {e}")
                    print(f"     OCP processing may not be automatically triggered")

            # DataUploadPhase with S3 client (direct upload via HTTPS)
            data_upload = DataUploadPhase(
                s3_client.s3,
                nise,
                k8s_client=k8s,
                db_client=db,
                provider_uuid=provider_uuid,
                kafka_client=kafka_client,
                org_id=org_id,
                s3_endpoint=s3_endpoint
            )

            # Use direct AWS CUR format upload (no need for in-pod execution)
            # Use monthly boundaries that MASU expects (first day of month to first day of next month)
            now = datetime.now()
            # Current month start
            start_date = datetime(now.year, now.month, 1)
            # Next month start (end date)
            if now.month == 12:
                end_date = datetime(now.year + 1, 1, 1)
            else:
                end_date = datetime(now.year, now.month + 1, 1)

            print(f"  ℹ️  Using monthly period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

            results['data_upload'] = data_upload.upload_provider_data(
                provider_type=provider_type,
                start_date=start_date,
                end_date=end_date,
                force=force,
                smoke_test=smoke_test
            )

            # Check for S3 errors
            if 'data_upload' in results:
                if not results['data_upload'].get('success', True) and 'error' in results['data_upload']:
                    print("\n❌ Data upload failed - S3 error")
                    should_skip_remaining = True
                    failed_phase = 'data_upload'

                # Check if upload was skipped due to invalid data
                elif results['data_upload'].get('skipped'):
                    reason = results['data_upload'].get('reason', 'unknown')
                    valid = results['data_upload'].get('valid', False)

                    if reason == 'invalid_data' or not valid:
                        print("\n❌ Data upload skipped - INVALID DATA IN S3")
                        print("   Found files but no valid manifest")
                        print("   Run with --force to regenerate")
                        should_skip_remaining = True
                        failed_phase = 'data_upload'
                    elif reason == 'valid_data_exists':
                        print("\n✅ Using existing valid data")
                        # Continue with processing

        # Phase 5-6: Processing (with provider UUID for targeted download)
        if should_skip_remaining:
            results['processing'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        else:
            # Extract manifest_uuid from data_upload result for targeted monitoring
            manifest_uuid = results.get('data_upload', {}).get('manifest_uuid')
            if manifest_uuid:
                print(f"  ℹ️  Monitoring specific manifest: {manifest_uuid} (faster)")

            processing = ProcessingPhase(
                k8s,
                db,
                timeout=timeout,
                provider_uuid=provider_uuid,
                org_id=org_id,
                manifest_uuid=manifest_uuid,
                provider_type=provider_type
            )
            results['processing'] = processing.run()
            if not results['processing']['passed'] and not results['processing'].get('skipped'):
                print("\n❌ Processing failed - skipping remaining phases")
                should_skip_remaining = True
                failed_phase = 'processing'

        # Look up actual schema_name from Customer table (Koku prefixes 'org' to org_id)
        # This is needed for the Validation phase
        tenant_schema = db.get_schema_name_for_org(org_id)
        print(f"  ℹ️  Using tenant schema: {tenant_schema}")

        # Phase 7: Validation (PostgreSQL-based)
        if should_skip_remaining:
            results['validation'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        elif skip_tests:
            # Tests explicitly skipped
            results['validation'] = {'passed': True, 'skipped': True, 'reason': '--skip-tests flag'}
        else:
            # Run standalone validation (uses dynamic dates matching data generation)
            print("\n📋 Running data validation...")
            # Calculate validation dates (same as data generation)
            now = datetime.now()
            val_start_date = datetime(now.year, now.month, 1)
            # For smoke tests, we generate only 1 day of data
            val_end_date = val_start_date + timedelta(days=1) if smoke_test else (
                datetime(now.year + 1, 1, 1) if now.month == 12 else datetime(now.year, now.month + 1, 1)
            )
            validation = SmokeValidationPhase(
                db_client=db,
                namespace=namespace,
                org_id=tenant_schema,  # Use actual schema name (e.g., orgorg1234567)
                cluster_id='test-cluster-123',
                start_date=val_start_date,
                end_date=val_end_date
            )
            results['validation'] = validation.run()
            if not results['validation']['passed']:
                print("\n❌ Validation failed")
                should_skip_remaining = True
                failed_phase = 'validation'

        # Final Summary
        print("\n" + "="*70)
        print("FINAL SUMMARY")
        print("="*70)

        elapsed = time.time() - start_time
        print(f"\nTotal Time: {elapsed:.1f}s ({elapsed/60:.1f} minutes)")

        # Define critical phases that MUST pass (never be skipped or failed)
        critical_phases = ['preflight', 'provider', 'data_upload', 'processing']

        print(f"    DEBUG: smoke_test={smoke_test}")

        # Validation is critical in smoke test mode (validates costs match nise YAML)
        if smoke_test:
            critical_phases.append('validation')
            print(f"    DEBUG: Added 'validation' to critical_phases")

        # IQE tests are critical in full validation mode only
        if not smoke_test:
            critical_phases.append('iqe_tests')

        print(f"    DEBUG: final critical_phases={critical_phases}")

        # Check critical phase status
        critical_failures = []
        critical_skipped = []
        critical_passed = []

        for phase in critical_phases:
            result = results.get(phase, {'passed': False, 'skipped': False})
            # Debug: Print phase status
            print(f"    DEBUG: {phase} -> passed={result.get('passed')}, skipped={result.get('skipped')}")
            if result.get('skipped', False):
                critical_skipped.append(phase)
            elif not result.get('passed', False):
                critical_failures.append(phase)
            else:
                critical_passed.append(phase)

        # Debug: Print critical phase lists
        print(f"    DEBUG: critical_passed={critical_passed}")
        print(f"    DEBUG: critical_skipped={critical_skipped}")
        print(f"    DEBUG: critical_failures={critical_failures}")
        print(f"    DEBUG: bool(critical_failures)={bool(critical_failures)}")
        print(f"    DEBUG: bool(critical_skipped)={bool(critical_skipped)}")
        print(f"    DEBUG: (critical_failures or critical_skipped)={(critical_failures or critical_skipped)}")

        # Count all phases for summary
        phases_passed = sum(1 for r in results.values() if r.get('passed', False) and not r.get('skipped', False))
        phases_skipped = sum(1 for r in results.values() if r.get('skipped', False))
        phases_failed = sum(1 for r in results.values() if not r.get('passed', False) and not r.get('skipped', False))
        phases_total = len(results) - phases_skipped

        print(f"\nPhases: {phases_passed}/{phases_total} passed", end="")
        if phases_skipped > 0:
            print(f" ({phases_skipped} skipped)")
        else:
            print()

        for phase_name, phase_result in results.items():
            if phase_result.get('skipped'):
                status = "⏭️ "
            elif phase_result.get('passed'):
                status = "✅"
            else:
                status = "❌"
            print(f"  {status} {phase_name}")

        # Overall result - ALL critical phases must pass
        validation_mode = "SMOKE TEST" if smoke_test else "FULL VALIDATION"
        test_failed = bool(critical_failures or critical_skipped)

        if test_failed:
            print(f"\n❌ E2E {validation_mode} FAILED")
            if critical_failures:
                print(f"\nCritical phases failed: {', '.join(critical_failures)}")
            if critical_skipped:
                print(f"Critical phases skipped: {', '.join(critical_skipped)}")
            print("\n⚠️  Validation cannot pass with critical phase failures/skips")
            exit_code = 1
        else:
            print(f"\n✅ E2E {validation_mode} PASSED")
            print(f"\nAll {len(critical_passed)} critical phases completed successfully!")
            print(f"Critical phases: {', '.join(critical_passed)}")
            if provider_type == 'OCP':
                print("OCP provider validated (architecture-agnostic)")
            else:
                print("Database layer validated (architecture-agnostic)")
            exit_code = 0

        # Run diagnostics: automatically on failure OR when --diagnose flag is passed
        if test_failed or diagnose:
            print("\n" + "="*70)
            if test_failed:
                print("🔍 RUNNING DIAGNOSTICS (auto-triggered by failure)")
            else:
                print("🔍 RUNNING DIAGNOSTICS (--diagnose flag)")
            print("="*70)
            print("\nCollecting infrastructure health information...\n")

            try:
                diagnostic = DeploymentValidationPhase(k8s, db)
                diagnostic_results = diagnostic.run_all_validations()

                # Show diagnostic summary
                if diagnostic_results.get('summary'):
                    summary = diagnostic_results['summary']
                    score = summary.get('score', 0)
                    if score >= 95:
                        print("\n✅ Infrastructure appears healthy - issue likely in application logic")
                    elif score >= 70:
                        print("\n⚠️  Some infrastructure issues detected - review above for details")
                    else:
                        print("\n❌ Significant infrastructure issues - likely root cause of failure")
            except Exception as diag_error:
                print(f"\n⚠️  Diagnostic collection failed: {diag_error}")
                print("   (This doesn't affect the test result)")

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        exit_code = 130
    except (click.exceptions.Exit, SystemExit):
        # Let Click handle exit codes properly
        raise
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        # Cleanup
        if 'db' in locals():
            db.close()

    # Exit with proper code using Click's context
    ctx.exit(exit_code)


if __name__ == '__main__':
    main()

