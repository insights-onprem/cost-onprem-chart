"""
Preflight checks for E2E validation suite.

Verifies system readiness before running main E2E phases.
"""

import subprocess
from typing import Dict, Any


class PreflightPhase:
    """Run preflight checks to verify system readiness."""

    def __init__(self, db_conn, namespace: str, s3_endpoint: str, bucket: str, k8s_client=None, postgres_pod: str = None):
        """
        Initialize preflight checks.

        Args:
            db_conn: Database connection object (optional, can use kubectl exec instead)
            namespace: Kubernetes namespace
            s3_endpoint: S3 endpoint URL
            bucket: S3 bucket name
            k8s_client: KubernetesClient for kubectl exec (if db_conn not available)
            postgres_pod: PostgreSQL pod name for kubectl exec
        """
        self.db_conn = db_conn
        self.namespace = namespace
        self.s3_endpoint = s3_endpoint
        self.bucket = bucket
        self.k8s_client = k8s_client
        self.postgres_pod = postgres_pod
        self.database = 'koku'

    def run(self) -> Dict[str, Any]:
        """
        Run all preflight checks.

        Returns:
            Dictionary with check results and overall pass/fail status
        """
        print("\n" + "="*70)
        print("🔍 PREFLIGHT CHECKS")
        print("="*70 + "\n")

        results = {}
        warnings = []

        # Check 1: Database connectivity (BLOCKING)
        print("1️⃣  Checking database connectivity...")
        results['database'] = self._check_database_connectivity()
        if results['database']['passed']:
            print(f"   ✅ Database: Connected")
            print(f"      - Migrations: {results['database']['migrations']}")
            print(f"      - Customers: {results['database']['customers']}")
            print(f"      - Tenants: {results['database']['tenants']}")
        else:
            print(f"   ❌ Database: {results['database'].get('error', 'FAILED')}")
            return {
                'passed': False,
                'checks': results,
                'error': 'Database connectivity check failed'
            }

        # Check 2: Provider existence (NON-BLOCKING)
        print("\n2️⃣  Checking provider data...")
        results['provider'] = self._check_provider_exists()
        if results['provider']['passed']:
            print(f"   ✅ Provider: Found {results['provider']['count']} provider(s)")
            if results['provider']['details']:
                for p in results['provider']['details']:
                    print(f"      - {p['name']} ({p['type']}, UUID: {p['uuid']})")
        else:
            print(f"   ⚠️  Provider: {results['provider'].get('warning', 'None found')}")
            print(f"      → Will create provider in provider phase")
            warnings.append(results['provider'].get('warning', 'No providers found'))

        # Check 3: S3 data verification (OPTIONAL, NON-BLOCKING)
        print("\n3️⃣  Checking S3 data availability...")
        results['s3_data'] = self._check_s3_data()
        if results['s3_data']['passed']:
            print(f"   ✅ S3 Data: Found {results['s3_data']['file_count']} files")
            if results['s3_data'].get('has_manifest'):
                print(f"      - Manifest: Present")
            if results['s3_data'].get('csv_count', 0) > 0:
                print(f"      - CSV files: {results['s3_data']['csv_count']}")
        elif results['s3_data'].get('skipped'):
            print(f"   ⏭️  S3 Data: Check skipped ({results['s3_data'].get('reason', 'unknown')})")
        else:
            print(f"   ⚠️  S3 Data: {results['s3_data'].get('warning', 'Not found')}")
            print(f"      → Will upload fresh data in data_upload phase")
            warnings.append(results['s3_data'].get('warning', 'No S3 data found'))

        print("\n" + "="*70)
        if warnings:
            print(f"⚠️  Preflight completed with {len(warnings)} warning(s)")
        else:
            print("✅ All preflight checks passed")
        print("="*70 + "\n")

        return {
            'passed': True,  # Non-blocking warnings don't fail preflight
            'checks': results,
            'warnings': warnings
        }

    def _check_database_connectivity(self) -> Dict[str, Any]:
        """
        Verify PostgreSQL is accessible and schema is initialized (using kubectl exec).

        Returns:
            Dictionary with check results
        """
        try:
            if self.k8s_client and self.postgres_pod:
                # Use kubectl exec to query database
                migrations_query = "SELECT COUNT(*) FROM django_migrations"
                customers_query = "SELECT COUNT(*) FROM api_customer"
                tenants_query = "SELECT COUNT(*) FROM api_tenant"

                migrations = int(self.k8s_client.postgres_exec(self.postgres_pod, self.database, migrations_query))
                customers = int(self.k8s_client.postgres_exec(self.postgres_pod, self.database, customers_query))
                tenants = int(self.k8s_client.postgres_exec(self.postgres_pod, self.database, tenants_query))

                if migrations == 0:
                    return {
                        'passed': False,
                        'error': 'No migrations found - database not initialized'
                    }

                return {
                    'passed': True,
                    'migrations': migrations,
                    'customers': customers,
                    'tenants': tenants
                }
            else:
                # Fallback to direct database connection
                query = """
                    SELECT
                        (SELECT COUNT(*) FROM django_migrations) as migrations,
                        (SELECT COUNT(*) FROM api_customer) as customers,
                        (SELECT COUNT(*) FROM api_tenant) as tenants
                """
                stats = self.db_conn.execute_query(query, fetch_one=True)

                if stats[0] == 0:  # No migrations
                    return {
                        'passed': False,
                        'error': 'No migrations found - database not initialized'
                    }

                return {
                    'passed': True,
                    'migrations': stats[0],
                    'customers': stats[1],
                    'tenants': stats[2]
                }
        except Exception as e:
            return {
                'passed': False,
                'error': f'Database connection failed: {str(e)}'
            }

    def _check_provider_exists(self) -> Dict[str, Any]:
        """
        Verify provider data persisted across deployments (using kubectl exec).

        Returns:
            Dictionary with check results
        """
        try:
            if self.k8s_client and self.postgres_pod:
                # Use kubectl exec to query database
                count_query = "SELECT COUNT(*) FROM api_provider"
                provider_count = int(self.k8s_client.postgres_exec(self.postgres_pod, self.database, count_query))

                if provider_count == 0:
                    return {
                        'passed': False,
                        'count': 0,
                        'warning': 'No providers found - will create in provider phase'
                    }

                # Get provider details (simplified for kubectl exec)
                details_query = """
                    SELECT name || '|' || type || '|' || uuid
                    FROM api_provider
                    ORDER BY created_timestamp DESC
                    LIMIT 5
                """
                details_output = self.k8s_client.postgres_exec(self.postgres_pod, self.database, details_query)
                details = []
                for line in details_output.strip().split('\n'):
                    if line and '|' in line:
                        parts = line.split('|')
                        if len(parts) == 3:
                            details.append({'name': parts[0], 'type': parts[1], 'uuid': parts[2]})

                return {
                    'passed': True,
                    'count': provider_count,
                    'details': details
                }
            else:
                # Fallback to direct database connection
                provider_count = self.db_conn.execute_query(
                    "SELECT COUNT(*) FROM api_provider",
                    fetch_one=True
                )[0]

                if provider_count == 0:
                    return {
                        'passed': False,
                        'count': 0,
                        'warning': 'No providers found - will create in provider phase'
                    }

                # Get provider details
                details_query = """
                    SELECT name, type, uuid
                    FROM api_provider
                    ORDER BY created_timestamp DESC
                    LIMIT 5
                """
                details_rows = self.db_conn.execute_query(details_query)
                details = [
                    {'name': row[0], 'type': row[1], 'uuid': str(row[2])}
                    for row in details_rows
                ]

                return {
                    'passed': True,
                    'count': provider_count,
                    'details': details
                }
        except Exception as e:
            return {
                'passed': False,
                'count': 0,
                'error': f'Provider check failed: {str(e)}',
                'warning': 'Could not verify provider - will create in provider phase'
            }

    def _check_s3_data(self) -> Dict[str, Any]:
        """
        Verify test data exists in S3 bucket (optional, non-blocking).

        Returns:
            Dictionary with check results
        """
        try:
            # Use kubectl exec to run aws CLI in MASU pod
            cmd = [
                'kubectl', 'exec', '-n', self.namespace,
                'deployment/koku-koku-api-masu', '--',
                'aws', '--endpoint-url', self.s3_endpoint,
                's3', 'ls', f's3://{self.bucket}/reports/test-report/',
                '--no-verify-ssl'
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                # Check for specific error types
                if 'NoSuchBucket' in result.stderr:
                    return {
                        'passed': False,
                        'warning': f'Bucket {self.bucket} does not exist'
                    }
                elif 'Connection' in result.stderr or 'timeout' in result.stderr.lower():
                    return {
                        'passed': False,
                        'skipped': True,
                        'reason': 'S3 connection timeout'
                    }
                else:
                    return {
                        'passed': False,
                        'warning': 'No test data found in S3'
                    }

            # Parse output to count files
            lines = result.stdout.strip().split('\n')
            files = [line for line in lines if line.strip()]

            # Check for manifest and CSVs
            has_manifest = any('Manifest.json' in line for line in files)
            csv_files = [line for line in files if '.csv' in line]
            csv_count = len(csv_files)

            if not has_manifest or csv_count == 0:
                return {
                    'passed': False,
                    'warning': f'Incomplete data: manifest={has_manifest}, csvs={csv_count}',
                    'file_count': len(files),
                    'has_manifest': has_manifest,
                    'csv_count': csv_count
                }

            return {
                'passed': True,
                'file_count': len(files),
                'has_manifest': has_manifest,
                'csv_count': csv_count
            }

        except subprocess.TimeoutExpired:
            return {
                'passed': False,
                'skipped': True,
                'reason': 'S3 check timed out'
            }
        except Exception as e:
            return {
                'passed': False,
                'skipped': True,
                'reason': f'S3 check error: {str(e)}'
            }
