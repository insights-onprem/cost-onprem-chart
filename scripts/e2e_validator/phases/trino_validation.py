"""
Phase 7: Trino Validation
===========================

Validates Trino tables and data accessibility.
"""

from typing import Dict, List


class TrinoValidationPhase:
    """Phase 7: Trino table and query validation"""

    def __init__(self, k8s_client, org_id: str, provider_type: str = 'aws'):
        """Initialize Trino validation phase

        Args:
            k8s_client: KubernetesClient instance
            org_id: Organization ID for schema validation
            provider_type: Provider type ('aws', 'ocp', 'azure', 'gcp')
        """
        self.k8s = k8s_client
        self.org_id = org_id
        self.provider_type = provider_type.lower()

    def check_trino_pod(self) -> Dict:
        """Verify Trino coordinator pod exists"""
        print("  🔍 Checking Trino coordinator pod...")
        pod = self.k8s.get_pod_by_component('trino-coordinator')

        if pod:
            print(f"    ✓ Found pod: {pod}")
            return {'passed': True, 'pod': pod}
        else:
            print("    ❌ Trino coordinator pod not found")
            return {'passed': False, 'pod': None}

    def verify_hive_schema(self) -> bool:
        """Verify org schema exists in Hive"""
        print(f"  🔍 Checking for Hive schema: {self.org_id}")
        trino_pod = self.k8s.get_pod_by_component('trino-coordinator')

        if not trino_pod:
            print("    ❌ Trino pod not available")
            return False

        try:
            output = self.k8s.exec_in_pod(
                trino_pod,
                ['/usr/bin/trino', '--execute', 'SHOW SCHEMAS FROM hive']
            )

            if self.org_id in output:
                print(f"    ✓ Schema '{self.org_id}' exists")
                return True
            else:
                print(f"    ❌ Schema '{self.org_id}' not found")
                print(f"    Available schemas: {output}")
                return False
        except Exception as e:
            print(f"    ❌ Error checking schema: {e}")
            return False

    def verify_tables(self) -> Dict:
        """Verify expected parquet tables exist in Trino

        Note: Trino contains parquet tables, not PostgreSQL summary tables.
        PostgreSQL summary tables are populated FROM Trino data.
        """
        print(f"  🔍 Checking Trino tables in {self.org_id} (provider: {self.provider_type})...")
        trino_pod = self.k8s.get_pod_by_component('trino-coordinator')

        if not trino_pod:
            return {'tables': [], 'expected': []}

        # Provider-specific table names (aligned with Koku TRINO_TABLE_MAP)
        if self.provider_type == 'ocp':
            expected_tables = [
                'openshift_pod_usage_line_items_daily',
                'openshift_pod_usage_line_items',
            ]
        elif self.provider_type == 'azure':
            expected_tables = [
                'azure_line_items_daily',
                'azure_line_items',
            ]
        elif self.provider_type == 'gcp':
            expected_tables = [
                'gcp_line_items_daily',
                'gcp_line_items',
            ]
        else:  # aws (default)
            expected_tables = [
                'aws_line_items_daily',
                'aws_line_items',
            ]

        try:
            output = self.k8s.exec_in_pod(
                trino_pod,
                ['/usr/bin/trino', '--execute',
                 f'SHOW TABLES FROM hive.{self.org_id}']
            )

            found = [t for t in expected_tables if t in output]

            if found:
                print(f"    ✓ Found {len(found)}/{len(expected_tables)} expected tables")
                for table in found:
                    print(f"      - {table}")
            else:
                print(f"    ⚠️  No expected tables found")
                print(f"    Available tables:\n{output}")

            return {'tables': found, 'expected': expected_tables, 'all_tables': output}
        except Exception as e:
            print(f"    ❌ Error checking tables: {e}")
            return {'tables': [], 'expected': expected_tables, 'error': str(e)}

    def run_sample_query(self) -> Dict:
        """Execute sample query to verify parquet data accessible

        Queries the parquet table in Trino based on provider type.
        """
        print("  🔍 Running sample query...")
        trino_pod = self.k8s.get_pod_by_component('trino-coordinator')

        if not trino_pod:
            return {'passed': False, 'error': 'Trino pod not available'}

        # Provider-specific table for sample query
        if self.provider_type == 'ocp':
            table = 'openshift_pod_usage_line_items_daily'
        elif self.provider_type == 'azure':
            table = 'azure_line_items_daily'
        elif self.provider_type == 'gcp':
            table = 'gcp_line_items_daily'
        else:
            table = 'aws_line_items_daily'

        query = f"SELECT COUNT(*) FROM hive.{self.org_id}.{table}"

        try:
            output = self.k8s.exec_in_pod(
                trino_pod,
                ['/usr/bin/trino', '--execute', query]
            )

            # Parse count from output
            try:
                # Output format: "  <count>\n(1 row)" or "<count>" or "\"<count>\""
                lines = output.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    # Remove quotes if present
                    line = line.strip('"')
                    # Try to parse as integer
                    if line.isdigit():
                        count = int(line)
                        print(f"    ✓ Query successful: {count} rows in table")
                        return {'passed': True, 'row_count': count}

                # Couldn't find numeric count
                print(f"    ⚠️  Query returned unexpected format:\n{output}")
                return {'passed': False, 'error': 'Could not parse count', 'output': output}
            except ValueError as e:
                print(f"    ❌ Could not parse query result: {e}")
                return {'passed': False, 'error': f'Parse error: {e}', 'output': output}
        except Exception as e:
            print(f"    ❌ Query failed: {e}")
            return {'passed': False, 'error': str(e)}

    def run(self) -> Dict:
        """Run full Trino validation

        Returns:
            Results dict with validation status
        """
        print("\n" + "="*70)
        print("Phase 7: Trino Validation")
        print("="*70 + "\n")

        results = {}

        # Check Trino pod
        results['pod_check'] = self.check_trino_pod()
        if not results['pod_check']['passed']:
            print("\n❌ Trino pod not available - skipping validation")
            return {'passed': False, 'results': results, 'reason': 'Trino pod not found'}

        # Check schema
        results['schema_check'] = self.verify_hive_schema()

        # Check tables
        results['tables_check'] = self.verify_tables()

        # Run sample query
        results['query_check'] = self.run_sample_query()

        # Determine overall pass/fail
        passed = (
            results['schema_check'] and
            len(results['tables_check']['tables']) > 0 and
            results['query_check']['passed']
        )

        if passed:
            print("\n✅ Phase 7 Complete - Trino validation successful")
        else:
            print("\n⚠️  Phase 7 Complete - Some Trino checks failed")
            if not results['schema_check']:
                print("  - Schema not found (may not have data yet)")
            if len(results['tables_check']['tables']) == 0:
                print("  - No tables found (data may not be processed)")
            if not results['query_check']['passed']:
                print("  - Sample query failed")

        return {'passed': passed, 'results': results}

