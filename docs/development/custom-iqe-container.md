# Custom IQE Container Guide

This guide explains how to build and deploy custom IQE containers with local plugin changes for testing against on-prem Cost Management.

## Quick Start

```bash
# Build and push to internal OpenShift registry
./scripts/build-custom-iqe.sh

# Run tests with custom image
IQE_IMAGE="default-route-openshift-image-registry.apps.your-cluster.com/cost-onprem/iqe-cost-management:custom" \
  ./scripts/run-iqe-tests.sh
```

## Prerequisites

1. **Podman** installed and running
2. **oc** CLI logged into target cluster
3. **iqe-cost-management-plugin** cloned locally

```bash
# Clone the IQE plugin (if not already done)
git clone git@github.com:RedHatQE/iqe-cost-management-plugin.git ../iqe-cost-management-plugin
```

## Building Custom Containers

### Using the Build Script

The `build-custom-iqe.sh` script automates the build process:

```bash
# Build with defaults (internal registry, tag: custom)
./scripts/build-custom-iqe.sh

# Build with custom tag
./scripts/build-custom-iqe.sh --tag my-feature

# Build only (no push)
./scripts/build-custom-iqe.sh --no-push

# Specify plugin path
./scripts/build-custom-iqe.sh --plugin-path /path/to/iqe-cost-management-plugin
```

### Manual Build Steps

If you need more control over the build process:

```bash
# 1. Create a Dockerfile
cat > /tmp/Dockerfile << 'EOF'
FROM quay.io/cloudservices/iqe-tests:cost-management

USER root
RUN pip uninstall -y iqe-cost-management || true
COPY iqe-cost-management-plugin /tmp/iqe-cost-management-plugin
RUN pip install /tmp/iqe-cost-management-plugin && \
    rm -rf /tmp/iqe-cost-management-plugin
USER 1001
EOF

# 2. Build the image
podman build -t my-iqe:custom -f /tmp/Dockerfile .

# 3. Tag for registry
REGISTRY=$(oc get route default-route -n openshift-image-registry -o jsonpath='{.spec.host}')
podman tag my-iqe:custom ${REGISTRY}/cost-onprem/iqe-cost-management:custom

# 4. Login and push
podman login -u kubeadmin -p $(oc whoami -t) ${REGISTRY} --tls-verify=false
podman push ${REGISTRY}/cost-onprem/iqe-cost-management:custom --tls-verify=false
```

## OpenShift Internal Registry

### Exposing the Registry

The internal registry must be exposed for external push access:

```bash
# Enable default route
oc patch configs.imageregistry.operator.openshift.io/cluster \
  --type merge \
  -p '{"spec":{"defaultRoute":true}}'

# Get the route
oc get route default-route -n openshift-image-registry
```

### Registry Authentication

```bash
# Login using kubeadmin token
REGISTRY=$(oc get route default-route -n openshift-image-registry -o jsonpath='{.spec.host}')
podman login -u kubeadmin -p $(oc whoami -t) ${REGISTRY} --tls-verify=false
```

### DNS Resolution

If the registry hostname doesn't resolve, add it to `/etc/hosts`:

```bash
# Get the router IP
ROUTER_IP=$(oc get svc router-default -n openshift-ingress -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Add to /etc/hosts
echo "${ROUTER_IP} default-route-openshift-image-registry.apps.your-cluster.com" | sudo tee -a /etc/hosts
```

## macOS with Podman

On macOS, Podman runs inside a Linux VM. Some considerations:

### Starting the Podman Machine

```bash
# Start the Podman machine
podman machine start

# Verify it's running
podman machine list
```

### Build Context

The build context is automatically mounted into the Podman VM. No special configuration is needed for most use cases.

### Network Issues

If you have connectivity issues to the OpenShift registry:

1. Ensure the Podman machine can resolve the registry hostname
2. The `--tls-verify=false` flag is required for self-signed certificates

## Using Custom Images

### With run-iqe-tests.sh

```bash
# Set the image via environment variable
export IQE_IMAGE="default-route-openshift-image-registry.apps.your-cluster.com/cost-onprem/iqe-cost-management:custom"
./scripts/run-iqe-tests.sh

# Or inline
IQE_IMAGE="..." ./scripts/run-iqe-tests.sh
```

### Image Pull Secrets

For internal registry images, the script automatically configures the appropriate `imagePullSecrets`. For external registries, you may need to create a pull secret:

```bash
# Create pull secret for external registry
kubectl create secret docker-registry iqe-pull-secret \
  --docker-server=quay.io \
  --docker-username=your-username \
  --docker-password=your-password \
  -n cost-onprem
```

## Common Issues

### "unauthorized: authentication required"

```bash
# Re-authenticate to registry
podman login -u kubeadmin -p $(oc whoami -t) ${REGISTRY} --tls-verify=false
```

### "certificate signed by unknown authority"

```bash
# Use --tls-verify=false for self-signed certs
podman push ${IMAGE} --tls-verify=false
```

### "invalid username/password"

The OpenShift token may have expired:

```bash
# Re-login to OpenShift
oc login ...

# Then re-authenticate to registry
podman login -u kubeadmin -p $(oc whoami -t) ${REGISTRY} --tls-verify=false
```

### Slow Push Times

Large images take time to push. The base IQE image is ~2GB. Consider:

- Using a faster network connection
- Building on a machine closer to the cluster
- Using incremental builds when possible
