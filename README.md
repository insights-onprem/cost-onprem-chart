# Cost Management On-Premise Helm Charts

This repository contains a Helm chart for deploying cost management solutions on-premise:

**`cost-onprem/`** - Unified chart containing all components: ROS, Kruize, Koku (Cost Management with Sources API), PostgreSQL, and Valkey

---

## 📊 Cost Management (Koku) Deployment

Complete Helm chart for deploying the full Cost Management stack with OCP cost analytics capabilities.

**🚀 Quick Start:**
```bash
# Automated deployment (recommended)
./scripts/install-helm-chart.sh
```

**📖 Documentation:**
- **[Cost Management Installation Guide](docs/operations/cost-management-installation.md)** - Complete deployment guide
- **Prerequisites**: OpenShift 4.18+, ODF with Direct Ceph RGW (150GB+), Kafka/Strimzi
- **Architecture**: Single unified chart with all components
- **E2E Testing**: Automated validation with `./scripts/run-pytest.sh` (pytest-based test suite)

**Key Features:**
- 📊 Complete OCP cost data pipeline (Kafka → CSV → PostgreSQL)
- 🗄️ PostgreSQL-based data processing and analytics
- 🔄 Optimized Kubernetes resources with production-ready defaults
- 🧪 Comprehensive E2E validation framework

---

## 🎯 Resource Optimization Service (ROS)

OpenShift Helm chart for deploying the Resource Optimization Service (ROS) with Kruize integration and future cost management capabilities.

## 🚀 Quick Start

### OpenShift Deployment

```bash
# Automated installation from Helm repository (recommended)
./scripts/install-helm-chart.sh

# Or install a specific chart version
CHART_VERSION=0.2.9 ./scripts/install-helm-chart.sh

# Or use local chart for development
USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem ./scripts/install-helm-chart.sh

# Or use Helm directly
helm repo add cost-onprem https://insights-onprem.github.io/cost-onprem-chart
helm repo update
helm install cost-onprem cost-onprem/cost-onprem --namespace cost-onprem --create-namespace
```

**Note:** See [Authentication Setup](#-authentication-setup) section for required prerequisites (Keycloak)

📖 **See [Installation Guide](docs/operations/installation.md) for detailed installation options**

## 📚 Documentation

> **📖 [Complete Documentation Index →](docs/README.md)**
> Comprehensive guides organized by use case, with detailed descriptions and navigation.

### Essential Guides

| 🚀 Getting Started | 🏭 Production Setup | 🔧 Operations |
|-------------------|-------------------|---------------|
| [Quick Start](docs/operations/quickstart.md)<br/>*Fast deployment walkthrough* | [Installation Guide](docs/operations/installation.md)<br/>*Detailed installation instructions* | [Troubleshooting](docs/operations/troubleshooting.md)<br/>*Common issues & solutions* |
| [Platform Guide](docs/architecture/platform-guide.md)<br/>*OpenShift deployment details* | [JWT Authentication](docs/api/native-jwt-authentication.md)<br/>*Ingress authentication (Keycloak)* | [Force Upload](docs/operations/force-operator-upload.md)<br/>*Testing & validation* |
| | [Scripts Reference](scripts/README.md)<br/>*Automation scripts* |
| | [Keycloak Setup](docs/api/keycloak-jwt-authentication-setup.md)<br/>*SSO configuration* | |

**Need more?** Configuration, security, templates, and specialized guides are available in the [Complete Documentation Index](docs/README.md).

## 🏗️ Repository Structure

```
cost-onprem-chart/
├── cost-onprem/    # Helm chart directory
│   ├── Chart.yaml             # Chart metadata (v0.2.0)
│   ├── values.yaml            # Default configuration
│   └── templates/             # Kubernetes resource templates (organized by service)
│       ├── ros/               # Resource Optimization Service
│       ├── kruize/            # Kruize optimization engine
│       ├── cost-management/   # Cost Management (includes Sources API)
│       ├── ingress/           # API gateway
│       ├── infrastructure/    # Database, Kafka, storage, cache
│       ├── auth/              # Authentication (CA certificates)
│       ├── monitoring/        # Prometheus ServiceMonitor
│       ├── shared/            # Shared resources
│       └── cost-management/   # Future cost management components
├── tests/                     # Pytest test suite
├── docs/                      # Documentation
├── scripts/                   # Installation and automation scripts
└── .github/workflows/         # CI/CD automation
```

## 📦 Services Deployed

### Stateful Services
- **PostgreSQL**: Unified database server hosting ROS, Kruize, Koku, and Sources databases
- **ODF**: Object storage (OpenShift Data Foundation with NooBaa S3)

### Kafka Infrastructure (Managed by Install Script)
- **Strimzi Operator**: Deploys and manages Kafka clusters
- **Kafka 3.8.0**: Message streaming with persistent storage (deployed via Strimzi CRDs)

### Application Services
- **API Gateway**: Centralized Envoy gateway for JWT authentication and API routing (port 9080)
- **Ingress**: File upload API processing
- **ROS API**: Main REST API for recommendations and status
- **ROS Processor**: Data processing service for cost optimization
- **ROS Recommendation Poller**: Kruize integration for recommendations
- **ROS Housekeeper**: Maintenance tasks and data cleanup
- **Kruize Autotune**: Optimization recommendation engine (internal service, protected by network policies)
- **Sources API**: Source management and integration
- **Valkey**: Caching layer for performance

**Security Architecture**:
- **Centralized Gateway**: Single API gateway with JWT validation (Keycloak) for all external API traffic
- **Backend Services**: Receive pre-authenticated requests from gateway with `X-Rh-Identity` header
- **Network Policies**: Restrict direct access to backend services while allowing Prometheus metrics scraping
- **Multi-tenancy**: `org_id` and `account_number` from authentication enable data isolation across organizations and accounts

**See [JWT Authentication Guide](docs/native-jwt-authentication.md) for detailed architecture**

## ⚙️ Configuration

### Resource Requirements

Complete Cost Management deployment requires significant cluster resources:

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| **CPU** | 10 cores | 12-14 cores |
| **Memory** | 24 Gi | 32-40 Gi |
| **Worker Nodes** | 3 × 8 Gi | 3 × 16 Gi |
| **Storage** | 300 Gi | 400+ Gi |
| **Pods** | ~55 | - |

**📖 See [Resource Requirements Guide](docs/resource-requirements.md) for detailed breakdown by component.**

### Storage Options
- **OpenShift**: ODF with Direct Ceph RGW (recommended for strong consistency)

**Note**: Direct Ceph RGW (`ocs-storagecluster-ceph-rgw`) is recommended over NooBaa for ROS deployments due to strong read-after-write consistency requirements. NooBaa has eventual consistency issues that can cause ROS processing failures.

### Storage Requirements
- **ODF**: OpenShift Data Foundation with NooBaa (required for S3-compatible storage)

**See [Configuration Guide](docs/configuration.md) for detailed requirements**

## 🌐 Access Points

Services accessible via OpenShift Routes:
```bash
oc get routes -n cost-onprem
```

Available endpoints:
- Health Check: `/ready`
- ROS API: `/api/ros/*`
- Cost Management API: `/api/cost-management/*`
- Sources API: `/api/cost-management/v1/sources/` (via Koku API)
- Upload API: `/api/ingress/*`

**See [Platform Guide](docs/architecture/platform-guide.md) for detailed access information**

## 🔐 Authentication Setup

### JWT Authentication

JWT authentication is **automatically enabled** and requires Keycloak configuration:

```bash
# Step 1: Deploy Red Hat Build of Keycloak (RHBK)
./scripts/deploy-rhbk.sh

# Step 2: Configure Cost Management Operator with JWT credentials
./scripts/setup-cost-mgmt-tls.sh

# Step 3: Deploy Cost Management On-Premise
./scripts/install-helm-chart.sh
```

**📖 See [Keycloak Setup Guide](docs/api/keycloak-jwt-authentication-setup.md) for detailed configuration instructions**

Key requirements:
- ✅ Keycloak realm with `org_id` and `account_number` claims
- ✅ Service account client credentials
- ✅ Self-signed CA certificate bundle (auto-configured)
- ✅ Cost Management Operator configured with JWT token URL

**Operator Support:**
- ✅ Red Hat Build of Keycloak (RHBK) v22+ - `k8s.keycloak.org/v2alpha1`

**Architecture**: [JWT Authentication Overview](docs/api/native-jwt-authentication.md)

## 🔧 Common Operations

### Deployment
```bash
# Install/upgrade from Helm repository
./scripts/install-helm-chart.sh

# Check deployment status
./scripts/install-helm-chart.sh status

# Run health checks
./scripts/install-helm-chart.sh health
```

### Cleanup
```bash
# Cleanup preserving data volumes
./scripts/install-helm-chart.sh cleanup

# Complete removal including data
./scripts/install-helm-chart.sh cleanup --complete
```

## 🧪 Testing & CI/CD

### Test Suite
```bash
# Run all tests (excludes extended by default)
./scripts/run-pytest.sh

# Run specific test suites
./scripts/run-pytest.sh --helm              # Helm chart validation
./scripts/run-pytest.sh --auth              # JWT authentication tests
./scripts/run-pytest.sh --infrastructure    # DB, S3, Kafka health
./scripts/run-pytest.sh --e2e               # End-to-end data flow

# Run E2E with extended tests (summary tables, Kruize)
./scripts/run-pytest.sh --extended

# Run ALL tests including extended
./scripts/run-pytest.sh --all

# Run by test type
./scripts/run-pytest.sh -m component        # Single-component tests
./scripts/run-pytest.sh -m integration      # Multi-component tests
```

**See [Test Suite Documentation](tests/README.md) for detailed usage**

### CI/CD Automation
- **Lint & Validate**: Chart validation on every PR
- **Automated Releases**: Chart-releaser publishes to [Helm repository](https://insights-onprem.github.io/cost-onprem-chart) on version bump
- **Version Tracking**: `--save-versions` flag generates `version_info.json` for traceability
- **Disconnected Support**: `oc-mirror` compatible (see [Disconnected Deployment Guide](docs/operations/disconnected-deployment.md))

## 🚨 Troubleshooting

**Quick diagnostics:**
```bash
# Check pods
kubectl get pods -n cost-onprem

# View logs
kubectl logs -n cost-onprem -l app.kubernetes.io/component=api

# Check storage
kubectl get pvc -n cost-onprem
```

**See [Troubleshooting Guide](docs/operations/troubleshooting.md) for comprehensive solutions**

## 📄 License

This project is licensed under the terms specified in the [LICENSE](LICENSE) file.

## 🛠️ Development Environment

New to this project? See the **[OCP Dev Setup with MinIO](docs/development/ocp-dev-setup-minio.md)** guide to set up a development environment on OpenShift using MinIO instead of ODF. This is the recommended approach for developers who don't have access to a multi-node OCP cluster with ODF.

| Setup | Nodes | Storage Backend | Use Case |
|-------|-------|-----------------|----------|
| **Dev/Test (MinIO)** | 1 (SNO) | MinIO (standalone) | Local development, testing, demos |
| **Production (ODF)** | 3+ | ODF with Direct Ceph RGW | Production deployments |

## 🤝 Contributing

See [Quick Start Guide](docs/operations/quickstart.md) for development environment setup.

## 📞 Support

For issues and questions:
- **Issues**: [GitHub Issues](https://github.com/insights-onprem/cost-onprem-chart/issues)
- **Documentation**: [Complete Documentation Index](docs/README.md)
- **Scripts**: [Automation Scripts Reference](scripts/README.md)
