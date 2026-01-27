# Cost Management On-Premise Helm Chart Scripts

Automation scripts for deploying, configuring, and testing the Cost Management On-Premise (CoP) with JWT authentication and TLS certificate handling.

## 📋 Available Scripts

| Script | Purpose | Environment |
|--------|---------|-------------|
| `deploy-test-cost-onprem.sh` | **Full deployment + test orchestration** | OpenShift |
| `run-pytest.sh` | Run pytest test suite | All environments |
| `deploy-strimzi.sh` | Deploy Kafka infrastructure | All environments |
| `install-helm-chart.sh` | Deploy CoP Helm chart | All environments |
| `deploy-rhbk.sh` | Deploy Red Hat Build of Keycloak | OpenShift |
| `setup-cost-mgmt-tls.sh` | Configure TLS certificates | OpenShift |
| `cost-mgmt-ocp-dataflow.sh` | **E2E Cost Management test** | All environments |
| `query-kruize.sh` | Query Kruize database | All environments |

## 🚀 Quick Start

### Standard OpenShift Deployment
```bash
# 1. Deploy Cost Management Operator with TLS support
./setup-cost-mgmt-tls.sh

# 2. Deploy Kafka infrastructure
./deploy-strimzi.sh

# 3. Deploy Cost Management
./install-helm-chart.sh

# 4. Validate the deployment (E2E test)
NAMESPACE=cost-onprem ./run-pytest.sh
```


### JWT Authentication Setup
```bash
# 1. Deploy Red Hat Build of Keycloak
./deploy-rhbk.sh

# 2. Deploy Kafka infrastructure
./deploy-strimzi.sh

# 3. Deploy CoP with JWT authentication
export JWT_AUTH_ENABLED=true
./install-helm-chart.sh

# 4. Configure TLS certificates
./setup-cost-mgmt-tls.sh

# 5. Test JWT flow through centralized gateway
NAMESPACE=cost-onprem ./run-pytest.sh --auth
```

## 📖 Script Documentation

### `install-helm-chart.sh`
Deploy or upgrade the CoP Helm chart with automatic configuration.

**Key features:**
- Installs from GitHub releases or local chart
- Auto-detects OpenShift and configures JWT authentication
- Manages namespace and deployment lifecycle
- **Automatically applies Cost Management Operator label** to namespace

**Namespace Labeling:**
The script automatically applies the `cost_management_optimizations=true` label to the deployment namespace. This label is **required** by the Cost Management Metrics Operator to collect resource optimization data from the namespace.

To remove the label (if needed):
```bash
kubectl label namespace cost-onprem cost_management_optimizations-
```

**Usage:**
```bash
# Basic installation
./install-helm-chart.sh

# Use local chart for development
export USE_LOCAL_CHART=true
./install-helm-chart.sh

# Custom namespace
export NAMESPACE=cost-onprem
./install-helm-chart.sh

# Check deployment status
./install-helm-chart.sh status

# Cleanup
./install-helm-chart.sh cleanup
```

**Environment variables:**
- `NAMESPACE`: Target namespace (default: `cost-onprem`)
- `USE_LOCAL_CHART`: Use local chart instead of GitHub (default: `false`)
- `JWT_AUTH_ENABLED`: Enable JWT authentication (default: auto-detect)
- `VALUES_FILE`: Custom values file path
- `KAFKA_BOOTSTRAP_SERVERS`: Use external Kafka (skips verification)

---

### `deploy-rhbk.sh`
Deploy Red Hat Build of Keycloak (RHBK) with CoP integration.

**What it creates:**
- RHBK Operator in target namespace
- Keycloak instance with `kubernetes` realm
- `cost-management-operator` client
- OpenShift OIDC integration

**Usage:**
```bash
# Deploy to default namespace (keycloak)
./deploy-rhbk.sh

# Deploy to custom namespace
RHBK_NAMESPACE=my-keycloak ./deploy-rhbk.sh

# Validate existing deployment
./deploy-rhbk.sh validate

# Clean up deployment
./deploy-rhbk.sh cleanup
```

---

### `setup-cost-mgmt-tls.sh`
Configure Cost Management Operator with comprehensive CA certificate support.

**Features:**
- Extracts CA certificates from 15+ sources (routers, Keycloak, system CAs, custom CAs)
- Creates consolidated CA bundle for self-signed certificate environments
- Configures Cost Management Operator with proper TLS settings

**Usage:**
```bash
# Complete setup (recommended for all environments)
./setup-cost-mgmt-tls.sh

# Custom namespace with verbose output
./setup-cost-mgmt-tls.sh -n my-cost-mgmt -v

# Dry-run to preview actions
./setup-cost-mgmt-tls.sh --dry-run
```

**Best for:** All OpenShift environments, especially those with self-signed certificates

---

### `deploy-strimzi.sh`
Deploy Strimzi operator and Kafka cluster.

**What it creates:**
- Strimzi Operator (Kafka cluster management)
- Kafka 3.8.0 cluster with persistent storage
- Required Kafka topics for Cost Management On-Premise

**Usage:**
```bash
# Basic deployment
./deploy-strimzi.sh

# Deploy for OpenShift with custom storage
KAFKA_ENVIRONMENT=ocp ./deploy-strimzi.sh

# Use existing Strimzi operator
STRIMZI_NAMESPACE=existing-strimzi ./deploy-strimzi.sh

# Use existing external Kafka
KAFKA_BOOTSTRAP_SERVERS=my-kafka:9092 ./deploy-strimzi.sh

# Validate existing deployment
./deploy-strimzi.sh validate

# Cleanup
./deploy-strimzi.sh cleanup
```

**Environment variables:**
- `KAFKA_NAMESPACE`: Target namespace (default: `kafka`)
- `KAFKA_CLUSTER_NAME`: Kafka cluster name (default: `cost-onprem-kafka`)
- `KAFKA_VERSION`: Kafka version (default: `3.8.0`)
- `STRIMZI_VERSION`: Strimzi operator version (default: `0.45.1`)
- `KAFKA_ENVIRONMENT`: Environment type - `dev` or `ocp` (default: `dev`)
- `STORAGE_CLASS`: Storage class name (auto-detected if empty)
- `KAFKA_BOOTSTRAP_SERVERS`: Use external Kafka (skips deployment)

---

### `deploy-test-cost-onprem.sh`
Complete orchestration script for deploying and testing Cost On-Prem with JWT authentication.

**OpenShift CI Integration:**
This script is invoked by the OpenShift CI step `insights-onprem-cost-onprem-chart-e2e`:
```
release/ci-operator/step-registry/insights-onprem/cost-onprem-chart/e2e/
└── insights-onprem-cost-onprem-chart-e2e-commands.sh
    └── bash ./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --verbose
```

**What it does:**
1. Deploys Red Hat Build of Keycloak (RHBK)
2. Deploys Kafka/Strimzi infrastructure
3. Installs Cost On-Prem Helm chart
4. Configures TLS certificates
5. **Runs pytest via `scripts/run-pytest.sh`** (CI mode - excludes extended tests)
6. Optionally saves deployment version info

**Usage:**
```bash
# Full deployment + tests
./deploy-test-cost-onprem.sh

# Run tests only (skip deployments)
./deploy-test-cost-onprem.sh --tests-only

# Skip specific steps
./deploy-test-cost-onprem.sh --skip-rhbk --skip-strimzi

# Save deployment version info for CI traceability
./deploy-test-cost-onprem.sh --save-versions
./deploy-test-cost-onprem.sh --save-versions custom-versions.json

# Dry run to preview actions
./deploy-test-cost-onprem.sh --dry-run --verbose
```

**Version tracking:** The `--save-versions` flag generates a `version_info.json` file containing:
- Helm chart version (source and deployed)
- Git SHA and branch
- Deployment timestamp
- Component image details

**Best for:** CI/CD pipelines, complete E2E deployment and validation

---

### `run-pytest.sh`
Run the pytest test suite for JWT authentication and data flow validation.

**Default CI Execution:**
```bash
# What OpenShift CI runs (via deploy-test-cost-onprem.sh):
NAMESPACE=cost-onprem ./scripts/run-pytest.sh

# Equivalent to:
pytest -m "not extended" --junit-xml=reports/junit.xml
```

**CI runs ~88 tests in ~3 minutes** (excludes extended tests that require ODF/S3).

**Suite options:**
- `--helm` - Helm chart validation tests
- `--auth` - JWT authentication tests
- `--infrastructure` - Infrastructure health tests (DB, S3, Kafka)
- `--cost-management` - Cost Management (Koku) pipeline tests
- `--ros` - ROS/Kruize recommendation tests
- `--e2e` - End-to-end data flow tests

**Filter options:**
- `--smoke` - Quick smoke tests only
- `--extended` - Run E2E tests INCLUDING extended (summary tables, Kruize)
- `--all` - Run ALL tests including extended

**Test type markers:**
- `-m component` - Single-component tests
- `-m integration` - Multi-component tests

**Usage:**
```bash
# Run all tests (excludes extended by default)
./run-pytest.sh

# Run specific test suites
./run-pytest.sh --helm
./run-pytest.sh --auth
./run-pytest.sh --e2e

# Run E2E with extended tests (summary tables, Kruize)
./run-pytest.sh --extended

# Run ALL tests including extended
./run-pytest.sh --all

# Run tests matching a pattern
./run-pytest.sh -k "test_jwt"

# Run only component tests
./run-pytest.sh -m component

# Setup environment only
./run-pytest.sh --setup-only
```

**Output:** JUnit XML report at `tests/reports/junit.xml`

**Requirements:**
- Python 3.10+
- OpenShift CLI (`oc`) logged in
- Cost On-Prem deployed with JWT authentication

**See also:** [Test Suite Documentation](../tests/README.md)

---

### `cost-mgmt-ocp-dataflow.sh`
End-to-end validation of Cost Management data pipeline (OCP provider).

**Test flow:**
1. Preflight checks (database, S3, Kafka connectivity)
2. Database migrations verification
3. Kafka cluster health validation
4. Provider setup (creates OCP provider via Sources API → Kafka → Listener)
5. Data generation with nise + upload to S3
6. Kafka message triggers MASU processing
7. Data processing validation (PostgreSQL tables populated)
8. Cost validation (PostgreSQL summary tables match expected values)

**Usage:**
```bash
# Run smoke test (recommended - ~3 minutes)
./cost-mgmt-ocp-dataflow.sh

# Run with diagnostics on failure
./cost-mgmt-ocp-dataflow.sh --diagnose

# Custom namespace
NAMESPACE=my-cost-mgmt ./cost-mgmt-ocp-dataflow.sh
```

**Expected Output (Success):**
```
======================================================================
  ✅ SMOKE VALIDATION PASSED
======================================================================

  📊 DATA PROOF - Actual rows from PostgreSQL:
  ------------------------------------------------------------------
  Date         Namespace            CPU(h)     CPU Req    Mem(GB)
  ------------------------------------------------------------------
  2025-12-01   test-namespace           6.00     12.00     12.00
  ------------------------------------------------------------------
  TOTALS       (1 rows)                 6.00     12.00     12.00
  ------------------------------------------------------------------

  📋 EXPECTED vs ACTUAL (from nise YAML):
  --------------------------------------------------
  Metric                      Expected     Actual Match
  --------------------------------------------------
  CPU Request (hours)            12.00      12.00 ✅
  Memory Request (GB-hrs)        24.00      24.00 ✅
  --------------------------------------------------

  ✅ File Processing: 3 checks passed
  ✅ Cost: 2 checks passed
======================================================================

Phases: 7/7 passed
  ✅ preflight
  ✅ migrations
  ✅ kafka_validation
  ✅ provider
  ✅ data_upload
  ✅ processing
  ✅ validation

✅ E2E SMOKE TEST PASSED

╔═══════════════════════════════════════════════════════════════╗
║  ✓ OCP E2E Validation PASSED                                  ║
║  Total time: 3m 19s                                          ║
╚═══════════════════════════════════════════════════════════════╝
```

**Key Validation Points:**
- **Data Proof**: Shows actual rows from PostgreSQL `reporting_ocpusagelineitem_daily_summary`
- **Expected vs Actual**: Compares nise-generated values against PostgreSQL aggregates
- **Match Icons**: ✅ indicates values match within 5% tolerance

**Requirements:**
- Cost Management deployed with `cost-onprem` chart
- Kafka cluster running (Strimzi)
- S3/ODF storage accessible

**Best for:** CI/CD pipelines, deployment validation, data flow verification

---

### `query-kruize.sh`
Query Kruize database for experiments and recommendations.

**What it does:**
- Connects to Kruize PostgreSQL database directly
- Lists experiments and their status
- Shows generated recommendations
- Supports custom SQL queries
- Displays database schema

**Usage:**
```bash
# List all experiments
./query-kruize.sh --experiments

# List all recommendations
./query-kruize.sh --recommendations

# Find experiments by pattern
./query-kruize.sh --experiment "test-cluster"

# Query by cluster ID
./query-kruize.sh --cluster "757b6bf6-9e91-486a-8a99-6d3e6d0f485c"

# Get detailed recommendation info
./query-kruize.sh --detail 5

# Run custom SQL query
./query-kruize.sh --query "SELECT COUNT(*) FROM kruize_experiments WHERE status='IN_PROGRESS';"

# Show database schema
./query-kruize.sh --schema

# Custom namespace
./query-kruize.sh --namespace cost-onprem --experiments
```

**Requirements:**
- Kruize deployed and running
- Database pod accessible via `oc exec`

**Best for:** Debugging, validating data flow, checking recommendation generation status

---

## 🧪 Test Strategy

### For CI/CD Pipelines
Use the orchestration script for comprehensive E2E deployment and validation:

**Cost Management Validation (recommended):**
```bash
# Full deployment + tests (recommended)
./deploy-test-cost-onprem.sh

# 2. Deploy Cost Management
./install-helm-chart.sh

# 3. Validate Cost Management data flow (~3 minutes)
./cost-mgmt-ocp-dataflow.sh || exit 1
```

The `cost-mgmt-ocp-dataflow.sh` script validates:
- ✅ Sources API → Kafka → Sources Listener integration
- ✅ OCP provider creation via production flow
- ✅ S3 upload → Kafka → MASU processing
- ✅ PostgreSQL data tables populated
- ✅ PostgreSQL summary aggregation
- ✅ Cost calculations match expected values
- ✅ **Shows actual data proof** (not just PASSED/FAILED)

**JWT Authentication Validation (if Keycloak enabled):**
```bash
# Run pytest authentication tests
NAMESPACE=cost-onprem ./run-pytest.sh --auth

# Or run tests only on existing deployment
./deploy-test-cost-onprem.sh --tests-only

# Or run full pytest suite
NAMESPACE=cost-onprem ./run-pytest.sh
```

The pytest test suite validates:
- ✅ Keycloak connectivity and JWT token generation
- ✅ JWT authentication on ingress and backend APIs
- ✅ Data upload with JWT authentication
- ✅ Full data flow (ingress → processor → Kruize)
- ✅ Recommendation generation

**Test output:** JUnit XML report at `tests/reports/junit.xml`

**See also:** [Test Suite Documentation](../tests/README.md)

---

## 🔧 Common Environment Variables

Most scripts support these variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `NAMESPACE` | Target namespace | `cost-onprem` |
| `VERBOSE` | Enable detailed logging | `false` |
| `DRY_RUN` | Preview without executing | `false` |
| `JWT_AUTH_ENABLED` | Enable JWT authentication | Auto-detect |
| `USE_LOCAL_CHART` | Use local chart for testing | `false` |

## 🚨 Troubleshooting

### Common Issues

**TLS Certificate Errors**
```bash
# Run comprehensive TLS setup
./setup-cost-mgmt-tls.sh --verbose
```

**JWT Authentication Failures**
```bash
# Run auth tests with verbose output
NAMESPACE=cost-onprem ./run-pytest.sh --auth -v

# Check centralized gateway logs
oc logs -n cost-onprem -l app.kubernetes.io/component=gateway
```

**Cost Management Operator Issues**
```bash
# Check operator logs
oc logs -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator

# Verify namespace labeling
oc label namespace <namespace> cost_management_optimizations=true
```

For detailed troubleshooting, see [Troubleshooting Guide](../docs/troubleshooting.md)

## 📚 Related Documentation

- **[Installation Guide](../docs/installation.md)** - Complete installation instructions
- **[JWT Authentication](../docs/native-jwt-authentication.md)** - JWT setup and configuration
- **[TLS Setup Guide](../docs/cost-management-operator-tls-setup.md)** - Detailed TLS configuration
- **[Configuration Reference](../docs/configuration.md)** - Helm values and configuration options
- **[Helm Templates Reference](../docs/helm-templates-reference.md)** - Technical chart details
- **[Troubleshooting](../docs/troubleshooting.md)** - Detailed troubleshooting guide

## 📝 Script Maintenance

### Dependencies
- `oc` (OpenShift CLI)
- `helm` (Helm CLI v3+)
- `jq` (JSON processor)
- `curl` (HTTP client)
- `openssl` (Certificate tools)
- `python3` (Python 3 interpreter - required for pytest tests)
- `python3-venv` (Virtual environment module - required for pytest tests)

### Logging Conventions
All scripts use color-coded output:
- 🟢 **SUCCESS**: Green for successful operations
- 🔵 **INFO**: Blue for informational messages
- 🟡 **WARNING**: Yellow for warnings
- 🔴 **ERROR**: Red for errors and failures

---

**Last Updated**: January 2026
**Maintainer**: CoP Engineering Team
**Supported Platform**: OpenShift 4.18+
**Tested With**: OpenShift 4.18.24
