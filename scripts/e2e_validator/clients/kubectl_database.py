"""
Kubectl Database Client
=======================

PostgreSQL client using kubectl exec - no port-forward needed.
More resilient to network issues than direct TCP connections.
"""

import json
import uuid
from typing import Dict, List, Optional, Tuple
from .kubernetes import KubernetesClient


class KubectlDatabaseClient:
    """PostgreSQL client via kubectl exec - no port-forward needed"""

    def __init__(self, k8s: KubernetesClient, pod_name: str = "postgres-0",
                 database: str = "koku", user: str = "koku"):
        """Initialize kubectl database client

        Args:
            k8s: KubernetesClient instance
            pod_name: PostgreSQL pod name
            database: Database name
            user: Database user
        """
        self.k8s = k8s
        self.pod_name = pod_name
        self.database = database
        self.user = user

    def execute_query(self, query: str, params: Optional[Tuple] = None,
                     fetch_one: bool = False, dict_cursor: bool = False):
        """Execute a query and return results

        Args:
            query: SQL query
            params: Query parameters (will be interpolated - use with caution)
            fetch_one: Return single row instead of all
            dict_cursor: Return results as dicts (requires column names in query)

        Returns:
            Query results
        """
        # Simple parameter interpolation (for basic types only)
        if params:
            # Convert params to SQL-safe strings
            safe_params = []
            for p in params:
                if p is None:
                    safe_params.append('NULL')
                elif isinstance(p, str):
                    # Escape single quotes
                    safe_params.append(f"'{p.replace(chr(39), chr(39)+chr(39))}'")
                elif isinstance(p, bool):
                    safe_params.append('TRUE' if p else 'FALSE')
                else:
                    safe_params.append(str(p))
            query = query % tuple(safe_params)

        # Execute via kubectl exec
        result = self.k8s.postgres_exec(
            pod_name=self.pod_name,
            database=self.database,
            sql=query,
            user=self.user
        )

        # Parse results
        if not result or result.strip() == '':
            return None if fetch_one else []

        lines = result.strip().split('\n')

        if fetch_one:
            if lines:
                # Return tuple of values
                values = lines[0].split('|')
                return tuple(v.strip() if v.strip() != '' else None for v in values)
            return None
        else:
            # Return list of tuples
            rows = []
            for line in lines:
                if line.strip():
                    values = line.split('|')
                    rows.append(tuple(v.strip() if v.strip() != '' else None for v in values))
            return rows

    def close(self):
        """No-op for kubectl client"""
        pass

    # ========================================================================
    # Provider Operations
    # ========================================================================

    def get_provider(self) -> Optional[Dict]:
        """Get first provider

        Returns:
            Provider dict or None
        """
        result = self.execute_query("""
            SELECT
                p.uuid::text,
                p.name,
                p.type,
                p.active,
                p.setup_complete,
                c.schema_name as org_id
            FROM api_provider p
            JOIN api_customer c ON p.customer_id = c.id
            ORDER BY p.created_timestamp DESC
            LIMIT 1
        """, fetch_one=True)

        if result:
            return {
                'uuid': result[0],
                'name': result[1],
                'type': result[2],
                'active': result[3] == 't' or result[3] == True,
                'setup_complete': result[4] == 't' or result[4] == True,
                'org_id': result[5]
            }
        return None

    def get_provider_count(self) -> int:
        """Get total provider count"""
        result = self.execute_query("SELECT COUNT(*) FROM api_provider", fetch_one=True)
        return int(result[0]) if result and result[0] else 0

    def get_provider_by_name(self, name: str) -> Optional[Dict]:
        """Get provider by name"""
        # Use %s placeholder that will be interpolated
        result = self.execute_query(f"""
            SELECT
                p.uuid::text,
                p.name,
                p.type,
                p.active,
                p.setup_complete,
                c.schema_name as org_id
            FROM api_provider p
            JOIN api_customer c ON p.customer_id = c.id
            WHERE p.name = '{name.replace("'", "''")}'
            LIMIT 1
        """, fetch_one=True)

        if result:
            return {
                'uuid': result[0],
                'name': result[1],
                'type': result[2],
                'active': result[3] == 't' or result[3] == True,
                'setup_complete': result[4] == 't' or result[4] == True,
                'org_id': result[5]
            }
        return None

    # ========================================================================
    # Migration Operations
    # ========================================================================

    def check_migrations(self) -> Dict[str, any]:
        """Check migration status

        Returns:
            Dict with migration info
        """
        # Check if django_migrations table exists
        exists_result = self.execute_query("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'django_migrations'
            )
        """, fetch_one=True)

        exists = exists_result and exists_result[0] == 't'

        if not exists:
            return {"applied": 0, "pending": True, "table_exists": False}

        # Count applied migrations
        count_result = self.execute_query("""
            SELECT COUNT(*) FROM django_migrations WHERE app = 'api'
        """, fetch_one=True)
        count = int(count_result[0]) if count_result and count_result[0] else 0

        # Check for specific critical migration
        has_polling_result = self.execute_query("""
            SELECT EXISTS (
                SELECT FROM django_migrations
                WHERE app = 'api' AND name = '0060_provider_polling_timestamp'
            )
        """, fetch_one=True)
        has_polling = has_polling_result and has_polling_result[0] == 't'

        return {
            "applied": count,
            "pending": count < 50,  # Rough estimate
            "table_exists": True,
            "has_critical": has_polling
        }

    def check_summary_tables(self) -> bool:
        """Check if summary tables have been populated

        Returns:
            True if any summary data exists
        """
        result = self.execute_query("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'reporting_ocpusagelineitem_daily_summary'
                AND table_schema NOT IN ('pg_catalog', 'information_schema', 'public')
            )
        """, fetch_one=True)
        return result and result[0] == 't'

    # ========================================================================
    # Manifest Operations
    # ========================================================================

    def get_manifest_count(self) -> int:
        """Get count of cost usage report manifests"""
        result = self.execute_query("""
            SELECT COUNT(*) FROM reporting_common_costusagereportmanifest
        """, fetch_one=True)
        return int(result[0]) if result and result[0] else 0

    def get_latest_manifest(self) -> Optional[Dict]:
        """Get latest manifest"""
        result = self.execute_query("""
            SELECT
                id,
                assembly_id,
                manifest_creation_datetime,
                provider_id
            FROM reporting_common_costusagereportmanifest
            ORDER BY manifest_creation_datetime DESC
            LIMIT 1
        """, fetch_one=True)

        if result:
            return {
                'id': result[0],
                'assembly_id': result[1],
                'manifest_creation_datetime': result[2],
                'provider_id': result[3]
            }
        return None

    # ========================================================================
    # Customer/Tenant Operations
    # ========================================================================

    def get_customer_count(self) -> int:
        """Get customer count"""
        result = self.execute_query("SELECT COUNT(*) FROM api_customer", fetch_one=True)
        return int(result[0]) if result and result[0] else 0

    def get_tenant_count(self) -> int:
        """Get tenant count"""
        result = self.execute_query("SELECT COUNT(*) FROM api_tenant", fetch_one=True)
        return int(result[0]) if result and result[0] else 0

    def get_schema_name_for_org(self, org_id: str) -> str:
        """Get schema name for an org_id

        Args:
            org_id: Organization ID (e.g., 'org1234567')

        Returns:
            Schema name (e.g., 'orgorg1234567'), falls back to 'org' + org_id if not found
        """
        result = self.execute_query(f"""
            SELECT schema_name FROM api_customer
            WHERE account_id = '{org_id.replace("'", "''")}'
               OR schema_name LIKE '%{org_id.replace("'", "''")}%'
            LIMIT 1
        """, fetch_one=True)
        # Koku prefixes 'org' to org_id for schema names
        return result[0] if result else f"org{org_id}"

