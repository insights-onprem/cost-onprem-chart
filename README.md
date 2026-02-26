# Cost Management On-Premise Helm Repository

This is the Helm chart repository for **cost-onprem** -- a unified Helm chart that deploys
Red Hat Cost Management and the Resource Optimization Service (ROS) on OpenShift.
The chart packages all required components (Koku, ROS, Kruize, Sources API, PostgreSQL,
Valkey, and an Envoy-based API gateway) into a single deployable unit with
JWT-based multi-tenant authentication, network policies, and Prometheus monitoring
out of the box.

For full documentation, architecture guides, and operational runbooks, see the
[main repository](https://github.com/insights-onprem/cost-onprem-chart).

## Using This Repository

### Add the Helm Repository

```bash
helm repo add cost-onprem https://insights-onprem.github.io/cost-onprem-chart
helm repo update
```

### Install the Chart

```bash
helm install cost-onprem cost-onprem/cost-onprem \
  --namespace cost-onprem \
  --create-namespace
```

### Install a Specific Version

```bash
# List available versions
helm search repo cost-onprem --versions

# Install a specific version
helm install cost-onprem cost-onprem/cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  --version 0.2.14
```

### OCI Registry (Alternative)

Charts are also published to the GitHub Container Registry:

```bash
helm install cost-onprem \
  oci://ghcr.io/insights-onprem/cost-onprem-chart/cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  --version 0.2.14
```

## Prerequisites

The chart requires OpenShift 4.18+, S3-compatible object storage, and a Kafka cluster.
Authentication is handled via Keycloak (RHBK).

Refer to the [Installation Guide](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/installation.md)
for detailed prerequisites, and the
[Resource Requirements Guide](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/resource-requirements.md)
for cluster sizing.

## Documentation

| Category | Link |
|----------|------|
| Quick Start | [Quickstart Guide](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/quickstart.md) |
| Full Installation | [Installation Guide](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/installation.md) |
| Architecture | [Platform Guide](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/architecture/platform-guide.md) |
| Configuration | [Configuration Reference](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/configuration.md) |
| Authentication | [Keycloak JWT Setup](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/api/keycloak-jwt-authentication-setup.md) |
| TLS | [TLS Certificate Options](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/tls-certificate-options.md) |
| Troubleshooting | [Troubleshooting Guide](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/operations/troubleshooting.md) |
| All Documentation | [Documentation Index](https://github.com/insights-onprem/cost-onprem-chart/blob/main/docs/README.md) |

## Releases

Each chart version is published as a
[GitHub Release](https://github.com/insights-onprem/cost-onprem-chart/releases)
with auto-generated release notes.

## License

See the [LICENSE](https://github.com/insights-onprem/cost-onprem-chart/blob/main/LICENSE) file in the main repository.
