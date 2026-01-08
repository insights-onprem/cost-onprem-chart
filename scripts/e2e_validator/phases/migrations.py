"""
Phase 2: Database Migrations
==============================

Handles Django migrations and database prerequisites.
"""

from typing import Dict


class MigrationsPhase:
    """Phase 2: Database migrations and prerequisites"""

    def __init__(self, k8s_client, db_client):
        """Initialize migrations phase

        Args:
            k8s_client: KubernetesClient instance
            db_client: DatabaseClient instance
        """
        self.k8s = k8s_client
        self.db = db_client

    def check_pg_stat_statements(self) -> bool:
        """Check if pg_stat_statements extension exists"""
        try:
            result = self.db.execute_query(
                "SELECT COUNT(*) FROM pg_extension WHERE extname='pg_stat_statements'",
                fetch_one=True
            )
            return result[0] > 0
        except Exception:
            return False

    def create_pg_stat_statements(self):
        """Create pg_stat_statements extension"""
        print("    Creating pg_stat_statements extension...")
        # Need superuser, execute via pod
        db_pod = self.k8s.get_pod_by_component('koku-db')
        if not db_pod:
            print("    ⚠️  Database pod not found, skipping extension creation")
            return

        try:
            self.k8s.exec_in_pod(
                db_pod,
                ['psql', '-U', 'postgres', '-c',
                 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;']
            )
            print("    ✓ pg_stat_statements extension created")
        except Exception as e:
            print(f"    ⚠️  Could not create extension: {e}")

    def run_django_migrations(self):
        """Execute Django migrations via MASU pod"""
        print("    Running Django migrations...")
        masu_pod = self.k8s.get_pod_by_component('masu')

        if not masu_pod:
            raise RuntimeError("MASU pod not found")

        output = self.k8s.exec_in_pod(
            masu_pod,
            ['python', '/opt/koku/koku/manage.py', 'migrate', '--noinput']
        )

        print("    ✓ Django migrations complete")
        return output

    def check_migrations_status(self) -> Dict:
        """Check if migrations are needed"""
        try:
            result = self.db.execute_query("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name = 'django_migrations'
            """, fetch_one=True)

            if result[0] == 0:
                return {'needed': True, 'reason': 'django_migrations table missing'}

            # Check for specific critical migration
            result = self.db.execute_query("""
                SELECT EXISTS (
                    SELECT FROM django_migrations
                    WHERE app = 'api' AND name = '0060_provider_polling_timestamp'
                )
            """, fetch_one=True)

            return {'needed': not result[0], 'has_critical': result[0]}
        except Exception as e:
            return {'needed': True, 'reason': f'Error checking: {str(e)}'}

    def run(self, skip: bool = False) -> Dict:
        """Run migrations phase

        Args:
            skip: Skip migrations if True

        Returns:
            Results dict
        """
        print("\n" + "="*70)
        print("Phase 2: Database Migrations")
        print("="*70 + "\n")

        if skip:
            print("⏭️  Skipped (--skip-migrations)")
            return {'passed': False, 'skipped': True}

        # Check status
        print("🔍 Checking migration status...")
        status = self.check_migrations_status()

        if not status['needed']:
            print("  ✅ All migrations already applied")
            return {'passed': True, 'already_complete': True}

        print(f"  ℹ️  Migrations needed: {status.get('reason', 'Unknown')}")

        # Create prerequisites
        print("\n📝 Creating database prerequisites...")

        if not self.check_pg_stat_statements():
            self.create_pg_stat_statements()
        else:
            print("    ✓ pg_stat_statements already installed")

        # Run migrations
        print("\n🔄 Applying Django migrations...")
        try:
            output = self.run_django_migrations()
            print("\n✅ Phase 2 Complete")
            return {'passed': True, 'output': output}
        except Exception as e:
            print(f"\n❌ Migration failed: {e}")
            return {'passed': False, 'error': str(e)}

