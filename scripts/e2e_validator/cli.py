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
from .clients.database import DatabaseClient
from .clients.nise import NiseClient
from .clients.kafka_producer import KafkaProducerClient
from .phases.preflight import PreflightPhase
from .phases.provider import ProviderPhase
from .phases.data_upload import DataUploadPhase
from .phases.processing import ProcessingPhase
from .phases.smoke_validation import SmokeValidationPhase
from .phases.deployment_validation import DeploymentValidationPhase
from .phases.kafka_validation import KafkaValidationPhase


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option('--namespace', default='cost-mgmt', help='Kubernetes namespace')
@click.option('--org-id', default='org1234567', help='Organization ID')
@click.option('--provider-type', type=click.Choice(['aws', 'azure', 'gcp', 'ocp'], case_sensitive=False), default='aws', help='Cloud provider type to test')
@click.option('--skip-migrations', is_flag=True, help='Skip database migrations')
@click.option('--skip-provider', is_flag=True, help='Skip provider setup')
@click.option('--skip-data', is_flag=True, help='Skip data upload')
@click.option('--skip-tests', is_flag=True, help='Skip IQE tests (only allowed in smoke-test mode)')
@click.option('--skip-deployment-validation', is_flag=True, help='Skip deployment validation')
@click.option('--quick', is_flag=True, help='Quick mode (skip all setup)')
@click.option('--smoke-test', is_flag=True, help='Smoke test mode: fast validation with minimal data')
@click.option('--force', is_flag=True, help='Force data regeneration even if data exists')
@click.option('--timeout', default=300, help='Processing timeout (seconds)')
@click.option('--scenarios', default='all', help='Comma-separated scenario list or "all"')
@click.pass_context
def main(ctx, namespace, org_id, provider_type, skip_migrations, skip_provider, skip_data,
         skip_tests, skip_deployment_validation, quick, smoke_test, force, timeout, scenarios):
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
        skip_deployment_validation = True
        timeout = 60  # Reduce timeout from 300s to 60s
        print("\n🚀 Smoke test mode enabled:")
        print("   - Running standalone validation (YAML-driven)")
        print("   - Skipping deployment validation")
        print("   - Reduced timeout to 60s")
        print("   - Minimal data (1 pod, 1 node, 1 cluster)")
        print("   - Estimated completion: ~90 seconds")
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
            print("  ❌ Failed to discover PostgreSQL pod with label app.kubernetes.io/component=postgresql")
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
        db_password = k8s.get_secret(db_secret_info['secret_name'], db_secret_info['key'])
        if not db_password:
            print(f"  ❌ Failed to get password from secret {db_secret_info['secret_name']}")
            ctx.exit(1)

        # Get database user from secret
        db_user = k8s.get_secret(db_secret_info['secret_name'], 'username')
        if not db_user:
            print(f"  ⚠️  Failed to get username from secret, defaulting to 'koku'")
            db_user = 'koku'

        # Use localhost for DB connection (requires port-forward or running in cluster)
        # Port-forward will be set up automatically if needed
        db = DatabaseClient(
            host='localhost',
            port=5432,
            user=db_user,
            password=db_password,
            database='koku'
        )
        print("  ✓ Database client")

        nise = NiseClient()
        print("  ✓ Nise client")

        # Set up database port-forward
        print("\n🔌 Setting up database port-forward...")
        import subprocess

        # Detect if we're on OpenShift (check for 'openshift' in context name)
        try:
            context_result = subprocess.run(
                ['kubectl', 'config', 'current-context'],
                capture_output=True,
                text=True,
                check=True
            )
            current_context = context_result.stdout.strip()
            if not current_context:
                print(f"  ❌ No Kubernetes context is set")
                print(f"     Please run: kubectl config use-context <context-name>")
                ctx.exit(1)
            is_openshift = 'openshift' in current_context.lower() or '/' in current_context
            cli_tool = 'oc' if is_openshift else 'kubectl'
            print(f"  🔍 Detected cluster type: {'OpenShift' if is_openshift else 'Kubernetes'} (using {cli_tool})")
            print(f"  📋 Context: {current_context}")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to get current context: {e}")
            print(f"     Please run: kubectl config use-context <context-name>")
            ctx.exit(1)
        except Exception as e:
            print(f"  ❌ Unexpected error detecting cluster type: {e}")
            ctx.exit(1)

        # Kill any existing port-forwards on 5432
        subprocess.run(['pkill', '-f', 'port-forward.*5432'],
                      capture_output=True, check=False)
        time.sleep(1)

        # Start port-forward with output redirected to avoid buffer issues
        # Use dynamically discovered PostgreSQL pod
        print(f"  🔗 Starting port-forward: {cli_tool} port-forward -n {namespace} pod/{postgres_pod} 5432:5432")
        port_forward_log = open('/tmp/port-forward-e2e.log', 'w')
        try:
            db_port_forward = subprocess.Popen(
                [cli_tool, 'port-forward', '-n', namespace,
                 f'pod/{postgres_pod}', '5432:5432'],
                stdout=port_forward_log,
                stderr=subprocess.STDOUT,
                text=True
            )
        except FileNotFoundError:
            print(f"  ❌ Command '{cli_tool}' not found in PATH")
            print(f"     Please install {cli_tool} CLI tool")
            ctx.exit(1)
        except Exception as e:
            print(f"  ❌ Failed to start port-forward: {e}")
            ctx.exit(1)

        # Wait and verify port-forward is established
        print("  ⏳ Waiting for port-forward to establish...")
        max_attempts = 10
        for attempt in range(max_attempts):
            time.sleep(1)
            # Check if process is still running
            if db_port_forward.poll() is not None:
                # Process terminated, get error details from log file
                print(f"  ❌ Port-forward process terminated unexpectedly (exit code: {db_port_forward.returncode})")
                port_forward_log.close()
                try:
                    with open('/tmp/port-forward-e2e.log', 'r') as log_file:
                        log_content = log_file.read().strip()
                        if log_content:
                            print(f"  📝 Port-forward log:")
                            for line in log_content.split('\n'):
                                print(f"     {line}")
                except:
                    pass
                print(f"")
                print(f"  💡 Troubleshooting tips:")
                print(f"     1. Verify the pod exists: {cli_tool} get pod -n {namespace} {postgres_pod}")
                print(f"     2. Check your permissions: {cli_tool} auth can-i get pods -n {namespace}")
                print(f"     3. Ensure port 5432 is not already in use: lsof -i :5432")
                print(f"     4. Verify network connectivity to the cluster")
                ctx.exit(1)
            # Try to connect to verify port-forward is working
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', 5432))
                sock.close()
                if result == 0:
                    print(f"  ✅ Database port-forward active (localhost:5432) - attempt {attempt + 1}")
                    break
            except Exception as e:
                if attempt == 0:  # Only print on first attempt to avoid spam
                    print(f"    Connection attempt {attempt + 1} failed: {e}")
                pass
            if attempt == max_attempts - 1:
                print("  ❌ Port-forward failed to establish after 10 attempts")
                db_port_forward.terminate()
                ctx.exit(1)

        # Additional wait to ensure database is ready
        time.sleep(2)

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

        bucket = "cost-data"

        # Fail-fast: skip remaining phases if a critical phase fails
        should_skip_remaining = False
        failed_phase = None

        # Phase 1: Preflight
        preflight = PreflightPhase(db, namespace, s3_endpoint, bucket)
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
            provider_phase = ProviderPhase(db, org_id=org_id, k8s_client=k8s)
            results['provider'] = provider_phase.run(skip=skip_provider, provider_type=provider_type, cluster_id='test-cluster-123')
            if not results['provider']['passed'] and not results['provider'].get('skipped'):
                print("\n❌ Provider setup failed - skipping remaining phases")
                should_skip_remaining = True
                failed_phase = 'provider'
            provider_uuid = results['provider'].get('provider_uuid')

        # Phase 4: Data Upload
        if should_skip_remaining:
            results['data_upload'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        elif skip_data:
            results['data_upload'] = {'passed': False, 'skipped': True, 'reason': '--skip-data'}
        else:
            from .clients.s3 import S3Client

            # Get S3 credentials
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
                manifest_uuid=manifest_uuid
            )
            results['processing'] = processing.run()
            if not results['processing']['passed'] and not results['processing'].get('skipped'):
                print("\n❌ Processing failed - skipping remaining phases")
                should_skip_remaining = True
                failed_phase = 'processing'

        # Phase 7: Trino validation
        if should_skip_remaining:
            results['trino'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        else:
            from .phases.trino_validation import TrinoValidationPhase
            trino_val = TrinoValidationPhase(k8s, org_id)
            results['trino'] = trino_val.run()
            if not results['trino']['passed'] and not results['trino'].get('skipped'):
                print("\n❌ Trino validation failed - skipping remaining phases")
                should_skip_remaining = True
                failed_phase = 'trino'

        # Phase 8: Validation
        if should_skip_remaining:
            results['validation'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        elif skip_tests:
            # Tests explicitly skipped
            results['validation'] = {'passed': True, 'skipped': True, 'reason': '--skip-tests flag'}
        else:
            # Run standalone validation (calculates expected values from nise YAML)
            print("\n📋 Running data validation...")
            validation = SmokeValidationPhase(
                db_client=db,
                namespace=namespace,
                org_id=org_id,
                cluster_id='test-cluster-123'
            )
            results['validation'] = validation.run()
            if not results['validation']['passed']:
                print("\n❌ Validation failed")
                should_skip_remaining = True
                failed_phase = 'validation'

        # Deployment Validation - always last, runs only if no critical phases failed
        if should_skip_remaining:
            results['deployment_validation'] = {'passed': False, 'skipped': True, 'reason': f'{failed_phase} failed'}
        elif skip_deployment_validation:
            results['deployment_validation'] = {'passed': False, 'skipped': True, 'reason': '--skip-deployment-validation'}
        else:
            deployment_val = DeploymentValidationPhase(k8s, db)
            results['deployment_validation'] = deployment_val.run_all_validations()
            # Deployment validation is informational - don't mark subsequent phases as failed
            if not results['deployment_validation']['passed']:
                print("\n⚠️  Deployment validation failed - some infrastructure checks did not pass")

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

        # Trino validation is critical for AWS/Azure/GCP (not for OCP-only)
        if provider_type in ['AWS', 'AZURE', 'GCP']:
            critical_phases.append('trino')

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

        if critical_failures or critical_skipped:
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
        if 'port_forward_log' in locals():
            try:
                port_forward_log.close()
            except:
                pass
        if 'db_port_forward' in locals():
            try:
                db_port_forward.terminate()
                db_port_forward.wait(timeout=5)
            except:
                db_port_forward.kill()

    # Exit with proper code using Click's context
    ctx.exit(exit_code)


if __name__ == '__main__':
    main()

