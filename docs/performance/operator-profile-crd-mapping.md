# Soft Recommendation: Operator CRD ↔ Helm Profile Mapping

> **Status**: Soft recommendation for discussion (COST-7618). Not a committed
> design. Grounded in the early operator architecture and scaffold repo.

## Context

| Source | What it is |
|--------|------------|
| [masayag gist](https://gist.github.com/masayag/cdb5906331e6f6cef2285118c89f02dd) | Architecture plan: native Go operator, OLM/OperatorHub, full `CostManagement` CRD sketch |
| [project-koku/koku-server-operator](https://github.com/project-koku/koku-server-operator) | Early scaffold (`CostManagement` CR exists; `spec` is still placeholder `Foo`) |
| This chart | Reference implementation — Helm overlays encode validated sizing |

Key constraints from the architecture plan that affect sizing:

- **No Helm→operator migration path** — chart is reference only; CRD API can be designed cleanly
- **Native Go operator** (not Helm-based) — profile defaults live in Go / webhook defaulting, not Helm merges
- **CMMO is out of scope** — separate product; this operator is the self-managed server
- **S3 is always a prerequisite** — never installed by the operator
- **Kafka / RHBK**: CR-only management when `deploy: true` (no OLM Subscription meta-operator)
- Planned samples: `costmanagement-minimal.yaml`, `costmanagement-production.yaml`, `costmanagement-byoi.yaml`

## Purpose of this recommendation

The architecture gist already defines a **component-level** CR (`database`, `cache`, `costManagement.listener`, `workers`, …). That is the right long-term shape for day-2 control.

What the gist does **not** yet have is a **sizing profile** abstraction. Our Helm overlays fill that gap for the chart today. Recommendation: add a thin `spec.profile` (or equivalent) that **defaults** the component fields to the same numbers as the overlays, without replacing the component-level API.

| Profile | Helm overlay (reference numbers) |
|---------|----------------------------------|
| Small | [`cost-onprem/values-small.yaml`](../../cost-onprem/values-small.yaml) (≡ chart defaults) |
| Medium | [`cost-onprem/values-medium.yaml`](../../cost-onprem/values-medium.yaml) |
| Large | [`cost-onprem/values-large.yaml`](../../cost-onprem/values-large.yaml) |
| XLarge | [`cost-onprem/values-xlarge.yaml`](../../cost-onprem/values-xlarge.yaml) |

## Recommended layering on the existing CRD sketch

Keep the gist's CR structure. Add one optional field:

```yaml
apiVersion: cost.redhat.com/v1alpha1
kind: CostManagement
metadata:
  name: costmanagement
  namespace: cost-onprem
spec:
  # NEW — soft recommendation (not in gist yet)
  # Mutating webhook / defaulting expands this into component fields below
  # when those fields are unset. Explicit component values always win.
  profile: medium  # small | medium | large | xlarge

  # --- Existing gist shape (abridged) ---
  database:
    deploy: true
    storage: { size: 50Gi }          # profile can default size + resources
    resources: {}                    # filled by profile if empty

  cache:
    deploy: true

  kafka:
    deploy: true

  objectStorage: {}                  # prerequisite / auto-detect

  authentication:
    deploy: true

  costManagement:
    listener:
      replicas: 2                    # or omit → profile supplies
      resources: {}
    workers:
      ocp: { replicas: 2 }
      summary: { replicas: 2 }

  resourceOptimization:
    processor:
      replicas: 2
    kruize:
      resources: {}                  # replicas forced to 1 (PERF-FINDING-004)

  gateway: {}
  ingress:
    maxUploadSize: 209715200         # or omit → profile supplies

  ui:
    enabled: true
```

**Defaulting rule (suggested):**

1. If `spec.profile` is set, apply profile defaults to any **unset** nested fields.
2. If a nested field is set in the CR, it wins (sparse override).
3. If `spec.profile` is unset, use **small** defaults (matches chart defaults / COST-7599).
4. Reflect the effective profile in `status.appliedProfile` (and optionally `status.discoveredConfig`).

This matches the gist's planned mutating webhook for defaulting, and maps cleanly onto the planned sample files:

| Planned operator sample | Suggested profile |
|-------------------------|-------------------|
| `costmanagement-minimal.yaml` | `small` |
| `costmanagement-production.yaml` | `medium` or `large` (product call) |
| `costmanagement-byoi.yaml` | any profile + `database/cache/kafka/authentication.deploy: false` |

## Field mapping: Helm overlay → gist CR paths

| Helm overlay path | Gist / operator CR path | Profile-relevant? |
|-------------------|-------------------------|-------------------|
| `resources.database.*` | `spec.database.resources` (+ `spec.database.storage.size`) | Yes |
| *(Valkey not in overlays today)* | `spec.cache.resources` / `persistence.size` | Optional later |
| `costManagement.listener.*` | `spec.costManagement.listener.*` | Yes |
| `costManagement.celery.workers.ocp.*` | `spec.costManagement.workers.ocp.*` | Yes |
| `costManagement.celery.workers.summary.*` | `spec.costManagement.workers.summary.*` | Yes |
| `ros.processor.replicas` | `spec.resourceOptimization.processor.replicas` | Yes |
| `resources.rosProcessor.*` | `spec.resourceOptimization.processor.resources` *(add if missing)* | Yes |
| `resources.kruize.*` | `spec.resourceOptimization.kruize.resources` | Yes |
| `resources.application.*` | Prefer dedicated `spec.ingress.resources` (FINDING-022) | Yes |
| `ingress.upload.maxUploadSize` / `maxMemory` | `spec.ingress.maxUploadSize` (+ add `maxMemory` if needed) | Yes |
| `jwtAuth.envoy.ingressTimeout` | `spec.gateway.ingressTimeout` *(add; gist has resources only)* | Yes |
| `jwtAuth.envoy.ingressPerTryTimeout` | `spec.gateway.ingressPerTryTimeout` | Yes |
| `gatewayRoute.annotations.haproxy...timeout` | Operator-owned Route annotation from same gateway timeout | Yes |

Gaps to close in the gist CR when sizing lands:

1. **Gateway timeouts** — needed for large/xlarge (PERF-FINDING-001 / 020); operator should restart Envoy on change.
2. **Ingress `maxMemory`** — chart has it; gist only shows `maxUploadSize`.
3. **ROS processor resources** — chart uses `resources.rosProcessor`; gist shows replicas only under `resourceOptimization.processor`.
4. **Dedicated ingress memory** — avoid sharing a generic application block (FINDING-022).

## Example: medium profile

**Helm (today):**

```bash
helm upgrade --install cost-onprem ./cost-onprem \
  -n cost-onprem \
  -f openshift-values.yaml \
  -f cost-onprem/values-medium.yaml
```

**Operator (proposed equivalent using gist + profile):**

```yaml
apiVersion: cost.redhat.com/v1alpha1
kind: CostManagement
metadata:
  name: costmanagement
  namespace: cost-onprem
spec:
  profile: medium
  database:
    deploy: true
  cache:
    deploy: true
  kafka:
    deploy: true
  authentication:
    deploy: true
  objectStorage: {}   # auto-detect ODF / OBC, or set endpoint + credentialsSecret
  ui:
    enabled: true
```

Sparse override (same CR, bump only listener replicas):

```yaml
spec:
  profile: medium
  costManagement:
    listener:
      replicas: 3
```

## Validation rules (from performance findings)

Worth encoding as CEL / webhook checks on the gist CR:

| Rule | Source |
|------|--------|
| `resourceOptimization.kruize` effective replicas = 1 | PERF-FINDING-004 |
| Kruize CPU limit ≥ 2000m | Liveness under load |
| `ingress.maxMemory` ≤ 128Mi until multipart upload lands | Pipeline stability / FINDING-024 |
| Large/xlarge → gateway timeouts ≥ 600s | PERF-FINDING-001 |
| Listener CPU boost is optional; not required for correctness through medium | PERF-FINDING-035 / VTC-001a |

## How this fits the operator roadmap

From the gist Phase 4 ("Performance testing at scale") and sample CR plans:

1. Keep building the component-level CR as designed.
2. Treat `cost-onprem/values-*.yaml` as the **numeric contract** for profile tables in Go (or generated assets).
3. Add `spec.profile` + webhook defaulting before OperatorHub GA so samples stay short.
4. Map `costmanagement-minimal.yaml` → small; production sample → medium/large with profile set.
5. Reuse chart E2E/perf suites against operator-deployed clusters (gist §9 already assumes this).

## Non-goals

- Replacing or wrapping CMMO
- Changing the gist's `deploy: true/false` infrastructure model
- Managing OLM Subscriptions for AMQ Streams / RHBK (Alternative D in the gist)
- Committing Helm coexistence — chart remains reference/test harness only

## Related

- Architecture: [Cost Management On-Premise Operator Architecture](https://gist.github.com/masayag/cdb5906331e6f6cef2285118c89f02dd)
- Scaffold: [project-koku/koku-server-operator](https://github.com/project-koku/koku-server-operator)
- [Sizing guide](./sizing-guide.md)
- [Profile overlays](../../cost-onprem/) — `values-{small,medium,large,xlarge}.yaml`
- [FINDINGS.md](./FINDINGS.md)
