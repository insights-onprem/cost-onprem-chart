"""
Phase 4: Data Upload
=====================

Generate test data with Nise and upload to S3.
This creates predictable scenarios for financial validation.
"""

import os
import gzip
import json
import tarfile
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import boto3

from ..clients.nise import NiseClient
from ..clients.kafka_producer import KafkaProducerClient


class DataUploadPhase:
    """Phase 4: Generate and upload test data"""

    def __init__(self,
                 s3_client,
                 nise_client: NiseClient,
                 bucket: str = "koku-bucket",
                 report_name: str = "test-report",
                 report_prefix: str = "reports",
                 k8s_client=None,
                 db_client=None,
                 provider_uuid: Optional[str] = None,
                 kafka_client: Optional[KafkaProducerClient] = None,
                 org_id: str = "org1234567",
                 s3_endpoint: Optional[str] = None):
        """Initialize data upload phase

        Args:
            s3_client: boto3 S3 client
            nise_client: NiseClient instance
            bucket: S3 bucket name
            report_name: Report name
            report_prefix: S3 key prefix (default: "reports/")
            k8s_client: KubernetesClient (for in-pod execution)
            db_client: DatabaseClient (for provider timestamp updates)
            provider_uuid: Provider UUID (for timestamp updates after upload)
            kafka_client: KafkaProducerClient (for triggering OCP processing)
            org_id: Organization ID for Kafka messages
            s3_endpoint: S3 endpoint URL for Kafka messages
        """
        self.s3 = s3_client
        self.nise = nise_client
        self.bucket = bucket
        self.report_name = report_name
        self.report_prefix = report_prefix
        self.k8s = k8s_client
        self.db = db_client
        self.provider_uuid = provider_uuid
        self.kafka = kafka_client
        self.org_id = org_id
        self.s3_endpoint = s3_endpoint

    def _generate_dynamic_ocp_static_report(self, start_date: datetime, end_date: datetime) -> str:
        """Generate a dynamic OCP static report YAML with current dates.

        This ensures the dates in the nise-generated data match the manifest dates,
        preventing date misalignment issues in aggregation.

        Args:
            start_date: Start date for data generation
            end_date: End date for data generation

        Returns:
            Path to the generated temporary YAML file
        """
        yaml_content = f"""---
# Dynamic OCP Static Report - Generated for E2E Testing
# Generated: {datetime.now().isoformat()}
# Date Range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}

generators:
  - OCPGenerator:
      start_date: {start_date.strftime('%Y-%m-%d')}
      end_date: {end_date.strftime('%Y-%m-%d')}
      nodes:
        - node:
          node_name: test-node-1
          cpu_cores: 2
          memory_gig: 8
          resource_id: test-resource-1
          namespaces:
            test-namespace:
              pods:
                - pod:
                  pod_name: test-pod-1
                  cpu_request: 0.5
                  mem_request_gig: 1
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600
                  cpu_usage:
                    full_period: 0.25
                  mem_usage_gig:
                    full_period: 0.5
                  labels: environment:test|app:smoke-test
"""
        # Create a temporary file for the YAML
        temp_yaml = tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.yml',
            prefix='e2e_ocp_report_',
            delete=False
        )
        temp_yaml.write(yaml_content)
        temp_yaml.close()

        return temp_yaml.name

    def cleanup_ocp_processing_artifacts(self) -> Dict[str, int]:
        """Clean up old data files for OCP

        This ensures a clean state for E2E tests by removing old data files from S3.

        Returns:
            Dict with counts of deleted artifacts:
                - files_deleted: Number of files deleted from S3
        """
        files_deleted = 0

        # Delete data files from S3
        data_prefixes = [
            f'data/{self.org_id}/OCP/',
            f'data/csv/{self.org_id}/OCP/',
        ]

        for prefix in data_prefixes:
            try:
                # List all objects under this prefix
                paginator = self.s3.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)

                for page in pages:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            self.s3.delete_object(Bucket=self.bucket, Key=obj['Key'])
                            files_deleted += 1
            except Exception as e:
                print(f"  ⚠️  Warning: Could not delete files from {prefix}: {e}")

        return {
            'files_deleted': files_deleted,
            # Backwards compatibility
            'parquet_deleted': files_deleted,
            'tables_dropped': 0
        }

    def _restart_koku_components_for_cache_clear(self):
        """Restart Redis and Koku listener to clear stale caches.

        This ensures a clean state by clearing any cached processing state.
        """
        import subprocess
        import time

        namespace = self.k8s.namespace if hasattr(self.k8s, 'namespace') else 'cost-onprem'

        print(f"\n🔄 Clearing Koku caches (workaround for table_exists cache bug)...")

        try:
            # Restart Redis to clear cache
            print(f"  🗑️  Restarting Redis to clear cache...")
            subprocess.run(
                ['kubectl', 'delete', 'pod', '-n', namespace, '-l', 'app.kubernetes.io/name=redis'],
                capture_output=True, timeout=30
            )

            # Wait for Redis to come back
            subprocess.run(
                ['kubectl', 'wait', '--for=condition=ready', 'pod',
                 '-l', 'app.kubernetes.io/name=redis', '-n', namespace, '--timeout=60s'],
                capture_output=True, timeout=70
            )
            print(f"  ✅ Redis restarted")

            # Restart listener to clear in-memory state
            print(f"  🔄 Restarting Koku listener...")
            subprocess.run(
                ['kubectl', 'delete', 'pod', '-n', namespace, '-l', 'app.kubernetes.io/component=listener'],
                capture_output=True, timeout=30
            )

            # Wait for listener to come back
            subprocess.run(
                ['kubectl', 'wait', '--for=condition=ready', 'pod',
                 '-l', 'app.kubernetes.io/component=listener', '-n', namespace, '--timeout=60s'],
                capture_output=True, timeout=70
            )
            print(f"  ✅ Listener restarted")

            # Brief pause to let components stabilize
            time.sleep(3)

        except Exception as e:
            print(f"  ⚠️  Warning: Could not restart components: {e}")
            print(f"  ℹ️  You may need to manually restart Redis and listener pods")

    def check_existing_data(self) -> Dict[str, any]:
        """Check if VALID test data already exists

        Validates that both manifest and CSV files are present and properly linked.

        Returns:
            Dict with:
                - exists: True if any files present
                - valid: True only if manifest + CSV files present and linked
                - count: Total object count
                - manifest: Manifest key if found
                - csv_files: List of CSV keys
                - error: Error message if S3 access fails
        """
        try:
            prefix_path = f'{self.report_prefix}/' if self.report_prefix else ''
            response = self.s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=f"{prefix_path}{self.report_name}/"
            )

            keys = [obj['Key'] for obj in response.get('Contents', [])]

            # Find manifest and CSV files
            manifests = [k for k in keys if k.endswith('-Manifest.json')]
            csv_files = [k for k in keys if k.endswith('.csv.gz') or k.endswith('.csv')]

            # Validate manifest if present
            valid_manifest = False
            if manifests:
                try:
                    manifest_obj = self.s3.get_object(
                        Bucket=self.bucket,
                        Key=manifests[0]
                    )
                    manifest_data = json.loads(manifest_obj['Body'].read())

                    # Check if manifest references existing CSV files
                    referenced_files = manifest_data.get('reportKeys', [])
                    valid_manifest = all(csv in keys for csv in referenced_files)

                except Exception as e:
                    print(f"    ⚠️  Manifest validation failed: {e}")
                    valid_manifest = False

            return {
                'exists': len(keys) > 0,
                'valid': valid_manifest and len(csv_files) > 0,
                'count': len(keys),
                'keys': keys,
                'manifest': manifests[0] if manifests else None,
                'csv_files': csv_files
            }

        except Exception as e:
            return {
                'exists': False,
                'valid': False,
                'count': 0,
                'error': str(e)
            }

    def generate_scenarios(self,
                          scenarios: List[str],
                          start_date: datetime,
                          end_date: datetime) -> Dict[str, str]:
        """Generate multiple test scenarios with Nise

        Args:
            scenarios: List of scenario names
            start_date: Start date
            end_date: End date

        Returns:
            Dict mapping scenario to local path
        """
        print(f"📊 Generating {len(scenarios)} test scenarios with Nise...")

        results = {}
        for scenario in scenarios:
            print(f"  - Generating '{scenario}'...")
            path = self.nise.generate_scenario(scenario, start_date, end_date)
            results[scenario] = path
            print(f"    ✓ Generated at {path}")

        return results

    def generate_basic_aws_data(self,
                               start_date: datetime,
                               end_date: datetime,
                               account_id: str = '123456789012') -> str:
        """Generate basic AWS CUR data

        Args:
            start_date: Start date
            end_date: End date
            account_id: AWS account ID

        Returns:
            Path to generated data
        """
        print("📊 Generating AWS CUR data with Nise...")
        print(f"  - Account: {account_id}")
        print(f"  - Period: {start_date.date()} to {end_date.date()}")

        # Generate with tags for testing
        path = self.nise.generate_aws_cur(
            start_date=start_date,
            end_date=end_date,
            account_id=account_id,
            tags=[
                'environment:production',
                'app:web-server',
                'cost-center:engineering'
            ]
        )

        print(f"  ✓ Data generated at {path}")
        return path

    def upload_nise_data(self,
                        data_path: str,
                        report_prefix: str = "") -> List[str]:
        """Upload Nise-generated data to S3

        Args:
            data_path: Path to Nise output directory
            report_prefix: S3 key prefix

        Returns:
            List of uploaded S3 keys
        """
        uploaded_keys = []

        # Find all files in Nise output
        for root, dirs, files in os.walk(data_path):
            for file in files:
                local_path = os.path.join(root, file)

                # Determine S3 key
                rel_path = os.path.relpath(local_path, data_path)
                s3_key = f"{self.report_name}/{report_prefix}{rel_path}"

                # Upload file
                print(f"  ⬆️  Uploading {file}...")
                with open(local_path, 'rb') as f:
                    self.s3.put_object(
                        Bucket=self.bucket,
                        Key=s3_key,
                        Body=f.read()
                    )

                uploaded_keys.append(s3_key)

        return uploaded_keys

    def create_manifest_for_nise_data(self,
                                     data_keys: List[str],
                                     start_date: datetime,
                                     end_date: datetime) -> str:
        """Create CUR manifest for Nise data

        Args:
            data_keys: List of S3 keys for data files
            start_date: Billing period start
            end_date: Billing period end

        Returns:
            Manifest S3 key
        """
        # Filter to only CSV files
        csv_keys = [k for k in data_keys if k.endswith('.csv.gz') or k.endswith('.csv')]

        manifest = {
            'reportId': f'{self.report_name}-{start_date.strftime("%Y%m%d")}',
            'reportName': self.report_name,
            'version': '1.0',
            'reportKeys': csv_keys,
            'billingPeriod': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            },
            'bucket': self.bucket,
            'reportPathPrefix': self.report_name,
            'timeGranularity': 'DAILY',
            'compression': 'GZIP',
            'format': 'textORcsv'
        }

        # Upload manifest
        period = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
        manifest_key = f"{self.report_name}/{period}/{self.report_name}-Manifest.json"

        self.s3.put_object(
            Bucket=self.bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode('utf-8')
        )

        return manifest_key

    def run_full_scenario_upload(self,
                                scenarios: Optional[List[str]] = None,
                                days_back: int = 30) -> Dict[str, any]:
        """Run complete data generation and upload

        This is the main entry point for Phase 4.

        Args:
            scenarios: List of scenarios (default: basic scenarios)
            days_back: How many days of data to generate

        Returns:
            Upload results dict
        """
        if scenarios is None:
            # Default comprehensive scenario set
            scenarios = [
                'basic_compute',
                'storage_costs',
                'tagged_resources',
            ]

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        print("\n" + "="*60)
        print("Phase 4: Generate & Upload Test Data (Nise)")
        print("="*60)

        # Check existing data
        print("\n🔍 Checking for existing data...")
        existing = self.check_existing_data()

        if existing['exists']:
            print(f"  ⚠️  Found {existing['count']} existing objects")
            print("  Skipping generation (use --force to regenerate)")
            return {
                'skipped': True,
                'existing_count': existing['count']
            }

        # Generate scenarios
        print("\n📊 Generating test scenarios...")
        scenario_paths = self.generate_scenarios(scenarios, start_date, end_date)

        # Upload all scenario data
        print("\n⬆️  Uploading to S3...")
        all_keys = []

        for scenario, path in scenario_paths.items():
            print(f"\n  Uploading scenario: {scenario}")
            keys = self.upload_nise_data(path, report_prefix=f"{scenario}/")
            all_keys.extend(keys)
            print(f"  ✓ Uploaded {len(keys)} files")

        # Create manifest
        print("\n📝 Creating CUR manifest...")
        manifest_key = self.create_manifest_for_nise_data(
            all_keys, start_date, end_date
        )
        print(f"  ✓ Manifest: {manifest_key}")

        # Verify upload
        final_check = self.check_existing_data()

        result = {
            'success': True,
            'scenarios': scenarios,
            'files_uploaded': len(all_keys),
            'total_objects': final_check['count'],
            'manifest_key': manifest_key,
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            }
        }

        print("\n✅ Phase 4 Complete")
        print(f"  - Scenarios: {len(scenarios)}")
        print(f"  - Files uploaded: {len(all_keys)}")
        print(f"  - Total S3 objects: {final_check['count']}")

        return result

    def run_quick_upload(self,
                        days_back: int = 7) -> Dict[str, any]:
        """Quick single-scenario upload for fast testing

        Args:
            days_back: Days of data to generate

        Returns:
            Upload results
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        print("\n🚀 Quick data upload (single scenario)...")

        # Generate basic AWS data only
        data_path = self.generate_basic_aws_data(start_date, end_date)

        # Upload
        print("\n⬆️  Uploading to S3...")
        keys = self.upload_nise_data(data_path)

        # Create manifest
        manifest_key = self.create_manifest_for_nise_data(
            keys, start_date, end_date
        )

        return {
            'success': True,
            'mode': 'quick',
            'files_uploaded': len(keys),
            'manifest_key': manifest_key
        }

    def _generate_minimal_aws_csv(self, start_date: datetime, end_date: datetime) -> str:
        """Generate minimal AWS CUR CSV for smoke tests (fast!)

        Returns CSV with 4 rows covering essential columns for pipeline testing.
        This is NOT for financial validation - use nise for that.
        """
        # Format dates for CSV
        usage_start = start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        usage_end = end_date.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Minimal but complete AWS CUR format
        csv_lines = [
            # Header
            'identity/LineItemId,identity/TimeInterval,bill/InvoiceId,bill/BillingEntity,bill/BillType,bill/PayerAccountId,bill/BillingPeriodStartDate,bill/BillingPeriodEndDate,lineItem/UsageAccountId,lineItem/LineItemType,lineItem/UsageStartDate,lineItem/UsageEndDate,lineItem/ProductCode,lineItem/UsageType,lineItem/Operation,lineItem/AvailabilityZone,lineItem/ResourceId,lineItem/UsageAmount,lineItem/NormalizationFactor,lineItem/NormalizedUsageAmount,lineItem/CurrencyCode,lineItem/UnblendedRate,lineItem/UnblendedCost,lineItem/BlendedRate,lineItem/BlendedCost,lineItem/LineItemDescription,lineItem/TaxType,product/ProductName,product/accountAssistance,product/architecturalReview,product/architectureSupport,product/availability,product/awsSupportPhone,product/awsSupportTicket,product/bestPractices,product/caseSeverity/respondAndResolveAccess,product/clockSpeed,product/contentType,product/currentGeneration,product/customerServiceAndCommunities,product/databaseEngine,product/dedicatedEbsThroughput,product/deploymentOption,product/description,product/durability,product/ecu,product/enhancedNetworkingSupported,product/feeCode,product/feeDescription,product/filesystemType,product/fromLocation,product/fromLocationType,product/group,product/groupDescription,product/includedServices,product/instanceFamily,product/instanceType,product/intelAvxAvailable,product/intelAvx2Available,product/intelTurboAvailable,product/io,product/isshadow,product/launchSupport,product/licenseModel,product/location,product/locationType,product/maxIopsBurstPerformance,product/maxIopsvolume,product/maxThroughputvolume,product/maxVolumeSize,product/memory,product/messageDeliveryFrequency,product/messageDeliveryOrder,product/minVolumeSize,product/networkPerformance,product/normalizedSizeFactor,product/operatingSystem,product/operation,product/operationsSupport,product/origin,product/physicalProcessor,product/preInstalledSw,product/proactiveGuidance,product/processorArchitecture,product/processorFeatures,product/productFamily,product/programmaticCaseManagement,product/provisioned,product/queueType,product/recipient,product/region,product/regionCode,product/requestDescription,product/requestType,product/resourceEndpoint,product/servicecode,product/servicename,product/sku,product/softwareType,product/standardGroup,product/standardOneYearNoUpfrontReservedInstancesPrice,product/standardStorageRetentionIncluded,product/standardThreeYearNoUpfrontReservedInstancesPrice,product/storage,product/storageClass,product/storageMedia,product/storageType,product/technicalSupport,product/tenancy,product/thirdpartySoftwareSupport,product/toLocation,product/toLocationType,product/training,product/transferType,product/usagetype,product/vcpu,product/version,product/volumeType,product/whoCanOpenCases,pricing/LeaseContractLength,pricing/OfferingClass,pricing/PurchaseOption,pricing/publicOnDemandCost,pricing/publicOnDemandRate,pricing/term,pricing/unit,reservation/AvailabilityZone,reservation/NormalizedUnitsPerReservation,reservation/NumberOfReservations,reservation/ReservationARN,reservation/TotalReservedNormalizedUnits,reservation/TotalReservedUnits,reservation/UnitsPerReservation,savingsPlan/SavingsPlanARN,savingsPlan/SavingsPlanEffectiveCost,savingsPlan/SavingsPlanRate,savingsPlan/TotalCommitmentToDate,savingsPlan/UsedCommitment',
            # Row 1: EC2 instance
            f'e6fce739e6bcb6ce,{usage_start}/{usage_end},12345678,AWS,Anniversary,123456789012,2024-11-01T00:00:00Z,2024-11-30T23:59:59Z,123456789012,Usage,{usage_start},{usage_end},AmazonEC2,BoxUsage:t3.micro,RunInstances,us-east-1a,i-1234567890abcdef0,1.0,,,USD,0.0104,0.0104,0.0104,0.0104,$0.0104 per On Demand Linux t3.micro Instance Hour,,Amazon Elastic Compute Cloud,,,,,,,,,,,Yes,,,,,,,,,,,,,,,,,,,,,,,,t3,t3.micro,Yes,Yes,Yes,,,No,No license required,US East (N. Virginia),AWS Region,,,,,,,,,,Linux,RunInstances:0002,,,,Intel Xeon Platinum 8000,NA,,x86_64,,Compute Instance (box usage),,No,,,,us-east-1,us-east-1,,,,,AmazonEC2,Amazon Elastic Compute Cloud,ABCDEFGHIJKLMNOP,,,,,,,,,Shared,,,,,,,USE1-BoxUsage:t3.micro,2,,,,,,,,,Hrs,,,,,,,,,,',
            # Row 2: S3 storage
            f'a1b2c3d4e5f6g7h8,{usage_start}/{usage_end},12345678,AWS,Anniversary,123456789012,2024-11-01T00:00:00Z,2024-11-30T23:59:59Z,123456789012,Usage,{usage_start},{usage_end},AmazonS3,TimedStorage-ByteHrs,,,,,1073741824.0,,,USD,0.000000027778,0.0298,0.000000027778,0.0298,$0.023 per GB - first 50 TB / month of storage used,,Amazon Simple Storage Service,,,,,,,,,,,,,,,,,,High,,,,,,,,,,,,,,,,,,,,,,,,,US East (N. Virginia),AWS Region,,,,,,AmazonS3,Amazon Simple Storage Service,QRSTUVWXYZ123456,,,,,,,Standard,,General Purpose,,,,,,,,,,,TimedStorage-ByteHrs,,,,,,,,,,,GB-Mo,,,,,,,,,,',
            # Row 3: RDS instance
            f'h8g7f6e5d4c3b2a1,{usage_start}/{usage_end},12345678,AWS,Anniversary,123456789012,2024-11-01T00:00:00Z,2024-11-30T23:59:59Z,123456789012,Usage,{usage_start},{usage_end},AmazonRDS,InstanceUsage:db.t3.micro,CreateDBInstance:0014,us-east-1b,db-ABCDEFGHIJKLMNOP,1.0,,,USD,0.017,0.017,0.017,0.017,$0.017 per RDS db.t3.micro instance hour (or partial hour),,Amazon Relational Database Service,,,,,,,,,,,,MySQL,,,,,,,,,,,,,,,,,db.t3,db.t3.micro,Yes,Yes,Yes,,,No,No license required,US East (N. Virginia),AWS Region,,,,,,,,,,MySQL,CreateDBInstance:0014,,,,Intel Xeon Platinum 8000,NA,,x86_64,,Database Instance,,,,,,,,,,,us-east-1,us-east-1,,,,,AmazonRDS,Amazon Relational Database Service,MNOPQRSTUVWXYZ12,,,,,,,,,Multi-AZ,,,,,,,,USE1-InstanceUsage:db.t3.micro,2,,,,,,,,,,,Hrs,,,,,,,,,,',
            # Row 4: Data transfer
            f'z9y8x7w6v5u4t3s2,{usage_start}/{usage_end},12345678,AWS,Anniversary,123456789012,2024-11-01T00:00:00Z,2024-11-30T23:59:59Z,123456789012,Usage,{usage_start},{usage_end},AmazonEC2,DataTransfer-Out-Bytes,,,,,10737418240.0,,,USD,0.000000000931,0.01,0.000000000931,0.01,$0.09 per GB - first 10 TB / month data transfer out beyond the global free tier,,Amazon Elastic Compute Cloud,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,US East (N. Virginia) To Internet,FromLocation-InterRegion,,,us-east-1,us-east-1,,,Internet,External,,,AmazonEC2,Amazon Elastic Compute Cloud,3456789ABCDEFGHI,,,,,,,,,,,,,,,,Internet,AWS to Internet,DataTransfer-Out-Bytes,,,,,,,,,,,,GB,,,,,,,,,,'
        ]

        return '\n'.join(csv_lines)

    def upload_aws_cur_format(self,
                             start_date: datetime,
                             end_date: datetime,
                             force: bool = False,
                             smoke_test: bool = False) -> Dict[str, any]:
        """Generate and upload AWS CUR format data directly

        Creates proper AWS Cost and Usage Report format data and uploads
        to S3 using the external HTTPS endpoint (no need for in-pod execution).

        Args:
            start_date: Start date for data
            end_date: End date for data
            force: Force regeneration even if data exists
            smoke_test: Use fast hardcoded CSV (for smoke tests) instead of nise

        Returns:
            Upload results dict
        """
        mode = "SMOKE TEST (fast 4-row CSV)" if smoke_test else "FULL VALIDATION (nise)"
        print("\n" + "="*60)
        print(f"Phase 4: Generate & Upload Test Data (AWS CUR Format) - {mode}")
        print("="*60)

        # Check existing
        print("\n🔍 Checking for existing data...")
        existing = self.check_existing_data()

        # Surface S3 errors immediately
        if 'error' in existing:
            print(f"\n❌ S3 access error: {existing['error']}")
            print("   Check credentials and endpoint configuration")
            return {
                'success': False,
                'error': existing['error']
            }

        if existing['exists'] and not force:
            if not existing.get('valid'):
                print(f"  ⚠️  Found {existing['count']} objects but NO VALID MANIFEST")
                print(f"  💡 Run with --force to regenerate")
                return {
                    'skipped': True,
                    'reason': 'invalid_data',
                    'valid': False,
                    'existing_count': existing['count']
                }
            else:
                print(f"  ✅ Valid data exists:")
                print(f"     - Manifest: {existing['manifest']}")
                print(f"     - CSV files: {len(existing['csv_files'])}")
                print(f"  💡 Run with --force to regenerate")
                return {
                    'passed': True,
                    'skipped': True,
                    'reason': 'valid_data_exists',
                    'valid': True,
                    'existing_count': existing['count'],
                    'manifest': existing['manifest']
                }

        if existing['exists'] and force:
            print(f"  🗑️  Force mode: Deleting {existing['count']} existing objects...")
            for key in existing.get('keys', []):
                self.s3.delete_object(Bucket=self.bucket, Key=key)
                print(f"     - Deleted: {key}")
            print(f"  ✅ Cleaned existing data")

        # Format dates - use MASU's monthly date format (YYYYMMDD-YYYYMMDD)
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        period = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"

        if smoke_test:
            # SMOKE TEST MODE: Fast 4-row hardcoded CSV
            print(f"\n📊 Generating AWS CUR data (smoke test mode - fast!)...")
            print(f"   Date range: {start_str} to {end_str}")
            print(f"   ⚡ Using hardcoded 4-row CSV (completes in ~1 second)")
            print(f"   ℹ️  For smoke tests only - NOT for financial validation")

            # Generate minimal CSV in memory
            csv_content = self._generate_minimal_aws_csv(start_date, end_date)

            # Upload to S3
            prefix_path = f'{self.report_prefix}/' if self.report_prefix else ''
            csv_key = f'{prefix_path}{self.report_name}/{self.report_name}.csv'

            print(f"\n⬆️  Uploading minimal CSV to S3...")
            print(f"  ⬆️  Uploading: {csv_key}")
            self.s3.put_object(
                Bucket=self.bucket,
                Key=csv_key,
                Body=csv_content.encode('utf-8')
            )
            uploaded_count = 1

        else:
            # FULL VALIDATION MODE: Nise-generated data
            print(f"\n📊 Generating AWS CUR data with nise...")
            print(f"   Date range: {start_str} to {end_str}")
            print(f"   ⚠️  This uses nise for controlled test scenarios (may take 1-5 minutes)")
            print(f"   ℹ️  This enables financial correctness validation")

            # Generate AWS CUR data using nise (controlled scenarios)
            output_dir = self.nise.generate_aws_cur(
                start_date=start_date,
                end_date=end_date,
                account_id='123456789012'
            )

            print(f"  ✅ Nise generation complete: {output_dir}")

            # Upload all nise-generated files to S3
            print(f"\n⬆️  Uploading nise-generated files to S3...")
            prefix_path = f'{self.report_prefix}/' if self.report_prefix else ''
            uploaded_count = 0

            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Preserve nise's directory structure
                    relative_path = os.path.relpath(file_path, output_dir)
                    s3_key = f'{prefix_path}{self.report_name}/{relative_path}'

                    print(f"  ⬆️  Uploading: {s3_key}")
                    with open(file_path, 'rb') as f:
                        self.s3.put_object(
                            Bucket=self.bucket,
                            Key=s3_key,
                            Body=f.read()
                        )
                    uploaded_count += 1

        # Verify upload
        response = self.s3.list_objects_v2(
            Bucket=self.bucket,
            Prefix=f'{prefix_path}{self.report_name}/'
        )
        total_objects = response.get('KeyCount', 0)

        print(f"\n✅ Upload Complete:")
        print(f"   - Files uploaded: {uploaded_count}")
        print(f"   - Total objects in S3: {total_objects}")
        print(f"   - Source: {output_dir}")

        # Reset provider timestamps after upload (belt-and-suspenders approach)
        # NOTE: Processing phase also does this, but we do it here too
        # in case there's a delay between upload and processing trigger
        if self.db and self.provider_uuid:
            print(f"\n🔄 Resetting provider timestamps to enable processing...")
            try:
                result = self.db.execute_query("""
                    UPDATE api_provider
                    SET data_updated_timestamp = NOW(),
                        polling_timestamp = NOW() - INTERVAL '10 minutes'
                    WHERE uuid = %s
                    RETURNING uuid, polling_timestamp, data_updated_timestamp
                """, (self.provider_uuid,))
                if result:
                    print(f"  ✅ Provider {self.provider_uuid} ready for polling")
                else:
                    print(f"  ⚠️  Provider not found in database")
            except Exception as e:
                print(f"  ⚠️  Failed to reset timestamps: {e}")
                print(f"  ℹ️  Processing may be delayed until next natural polling cycle")

        return {
            'passed': True,
            'success': True,
            'mode': 'smoke-test' if smoke_test else 'nise-generated',
            'files_uploaded': uploaded_count,
            'total_objects': total_objects
        }

    def upload_ocp_format(self,
                          start_date: datetime,
                          end_date: datetime,
                          force: bool = False,
                          smoke_test: bool = False) -> Dict[str, any]:
        """Upload OCP cost and usage data in TAR.GZ format

        This method creates OCP reports matching the koku-metrics-operator format:
        1. Generate CSV files with nise
        2. Create manifest.json
        3. Package into TAR.GZ
        4. Upload TAR.GZ to S3
        5. Send Kafka message with TAR.GZ URL

        Args:
            start_date: Start date for data
            end_date: End date for data
            force: Force regeneration even if data exists
            smoke_test: Use minimal nise scenario (faster) vs full scenario (more comprehensive)

        Returns:
            Upload results dict
        """
        mode = "SMOKE TEST (minimal nise scenario)" if smoke_test else "FULL VALIDATION (comprehensive nise scenario)"
        print("\n" + "="*60)
        print(f"Phase 4: Generate & Upload Test Data (OCP Format - TAR.GZ) - {mode}")
        print("="*60)

        # Check existing
        print("\n🔍 Checking for existing data...")
        existing = self.check_existing_data()

        if 'error' in existing:
            print(f"\n❌ S3 access error: {existing['error']}")
            return {'success': False, 'error': existing['error']}

        if existing['exists'] and not force:
            # For TAR.GZ format, we check for .tar.gz files
            tarball_keys = [k for k in existing.get('keys', []) if k.endswith('.tar.gz')]
            if tarball_keys:
                print(f"  ✅ Valid tarball exists: {tarball_keys[0]}")
                print(f"  💡 Run with --force to regenerate")
                return {'passed': True, 'skipped': True, 'reason': 'valid_data_exists', 'valid': True}
            else:
                print(f"  ⚠️  Found {existing['count']} objects but NO TARBALL")
                print(f"  💡 Run with --force to regenerate")
                return {'skipped': True, 'reason': 'invalid_data', 'valid': False}

        if existing['exists'] and force:
            print(f"  🗑️  Force mode: Deleting {existing['count']} existing objects...")
            for key in existing.get('keys', []):
                self.s3.delete_object(Bucket=self.bucket, Key=key)
            print(f"  ✅ Cleaned existing data")

            # Also clean up S3 data files
            # This ensures reports aren't seen as "already processed"
            print(f"  🧹 Cleaning up processing artifacts...")
            cleanup_result = self.cleanup_ocp_processing_artifacts()
            if cleanup_result.get('files_deleted', 0) > 0:
                print(f"     - Deleted {cleanup_result['files_deleted']} S3 files")

        prefix_path = f'{self.report_prefix}/' if self.report_prefix else ''
        cluster_id = 'test-cluster-123'
        request_id = f"e2e-test-{uuid.uuid4()}"

        # Adjust date range for smoke test to minimize data generation
        if smoke_test:
            # SMOKE TEST: Just 1 day of data to avoid file splitting
            nise_start = start_date
            nise_end = start_date + timedelta(days=1)
        else:
            # FULL TEST: Use provided date range
            nise_start = start_date
            nise_end = end_date

        start_str = nise_start.strftime('%Y-%m-%d')
        end_str = nise_end.strftime('%Y-%m-%d')

        # Step 1: Generate OCP data using nise
        print(f"\n📊 Generating OCP data with nise ({mode})...")
        print(f"   Date range: {start_str} to {end_str}")
        print(f"   Cluster: {cluster_id}")

        if smoke_test:
            print(f"   📋 Mode: MINIMAL scenario for fast validation (~30-60 seconds)")
            print(f"   ℹ️  - POD USAGE ONLY (excluding VM/storage to isolate issues)")
            print(f"   ℹ️  - Suitable for smoke testing and CI/CD")
            # Generate dynamic static report with current dates
            static_report = self._generate_dynamic_ocp_static_report(nise_start, nise_end)
            print(f"   📅 Using dynamic dates: {start_str} to {end_str}")
        else:
            print(f"   📋 Mode: COMPREHENSIVE scenario (~1-3 minutes)")
            print(f"   ℹ️  - Full data set with multiple days and resources")
            print(f"   ℹ️  - Complete financial validation")
            static_report = None

        output_dir = self.nise.generate_ocp_usage(
            start_date=nise_start,
            end_date=nise_end,
            cluster_id=cluster_id,
            static_report_file=static_report if smoke_test else None
        )

        print(f"  ✅ Nise generation complete: {output_dir}")

        # Step 2: Collect CSV filenames
        csv_files = []
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                if file.endswith('.csv'):
                    csv_files.append(file)

        # Filter for smoke test: Include pod_usage and labels (needed for summary aggregation)
        if smoke_test:
            essential_files = [f for f in csv_files if any(x in f.lower() for x in ['pod_usage', 'node_label', 'namespace_label'])]
            if essential_files:
                print(f"\n📋 Smoke test mode: Filtering to essential files (pod + labels)")
                print(f"   - Found {len(csv_files)} total files")
                print(f"   - Using {len(essential_files)} essential file(s)")
                csv_files = essential_files
            else:
                print(f"\n⚠️  No essential files found in {len(csv_files)} files!")

        print(f"\n📋 Found {len(csv_files)} CSV files")

        # Step 3: Create manifest.json
        print(f"\n📝 Creating manifest.json...")
        manifest = self._create_ocp_manifest(
            csv_files=csv_files,
            cluster_id=cluster_id,
            start_date=start_date,
            end_date=end_date
        )
        print(f"  ✅ Manifest UUID: {manifest['uuid']}")

        # Step 4: Create TAR.GZ package
        print(f"\n📦 Creating TAR.GZ package...")
        try:
            tarball_path = self._create_tarball(
                csv_directory=output_dir,
                manifest=manifest,
                request_id=request_id
            )
        except Exception as e:
            print(f"  ❌ Failed to create tarball: {e}")
            return {'success': False, 'error': str(e)}

        # Step 5: Upload TAR.GZ to S3
        tarball_name = os.path.basename(tarball_path)
        s3_key = f'{prefix_path}{self.report_name}/{tarball_name}'

        print(f"\n⬆️  Uploading TAR.GZ to S3...")
        print(f"  ⬆️  Key: {s3_key}")
        try:
            with open(tarball_path, 'rb') as f:
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=s3_key,
                    Body=f.read()
                )
            print(f"  ✅ Upload complete")
        except Exception as e:
            print(f"  ❌ Upload failed: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            # Clean up temp tarball
            import shutil
            shutil.rmtree(os.path.dirname(tarball_path), ignore_errors=True)

        # Verify upload
        response = self.s3.list_objects_v2(
            Bucket=self.bucket,
            Prefix=f'{prefix_path}{self.report_name}/'
        )
        total_objects = response.get('KeyCount', 0)

        print(f"\n✅ Upload Complete:")
        print(f"   - Tarball uploaded: {tarball_name}")
        print(f"   - CSVs packaged: {len(csv_files)}")
        print(f"   - Total S3 objects: {total_objects}")

        # Step 6: Generate S3 Presigned URL
        # The listener uses requests.get(url) which requires pre-authenticated URLs.
        # In production, Red Hat Ingress API provides these. For on-prem E2E tests,
        # we generate S3 presigned URLs that work with requests.get() without credentials.
        print(f"\n🔐 Generating presigned URL for listener download...")
        try:
            presigned_url = self.s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket,
                    'Key': s3_key
                },
                ExpiresIn=3600  # Valid for 1 hour (enough for E2E test)
            )
            print(f"  ✅ Presigned URL generated (expires in 1 hour)")
            print(f"  ℹ️  This URL works with requests.get() - no additional credentials needed")
        except Exception as e:
            print(f"  ❌ Failed to generate presigned URL: {e}")
            return {'success': False, 'error': f'Presigned URL generation failed: {str(e)}'}

        # Step 7: Send Kafka message with presigned URL
        kafka_result = None
        if self.kafka:
            print(f"\n📨 Publishing Kafka message to trigger OCP processing...")
            print(f"  Presigned URL: {presigned_url[:80]}...")

            try:
                kafka_result = self.kafka.send_ocp_report_message(
                    request_id=request_id,
                    tarball_url=presigned_url,  # Presigned URL!
                    org_id=self.org_id,
                    cluster_id=cluster_id
                )

                if kafka_result.get('success'):
                    print(f"  ✅ Kafka message published successfully")
                    print(f"     Request ID: {kafka_result.get('request_id')}")
                else:
                    print(f"  ⚠️  Kafka publishing failed: {kafka_result.get('error')}")
            except Exception as e:
                print(f"  ⚠️  Failed to publish Kafka message: {e}")

        return {
            'passed': True,
            'success': True,
            'mode': 'minimal-nise-tarball' if smoke_test else 'comprehensive-nise-tarball',
            'tarball_uploaded': tarball_name,
            'csv_files_packaged': len(csv_files),
            'total_objects': total_objects,
            'presigned_url': presigned_url,
            'kafka_published': kafka_result.get('success') if kafka_result else False,
            'manifest_uuid': manifest['uuid']  # Track for monitoring
        }

    def upload_provider_data(self,
                            provider_type: str,
                            start_date: datetime,
                            end_date: datetime,
                            force: bool = False,
                            smoke_test: bool = False) -> Dict[str, any]:
        """Generic multi-provider upload method

        Routes to the appropriate provider-specific upload method.

        Args:
            provider_type: 'AWS', 'Azure', 'GCP', or 'OCP'
            start_date: Start date for data
            end_date: End date for data
            force: Force regeneration even if data exists
            smoke_test: Use fast hardcoded CSV (smoke tests) instead of nise

        Returns:
            Upload results dict
        """
        if provider_type == 'AWS':
            return self.upload_aws_cur_format(start_date, end_date, force, smoke_test)
        elif provider_type == 'Azure':
            return self.upload_azure_export_format(start_date, end_date, force)
        elif provider_type == 'GCP':
            return self.upload_gcp_export_format(start_date, end_date, force)
        elif provider_type == 'OCP':
            return self.upload_ocp_format(start_date, end_date, force, smoke_test)
        else:
            return {
                'success': False,
                'error': f'Unsupported provider type: {provider_type}'
            }

    def _create_ocp_manifest(self,
                             csv_files: List[str],
                             cluster_id: str,
                             start_date: datetime,
                             end_date: datetime) -> Dict:
        """Create OCP manifest.json matching koku-metrics-operator format

        Args:
            csv_files: List of CSV filenames (not full paths)
            cluster_id: OpenShift cluster ID
            start_date: Report start date
            end_date: Report end date

        Returns:
            Manifest dictionary
        """
        manifest_uuid = str(uuid.uuid4())

        manifest = {
            "uuid": manifest_uuid,
            "cluster_id": cluster_id,
            "version": "e2e-test-v1.0",
            "date": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            "files": csv_files,
            "resource_optimization_files": None,
            "start": start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "end": end_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "certified": True,
            "daily_reports": True
        }

        return manifest

    def _create_tarball(self,
                        csv_directory: str,
                        manifest: Dict,
                        request_id: str) -> str:
        """Create TAR.GZ package with manifest.json and CSV files

        Args:
            csv_directory: Directory containing CSV files
            manifest: Manifest dictionary
            request_id: Request ID for the tarball filename

        Returns:
            Path to created tarball
        """
        # Create temp directory for tarball creation
        temp_dir = tempfile.mkdtemp(prefix='e2e-ocp-tar-')

        try:
            # Write manifest.json to temp directory
            manifest_path = os.path.join(temp_dir, 'manifest.json')
            with open(manifest_path, 'w') as f:
                json.dump(manifest, f, indent=2)

            # Create tarball
            tarball_name = f"{request_id}.tar.gz"
            tarball_path = os.path.join(temp_dir, tarball_name)

            with tarfile.open(tarball_path, 'w:gz') as tar:
                # Add manifest.json
                tar.add(manifest_path, arcname='manifest.json')

                # Add all CSV files from the nise output directory
                for root, dirs, files in os.walk(csv_directory):
                    for file in files:
                        if file.endswith('.csv'):
                            file_path = os.path.join(root, file)
                            # Add with just the filename (no directory structure)
                            tar.add(file_path, arcname=file)

            print(f"  ✅ Created tarball: {tarball_name}")
            print(f"     - manifest.json")
            print(f"     - {len(manifest['files'])} CSV files")

            return tarball_path

        except Exception as e:
            print(f"  ❌ Failed to create tarball: {e}")
            # Clean up temp directory on error
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def upload_azure_export_format(self,
                                   start_date: datetime,
                                   end_date: datetime,
                                   force: bool = False) -> Dict[str, any]:
        """Generate and upload Azure Cost Export format data

        Creates Azure cost export format data and uploads to S3/MinIO.
        For on-prem deployments, Azure data is stored in S3 (MinIO).

        Args:
            start_date: Start date for data
            end_date: End date for data
            force: Force regeneration even if data exists

        Returns:
            Upload results dict
        """
        print("\n" + "="*60)
        print("Phase 4: Generate & Upload Test Data (Azure Export Format)")
        print("="*60)

        # Check existing
        print("\n🔍 Checking for existing data...")
        existing = self.check_existing_data()

        if 'error' in existing:
            print(f"\n❌ S3 access error: {existing['error']}")
            return {'success': False, 'error': existing['error']}

        if existing['exists'] and not force:
            if existing.get('valid'):
                print(f"  ✅ Valid data exists")
                print(f"  💡 Run with --force to regenerate")
                return {
                    'passed': True,
                    'skipped': True,
                    'reason': 'valid_data_exists',
                    'valid': True
                }

        if existing['exists'] and force:
            print(f"  🗑️  Force mode: Deleting {existing['count']} existing objects...")
            for key in existing.get('keys', []):
                self.s3.delete_object(Bucket=self.bucket, Key=key)
            print(f"  ✅ Cleaned existing data")

        # Generate Azure CSV data inline (fast!)
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        period = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"

        print(f"\n📊 Generating Azure cost export data ({start_str} to {end_str})...")
        print(f"   Period directory: {period}")

        # Azure Cost Export CSV format
        csv_data = f"""Date,BillingPeriodStartDate,BillingPeriodEndDate,Quantity,ResourceRate,CostInBillingCurrency,EffectivePrice,UnitPrice,PayGPrice,SubscriptionId,ResourceGroup,MeterCategory,MeterSubCategory,ResourceLocation,ServiceName,ResourceId
{start_str},{start_str},{end_str},24.0,0.0416,1.00,0.0416,0.0416,0.0416,11111111-1111-1111-1111-111111111111,test-rg,Virtual Machines,General Purpose,East US,Virtual Machines,/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/vm1
{start_str},{start_str},{end_str},24.0,0.0832,2.00,0.0832,0.0832,0.0832,11111111-1111-1111-1111-111111111111,test-rg,Virtual Machines,Memory Optimized,East US,Virtual Machines,/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/vm2
{start_str},{start_str},{end_str},100.0,0.08,8.00,0.08,0.08,0.08,11111111-1111-1111-1111-111111111111,test-rg,Storage,Block Blob Storage,East US,Storage,/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Storage/storageAccounts/storage1
{start_str},{start_str},{end_str},50.0,0.08,4.00,0.08,0.08,0.08,11111111-1111-1111-1111-111111111111,test-rg,Storage,Block Blob Storage,West US,Storage,/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Storage/storageAccounts/storage2
{start_str},{start_str},{end_str},1000.0,0.0004,0.40,0.0004,0.0004,0.0004,11111111-1111-1111-1111-111111111111,test-rg,Networking,Bandwidth,Global,Bandwidth,/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Network/virtualNetworks/vnet1
"""

        # Upload CSV files to S3
        print(f"\n⬆️  Uploading Azure files to S3...")
        prefix_path = f'{self.report_prefix}/' if self.report_prefix else ''

        # Upload to current month
        csv_key = f'{prefix_path}{self.report_name}/{period}/costreport_{period}.csv'
        print(f"  ⬆️  Uploading: {csv_key}")
        self.s3.put_object(
            Bucket=self.bucket,
            Key=csv_key,
            Body=csv_data.encode('utf-8')
        )

        # Upload to previous month for MASU discovery
        if start_date.month == 1:
            prev_month_start = datetime(start_date.year - 1, 12, 1)
            prev_month_end = datetime(start_date.year, 1, 1)
        else:
            prev_month_start = datetime(start_date.year, start_date.month - 1, 1)
            prev_month_end = datetime(start_date.year, start_date.month, 1)

        prev_period = f"{prev_month_start.strftime('%Y%m%d')}-{prev_month_end.strftime('%Y%m%d')}"
        prev_csv_key = f'{prefix_path}{self.report_name}/{prev_period}/costreport_{prev_period}.csv'

        print(f"  ⬆️  Copying to previous month: {prev_csv_key}")
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={'Bucket': self.bucket, 'Key': csv_key},
            Key=prev_csv_key
        )

        print(f"\n✅ Azure data uploaded successfully")
        print(f"  Files: 2 (current + previous month)")

        return {
            'passed': True,
            'success': True,
            'mode': 'azure-export',
            'files_uploaded': uploaded_count,
            'provider_type': 'Azure'
        }

    def upload_gcp_export_format(self,
                                 start_date: datetime,
                                 end_date: datetime,
                                 force: bool = False) -> Dict[str, any]:
        """Generate and upload GCP Billing Export format data

        Creates GCP billing export format data and uploads to S3/MinIO.
        For on-prem deployments, GCP data is stored in S3 (MinIO).

        Args:
            start_date: Start date for data
            end_date: End date for data
            force: Force regeneration even if data exists

        Returns:
            Upload results dict
        """
        print("\n" + "="*60)
        print("Phase 4: Generate & Upload Test Data (GCP Export Format)")
        print("="*60)

        # Check existing
        print("\n🔍 Checking for existing data...")
        existing = self.check_existing_data()

        if 'error' in existing:
            print(f"\n❌ S3 access error: {existing['error']}")
            return {'success': False, 'error': existing['error']}

        if existing['exists'] and not force:
            if existing.get('valid'):
                print(f"  ✅ Valid data exists")
                print(f"  💡 Run with --force to regenerate")
                return {
                    'passed': True,
                    'skipped': True,
                    'reason': 'valid_data_exists',
                    'valid': True
                }

        if existing['exists'] and force:
            print(f"  🗑️  Force mode: Deleting {existing['count']} existing objects...")
            for key in existing.get('keys', []):
                self.s3.delete_object(Bucket=self.bucket, Key=key)
            print(f"  ✅ Cleaned existing data")

        # Generate GCP CSV data inline (fast!)
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        period = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"

        print(f"\n📊 Generating GCP billing export data ({start_str} to {end_str})...")
        print(f"   Period directory: {period}")

        # GCP Billing Export CSV format
        csv_data = f"""usage_start_time,usage_end_time,export_time,cost,currency_conversion_rate,usage_amount,usage_amount_in_pricing_units,credit_amount,invoice_month,project_id,project_name,service_description,sku_description,location_region
{start_str}T00:00:00Z,{start_str}T01:00:00Z,{end_str}T00:00:00Z,1.00,1.0,24.0,24.0,0.0,{start_date.strftime('%Y%m')},test-project-12345,Test Project,Compute Engine,N1 Predefined Instance Core running,us-east1
{start_str}T00:00:00Z,{start_str}T01:00:00Z,{end_str}T00:00:00Z,2.00,1.0,24.0,24.0,0.0,{start_date.strftime('%Y%m')},test-project-12345,Test Project,Compute Engine,N1 Predefined Instance Ram running,us-east1
{start_str}T00:00:00Z,{start_str}T01:00:00Z,{end_str}T00:00:00Z,8.00,1.0,100.0,100.0,0.0,{start_date.strftime('%Y%m')},test-project-12345,Test Project,Cloud Storage,Standard Storage US,us-east1
{start_str}T00:00:00Z,{start_str}T01:00:00Z,{end_str}T00:00:00Z,4.00,1.0,50.0,50.0,0.0,{start_date.strftime('%Y%m')},test-project-12345,Test Project,Cloud Storage,Standard Storage US,us-west1
{start_str}T00:00:00Z,{start_str}T01:00:00Z,{end_str}T00:00:00Z,0.40,1.0,1000.0,1000.0,0.0,{start_date.strftime('%Y%m')},test-project-12345,Test Project,Networking,Network Internet Egress from Americas to Americas,us
"""

        # Upload CSV files to S3
        print(f"\n⬆️  Uploading GCP files to S3...")
        prefix_path = f'{self.report_prefix}/' if self.report_prefix else ''

        # Upload to current month
        csv_key = f'{prefix_path}{self.report_name}/{period}/gcp_billing_{period}.csv'
        print(f"  ⬆️  Uploading: {csv_key}")
        self.s3.put_object(
            Bucket=self.bucket,
            Key=csv_key,
            Body=csv_data.encode('utf-8')
        )

        # Upload to previous month for MASU discovery
        if start_date.month == 1:
            prev_month_start = datetime(start_date.year - 1, 12, 1)
            prev_month_end = datetime(start_date.year, 1, 1)
        else:
            prev_month_start = datetime(start_date.year, start_date.month - 1, 1)
            prev_month_end = datetime(start_date.year, start_date.month, 1)

        prev_period = f"{prev_month_start.strftime('%Y%m%d')}-{prev_month_end.strftime('%Y%m%d')}"
        prev_csv_key = f'{prefix_path}{self.report_name}/{prev_period}/gcp_billing_{prev_period}.csv'

        print(f"  ⬆️  Copying to previous month: {prev_csv_key}")
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={'Bucket': self.bucket, 'Key': csv_key},
            Key=prev_csv_key
        )

        print(f"\n✅ GCP data uploaded successfully")
        print(f"  Files: 2 (current + previous month)")

        return {
            'passed': True,
            'success': True,
            'mode': 'gcp-export',
            'files_uploaded': uploaded_count,
            'provider_type': 'GCP'
        }

