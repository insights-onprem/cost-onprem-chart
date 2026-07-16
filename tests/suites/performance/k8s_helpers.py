"""
Kubernetes resource patching and statistics helpers for performance tests.

Shared by test_valkey_eviction.py, test_db_resource_sweep.py, and
test_api_latency.py.
"""

import json
import statistics
import subprocess
from typing import Any, Dict, List, Optional

from utils import run_oc_command


# =============================================================================
# Percentile Calculation
# =============================================================================


def calculate_percentiles(
    latencies: List[float], errors: int = 0
) -> Dict[str, Any]:
    """Calculate p50/p95/p99 latency statistics.

    Works with any list of numeric measurements (seconds, milliseconds, etc.).
    """
    if not latencies:
        return {"p50": 0, "p95": 0, "p99": 0, "min": 0, "max": 0, "avg": 0, "count": 0, "errors": errors}

    n = len(latencies)
    if n < 2:
        v = round(latencies[0], 4)
        return {"p50": v, "p95": v, "p99": v, "min": v, "max": v, "avg": v, "count": n, "errors": errors}

    q = statistics.quantiles(latencies, n=100, method="inclusive")
    return {
        "p50": round(q[49], 4),
        "p95": round(q[94], 4),
        "p99": round(q[98], 4),
        "min": round(min(latencies), 4),
        "max": round(max(latencies), 4),
        "avg": round(statistics.mean(latencies), 4),
        "count": n,
        "errors": errors,
    }


# =============================================================================
# K8s Resource Patching
# =============================================================================


def get_resource_spec(
    namespace: str, kind: str, name: str
) -> dict:
    """Read the current container resource spec from a Deployment or StatefulSet."""
    result = run_oc_command(
        ["get", kind, name, "-n", namespace,
         "-o", "jsonpath={.spec.template.spec.containers[0].resources}"],
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return {}


def patch_resource_spec(
    namespace: str, kind: str, name: str, resources: dict,
    rollout_timeout: int = 180,
) -> bool:
    """Patch a Deployment or StatefulSet's container resources and wait for rollout.

    For StatefulSets, also deletes the pod to force recreation since
    StatefulSet updates don't automatically roll pods on resource changes.
    """
    result = run_oc_command(
        ["patch", kind, name, "-n", namespace,
         "--type=json",
         "-p", json.dumps([{
             "op": "replace",
             "path": "/spec/template/spec/containers/0/resources",
             "value": resources,
         }])],
        check=False,
    )
    if result.returncode != 0:
        print(f"[k8s-patch] Failed to patch {kind}/{name}: {result.stderr.strip()[:200]}")
        return False

    print(f"[k8s-patch] Patched {kind}/{name}")

    # StatefulSets need a pod delete to pick up resource changes
    if kind.lower() in ("statefulset", "sts"):
        _delete_first_pod(namespace, name)

    try:
        rollout = run_oc_command(
            ["rollout", "status", f"{kind}/{name}",
             "-n", namespace, f"--timeout={rollout_timeout}s"],
            check=False,
            timeout=rollout_timeout + 30,
        )
    except subprocess.TimeoutExpired:
        print(f"[k8s-patch] Rollout timed out for {kind}/{name}")
        return False

    if rollout.returncode != 0:
        print(f"[k8s-patch] Rollout failed: {rollout.stderr.strip()[:200]}")
        return False

    print(f"[k8s-patch] {kind}/{name} ready")
    return True


def restore_resource_spec(
    namespace: str, kind: str, name: str, resources: dict,
    default: Optional[dict] = None,
) -> bool:
    """Restore a Deployment or StatefulSet to previously-captured resources."""
    if not resources:
        resources = default or {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        }
    success = patch_resource_spec(namespace, kind, name, resources)
    label = f"{kind}/{name}"
    if success:
        print(f"[k8s-restore] {label} resources restored")
    else:
        print(f"[k8s-restore] WARNING: failed to restore {label}")
    return success


def merge_resources(original: dict, overrides: dict) -> dict:
    """Merge override resources into original, preserving unspecified fields.

    Example: merge_resources(
        {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}},
        {"limits": {"cpu": "4000m"}}
    ) -> {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "4000m", "memory": "512Mi"}}
    """
    merged = {
        "requests": dict(original.get("requests", {})),
        "limits": dict(original.get("limits", {})),
    }
    for section in ("requests", "limits"):
        if section in overrides:
            merged[section].update(overrides[section])
    return merged


def _delete_first_pod(namespace: str, owner_name: str) -> None:
    """Delete the first pod owned by a StatefulSet to force recreation.

    Uses the StatefulSet naming convention (<owner_name>-0) to identify the pod.
    """
    pod_name = f"{owner_name}-0"
    print(f"[k8s-patch] Deleting pod {pod_name} to apply new resources...")
    run_oc_command(
        ["delete", "pod", pod_name, "-n", namespace, "--wait=false"],
        check=False,
    )
