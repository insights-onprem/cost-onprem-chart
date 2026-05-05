# Disconnected (Air-Gapped) Deployment Guide

Deploy Cost Management On-Premise in disconnected OpenShift environments using `oc-mirror` for chart and image mirroring.

## Overview

In disconnected environments, clusters have no direct internet access. The `oc-mirror` tool mirrors Helm charts and container images from public registries to an internal mirror registry. The cost-onprem chart is designed to support offline templating -- `helm template` works with default values only (no `--set` flags required), which is exactly how `oc-mirror` discovers images.

> **Important:** Some images used by the chart cannot be auto-discovered by
> `oc-mirror` (for example, images referenced only in Helm hooks such as
> `pre-install`/`pre-upgrade`). Those **must** be listed explicitly in the
> `additionalImages` section of the `ImageSetConfiguration`. See
> [Discovering container images](#discovering-container-images) for details on
> image discovery and [Step 1](#step-1-create-imagesetconfiguration) for
> ready-to-use example files with the correct `additionalImages` already
> included.

## Prerequisites

- **oc-mirror v2** installed ([installation guide](https://docs.okd.io/latest/disconnected/mirroring/about-installing-oc-mirror-v2.html))
- Access to a mirror registry (e.g., `mirror.example.com:5000`)
- A connected workstation with internet access for running `oc-mirror`
- OpenShift CLI (`oc`) configured for the disconnected cluster

## Discovering container images

Image tags and repositories change with chart releases. Instead of copying a
static list into this document, use the provided example `ImageSetConfiguration`
files with the correct `additionalImages` already included. The examples are
kept aligned with CI validation to ensure complete image coverage.

**Auto-discovered:** Any image that appears in the manifests `oc-mirror`
renders from the Helm chart (it runs `helm template` internally) is mirrored
automatically when you list the chart under `mirror.helm`.

**`additionalImages`:** Images that are *not* visible during that pass (commonly
resources behind [Helm hooks](https://helm.sh/docs/topics/charts_hooks/) such
as `pre-install`/`pre-upgrade`) must be added manually. The repository keeps the
canonical hook list in `.github/workflows/lint-and-validate.yml` under
`additionalImages` so CI matches disconnected mirroring; align your
`ImageSetConfiguration` with that block when you upgrade the chart.

**Not from the chart:** `install-helm-chart.sh` can pull `amazon/aws-cli` for
one-shot S3 bucket creation. That image is not part of the Helm chart; add it
to `additionalImages` only if you use that script path, or set `S3_CLI_IMAGE` to
a mirrored image, or use `SKIP_S3_SETUP=true` / manual bucket creation.

### List images from an `oc-mirror` plan (`mapping.txt`)

Run `oc-mirror` in dry-run mode with the same `ImageSetConfiguration` you will
use for mirroring (Helm section plus `additionalImages`). The tool writes a
`mapping.txt` file under the workspace directory listing every source image in
the plan.

The project CI uses a throwaway registry only as a destination for planning; you
can mirror the same way. Example using the repository example
[`imageset-config-cost-onprem.sample.yaml`](../examples/disconnected/imageset-config-cost-onprem.sample.yaml):

```bash
cd /path/to/cost-onprem-chart

# Local registry as dry-run destination (same pattern as CI; stop/remove when done).
podman run -d --name oc-mirror-registry -p 5050:5000 docker.io/library/registry:2

oc-mirror --v2 --config docs/examples/disconnected/imageset-config-cost-onprem.sample.yaml \
  --workspace file:///tmp/oc-mirror-workspace \
  docker://localhost:5050 --dry-run --dest-tls-verify=false

MAPPING="$(find /tmp/oc-mirror-workspace -name mapping.txt -type f | head -1)"
cut -d= -f1 "$MAPPING" | sed 's|docker://||' | sort -u
```

To dry-run against a **local** chart directory, copy the example file and swap
`mirror.helm` to `mirror.helm.local` as described in the comments at the top of
that YAML, then pass your edited file to `--config`.

For mirroring operator prerequisites (AMQ Streams, RHBK, optional ODF), start
from
[`imageset-config-cost-onprem-with-prerequisites.sample.yaml`](../examples/disconnected/imageset-config-cost-onprem-with-prerequisites.sample.yaml)
instead.

> **Why hooks matter:** `oc-mirror` discovers images by rendering the chart
> internally. Anything not included in that pass must appear in
> `additionalImages`. CI enforces that the full chart image set is covered by
> the mirror plan; see `.github/workflows/lint-and-validate.yml`.

## Step 1: Create ImageSetConfiguration

Reuse the example files under `docs/examples/disconnected/` (copy and edit as
needed):

| File | Purpose | Use Case |
|------|---------|----------|
| [`imageset-config-cost-onprem.sample.yaml`](../examples/disconnected/imageset-config-cost-onprem.sample.yaml) | cost-onprem Helm chart + `additionalImages` for hooks and `install-helm-chart.sh` S3 setup | **Chart-only mirroring** — when OpenShift platform and operators are already available |
| [`imageset-config-cost-onprem-with-prerequisites.sample.yaml`](../examples/disconnected/imageset-config-cost-onprem-with-prerequisites.sample.yaml) | **Complete deployment** — OpenShift 4.20.12 platform + cost-onprem + all operators (AMQ Streams, RHBK, Local Storage, ODF) | **Full disconnected environment** — mirrors everything for `deploy-kafka.sh`, `deploy-rhbk.sh`, and storage setup |

**Chart version:** Edit `mirror.helm.repositories[].charts[].version` in both files to match your target release.

**Platform version:** The comprehensive example targets OpenShift 4.20.12. Adjust `platform.channels[].minVersion/maxVersion` for your specific patch level.

**Operator channels:** The comprehensive example uses tested operator channels for OpenShift 4.20. Verify channels exist for your OpenShift version in OperatorHub before mirroring.

**Note:** The comprehensive example is a tested, working configuration that mirrors everything needed for a complete disconnected OpenShift deployment with cost-onprem.

Copy one of the examples to `imageset-config.yaml` (or keep any path you
prefer and pass it to `oc-mirror -c`).

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
# Extract and install the mirrored chart (oc-mirror saves charts as .tgz files)
# Find the extracted chart in mirror-output/
tar -xzf mirror-output/*/charts/*.tgz

# Option A: Install directly with Helm
helm install cost-onprem ./cost-onprem \
  --namespace cost-onprem \
  --create-namespace

# Option B: Use the install script with the extracted chart
USE_LOCAL_CHART=true LOCAL_CHART_PATH=./cost-onprem \
  ./scripts/install-helm-chart.sh
```

> **Note:** `oc-mirror` mirrors container images but does not create OCI chart artifacts. The chart must be extracted from the `.tgz` file in the mirror output and installed as a local chart.

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

When new versions are released, bump the chart version in
`ImageSetConfiguration`, re-run the commands under
[Discovering container images](#discovering-container-images), and repeat the
mirror process (Steps 2-5). The install script supports version pinning:

```bash
CHART_VERSION=0.2.20-rc3 ./scripts/install-helm-chart.sh
```

> **Remember:** Use the provided example `ImageSetConfiguration` files which
> already include the correct `additionalImages` for hook-only containers. If
> contributing upstream, keep `.github/workflows/lint-and-validate.yml`
> `additionalImages` aligned with the example files. CI validates that all
> chart images are covered by the mirror plan.

## References

- [oc-mirror v2 documentation](https://docs.okd.io/latest/disconnected/mirroring/about-installing-oc-mirror-v2.html)
- [oc-mirror ImageSetConfiguration design](https://github.com/openshift/oc-mirror/blob/main/docs/design/imageset-configuration.md)
- [Helm chart values reference](../operations/configuration.md)
