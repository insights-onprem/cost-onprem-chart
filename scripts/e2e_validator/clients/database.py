"""
Database Client
===============

Direct PostgreSQL connection - no kubectl exec psql needed.
"""

import uuid
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple, Iterator
import psycopg2
from psycopg2.extras import RealDictCursor


class DatabaseClient:
    """Direct PostgreSQL client"""

    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        """Initialize database client

        Args:
            host: Database host
            port: Database port
            user: Database user
            password: Database password
            database: Database name
        """
        self.connection_params = {
            'host': host,
            'port': port,
            'user': user,
            'password': password,
            'database': database
        }
        self._conn = None

    @property
    def conn(self):
        """Get or create connection"""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**self.connection_params)
        return self._conn

    def close(self):
        """Close connection"""
        if self._conn and not self._conn.closed:
            self._conn.close()

    @contextmanager
    def cursor(self, dict_cursor: bool = False) -> Iterator:
        """Get a cursor context manager

        Args:
            dict_cursor: Return results as dicts instead of tuples

        Yields:
            Database cursor
        """
        cursor_factory = RealDictCursor if dict_cursor else None
        cur = self.conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def execute_query(self, query: str, params: Optional[Tuple] = None,
                     fetch_one: bool = False, dict_cursor: bool = False):
        """Execute a query and return results

        Args:
            query: SQL query
            params: Query parameters
            fetch_one: Return single row instead of all
            dict_cursor: Return results as dicts

        Returns:
            Query results
        """
        with self.cursor(dict_cursor=dict_cursor) as cur:
            cur.execute(query, params or ())
            if fetch_one:
                return cur.fetchone()
            return cur.fetchall()

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
        """, fetch_one=True, dict_cursor=True)

        return dict(result) if result else None

    def get_provider_count(self) -> int:
        """Get total provider count"""
        result = self.execute_query("SELECT COUNT(*) FROM api_provider", fetch_one=True)
        return result[0] if result else 0

    def create_provider(self, name: str, org_id: str,
                       bucket: str = "cost-data",
                       report_name: str = "test-report") -> str:
        """Create provider using direct SQL

        Args:
            name: Provider name
            org_id: Organization ID (schema name)
            bucket: S3 bucket name
            report_name: Report name

        Returns:
            Provider UUID
        """
        with self.cursor() as cur:
            # Get or create customer
            cur.execute("""
                INSERT INTO api_customer (
                    date_created,
                    schema_name,
                    account_id
                )
                VALUES (NOW(), %s, %s)
                ON CONFLICT (schema_name)
                DO UPDATE SET schema_name = EXCLUDED.schema_name
                RETURNING id
            """, (org_id, org_id[:10]))
            customer_id = cur.fetchone()[0]

            # Create provider
            provider_uuid = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO api_provider (
                    uuid,
                    name,
                    type,
                    authentication,
                    billing_source,
                    customer_id,
                    created_by_id,
                    setup_complete,
                    active,
                    paused,
                    data_updated_timestamp,
                    created_timestamp
                )
                VALUES (
                    %s, %s, 'AWS',
                    '{"credentials": {}}'::jsonb,
                    '{"data_source": {"bucket": %s, "report_name": %s, "report_prefix": "", "storage_only": true}}'::jsonb,
                    %s, 1, true, true, false,
                    NOW(), NOW()
                )
                RETURNING uuid::text
            """, (provider_uuid, name, bucket, report_name, customer_id))

            return cur.fetchone()[0]

    # ========================================================================
    # Migration Operations
    # ========================================================================

    def check_migrations(self) -> Dict[str, any]:
        """Check migration status

        Returns:
            Dict with migration info
        """
        # Check if django_migrations table exists
        exists = self.execute_query("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'django_migrations'
            )
        """, fetch_one=True)[0]

        if not exists:
            return {"applied": 0, "pending": True, "table_exists": False}

        # Count applied migrations
        count = self.execute_query("""
            SELECT COUNT(*) FROM django_migrations WHERE app = 'api'
        """, fetch_one=True)[0]

        # Check for specific critical migration
        has_polling = self.execute_query("""
            SELECT EXISTS (
                SELECT FROM django_migrations
                WHERE app = 'api' AND name = '0060_provider_polling_timestamp'
            )
        """, fetch_one=True)[0]

        return {
            "applied": count,
            "pending": count < 50,  # Rough estimate
            "table_exists": True,
            "has_critical": has_polling
        }

    def check_hive_database(self) -> bool:
        """Check if Hive database and role exist"""
        # Check role
        has_role = self.execute_query("""
            SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hive')
        """, fetch_one=True)[0]

        # Check database
        has_db = self.execute_query("""
            SELECT EXISTS (
                SELECT FROM pg_database WHERE datname = 'hive'
            )
        """, fetch_one=True)[0]

        return has_role and has_db

    def create_hive_prerequisites(self):
        """Create Hive role and database if needed"""
        # Note: These need to be run with autocommit
        old_isolation = self.conn.isolation_level
        self.conn.set_isolation_level(0)  # AUTOCOMMIT

        try:
            with self.cursor() as cur:
                # Create role
                try:
                    cur.execute("CREATE ROLE hive WITH LOGIN PASSWORD 'hivepass'")
                except psycopg2.errors.DuplicateObject:
                    pass

                # Create database
                try:
                    cur.execute("CREATE DATABASE hive OWNER hive")
                except psycopg2.errors.DuplicateDatabase:
                    pass
        finally:
            self.conn.set_isolation_level(old_isolation)

    # ========================================================================
    # Manifest Operations
    # ========================================================================

    def get_manifest_count(self) -> int:
        """Get count of cost usage report manifests"""
        result = self.execute_query("""
            SELECT COUNT(*) FROM reporting_common_costusagereportmanifest
        """, fetch_one=True)
        return result[0] if result else 0

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
        """, fetch_one=True, dict_cursor=True)

        return dict(result) if result else None

