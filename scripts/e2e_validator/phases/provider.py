"""
Phase 3: Provider Setup
========================

Create or verify cost provider configuration using Sources API (preferred)
or direct database access (fallback for testing).
"""

from typing import Dict, Optional
import uuid


class ProviderPhase:
    """Phase 3: Provider setup"""

    def __init__(self, db_client, org_id: str = "org1234567",
                 k8s_client=None, sources_api_client=None):
        """Initialize provider phase

        Args:
            db_client: DatabaseClient instance
            org_id: Organization ID
            k8s_client: KubernetesClient for executing Django ORM operations
            sources_api_client: SourcesAPIClient for proper Day 2 operations
        """
        self.db = db_client
        self.org_id = org_id
        self.k8s = k8s_client
        self.sources_api = sources_api_client

    def get_existing_provider(self) -> Optional[Dict]:
        """Check for existing provider"""
        return self.db.get_provider()

    def _ensure_tenant_and_sources(self, provider_uuid: str, provider_name: str, provider_type: str):
        """Ensure tenant and sources entries exist for provider (idempotent check)

        This is critical for existing providers that may have been created before
        the E2E script was fixed. Without these entries:
        - Tenant: Workers have no tenant to enumerate
        - Sources: Workers report "no accounts to be polled"

        Args:
            provider_uuid: Provider UUID
            provider_name: Provider name
            provider_type: Provider type (OCP, AWS, etc.)
        """
        if not self.k8s:
            return

        masu_pod = self.k8s.get_pod_by_component('masu')
        if not masu_pod:
            print("  ⚠️  MASU pod not found, skipping tenant/sources verification")
            return

        # Python code to check/create tenant and sources entries
        django_code = f'''
import os, sys, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "koku.settings")
sys.path.append("/opt/koku/koku")
django.setup()

from api.models import Tenant, Provider
from django.db import connection
import json

org_id = "{self.org_id}"
provider_uuid = "{provider_uuid}"
provider_type_str = "{provider_type}"

# Check/create tenant
tenant, tenant_created = Tenant.objects.get_or_create(schema_name=org_id)
if tenant_created:
    print("TENANT_CREATED")
else:
    print("TENANT_EXISTS")

# Check/create sources entry
try:
    provider = Provider.objects.get(uuid=provider_uuid)
    provider_id = provider.pk  # Use pk instead of id

    # Get billing source for sources entry
    billing_source = {{}}
    if provider.billing_source:
        billing_source = provider.billing_source.data_source or {{}}

    billing_source_json = json.dumps(billing_source)

    with connection.cursor() as cursor:
        # Check if sources entry exists
        cursor.execute("""
            SELECT COUNT(*) FROM api_sources WHERE koku_uuid = %s
        """, [provider_uuid])
        exists = cursor.fetchone()[0] > 0

        if not exists:
            # Get next source_id (integer sequence)
            cursor.execute("SELECT COALESCE(MAX(source_id), 0) + 1 FROM api_sources")
            next_source_id = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO public.api_sources (
                    source_id, source_uuid, name, "offset", source_type,
                    authentication, billing_source, koku_uuid, provider_id,
                    org_id, account_id, paused, pending_delete,
                    pending_update, out_of_order_delete
                ) VALUES (
                    %s, gen_random_uuid(), %s, 0, %s, '{{}}', %s::jsonb,
                    %s::uuid, %s::uuid, %s, %s, false, false, false, false
                )
            """, [
                next_source_id, provider.name, provider_type_str,
                billing_source_json, provider_uuid, provider_uuid, org_id, org_id
            ])
            print("SOURCES_ENTRY_CREATED")
        else:
            print("SOURCES_ENTRY_EXISTS")
except Exception as e:
    print("ERROR=" + str(e))
'''

        try:
            output = self.k8s.python_exec(masu_pod, django_code)

            for line in output.split('\n'):
                if 'TENANT_CREATED' in line:
                    print("  ✅ Tenant record created")
                elif 'TENANT_EXISTS' in line:
                    print("  ✓ Tenant record exists")
                elif 'SOURCES_ENTRY_CREATED' in line:
                    print("  ✅ Sources entry created")
                elif 'SOURCES_ENTRY_EXISTS' in line:
                    print("  ✓ Sources entry exists")
                elif line.startswith('ERROR='):
                    print(f"  ⚠️  {line}")
        except Exception as e:
            print(f"  ⚠️  Failed to verify tenant/sources: {e}")

    def _sync_provider_to_tenant_schema(self, provider_uuid: str):
        """Sync provider from public.api_provider to tenant schema.

        Cost Management uses a multi-tenant architecture where:
        - public.api_provider contains the global provider registry
        - {schema}.reporting_tenant_api_provider contains per-tenant provider views

        Billing records have FK constraints to the tenant schema table, so providers
        must be synced for data processing to work.

        Args:
            provider_uuid: The provider UUID to sync
        """
        if not self.k8s:
            raise ValueError("KubernetesClient required for database operations")

        # Try to find postgres pod (component label or by name)
        postgres_pod = self.k8s.get_pod_by_component('postgresql')
        if not postgres_pod:
            postgres_pod = self.k8s.discover_postgresql_pod()
        if not postgres_pod:
            raise RuntimeError("Postgres pod not found")

        sync_sql = f"""
        INSERT INTO {self.org_id}.reporting_tenant_api_provider (uuid, name, type, provider_id)
        SELECT uuid, name, type, uuid FROM public.api_provider
        WHERE uuid = '{provider_uuid}'
        ON CONFLICT (uuid) DO NOTHING;
        """

        try:
            result = self.k8s.postgres_exec(postgres_pod, 'koku', sync_sql)
            print(f"  ✓ Provider {provider_uuid} synced to tenant schema {self.org_id}")
        except Exception as e:
            print(f"  ⚠️  Failed to sync provider to tenant schema: {e}")
            raise RuntimeError(f"Provider sync failed: {e}")

    def create_provider_via_django_orm(self,
                                       name: str = "AWS Test Provider E2E",
                                       provider_type: str = "AWS",
                                       bucket: str = "cost-data",
                                       report_name: str = "test-report",
                                       report_prefix: str = "reports",
                                       cluster_id: str = "test-cluster-123") -> str:
        """Create provider using Django ORM AND provision tenant

        This does TWO things:
        1. Provisions tenant (schema + migrations + tables)
        2. Creates provider (customer + provider + auth + billing source)

        Args:
            name: Provider name
            provider_type: Provider type ('AWS', 'Azure', 'GCP')
            bucket: S3 bucket name
            report_name: Report name
            report_prefix: Report prefix in bucket

        Returns:
            Provider UUID
        """
        if not self.k8s:
            raise ValueError("KubernetesClient required for Django ORM operations")

        masu_pod = self.k8s.get_pod_by_component('masu')
        if not masu_pod:
            raise RuntimeError("MASU pod not found")

        # Map provider type string to Django constant
        provider_type_map = {
            'AWS': 'Provider.PROVIDER_AWS',
            'Azure': 'Provider.PROVIDER_AZURE',
            'GCP': 'Provider.PROVIDER_GCP',
            'OCP': 'Provider.PROVIDER_OCP'
        }
        django_provider_type = provider_type_map.get(provider_type, 'Provider.PROVIDER_AWS')

        # Python code to run in MASU pod - provisions tenant AND creates provider
        django_code = f'''
import os, sys, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "koku.settings")
sys.path.append("/opt/koku/koku")
django.setup()
from api.models import Customer, Provider
from api.provider.models import ProviderAuthentication, ProviderBillingSource
from django.db import connection
from django.core.management import call_command
import uuid

org_id = "{self.org_id}"
provider_type_str = "{provider_type}"

print("DEBUG: org_id=" + org_id)
print("DEBUG: provider_type_str=" + provider_type_str)

try:
    # STEP 1: Provision tenant schema
    print("TENANT_PROVISIONING_START")

    # Create Tenant record (CRITICAL: required for celery workers to find accounts)
    from api.models import Tenant
    tenant, tenant_created = Tenant.objects.get_or_create(
        schema_name=org_id
    )
    if tenant_created:
        print("TENANT_RECORD_CREATED")
    else:
        print("TENANT_RECORD_EXISTS")

    # Create PostgreSQL schema
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS " + org_id)
        cursor.execute("GRANT ALL ON SCHEMA " + org_id + " TO " + connection.settings_dict['USER'])
    print("TENANT_SCHEMA_CREATED")

    # Run tenant migrations (creates 217 reporting tables)
    call_command('migrate_schemas', schema_name=org_id, verbosity=0)
    print("TENANT_MIGRATIONS_COMPLETE")

    # STEP 2: Create provider
    customer, _ = Customer.objects.get_or_create(
        schema_name=org_id,
        defaults={{"uuid": str(uuid.uuid4()), "org_id": org_id}}
    )
    provider, created = Provider.objects.get_or_create(
        name="{name}",
        defaults={{
            "uuid": str(uuid.uuid4()),
            "type": {django_provider_type},
            "setup_complete": False,
            "active": True,
            "paused": False,
            "customer": customer
        }}
    )
    print("PROVIDER_TYPE=" + provider_type_str)

    if created:
        # Create authentication with UUID and cluster_id for OCP
        credentials = {{}}
        if provider_type_str == 'OCP':
            credentials = {{"cluster_id": "{cluster_id}"}}

        auth, _ = ProviderAuthentication.objects.get_or_create(
            uuid=str(uuid.uuid4()),
            defaults={{"credentials": credentials}}
        )

        # Create provider-specific billing source
        if provider_type_str == 'AWS':
            data_source = {{
                "bucket": "{bucket}",
                "report_name": "{report_name}",
                "report_prefix": "{report_prefix}"
            }}
        elif provider_type_str == 'Azure':
            data_source = {{
                "resource_group": {{
                    "directory": "",
                    "export_name": "{report_name}"
                }},
                "storage_account": {{
                    "local_dir": "{bucket}",
                    "container": ""
                }}
            }}
        elif provider_type_str == 'GCP':
            data_source = {{
                "dataset": "{bucket}",
                "table_id": "{report_name}"
            }}
        elif provider_type_str == 'OCP':
            data_source = {{
                "bucket": "{bucket}",
                "report_prefix": "{report_prefix}"
            }}
        else:
            data_source = {{
                "bucket": "{bucket}",
                "report_name": "{report_name}",
                "report_prefix": "{report_prefix}"
            }}

        billing, _ = ProviderBillingSource.objects.get_or_create(
            uuid=str(uuid.uuid4()),
            defaults={{"data_source": data_source}}
        )

        # Link to provider
        provider.authentication = auth
        provider.billing_source = billing
        provider.setup_complete = True
        provider.save()

    # CRITICAL: Sync provider to tenant schema for bill creation FK constraint
    # In production this happens automatically, but in E2E we need explicit sync
    with connection.cursor() as cursor:
        sql = "INSERT INTO " + org_id + ".reporting_tenant_api_provider (uuid, name, type, provider_id) VALUES (%s, %s, %s, %s) ON CONFLICT (uuid) DO NOTHING"
        cursor.execute(sql, (str(provider.uuid), provider.name, provider.type, str(provider.uuid)))
    print("PROVIDER_SYNCED_TO_TENANT_SCHEMA")

    # CRITICAL: Create api_sources entry for account polling
    # Celery's check_report_updates task queries api_sources to find accounts
    # Without this, workers report "no accounts to be polled"
    import json
    billing_source_json = json.dumps(data_source)

    # Get provider.pk (primary key) which is always available
    provider_pk = provider.pk

    with connection.cursor() as cursor:
        # Get next source_id (integer sequence)
        cursor.execute("SELECT COALESCE(MAX(source_id), 0) + 1 FROM api_sources")
        next_source_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO public.api_sources (
                source_id,
                source_uuid,
                name,
                "offset",
                source_type,
                authentication,
                billing_source,
                koku_uuid,
                provider_id,
                org_id,
                account_id,
                paused,
                pending_delete,
                pending_update,
                out_of_order_delete
            ) VALUES (
                %s,
                gen_random_uuid(),
                %s,
                0,
                %s,
                '{{}}',
                %s::jsonb,
                %s,
                %s::uuid,
                %s,
                %s,
                false,
                false,
                false,
                false
            ) ON CONFLICT (koku_uuid) DO UPDATE SET
                source_type = EXCLUDED.source_type,
                provider_id = EXCLUDED.provider_id,
                org_id = EXCLUDED.org_id,
                account_id = EXCLUDED.account_id,
                billing_source = EXCLUDED.billing_source
        """, [
            next_source_id,
            provider.name,
            provider_type_str,
            billing_source_json,
            str(provider.uuid),
            str(provider.uuid),  # provider_id = provider UUID
            org_id,
            org_id
        ])
    print("SOURCES_ENTRY_CREATED_OR_UPDATED")

    print("PROVIDER_UUID=" + str(provider.uuid))
    print("PROVIDER_NAME=" + provider.name)
except Exception as e:
    import traceback
    print("ERROR=" + str(e))
    print("TRACEBACK=" + traceback.format_exc())
'''

        # Execute in MASU pod
        output = self.k8s.python_exec(masu_pod, django_code)

        # Parse output
        provider_uuid = None
        error_msg = None
        traceback_msg = None

        for line in output.split('\n'):
            if line.startswith('PROVIDER_UUID='):
                provider_uuid = line.split('=', 1)[1].strip()
            elif line.startswith('ERROR='):
                error_msg = line.split('=', 1)[1].strip()
            elif line.startswith('TRACEBACK='):
                traceback_msg = line.split('=', 1)[1].strip()

        if error_msg:
            error_detail = f"{error_msg}"
            if traceback_msg:
                error_detail += f"\n{traceback_msg}"
            raise RuntimeError(f"Provider creation failed: {error_detail}")

        if not provider_uuid:
            raise RuntimeError(f"Failed to parse provider UUID from output: {output}")

        # Sync provider to tenant schema (required for billing records)
        self._sync_provider_to_tenant_schema(provider_uuid)

        return provider_uuid

    def create_provider_via_sources_api(self,
                                        name: str = "AWS Test Provider E2E",
                                        bucket: str = "cost-data",
                                        report_name: str = "test-report",
                                        report_prefix: str = "reports") -> str:
        """Create provider via Sources API (Red Hat recommended method)

        This is the proper Day 2 operations approach that:
        1. Creates a source in Sources API
        2. Creates authentication
        3. Links to Cost Management application with S3 config

        Args:
            name: Provider name
            bucket: S3 bucket name
            report_name: Cost and Usage Report name
            report_prefix: Report prefix in bucket

        Returns:
            Source ID (used as provider UUID in Koku)
        """
        if not self.sources_api:
            raise ValueError("SourcesAPIClient required for Sources API operations")

        result = self.sources_api.create_aws_source_full(
            name=name,
            bucket=bucket,
            report_name=report_name,
            report_prefix=report_prefix
        )

        return result['source_id']

    def create_provider(self,
                       name: str = "AWS Test Provider E2E",
                       provider_type: str = "AWS",
                       bucket: str = "cost-data",
                       report_name: str = "test-report",
                       report_prefix: str = "reports",
                       cluster_id: str = "test-cluster-123") -> str:
        """Create provider using best available method

        Priority:
        1. Django ORM (properly provisions tenant + creates provider)
        2. Sources API (if available - Red Hat recommended for Day 2)
        3. Direct SQL (fallback, may not provision tenant properly)

        Args:
            name: Provider name
            provider_type: Provider type ('AWS', 'Azure', 'GCP')
            bucket: S3 bucket name (or storage account for Azure)
            report_name: Report name
            report_prefix: Report prefix in bucket

        Returns:
            Provider UUID
        """
        # Use Django ORM approach if k8s client available (preferred for E2E)
        if self.k8s:
            return self.create_provider_via_django_orm(
                name=name,
                provider_type=provider_type,
                bucket=bucket,
                report_name=report_name,
                report_prefix=report_prefix,
                cluster_id=cluster_id
            )

        # Sources API only works for AWS currently
        if self.sources_api and provider_type == 'AWS':
            return self.create_provider_via_sources_api(name, bucket, report_name, report_prefix)

        # Fallback to direct SQL (won't provision tenant properly!)
        return self.db.create_provider(
            name=name,
            org_id=self.org_id,
            bucket=bucket,
            report_name=report_name,
            report_prefix=report_prefix
        )

    def run(self, skip: bool = False, provider_type: str = "AWS", cluster_id: str = "test-cluster-123") -> Dict:
        """Run provider setup phase

        Args:
            skip: Skip provider creation if True
            provider_type: Provider type to create ('AWS', 'Azure', 'GCP')

        Returns:
            Results dict
        """
        print("\n" + "="*70)
        print(f"Phase 3: Provider Setup ({provider_type})")
        print("="*70 + "\n")

        if skip:
            print("⏭️  Skipped (--skip-provider)")
            existing = self.get_existing_provider()
            if existing:
                print(f"  ℹ️  Existing provider: {existing['name']}")
                print(f"  ℹ️  UUID: {existing['uuid']}")
                return {
                    'passed': True,
                    'skipped': True,
                    'provider_uuid': existing['uuid'],
                    'provider_name': existing['name']
                }
            else:
                print("  ⚠️  No existing provider found")
                return {
                    'passed': False,
                    'skipped': True,
                    'error': 'No provider found and creation skipped'
                }

        # Check for existing provider
        print("🔍 Checking for existing provider...")
        existing = self.get_existing_provider()

        if existing:
            print(f"  ✅ Provider exists: {existing['name']}")
            print(f"  ℹ️  UUID: {existing['uuid']}")
            print(f"  ℹ️  Active: {existing['active']}")

            # Check if billing source is configured
            has_billing_source = self.db.execute_query("""
                SELECT bs.id, bs.data_source
                FROM api_provider p
                LEFT JOIN api_providerbillingsource bs ON p.billing_source_id = bs.id
                WHERE p.uuid = %s
            """, (existing['uuid'],))

            if has_billing_source and has_billing_source[0] and has_billing_source[0][0] is not None:
                # Check if authentication is also configured
                has_authentication = self.db.execute_query("""
                    SELECT pa.id, pa.credentials
                    FROM api_provider p
                    LEFT JOIN api_providerauthentication pa ON p.authentication_id = pa.id
                    WHERE p.uuid = %s
                """, (existing['uuid'],))

                if has_authentication and has_authentication[0] and has_authentication[0][0] is not None:
                    print(f"  ✅ Billing source configured")
                    print(f"  ✅ Authentication configured")

                    # For OCP, check if authentication has cluster_id
                    if provider_type == 'OCP':
                        import json
                        auth_id, credentials = has_authentication[0]
                        if not credentials or not credentials.get('cluster_id'):
                            print(f"  ⚠️  OCP authentication missing cluster_id, updating...")
                            try:
                                credentials = credentials or {}
                                credentials['cluster_id'] = cluster_id
                                # Use a different method that doesn't expect results
                                import psycopg2
                                conn = self.db.conn
                                with conn.cursor() as cursor:
                                    cursor.execute("""
                                        UPDATE api_providerauthentication
                                        SET credentials = %s::jsonb
                                        WHERE id = %s
                                    """, (json.dumps(credentials), auth_id))
                                    conn.commit()
                                print(f"  ✅ Authentication updated with cluster_id: {cluster_id}")
                            except Exception as e:
                                print(f"  ⚠️  Failed to update authentication: {e}")

                    # CRITICAL: Verify tenant and sources entries exist (for idempotency)
                    # These are required for account polling but may be missing on existing providers
                    if self.k8s:
                        print("\n🔍 Verifying tenant and sources configuration...")
                        self._ensure_tenant_and_sources(existing['uuid'], existing['name'], provider_type)

                    return {
                        'passed': True,
                        'created': False,
                        'provider_uuid': existing['uuid'],
                        'provider_name': existing['name']
                    }
                else:
                    print(f"  ✅ Billing source configured")
                    print(f"  ⚠️  Authentication missing, creating...")
                    # Create authentication for existing provider
                    try:
                        import uuid as uuid_lib
                        auth_uuid = str(uuid_lib.uuid4())

                        # Step 1: Insert authentication
                        self.db.execute_query("""
                            INSERT INTO api_providerauthentication (uuid, credentials)
                            VALUES (%s::uuid, %s::jsonb)
                            RETURNING uuid
                        """, (
                            auth_uuid,
                            '{}'
                        ))

                        # Step 2: Get the internal ID of the authentication
                        authentication = self.db.execute_query("""
                            SELECT id FROM api_providerauthentication WHERE uuid = %s
                        """, (auth_uuid,))

                        if not authentication or not authentication[0]:
                            raise Exception("Failed to retrieve authentication ID")

                        authentication_id = authentication[0][0]

                        # Step 3: Update provider to link authentication
                        self.db.execute_query("""
                            UPDATE api_provider
                            SET authentication_id = %s
                            WHERE uuid = %s
                            RETURNING uuid
                        """, (authentication_id, existing['uuid']))

                        print(f"  ✅ Authentication created and linked")

                        # Verify tenant and sources entries exist
                        if self.k8s:
                            print("\n🔍 Verifying tenant and sources configuration...")
                            self._ensure_tenant_and_sources(existing['uuid'], existing['name'], provider_type)

                        return {
                            'passed': True,
                            'created': False,
                            'authentication_created': True,
                            'provider_uuid': existing['uuid'],
                            'provider_name': existing['name']
                        }
                    except Exception as e:
                        print(f"  ❌ Failed to create authentication: {e}")
                        return {
                            'passed': False,
                            'error': f'Authentication creation failed: {e}'
                        }
            else:
                print(f"  ⚠️  Billing source missing, creating...")
                # Create billing source for existing provider
                try:
                    import uuid as uuid_lib
                    billing_source_uuid = str(uuid_lib.uuid4())

                    # Step 1: Insert billing source
                    self.db.execute_query("""
                        INSERT INTO api_providerbillingsource (uuid, data_source)
                        VALUES (%s::uuid, %s::jsonb)
                        RETURNING uuid
                    """, (
                        billing_source_uuid,
                        '{"bucket": "cost-data", "report_name": "test-report", "report_prefix": "reports"}'
                    ))

                    # Step 2: Get the internal ID of the billing source
                    billing_source = self.db.execute_query("""
                        SELECT id FROM api_providerbillingsource WHERE uuid = %s
                    """, (billing_source_uuid,))

                    if not billing_source or not billing_source[0]:
                        raise Exception("Failed to retrieve billing source ID")

                    billing_source_id = billing_source[0][0]

                    # Step 3: Update provider to link billing source
                    self.db.execute_query("""
                        UPDATE api_provider
                        SET billing_source_id = %s
                        WHERE uuid = %s
                        RETURNING uuid
                    """, (billing_source_id, existing['uuid']))

                    print(f"  ✅ Billing source created and linked")

                    # Now check and create authentication if missing
                    has_authentication = self.db.execute_query("""
                        SELECT pa.id, pa.credentials
                        FROM api_provider p
                        LEFT JOIN api_providerauthentication pa ON p.authentication_id = pa.id
                        WHERE p.uuid = %s
                    """, (existing['uuid'],))

                    if not has_authentication or not has_authentication[0] or has_authentication[0][0] is None:
                        print(f"  ⚠️  Authentication missing, creating...")
                        # Create authentication for existing provider
                        try:
                            import uuid as uuid_lib
                            import json
                            auth_uuid = str(uuid_lib.uuid4())

                            # For OCP, include cluster_id in credentials
                            credentials = {}
                            if provider_type == 'OCP':
                                credentials = {"cluster_id": cluster_id}

                            # Step 1: Insert authentication
                            self.db.execute_query("""
                                INSERT INTO api_providerauthentication (uuid, credentials)
                                VALUES (%s::uuid, %s::jsonb)
                                RETURNING uuid
                            """, (
                                auth_uuid,
                                json.dumps(credentials)
                            ))

                            # Step 2: Get the internal ID of the authentication
                            authentication = self.db.execute_query("""
                                SELECT id FROM api_providerauthentication WHERE uuid = %s
                            """, (auth_uuid,))

                            if not authentication or not authentication[0]:
                                raise Exception("Failed to retrieve authentication ID")

                            authentication_id = authentication[0][0]

                            # Step 3: Update provider to link authentication
                            self.db.execute_query("""
                                UPDATE api_provider
                                SET authentication_id = %s
                                WHERE uuid = %s
                                RETURNING uuid
                            """, (authentication_id, existing['uuid']))

                            print(f"  ✅ Authentication created and linked")
                        except Exception as e:
                            print(f"  ❌ Failed to create authentication: {e}")
                            return {
                                'passed': False,
                                'error': f'Authentication creation failed: {e}'
                            }
                    else:
                        # Authentication exists, check if it has cluster_id for OCP
                        if provider_type == 'OCP':
                            import json
                            auth_id, credentials = has_authentication[0]
                            if not credentials or not credentials.get('cluster_id'):
                                print(f"  ⚠️  OCP authentication missing cluster_id, updating...")
                                try:
                                    credentials = credentials or {}
                                    credentials['cluster_id'] = cluster_id
                                    self.db.execute_query("""
                                        UPDATE api_providerauthentication
                                        SET credentials = %s::jsonb
                                        WHERE id = %s
                                    """, (json.dumps(credentials), auth_id))
                                    print(f"  ✅ Authentication updated with cluster_id")
                                except Exception as e:
                                    print(f"  ⚠️  Failed to update authentication: {e}")

                    # Verify tenant and sources entries exist
                    if self.k8s:
                        print("\n🔍 Verifying tenant and sources configuration...")
                        self._ensure_tenant_and_sources(existing['uuid'], existing['name'], provider_type)

                    return {
                        'passed': True,
                        'created': False,
                        'billing_source_created': True,
                        'provider_uuid': existing['uuid'],
                        'provider_name': existing['name']
                    }
                except Exception as e:
                    print(f"  ❌ Failed to create billing source: {e}")
                    return {
                        'passed': False,
                        'error': f'Billing source creation failed: {e}'
                    }

        # Create new provider
        provider_name = f"{provider_type} Test Provider E2E"
        print(f"\n📝 Creating new {provider_type} provider...")
        try:
            provider_uuid = self.create_provider(
                name=provider_name,
                provider_type=provider_type,
                cluster_id=cluster_id
            )
            print(f"  ✅ {provider_type} provider created")
            print(f"  ℹ️  Name: {provider_name}")
            print(f"  ℹ️  UUID: {provider_uuid}")

            return {
                'passed': True,
                'created': True,
                'provider_uuid': provider_uuid,
                'provider_name': provider_name,
                'provider_type': provider_type
            }
        except Exception as e:
            print(f"  ❌ Provider creation failed: {e}")
            return {
                'passed': False,
                'error': str(e)
            }

