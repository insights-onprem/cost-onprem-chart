# Cost Management On-Premise OpenShift Quick Start Guide

This guide walks you through deploying and testing the Cost Management On-Premise backend services on OpenShift clusters using the Helm chart from the [cost-onprem-chart repository](https://github.com/insights-onprem/cost-onprem-chart).

## Helm Chart Location

The Cost Management On-Premise Helm chart is maintained in a separate repository: **[insights-onprem/cost-onprem-chart](https://github.com/insights-onprem/cost-onprem-chart)**

### Deployment Methods

The deployment scripts provide flexible options for OpenShift:

1. **Helm Chart Deployment**: The `install-helm-chart.sh` script deploys the Cost Management On-Premise services
2. **Development Mode**: Use `USE_LOCAL_CHART=true` to install from a local chart directory

### Chart Features

- **46 OpenShift templates** for complete Cost Management On-Premise stack deployment
- **Automated CI/CD** with lint validation, version checking, and deployment testing
- **Comprehensive documentation** and troubleshooting guides

## Prerequisites

### System Resources
Ensure your cluster has adequate resources for the deployment:
- **Memory**: At least 16GB RAM (24GB+ recommended)
- **CPU**: 8+ cores
- **Storage**: S3-compatible object storage (ODF, AWS S3, MinIO, or other)

The deployment includes:
- PostgreSQL databases (unified)
- Kafka cluster (via Strimzi)
- Kruize optimization engine (1-2Gi - most memory intensive)
- Celery workers
- Various application services

### Required Tools
Install these tools on your system:

```bash
# Install OpenShift CLI
# Download from: https://mirror.openshift.com/pub/openshift-v4/clients/ocp/
# Or use package manager
brew install openshift-cli  # macOS
```

### Verify Installation
```bash
oc version
kubectl version --client
helm version
```

## Quick Deployment

### 1. Navigate to Scripts Directory
```bash
cd /path/to/cost-onprem-chart/scripts/
```

### 2. Deploy Kafka Infrastructure (Strimzi)
```bash
# Deploy Strimzi operator and Kafka cluster
./deploy-strimzi.sh
```

### 3. Deploy Cost Management On-Premise Services
```bash
# Deploy with auto-configuration (recommended)
./install-helm-chart.sh
```

The script will:
- ✅ Download latest Helm chart release from GitHub
- ✅ Deploy all services with OpenShift configuration
- ✅ Auto-detect and configure S3 storage
- ✅ Run comprehensive health checks
- ✅ Verify connectivity and authentication

**Expected Output:**
```
[INFO] Running health checks...
[SUCCESS] ✓ All pods are ready
[SUCCESS] ✓ Routes are accessible
[SUCCESS] All core services are healthy and operational!
```

### 4. Verify Deployment
```bash
# Check deployment status
./install-helm-chart.sh status

# Run health checks
./install-helm-chart.sh health
```

### Alternative: Manual Helm Chart Installation

If you prefer to manually install the Helm chart or need a specific version:

#### Install Latest Chart Release
```bash
# Download and install latest chart release
LATEST_URL=$(curl -s https://api.github.com/repos/insights-onprem/cost-onprem-chart/releases/latest | jq -r '.assets[] | select(.name | endswith(".tgz")) | .browser_download_url')
curl -L -o cost-onprem-latest.tgz "$LATEST_URL"
helm install cost-onprem cost-onprem-latest.tgz -n cost-onprem --create-namespace
```

#### Install Specific Chart Version
```bash
# Install a specific version (e.g., v0.1.0)
VERSION="v0.1.0"
curl -L -o cost-onprem-${VERSION}.tgz "https://github.com/insights-onprem/cost-onprem-chart/releases/download/${VERSION}/cost-onprem-${VERSION}.tgz"
helm install cost-onprem cost-onprem-${VERSION}.tgz -n cost-onprem --create-namespace
```

#### Development Mode (Local Chart)
```bash
# Use local chart source
USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem ./install-helm-chart.sh

# Or direct Helm installation
helm install cost-onprem ./cost-onprem -n cost-onprem --create-namespace
```

## Access Points

After successful deployment, services are accessible via OpenShift Routes:

```bash
# List all routes
oc get routes -n cost-onprem
```

| Service | Route | Description |
|---------|-------|-------------|
| **ROS API** | `cost-onprem-main-cost-onprem.apps...` | Main REST API |
| **Cost Management API** | `cost-onprem-api-cost-onprem.apps...` | Cost reports API |
| **Ingress** | `cost-onprem-ingress-cost-onprem.apps...` | File upload endpoint |
| **UI** | `cost-onprem-ui-cost-onprem.apps...` | Web interface |
| **Kruize API** | `cost-onprem-kruize-cost-onprem.apps...` | Optimization engine |

### Quick Access Test
```bash
# Get route hostnames
MAIN_ROUTE=$(oc get route cost-onprem-main -n cost-onprem -o jsonpath='{.spec.host}')
INGRESS_ROUTE=$(oc get route cost-onprem-ingress -n cost-onprem -o jsonpath='{.spec.host}')

# Test ROS API health
curl -k https://$MAIN_ROUTE/ready

# Test Ingress version
curl -k https://$INGRESS_ROUTE/api/ingress/v1/version
```

## End-to-End Data Flow Testing

### Run Complete Test
```bash
# Run the pytest E2E test suite (~3 minutes)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --e2e
```

The test will:
- ✅ Create OCP provider via Koku Sources API
- ✅ Generate test data with NISE
- ✅ Upload data to S3 bucket
- ✅ Publish Kafka event for processing
- ✅ Verify data processing in PostgreSQL
- ✅ Validate cost calculations

**Expected Output:**
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

Phases: 7/7 passed
  ✅ preflight
  ✅ migrations
  ✅ kafka_validation
  ✅ provider
  ✅ data_upload
  ✅ processing
  ✅ validation

✅ E2E SMOKE TEST PASSED
```

### View Service Logs
```bash
# View specific service logs
oc logs -n cost-onprem -l app.kubernetes.io/component=processor -f

# View all pods
oc get pods -n cost-onprem
```

### Monitor Processing
```bash
# Watch pods in real-time
oc get pods -n cost-onprem -w

# Check persistent volumes
oc get pvc -n cost-onprem

# View all services
oc get svc -n cost-onprem
```

## Configuration

### Environment Variables
```bash
# Customize deployment
export HELM_RELEASE_NAME=my-cost-onprem
export NAMESPACE=my-namespace

# Deploy with custom settings
./install-helm-chart.sh
```

### Helm Values Override
```bash
# Create custom values file
cat > my-values.yaml << EOF
global:
  storageClass: "ocs-storagecluster-ceph-rbd"
database:
  storage:
    size: 20Gi
resources:
  application:
    requests:
      memory: "256Mi"
      cpu: "200m"
EOF

# Deploy with custom values
helm upgrade --install cost-onprem ./cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  -f my-values.yaml
```

## Cleanup

### Remove Deployment Only
```bash
# Remove Helm release and namespace
./install-helm-chart.sh cleanup
```

### Manual Cleanup
```bash
# Delete Helm release
helm uninstall cost-onprem -n cost-onprem

# Delete namespace
oc delete namespace cost-onprem
```

## Quick Status Check

Use this script to verify all services are working:

```bash
#!/bin/bash
echo "=== Cost Management On-Premise Status Check ==="

# Check pod status
echo "Pod Status:"
oc get pods -n cost-onprem

# Check services with issues
echo -e "\nPods with issues:"
oc get pods -n cost-onprem --field-selector=status.phase!=Running

# Check routes
echo -e "\nRoutes:"
oc get routes -n cost-onprem

# Check API endpoints
echo -e "\nAPI Health Checks:"
MAIN_ROUTE=$(oc get route cost-onprem-main -n cost-onprem -o jsonpath='{.spec.host}' 2>/dev/null)
if [ -n "$MAIN_ROUTE" ]; then
    curl -sk https://$MAIN_ROUTE/ready >/dev/null && echo "✓ ROS API" || echo "✗ ROS API failed"
fi

echo -e "\nFor detailed troubleshooting, run: NAMESPACE=cost-onprem ./scripts/run-pytest.sh -v"
```

## Next Steps

After successful deployment:

1. **Configure JWT Authentication**: See [Keycloak Setup Guide](keycloak-jwt-authentication-setup.md)
2. **Set Up TLS**: See [TLS Certificate Options](tls-certificate-options.md)
3. **Explore APIs**: Use the access points to interact with services
4. **Load Test Data**: Upload your own cost management files
5. **Monitor Metrics**: Check Kruize recommendations and optimizations

## Support

For issues or questions:
- Check [Troubleshooting Guide](troubleshooting.md)
- Run E2E test: `NAMESPACE=cost-onprem ./scripts/run-pytest.sh --e2e`
- Check pod logs: `oc logs -n cost-onprem <pod-name>`
- Verify configuration: `helm get values cost-onprem -n cost-onprem`
