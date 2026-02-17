# Disconnected (Air-Gapped) Deployment Guide

Deploy Cost Management On-Premise in disconnected OpenShift environments using `oc-mirror` for chart and image mirroring.

## Overview

In disconnected environments, clusters have no direct internet access. The `oc-mirror` tool mirrors Helm charts and container images from public registries to an internal mirror registry. The cost-onprem chart is designed to support offline templating -- `helm template` works with default values only (no `--set` flags required), which is exactly how `oc-mirror` discovers images.

## Prerequisites

- **oc-mirror v2** installed ([installation guide](https://docs.okd.io/latest/disconnected/mirroring/about-installing-oc-mirror-v2.html))
- Access to a mirror registry (e.g., `mirror.example.com:5000`)
- A connected workstation with internet access for running `oc-mirror`
- OpenShift CLI (`oc`) configured for the disconnected cluster

## Step 1: Create ImageSetConfiguration

Create a file named `imageset-config.yaml`:

```yaml
apiVersion: mirror.openshift.io/v2alpha1
kind: ImageSetConfiguration
mirror:
  helm:
    repositories:
      - name: cost-onprem
        url: https://insights-onprem.github.io/cost-onprem-chart
        charts:
          - name: cost-onprem
            version: "0.2.9"
  additionalImages:
    # Custom images (quay.io/insights-onprem)
    - name: quay.io/insights-onprem/ros-ocp-backend:latest
    - name: quay.io/insights-onprem/koku:sources
    - name: quay.io/insights-onprem/insights-ingress-go:latest
    - name: quay.io/insights-onprem/koku-ui-onprem:latest
    - name: quay.io/insights-onprem/postgresql:16
    # Third-party images
    - name: registry.access.redhat.com/ubi9/ubi-minimal:latest
    - name: quay.io/redhat-services-prod/kruize-autotune-tenant/autotune:d0b4337
    - name: registry.redhat.io/rhel10/valkey-8:latest
    - name: registry.redhat.io/openshift-service-mesh/proxyv2-rhel9:2.6
    - name: registry.redhat.io/rhceph/oauth2-proxy-rhel9:v7.6.0
```

## Step 2: Mirror to Disk

On the connected workstation, mirror the chart and images to a local archive:

```bash
oc-mirror --v2 -c imageset-config.yaml file://mirror-output
```

This creates a directory `mirror-output/` containing:
- The packaged Helm chart
- All container images as OCI archives
- A mapping file for the mirror registry

## Step 3: Transfer to Disconnected Environment

Copy the `mirror-output/` directory to the disconnected environment using your preferred transfer method (USB drive, secure file transfer, etc.).

## Step 4: Mirror to Internal Registry

On the disconnected cluster (or a bastion host with access to the mirror registry):

```bash
oc-mirror --v2 -c imageset-config.yaml \
  --from file://mirror-output \
  docker://mirror.example.com:5000
```

## Step 5: Apply ICSP/IDMS

After mirroring, `oc-mirror` generates `ImageContentSourcePolicy` (ICSP) or `ImageDigestMirrorSet` (IDMS) resources. Apply them to the cluster:

```bash
oc apply -f mirror-output/results-*/
```

This configures the cluster to pull images from the mirror registry instead of the original registries.

## Step 6: Install the Chart

Install the chart from the mirrored registry. Use the install script with the local chart:

```bash
# Option A: Use the mirrored chart directly
helm install cost-onprem oci://mirror.example.com:5000/cost-onprem/cost-onprem \
  --version 0.2.9 \
  --namespace cost-onprem \
  --create-namespace

# Option B: Use the install script with the extracted chart
USE_LOCAL_CHART=true LOCAL_CHART_PATH=./cost-onprem \
  ./scripts/install-helm-chart.sh
```

The ICSP/IDMS applied in Step 5 ensures that all image pulls are redirected to the mirror registry automatically.

## Verification

After installation, verify that all pods are running and images are pulled from the mirror registry:

```bash
# Check all pods are running
kubectl get pods -n cost-onprem -l app.kubernetes.io/instance=cost-onprem

# Verify images come from mirror registry
kubectl get pods -n cost-onprem -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' | sort -u
```

## Updating Images

When new versions are released, update the `ImageSetConfiguration` with the new chart version and image tags, then repeat the mirror process (Steps 2-5). The install script supports version pinning:

```bash
CHART_VERSION=0.3.0 ./scripts/install-helm-chart.sh
```

## References

- [oc-mirror v2 documentation](https://docs.okd.io/latest/disconnected/mirroring/about-installing-oc-mirror-v2.html)
- [oc-mirror ImageSetConfiguration design](https://github.com/openshift/oc-mirror/blob/main/docs/design/imageset-configuration.md)
- [Helm chart values reference](../operations/configuration.md)
