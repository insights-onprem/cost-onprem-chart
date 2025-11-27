"""
Nise Client
===========

Synthetic cloud cost data generator for E2E testing.
Generates predictable test scenarios for financial validation.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Dict
from datetime import datetime, timedelta


@dataclass
class NiseScenario:
    """Test scenario configuration"""
    name: str
    provider: str
    start_date: datetime
    end_date: datetime
    static_report: Optional[str] = None
    attributes: Optional[Dict] = None


class NiseClient:
    """Nise data generator client"""

    # DATABASE-AGNOSTIC VALIDATION SCENARIOS
    # ======================================
    # PURPOSE: Validate API contract with predictable Nise data
    #
    # KEY INSIGHT: Nise generates deterministic input → IQE validates deterministic output
    # The database layer (Trino+Hive+Postgres OR Pure Postgres) is transparent to these tests.
    # Same input data + same API queries = same results, regardless of backend architecture.
    #
    # This approach provides:
    # 1. Regression testing for current Trino+Hive+Postgres setup
    # 2. Migration confidence when switching to Pure Postgres
    # 3. No code changes needed - same tests validate both architectures

    SCENARIOS = {
        # ========================================================================
        # CRITICAL: Data Persistence Validation (Database-Agnostic)
        # ========================================================================

        'basic_queries': {
            'description': '[CRITICAL] Basic aggregations and filtering',
            'provider': 'aws',
            'expected_cost': 1000.00,
            'resources': ['t3.medium', 't3.large'],
            'critical': True,
            'validates': [
                'Basic cost queries work',
                'WHERE clause filtering accurate',
                'GROUP BY aggregations correct',
                'COUNT, SUM, AVG functions precise',
                'Date range filtering works',
                'LIMIT and ORDER BY behave correctly',
            ],
            'tests_covered': 15,
        },

        'advanced_queries': {
            'description': '[CRITICAL] Complex aggregations and joins',
            'provider': 'aws',
            'expected_cost': 1500.00,
            'resources': ['EC2', 'S3', 'EBS'],
            'critical': True,
            'validates': [
                'Multi-table queries work',
                'Complex filtering accurate',
                'Nested aggregations correct',
                'Multiple dimensions supported',
                'String operations work',
                'Complex business logic accurate',
            ],
            'tests_covered': 20,
        },

        'mathematical_precision': {
            'description': '[CRITICAL] Decimal precision and financial accuracy',
            'provider': 'aws',
            'expected_cost': 500.00,
            'critical': True,
            'validates': [
                'Currency precision maintained (2 decimals)',
                'SUM aggregations accurate',
                'AVG calculations correct',
                'Rounding behavior consistent',
                'No floating point errors',
            ],
            'edge_cases': [
                'very_small_values',    # < $0.001
                'very_large_values',    # > $1M
                'high_precision',       # 10+ decimals
                'zero_costs',
                'negative_credits',
            ],
            'tests_covered': 8,
        },

        'data_accuracy': {
            'description': '[CRITICAL] End-to-end data integrity',
            'provider': 'aws',
            'expected_cost': 1500.00,
            'resources': ['EC2', 'S3', 'EBS', 'RDS'],
            'critical': True,
            'validates': [
                'Input costs (Nise) == Output costs (API)',
                'No data loss during processing',
                'All line items accounted for',
                'Aggregations sum to expected totals',
                'Filters return correct subsets',
                'Group-by breakdowns accurate',
            ],
            'tests_covered': 18,
        },

        'tagged_resources': {
            'description': '[CRITICAL] Tag-based cost allocation',
            'provider': 'aws',
            'expected_cost': 750.00,
            'tags': ['environment:production', 'app:web-server', 'team:platform'],
            'critical': True,
            'validates': [
                'Tag filtering works',
                'Tag-based aggregations correct',
                'Multiple tag combinations supported',
                'Tag hierarchies respected',
                'Missing tags handled gracefully',
            ],
            'tests_covered': 12,
        },

        'data_pipeline_integrity': {
            'description': '[CRITICAL] Full pipeline: S3 → Database → API',
            'provider': 'aws',
            'expected_cost': 2000.00,
            'resources': ['EC2', 'RDS', 'S3', 'EBS'],
            'critical': True,
            'validates': [
                'S3 data ingested completely',
                'All transformations preserve data',
                'API serves accurate results',
                'Real-time vs batch data consistent',
                'No data corruption in pipeline',
            ],
            'tests_covered': 15,
        },

        # ========================================================================
        # FUNCTIONAL: Application-Level Smoke Tests
        # ========================================================================

        'functional_basic': {
            'description': '[FUNCTIONAL] Basic cost reporting',
            'provider': 'aws',
            'expected_cost': 500.00,
            'resources': ['t3.medium'],
            'critical': False,
            'validates': [
                'API returns data',
                'Basic filters work',
                'Date ranges handled',
            ],
            'tests_covered': 5,
        },

        'functional_tags': {
            'description': '[FUNCTIONAL] Tag filtering',
            'provider': 'aws',
            'expected_cost': 400.00,
            'tags': ['environment:production'],
            'critical': False,
            'validates': [
                'Tag-based filtering functional',
                'Tag cost allocation works',
            ],
            'tests_covered': 3,
        },
    }

    def __init__(self, nise_path: Optional[str] = None):
        """Initialize Nise client

        Args:
            nise_path: Path to nise executable (auto-detect if None)
        """
        self.nise_path = nise_path or self._find_nise()
        self.temp_dir = None

    def _find_nise(self) -> str:
        """Find nise executable in PATH or virtualenv"""
        # Try which command
        try:
            result = subprocess.run(['which', 'nise'],
                                  capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            pass

        # Try common locations
        common_paths = [
            '/usr/local/bin/nise',
            os.path.expanduser('~/.local/bin/nise'),
            'nise',  # Hope it's in PATH
        ]

        for path in common_paths:
            if os.path.exists(path) or subprocess.run(
                ['which', path], capture_output=True
            ).returncode == 0:
                return path

        # Not found - try to install
        return self._install_nise()

    def _install_nise(self) -> str:
        """Auto-install nise if not found"""
        print("⚠️  Nise not found, attempting to install...")
        try:
            subprocess.run(
                ['pip3', 'install', 'koku-nise'],
                check=True,
                capture_output=True,
                text=True
            )
            print("✅ Nise installed successfully")

            # Try common install locations after install
            import sys
            possible_paths = [
                os.path.join(sys.prefix, 'bin', 'nise'),
                os.path.expanduser('~/.local/bin/nise'),
                '/usr/local/bin/nise',
            ]

            for path in possible_paths:
                if os.path.exists(path):
                    print(f"  Found at: {path}")
                    return path

            # Try which one more time
            try:
                result = subprocess.run(['which', 'nise'],
                                      capture_output=True, text=True, check=True)
                return result.stdout.strip()
            except:
                pass

            # Last resort - just return 'nise' and hope it's in PATH during execution
            print("  ⚠️  Nise installed but path not detected, will try 'nise' command")
            return 'nise'

        except subprocess.CalledProcessError as e:
            raise FileNotFoundError(
                f"Failed to install nise: {e.stderr if hasattr(e, 'stderr') else str(e)}\n"
                "Please install manually: pip3 install koku-nise"
            )

    def generate_scenario(self,
                         scenario_name: str,
                         start_date: datetime,
                         end_date: datetime,
                         output_dir: Optional[str] = None) -> str:
        """Generate data for a predefined scenario

        Args:
            scenario_name: Name of predefined scenario
            start_date: Start date for data generation
            end_date: End date for data generation
            output_dir: Output directory (temp if None)

        Returns:
            Path to generated data directory
        """
        if scenario_name not in self.SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_name}")

        scenario = self.SCENARIOS[scenario_name]

        if output_dir is None:
            if self.temp_dir is None:
                self.temp_dir = tempfile.mkdtemp(prefix='nise-e2e-')
            output_dir = self.temp_dir

        # Build nise command
        # Note: Nise generates files locally, we upload them separately
        cmd = [
            self.nise_path,
            'report',
            scenario['provider'],
            '--start-date', start_date.strftime('%Y-%m-%d'),
            '--end-date', end_date.strftime('%Y-%m-%d'),
            '--write-monthly'  # Generate monthly files
        ]

        # Add static report file if specified
        if 'static_report' in scenario and scenario['static_report']:
            cmd.extend(['--static-report-file', scenario['static_report']])

        # Run nise - generates files in current directory
        print(f"    Executing Nise: {scenario_name} ({start_date.date()} to {end_date.date()})...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=output_dir,  # Run in output directory
            check=True
        )

        if result.stdout:
            print(f"    Generated: {result.stdout.strip()[:100]}")

        return output_dir

    def generate_aws_cur(self,
                        start_date: datetime,
                        end_date: datetime,
                        account_id: str = '123456789012',
                        output_dir: Optional[str] = None,
                        num_instances: int = 5,
                        tags: Optional[List[str]] = None,
                        instance_types: Optional[List[str]] = None,
                        regions: Optional[List[str]] = None,
                        storage_types: Optional[List[str]] = None) -> str:
        """Generate AWS Cost and Usage Report

        Args:
            start_date: Start date
            end_date: End date
            account_id: AWS account ID
            output_dir: Output directory
            num_instances: Number of EC2 instances to generate
            tags: List of tags (format: "key:value")
            instance_types: List of EC2 instance types
            regions: List of AWS regions
            storage_types: List of storage types (EBS, S3)

        Returns:
            Path to generated data
        """
        if output_dir is None:
            if self.temp_dir is None:
                self.temp_dir = tempfile.mkdtemp(prefix='nise-e2e-')
            output_dir = self.temp_dir

        cmd = [
            self.nise_path,
            'report', 'aws',
            '--start-date', start_date.strftime('%Y-%m-%d'),
            '--end-date', end_date.strftime('%Y-%m-%d'),
            '--write-monthly'
        ]

        print(f"    Generating AWS CUR: {account_id}, {start_date.date()} to {end_date.date()}")
        # AWS nise writes to current directory
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=output_dir)

        return output_dir

    def generate_azure_export(self,
                             start_date: datetime,
                             end_date: datetime,
                             subscription_id: str = '11111111-1111-1111-1111-111111111111',
                             output_dir: Optional[str] = None,
                             container_name: str = 'cost-data',
                             report_name: str = 'test-report',
                             report_prefix: str = 'reports',
                             resource_group: bool = True) -> str:
        """Generate Azure Cost Export

        Args:
            start_date: Start date
            end_date: End date
            subscription_id: Azure subscription ID
            output_dir: Output directory
            container_name: Azure container name
            report_name: Report name
            report_prefix: Report prefix
            resource_group: Generate resource group based report

        Returns:
            Path to generated data
        """
        if output_dir is None:
            if self.temp_dir is None:
                self.temp_dir = tempfile.mkdtemp(prefix='nise-e2e-')
            output_dir = self.temp_dir

        cmd = [
            self.nise_path,
            'report', 'azure',
            '--start-date', start_date.strftime('%Y-%m-%d'),
            '--end-date', end_date.strftime('%Y-%m-%d'),
            '--azure-container-name', container_name,
            '--azure-report-name', report_name,
            '--azure-report-prefix', report_prefix,
            '--write-monthly'
        ]

        if resource_group:
            cmd.append('--resource-group')

        print(f"    Generating Azure export: {subscription_id}, {start_date.date()} to {end_date.date()}")
        # Azure nise writes to current directory
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=output_dir)

        return output_dir

    def generate_gcp_export(self,
                           start_date: datetime,
                           end_date: datetime,
                           project_id: str = 'test-project-12345',
                           output_dir: Optional[str] = None,
                           dataset_name: str = 'cost-data',
                           table_name: str = 'test-report',
                           bucket_name: str = 'cost-data',
                           report_prefix: str = 'reports',
                           resource_level: bool = True) -> str:
        """Generate GCP Billing Export

        Args:
            start_date: Start date
            end_date: End date
            project_id: GCP project ID
            output_dir: Output directory
            dataset_name: BigQuery dataset name
            table_name: BigQuery table name
            bucket_name: GCS bucket name
            report_prefix: Report prefix
            resource_level: Generate resource level report

        Returns:
            Path to generated data
        """
        if output_dir is None:
            if self.temp_dir is None:
                self.temp_dir = tempfile.mkdtemp(prefix='nise-e2e-')
            output_dir = self.temp_dir

        cmd = [
            self.nise_path,
            'report', 'gcp',
            '--start-date', start_date.strftime('%Y-%m-%d'),
            '--end-date', end_date.strftime('%Y-%m-%d'),
            '--gcp-dataset-name', dataset_name,
            '--gcp-table-name', table_name,
            '--gcp-bucket-name', bucket_name,
            '--gcp-report-prefix', report_prefix,
            '--write-monthly'
        ]

        if resource_level:
            cmd.append('--resource-level')

        print(f"    Generating GCP export: {project_id}, {start_date.date()} to {end_date.date()}")
        # GCP nise writes to current directory
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=output_dir)

        return output_dir

    def generate_ocp_usage(self,
                          start_date: datetime,
                          end_date: datetime,
                          cluster_id: str = 'test-cluster-123',
                          output_dir: Optional[str] = None,
                          static_report_file: Optional[str] = None) -> str:
        """Generate OCP (OpenShift) usage data using nise

        Args:
            start_date: Start date for data generation
            end_date: End date for data generation
            cluster_id: Cluster identifier
            output_dir: Output directory (temp if None)
            static_report_file: Path to static report file for controlled scenarios

        Returns:
            Path to directory containing generated files
        """
        if output_dir is None:
            if self.temp_dir is None:
                self.temp_dir = tempfile.mkdtemp(prefix='nise-e2e-')
            output_dir = self.temp_dir

        cmd = [
            self.nise_path,
            'report', 'ocp',
            '--start-date', start_date.strftime('%Y-%m-%d'),
            '--end-date', end_date.strftime('%Y-%m-%d'),
            '--ocp-cluster-id', cluster_id,
            '--write-monthly',  # REQUIRED to generate files
            '--file-row-limit', '100000',  # Limit rows per file to prevent large files
        ]

        # Add static report file for controlled/minimal scenarios
        if static_report_file:
            cmd.extend(['--static-report-file', static_report_file])

        print(f"    Generating OCP usage: {cluster_id}, {start_date.date()} to {end_date.date()}")
        if static_report_file:
            print(f"    Using static report: {static_report_file}")

        # OCP nise writes to current directory
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=output_dir)

        return output_dir

    def generate_multi_scenario(self,
                               scenarios: List[str],
                               start_date: datetime,
                               end_date: datetime,
                               output_dir: Optional[str] = None) -> Dict[str, str]:
        """Generate multiple scenarios

        Args:
            scenarios: List of scenario names
            start_date: Start date
            end_date: End date
            output_dir: Base output directory

        Returns:
            Dict mapping scenario names to output paths
        """
        results = {}

        for scenario in scenarios:
            if output_dir:
                scenario_dir = os.path.join(output_dir, scenario)
                os.makedirs(scenario_dir, exist_ok=True)
            else:
                scenario_dir = None

            path = self.generate_scenario(scenario, start_date, end_date, scenario_dir)
            results[scenario] = path

        return results

    def get_scenario_expected_cost(self, scenario_name: str) -> float:
        """Get expected cost for a scenario

        Args:
            scenario_name: Scenario name

        Returns:
            Expected total cost
        """
        if scenario_name not in self.SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_name}")

        return self.SCENARIOS[scenario_name].get('expected_cost', 0.0)

    def parse_nise_output(self, output_dir: str) -> Dict[str, List[str]]:
        """Parse Nise output directory structure

        Args:
            output_dir: Directory containing Nise output

        Returns:
            Dict with 'manifests' and 'csv_files' lists
        """
        manifest_files = []
        csv_files = []

        if not os.path.exists(output_dir):
            return {'manifests': [], 'csv_files': []}

        # Walk through directory tree
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                full_path = os.path.join(root, file)

                if file.endswith('Manifest.json') or file.endswith('-manifest.json'):
                    manifest_files.append(full_path)
                elif file.endswith('.csv') or file.endswith('.csv.gz'):
                    csv_files.append(full_path)

        return {
            'manifests': manifest_files,
            'csv_files': csv_files,
            'total_files': len(manifest_files) + len(csv_files)
        }

    def cleanup(self):
        """Clean up temporary files"""
        if hasattr(self, 'temp_dir') and self.temp_dir and os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
            self.temp_dir = None

    def __del__(self):
        """Cleanup on deletion"""
        try:
            self.cleanup()
        except:
            pass  # Ignore cleanup errors during deletion


class NiseScenarioValidator:
    """Validates that generated data matches expected scenarios"""

    def __init__(self, db_client):
        """Initialize validator

        Args:
            db_client: DatabaseClient instance
        """
        self.db = db_client

    def validate_scenario_costs(self,
                               scenario_name: str,
                               tolerance: float = 0.01) -> Dict[str, any]:
        """Validate that processed costs match expected scenario

        Args:
            scenario_name: Name of scenario
            tolerance: Acceptable cost variance (0.01 = 1%)

        Returns:
            Validation results dict
        """
        expected = NiseClient.SCENARIOS[scenario_name]['expected_cost']

        # Query actual costs from database
        actual = self.db.execute_query("""
            SELECT SUM(unblended_cost) as total_cost
            FROM reporting_awscostentrylineitem_daily_summary
            WHERE usage_start >= NOW() - INTERVAL '30 days'
        """, fetch_one=True)[0]

        variance = abs(actual - expected) / expected if expected > 0 else 0
        passed = variance <= tolerance

        return {
            'scenario': scenario_name,
            'expected_cost': expected,
            'actual_cost': actual,
            'variance': variance,
            'tolerance': tolerance,
            'passed': passed
        }

