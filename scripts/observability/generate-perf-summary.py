#!/usr/bin/env python3
"""
Generate perf-summary.json — a flat, Infinity-datasource-queryable summary of a perf run.

This file is uploaded to MinIO alongside the raw results and lets a persistent
Grafana instance (with the Infinity datasource) visualize historical runs without
needing access to the test cluster or its Prometheus instance.

Output: <run-dir>/results/perf-summary.json

Schema:
  {
    "run":     { run-level metadata },
    "tests":   [ { per-test flat row }, ... ],
    "api":     [ { per-endpoint latency row }, ... ],
    "ingestion": [ { per-ingestion-test row }, ... ]
  }

Usage:
    python3 scripts/observability/generate-perf-summary.py --run-dir tests/perf-runs/<id>
    python3 scripts/observability/generate-perf-summary.py --run-dir tests/perf-runs/<id> --update-index
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from run_utils import load_metadata, load_session, parse_junit


def _import_report_module():
    """Dynamically import generate-perf-run-report to reuse KPI_THRESHOLDS."""
    script_dir = Path(__file__).resolve().parent
    report_path = script_dir / "generate-perf-run-report.py"
    if not report_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("perf_run_report", report_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_metrics_snapshots(run_dir: Path) -> dict:
    """Load and aggregate metrics from collected snapshots.
    
    Metrics are collected by collect-metrics.sh and stored in:
    - ./perf-runs/{run_id}/metrics/ (from script working directory)
    - Or {run_dir}/metrics/ if present
    
    Returns aggregated resource summary with min/max/avg statistics.
    """
    import statistics
    
    # Try multiple possible locations for metrics
    run_id = run_dir.name
    possible_dirs = [
        run_dir / "metrics",
        run_dir.parent.parent / "perf-runs" / run_id / "metrics",
        Path("perf-runs") / run_id / "metrics",
    ]
    
    metrics_dir = None
    for d in possible_dirs:
        if d.exists() and list(d.glob("snapshot_*.json")):
            metrics_dir = d
            break
    
    if not metrics_dir:
        return {}
    
    snapshots = []
    for snap_file in sorted(metrics_dir.glob("snapshot_*.json")):
        try:
            snapshots.append(json.loads(snap_file.read_text()))
        except Exception:
            continue
    
    if not snapshots:
        return {}
    
    # Collect time-series data for key metrics
    cpu_values = []
    mem_values = []
    valkey_mem = []
    valkey_clients = []
    valkey_cmds = []
    pg_connections = []
    pg_cache_hit = []
    
    for snap in snapshots:
        m = snap.get("metrics", {})
        
        cpu = m.get("pod_cpu_usage", 0)
        if isinstance(cpu, (int, float)) and cpu > 0:
            cpu_values.append(cpu)
        
        mem = m.get("pod_memory_usage_bytes", 0)
        if isinstance(mem, (int, float)) and mem > 0:
            mem_values.append(mem / 1024 / 1024)  # MB
        
        vk_mem = m.get("valkey_memory_used_bytes", 0)
        if isinstance(vk_mem, (int, float)) and vk_mem > 0:
            valkey_mem.append(vk_mem / 1024 / 1024)  # MB
        
        vk_clients = m.get("valkey_connected_clients", 0)
        if isinstance(vk_clients, (int, float)):
            valkey_clients.append(vk_clients)
        
        vk_cmds = m.get("valkey_commands_per_sec", 0)
        if isinstance(vk_cmds, (int, float)):
            valkey_cmds.append(vk_cmds)
        
        pg_conn = m.get("pg_connections_active")
        if isinstance(pg_conn, (int, float)):
            pg_connections.append(pg_conn)
        elif isinstance(pg_conn, list) and pg_conn:
            for item in pg_conn:
                if isinstance(item, dict) and "value" in item:
                    pg_connections.append(float(item["value"]))
        
        pg_hit = m.get("pg_cache_hit_rate")
        if isinstance(pg_hit, (int, float)):
            pg_cache_hit.append(pg_hit)
        elif isinstance(pg_hit, list) and pg_hit:
            for item in pg_hit:
                if isinstance(item, dict) and "value" in item:
                    pg_cache_hit.append(float(item["value"]))
    
    def _stats(values):
        if not values:
            return None
        return {
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "avg": round(statistics.mean(values), 3),
        }
    
    return {
        "snapshot_count": len(snapshots),
        "time_start": snapshots[0].get("timestamp", ""),
        "time_end": snapshots[-1].get("timestamp", ""),
        "pod_cpu_cores": _stats(cpu_values),
        "pod_memory_mb": _stats(mem_values),
        "valkey_memory_mb": _stats(valkey_mem),
        "valkey_clients": _stats(valkey_clients),
        "valkey_cmds_sec": _stats(valkey_cmds),
        "pg_connections": _stats(pg_connections),
        "pg_cache_hit_pct": _stats(pg_cache_hit),
    }


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def build_summary(run_dir: Path) -> dict:
    session  = load_session(run_dir)
    metadata = load_metadata(run_dir)
    junit    = parse_junit(run_dir) or {}
    results  = (session or {}).get("results", [])
    run_id   = run_dir.name

    # Run-level metadata
    total_s = sum(
        sum(t.get("duration_seconds", 0) for t in r.get("timings", []))
        for r in results
    )
    cluster = metadata.get("cluster_info") or (results[0].get("cluster_info") if results else {}) or {}

    run_meta = {
        "run_id":        run_id,
        "timestamp":     metadata.get("created_at") or (results[0].get("timestamp") if results else ""),
        "chart_version": metadata.get("chart_version") or (results[0].get("chart_version") if results else "unknown"),
        "profile":       metadata.get("perf_profile") or (results[0].get("profile") if results else "unknown"),
        "perf_suite":    metadata.get("perf_suite", "all"),
        "total_tests":   junit.get("total", len(results)),
        "passed":        junit.get("passed", sum(1 for r in results if r.get("passed"))),
        "failed":        junit.get("failed", sum(1 for r in results if not r.get("passed"))),
        "duration_min":  round(total_s / 60, 1),
        "ocp_version":   cluster.get("ocp_version", ""),
        "node_count":    cluster.get("node_count", 0),
        "storage_type":  cluster.get("storage_type", ""),
        "s3_backend":    cluster.get("s3_backend", ""),
        "namespace":     metadata.get("namespace", "cost-onprem"),
    }

    # Flat test rows — one row per test result
    test_rows = []
    for r in results:
        dur_s = round(sum(t.get("duration_seconds", 0) for t in r.get("timings", [])), 1)
        m = r.get("metrics") or {}
        test_rows.append({
            "run_id":       run_id,
            "test_name":    r["test_name"],
            "short_name":   r["test_name"].replace("test_perf_", "").replace("_baseline", "")[:50],
            "status":       "PASS" if r.get("passed") else "FAIL",
            "passed":       1 if r.get("passed") else 0,
            "duration_s":   dur_s,
            "duration_min": round(dur_s / 60, 2),
            "error":        (r.get("error_message") or "")[:120],
            "profile":      r.get("profile", run_meta["profile"]),
            "chart_version": r.get("chart_version", run_meta["chart_version"]),
            "timestamp":    r.get("timestamp", ""),
            # Carry through summary metrics for quick access
            "upload_throughput_mb_s": round(
                (m.get("upload") or {}).get("upload_mb_per_second", 0) or
                m.get("upload_throughput_mb_s", 0), 4
            ),
            "listener_cpu_cores": round(m.get("listener_cpu_cores", 0), 4),
            "api_p95_ms": round(m.get("aggregate_p95", 0) * 1000, 1),
            "within_window": int(m.get("within_window", -1)),
            # ROS throughput (populated for ROS tests, 0 for others)
            "experiment_count": m.get("experiment_count", 0),
            "experiment_rate_per_min": round(
                m.get("experiment_count", 0) / max(m.get("experiment_creation_time_sec") or m.get("processing_time_sec") or 1, 0.001) * 60, 1
            ) if m.get("experiment_count") and (m.get("experiment_creation_time_sec") or m.get("processing_time_sec")) else 0,
        })

    # API latency rows — one row per endpoint per iteration count
    api_rows = []
    for r in results:
        if "api" not in r.get("test_name", ""):
            continue
        m = r.get("metrics") or {}
        # API-001/004/005/006 style: metrics.results keyed by endpoint
        for ep, ep_data in (m.get("results") or {}).items():
            lat = ep_data.get("latencies") or {}
            if not lat:
                continue
            ep_short = ep.rstrip("/").split("/")[-1] or ep
            iterations = ep_data.get("iterations", m.get("iterations", 0))
            api_rows.append({
                "run_id":     run_id,
                "test":       r["test_name"].replace("test_perf_", "")[:40],
                "endpoint":   ep_short,
                "iterations": iterations,
                "p50_ms":     round(lat.get("p50", 0) * 1000, 2),
                "p95_ms":     round(lat.get("p95", 0) * 1000, 2),
                "p99_ms":     round(lat.get("p99", 0) * 1000, 2),
                "avg_ms":     round(lat.get("avg", 0) * 1000, 2),
                "success_rate": round(ep_data.get("success_rate", 1.0), 4),
                "passed":     1 if r.get("passed") else 0,
                "profile":    r.get("profile", run_meta["profile"]),
            })
        # API-002 style: latencies dict directly in metrics
        lat = m.get("latencies")
        if isinstance(lat, dict) and "p50" in lat and not m.get("results"):
            api_rows.append({
                "run_id":      run_id,
                "test":        r["test_name"].replace("test_perf_", "")[:40],
                "endpoint":    f'{m.get("concurrent_users","?")}users',
                "iterations":  m.get("total_requests", 0),
                "p50_ms":      round(lat.get("p50", 0) * 1000, 2),
                "p95_ms":      round(lat.get("p95", 0) * 1000, 2),
                "p99_ms":      round(lat.get("p99", 0) * 1000, 2),
                "avg_ms":      round(lat.get("avg", 0) * 1000, 2),
                "success_rate": round(m.get("success_rate", 1.0), 4),
                "passed":      1 if r.get("passed") else 0,
                "profile":     r.get("profile", run_meta["profile"]),
            })

    # Ingestion rows — one row per ingestion test
    # Match test names like test_perf_ing_001, test_perf_ing_002, etc.
    # Avoid false positives like api_006_tag_filtering (contains 'ing' in 'filtering')
    ing_rows = []
    for r in results:
        test_name = r.get("test_name", "")
        if "_ing_" not in test_name and not test_name.startswith("ing_"):
            continue
        m = r.get("metrics") or {}
        timings = {t["name"]: round(t["duration_seconds"], 2) for t in r.get("timings", [])}
        upload = m.get("upload") or {}
        ing_rows.append({
            "run_id":              run_id,
            "test":                r["test_name"].replace("test_perf_", "")[:50],
            "passed":              1 if r.get("passed") else 0,
            "profile":             m.get("profile", r.get("profile", run_meta["profile"])),
            "upload_size_mb":      round(
                upload.get("package_size_mb") or m.get("actual_size_mb", 0), 2
            ),
            "upload_speed_mb_s":   round(
                upload.get("upload_mb_per_second") or m.get("upload_throughput_mb_s", 0), 4
            ),
            "upload_time_s":       round(
                upload.get("upload_seconds") or timings.get("data_generation_and_upload", 0), 1
            ),
            "processing_time_s":   round(
                m.get("processing_time_seconds") or timings.get("processing_wait", 0) or
                timings.get("summary_table_wait", 0), 1
            ),
            "processing_time_min": round(
                (m.get("processing_time_seconds") or timings.get("processing_wait", 0) or
                 timings.get("summary_table_wait", 0)) / 60, 2
            ),
            "listener_cpu_cores":  round(m.get("listener_cpu_cores", 0), 4),
            "concurrent_sources":  m.get("concurrent_sources", 1),
            "within_window":       int(m.get("within_window", -1)),
            "error":               (r.get("error_message") or "")[:120],
            "chart_version":       r.get("chart_version", run_meta["chart_version"]),
        })

    # ROS throughput rows — one row per ROS test
    ros_rows = []
    for r in results:
        test_name = r.get("test_name", "")
        if "_ros_" not in test_name and not test_name.startswith("ros_"):
            continue
        m = r.get("metrics") or {}
        timings = {t["name"]: round(t["duration_seconds"], 2) for t in r.get("timings", [])}
        exp_count = m.get("experiment_count")
        workload_count = m.get("workload_count")
        exp_time = m.get("experiment_creation_time_sec") or m.get("processing_time_sec")
        rate_per_min = round(exp_count / exp_time * 60, 1) if exp_count and exp_time and exp_time > 0 else 0
        secs_per_exp = round(exp_time / exp_count, 2) if exp_count and exp_time and exp_count > 0 else 0
        ros_rows.append({
            "run_id":              run_id,
            "test":                r["test_name"].replace("test_perf_", "")[:50],
            "passed":              1 if r.get("passed") else 0,
            "profile":             r.get("profile", run_meta["profile"]),
            "workload_count":      workload_count or 0,
            "experiment_count":    exp_count or 0,
            "experiment_time_s":   round(exp_time, 1) if exp_time else 0,
            "rate_per_min":        rate_per_min,
            "seconds_per_exp":     secs_per_exp,
            "peak_memory_mb":      round(m.get("peak_memory_mb", 0), 1),
            "kruize_restarts":     m.get("kruize_restarts", 0),
            "recommendation_count": m.get("recommendation_count", 0),
            "error":               (r.get("error_message") or "")[:120],
            "chart_version":       r.get("chart_version", run_meta["chart_version"]),
        })

    # KPI evaluation (import thresholds from the report generator)
    kpi_violations = 0
    kpi_warnings = 0
    try:
        report_module = _import_report_module()
        if report_module:
            for r in results:
                evals = report_module.evaluate_kpis(r)
                for e in evals:
                    if e["status"] == "red":
                        kpi_violations += 1
                    elif e["status"] == "yellow":
                        kpi_warnings += 1
                    test_name = r["test_name"]
                    for row in test_rows:
                        if row["test_name"] == test_name and "kpi_status" not in row:
                            worst = "green"
                            for ev in evals:
                                if ev["status"] == "red":
                                    worst = "red"
                                    break
                                if ev["status"] == "yellow":
                                    worst = "yellow"
                            row["kpi_status"] = worst
                            break
    except Exception:
        pass

    run_meta["kpi_violations"] = kpi_violations
    run_meta["kpi_warnings"] = kpi_warnings

    # Load resource metrics from collected snapshots
    resources = load_metrics_snapshots(run_dir)

    return {
        "run":       run_meta,
        "tests":     test_rows,
        "api":       api_rows,
        "ingestion": ing_rows,
        "ros":       ros_rows,
        "resources": resources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

def _get_boto3_client(endpoint: str, key: str, secret: str):
    """Build an IPv4-safe boto3 S3 client."""
    import socket as _socket
    _orig = _socket.getaddrinfo
    _socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == _socket.AF_INET] or _orig(*a, **kw)

    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config as BotoConfig
    except ImportError:
        return None

    no_sign = os.environ.get("S3_NO_SIGN_REQUEST", "true").lower() == "true"
    cfg = BotoConfig(
        signature_version=UNSIGNED if (no_sign and not key) else None,
        s3={"addressing_style": "path"},
        connect_timeout=10,
        read_timeout=30,
        retries={"max_attempts": 2},
    )
    no_ssl = os.environ.get("S3_NO_VERIFY_SSL", "true").lower() == "true"
    kwargs = {"config": cfg, "verify": not no_ssl}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if key and secret:
        kwargs["aws_access_key_id"] = key
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("s3", **kwargs)


def update_s3_index(run_dir: Path, summary: dict,
                    s3_endpoint: str, s3_bucket: str, s3_prefix: str,
                    aws_key: str, aws_secret: str) -> bool:
    """
    Download the current index.json from the bucket, append/update this run's
    entry, and re-upload.  Returns True on success.
    """
    index_key  = f"{s3_prefix.rstrip('/')}/index.json"
    resources = summary.get("resources", {})
    run_entry  = {
        "run_id":        summary["run"]["run_id"],
        "start_time":    resources.get("time_start", summary["run"]["timestamp"]),
        "end_time":      resources.get("time_end", ""),
        "chart_version": summary["run"]["chart_version"],
        "profile":       summary["run"]["profile"],
        "passed":        summary["run"]["passed"],
        "failed":        summary["run"]["failed"],
        "total_tests":   summary["run"]["total_tests"],
        "duration_min":  summary["run"]["duration_min"],
        "summary_path":  f"{s3_prefix.rstrip('/')}/{summary['run']['run_id']}/results/perf-summary.json",
    }

    client = _get_boto3_client(s3_endpoint, aws_key, aws_secret)
    if client is None:
        print("[WARN] boto3 not available, cannot update index.json")
        return False

    # Try to load existing index
    index: dict = {"runs": [], "updated_at": ""}
    try:
        resp = client.get_object(Bucket=s3_bucket, Key=index_key)
        index = json.loads(resp["Body"].read())
    except Exception:
        pass  # New index or doesn't exist yet

    # Upsert this run
    runs = [x for x in index.get("runs", []) if x.get("run_id") != run_entry["run_id"]]
    runs.insert(0, run_entry)
    index = {"runs": runs, "updated_at": datetime.now(timezone.utc).isoformat()}

    # Upload
    try:
        client.put_object(
            Bucket=s3_bucket,
            Key=index_key,
            Body=json.dumps(index, indent=2).encode(),
            ContentType="application/json",
        )
        print(f"[OK] Index updated: s3://{s3_bucket}/{index_key}")
        return True
    except Exception as e:
        print(f"[WARN] Could not update index: {e}")
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate perf-summary.json for a run")
    parser.add_argument("--run-dir",      required=True, help="Path to perf run directory")
    parser.add_argument("--update-index", action="store_true",
                        help="Also update the bucket-level index.json")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"[ERROR] Run directory not found: {run_dir}", file=sys.stderr)
        raise SystemExit(1)

    summary = build_summary(run_dir)
    out = run_dir / "results" / "perf-summary.json"
    out.write_text(json.dumps(summary, indent=2))

    r = summary["run"]
    print(f"[OK] {out}")
    print(f"     {r['total_tests']} tests · {r['passed']}/{r['total_tests']} passed · {r['duration_min']} min")
    print(f"     {len(summary['api'])} API rows · {len(summary['ingestion'])} ingestion rows · {len(summary['ros'])} ROS rows")

    if args.update_index:
        s3_endpoint = os.environ.get("S3_ENDPOINT", "")
        s3_bucket   = os.environ.get("S3_BUCKET", "")
        s3_prefix   = os.environ.get("S3_PREFIX", "cost-onprem-performance")
        aws_key     = os.environ.get("AWS_ACCESS_KEY_ID", "")
        aws_secret  = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        if s3_bucket:
            update_s3_index(run_dir, summary, s3_endpoint, s3_bucket, s3_prefix,
                            aws_key, aws_secret)
        else:
            print("[WARN] S3_BUCKET not set — skipping index update")


if __name__ == "__main__":
    main()
