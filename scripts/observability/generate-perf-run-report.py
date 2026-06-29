#!/usr/bin/env python3
"""
Performance Run Visual Report Generator
FLPATH-4061 / FLPATH-4036

Generates a self-contained HTML snapshot of a single perf run including:
- KPI summary cards
- Test pass/fail timeline
- API latency charts (p50/p95/p99 per endpoint)
- Ingestion throughput and processing time charts
- Concurrent upload scaling chart
- Prometheus metrics time-series (if metrics snapshots are available)

Usage:
    python3 scripts/observability/generate-perf-run-report.py --run-dir tests/perf-runs/<run-id>
    python3 scripts/observability/generate-perf-run-report.py --run-dir tests/perf-runs/<run-id> --output report.html

The report is fully self-contained (Chart.js loaded from CDN, with inline fallback data).
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from run_utils import load_metadata, load_session, parse_junit


# ---------------------------------------------------------------------------
# KPI thresholds — green / yellow / red
#
# Each entry maps a test-name pattern to a list of KPI checks.  Every check
# has a ``metric`` (dot-path into the result's metrics dict), a comparison
# direction, and two boundaries.  The colours work as follows:
#
#   green  : value OP green_threshold
#   yellow : value OP yellow_threshold  (but not green)
#   red    : otherwise
#
# ``op`` is one of "<" (lower is better, e.g. latency) or ">" (higher is
# better, e.g. throughput).  For boolean metrics use "==" with 1/0.
# ---------------------------------------------------------------------------

KPI_THRESHOLDS: dict[str, list[dict]] = {
    # --- API Latency (P95 targets in seconds) ---
    "api_001": [
        {"label": "P95 latency",   "metric": "aggregate_p95",          "op": "<", "green": 2.0,  "yellow": 5.0,  "unit": "s"},
        {"label": "Success rate",   "metric": "aggregate_success_rate", "op": ">", "green": 0.95, "yellow": 0.80, "unit": "%"},
    ],
    "api_002": [
        {"label": "P95 latency",   "metric": "latencies.p95",  "op": "<", "green": 2.0,  "yellow": 5.0,  "unit": "s"},
        {"label": "Success rate",   "metric": "success_rate",   "op": ">", "green": 0.95, "yellow": 0.80, "unit": "%"},
    ],
    "api_003": [
        {"label": "Read P95",      "metric": "read_latencies.p95",   "op": "<", "green": 2.0,  "yellow": 5.0,  "unit": "s"},
        {"label": "Create P95",    "metric": "create_latencies.p95", "op": "<", "green": 3.0,  "yellow": 6.0,  "unit": "s"},
    ],
    "api_004": [
        {"label": "P95 latency",   "metric": "latencies.p95",  "op": "<", "green": 3.0,  "yellow": 5.0,  "unit": "s"},
        {"label": "Success rate",   "metric": "success_rate",   "op": ">", "green": 0.95, "yellow": 0.80, "unit": "%"},
    ],
    "api_005": [
        {"label": "P95 latency",   "metric": "latencies.p95",  "op": "<", "green": 10.0,  "yellow": 15.0, "unit": "s"},
        {"label": "Success rate",   "metric": "success_rate",   "op": ">", "green": 0.90, "yellow": 0.70, "unit": "%"},
    ],
    "api_006": [
        {"label": "P95 latency",   "metric": "latencies.p95",  "op": "<", "green": 5.0,  "yellow": 8.0,  "unit": "s"},
        {"label": "Success rate",   "metric": "success_rate",   "op": ">", "green": 0.95, "yellow": 0.80, "unit": "%"},
    ],
    "api_status": [
        {"label": "P95 latency",   "metric": "latencies.p95",  "op": "<", "green": 0.5,  "yellow": 1.0,  "unit": "s"},
    ],
    # --- Ingestion ---
    "ing_001": [
        {"label": "Processing done", "metric": "processing_completed", "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
        {"label": "Upload speed",    "metric": "upload.upload_mb_per_second", "op": ">", "green": 0.5, "yellow": 0.2, "unit": "MB/s"},
    ],
    "ing_002": [
        {"label": "Processing done", "metric": "processing_completed", "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
        {"label": "Throughput",      "metric": "processing_throughput_mb_s", "op": ">", "green": 0.1, "yellow": 0.05, "unit": "MB/s"},
        {"label": "Upload speed",    "metric": "upload.upload_mb_per_second", "op": ">", "green": 0.5, "yellow": 0.2, "unit": "MB/s"},
    ],
    "ing_003": [
        {"label": "All processed", "metric": "processing_completed", "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
    ],
    "ing_004": [
        {"label": "Processing done", "metric": "processing_completed", "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
    ],
    "ing_005": [
        {"label": "Error rate",    "metric": "error_rate",           "op": "<", "green": 0.05, "yellow": 0.10, "unit": "%"},
    ],
    "ing_006": [
        {"label": "Within 6h window", "metric": "within_window", "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
    ],
    # --- ROS ---
    "ros_001": [
        {"label": "Experiments",      "metric": "experiment_count",        "op": ">=", "green": 1, "yellow": 1, "unit": ""},
        {"label": "E2E time",         "metric": "total_e2e_time_sec",     "op": "<", "green": 120, "yellow": 300, "unit": "s"},
    ],
    "ros_002": [
        {"label": "Experiments (90%)", "metric": "experiment_count",       "op": ">", "green": 45, "yellow": 25, "unit": "",
         "profile_thresholds": {"baseline": {"green": 2, "yellow": 1}, "small": {"green": 45, "yellow": 25}, "medium": {"green": 180, "yellow": 100}, "large": {"green": 900, "yellow": 500}}},
        {"label": "Throughput", "metric": "experiment_creation_rate_per_min", "op": ">", "green": 15, "yellow": 10, "unit": "exp/min"},
    ],
    "ros_003": [
        {"label": "Refresh done",     "metric": "refresh_complete",        "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
        {"label": "Speedup ratio",    "metric": "speedup_ratio",          "op": ">=", "green": 1.0, "yellow": 0.5, "unit": "x",
         "profile_thresholds": {"baseline": {"green": 0.8, "yellow": 0.5}, "small": {"green": 0.9, "yellow": 0.5}}},
    ],
    "ros_004": [
        {"label": "Experiments (80%)", "metric": "experiment_count",       "op": ">", "green": 80, "yellow": 50, "unit": "",
         "profile_thresholds": {"baseline": {"green": 2, "yellow": 1}, "small": {"green": 40, "yellow": 25}, "medium": {"green": 160, "yellow": 100}, "large": {"green": 800, "yellow": 500}}},
        {"label": "Throughput", "metric": "experiment_creation_rate_per_min", "op": ">", "green": 15, "yellow": 10, "unit": "exp/min"},
    ],
    # --- Scale ---
    "scale_001": [
        {"label": "Sources created",  "metric": "sources_created",        "op": ">=", "green": 5, "yellow": 3, "unit": ""},
    ],
    "scale_002": [
        {"label": "API P95 at ramp",  "metric": "final_p95_latency",     "op": "<", "green": 2.0, "yellow": 5.0, "unit": "s"},
    ],
    "scale_003": [
        {"label": "All queries OK",   "metric": "all_queries_passed",    "op": "==", "green": 1, "yellow": 1, "unit": "bool"},
    ],
    "scale_004": [
        {"label": "Success rate",     "metric": "success_rate",           "op": ">", "green": 0.95, "yellow": 0.90, "unit": "%"},
    ],
    "scale_005": [
        {"label": "P95 latency",      "metric": "latencies.p95",         "op": "<", "green": 2.0, "yellow": 3.0, "unit": "s"},
    ],
    # --- Soak ---
    "soak_001": [
        {"label": "Zero restarts",    "metric": "pod_restart_count",     "op": "==", "green": 0, "yellow": 0, "unit": ""},
        {"label": "Upload failures",  "metric": "uploads_failed",        "op": "==", "green": 0, "yellow": 0, "unit": ""},
    ],
    "soak_002": [
        {"label": "No leak detected", "metric": "leak_detected",         "op": "==", "green": 0, "yellow": 0, "unit": "bool"},
    ],
    "soak_003": [
        {"label": "No warnings",      "metric": "warning_count",         "op": "==", "green": 0, "yellow": 0, "unit": ""},
    ],
    "soak_004": [
        {"label": "No concerns",      "metric": "concern_count",         "op": "==", "green": 0, "yellow": 0, "unit": ""},
    ],
}


def _resolve_metric(metrics: dict, path: str):
    """Resolve a dot-separated metric path, e.g. 'upload.upload_mb_per_second'."""
    obj = metrics
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def evaluate_kpis(result: dict, profile: str = "baseline") -> list[dict]:
    """Evaluate KPI thresholds for a single test result.

    Returns a list of dicts: {label, value, unit, status, green, yellow}.
    status is one of 'green', 'yellow', 'red'.
    
    If a check has 'profile_thresholds', uses profile-specific thresholds.
    """
    test_name = result.get("test_name", "")
    metrics = result.get("metrics") or {}
    evaluations = []

    for pattern, checks in KPI_THRESHOLDS.items():
        if pattern not in test_name:
            continue
        for check in checks:
            val = _resolve_metric(metrics, check["metric"])
            if val is None:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue

            op = check["op"]
            # Use profile-specific thresholds if available
            profile_th = check.get("profile_thresholds", {}).get(profile)
            g = profile_th["green"] if profile_th else check["green"]
            y = profile_th["yellow"] if profile_th else check["yellow"]

            if op == "<":
                status = "green" if val < g else ("yellow" if val < y else "red")
            elif op == "<=":
                status = "green" if val <= g else ("yellow" if val <= y else "red")
            elif op == ">":
                status = "green" if val > g else ("yellow" if val > y else "red")
            elif op == ">=":
                status = "green" if val >= g else ("yellow" if val >= y else "red")
            elif op == "==":
                status = "green" if val == g else "red"
            else:
                status = "green"

            evaluations.append({
                "label": check["label"],
                "metric": check["metric"],
                "op": op,
                "value": val,
                "unit": check["unit"],
                "status": status,
                "green": g,
                "yellow": y,
            })
    return evaluations


def aggregate_kpi_status(all_evaluations: list[dict]) -> str:
    """Return worst status across all evaluations."""
    statuses = {e["status"] for e in all_evaluations}
    if "red" in statuses:
        return "red"
    if "yellow" in statuses:
        return "yellow"
    return "green"


def _kpi_status_icon(status: str, kpi: dict | None = None) -> str:
    """Generate a KPI status dot with optional hover tooltip."""
    icons = {"green": "&#9679;", "yellow": "&#9679;", "red": "&#9679;"}
    
    tooltip = ""
    if kpi:
        label = kpi.get("label", "")
        val = kpi.get("value")
        val_str = f'{val:.3f}' if isinstance(val, float) else str(val)
        unit = kpi.get("unit", "")
        op = kpi.get("op", "<")
        green = kpi.get("green", "?")
        op_txt = {"<": "<", ">": ">", "==": "=", ">=": "≥", "<=": "≤"}.get(op, op)
        tooltip = f'{label}: {val_str}{unit} (need {op_txt}{green} for green)'
        tooltip = tooltip.replace('"', '&quot;')
    
    title_attr = f'title="{tooltip}"' if tooltip else ""
    return f'<span {title_attr} style="color:var(--kpi-{status});font-size:16px;vertical-align:middle;cursor:help;">{icons.get(status, "")}</span>'


def _build_resource_summary_html(rs: dict) -> str:
    """Build the resource summary HTML section."""
    if not rs or not rs.get("snapshot_count"):
        return ""
    
    def fmt(v, suffix=""):
        return f"{v}{suffix}" if v is not None else "—"
    
    cards = []
    # Listener CPU
    cpu = rs.get("listener_cpu", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Listener CPU (cores)</div>
          <div class="values">
            <span><div class="v">{fmt(cpu.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(cpu.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    # Celery CPU
    celery = rs.get("celery_cpu", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Celery Worker CPU (cores)</div>
          <div class="values">
            <span><div class="v">{fmt(celery.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(celery.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    # Memory
    mem = rs.get("memory_mb", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Process Memory (MB)</div>
          <div class="values">
            <span><div class="v">{fmt(mem.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(mem.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    # Valkey
    valkey = rs.get("valkey_mb", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Valkey Memory (MB)</div>
          <div class="values">
            <span><div class="v">{fmt(valkey.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(valkey.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    # Postgres CPU
    pg_cpu = rs.get("postgres_cpu", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Postgres CPU (cores)</div>
          <div class="values">
            <span><div class="v">{fmt(pg_cpu.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(pg_cpu.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    # Postgres Memory
    pg = rs.get("postgres_mb", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Postgres Memory (MB)</div>
          <div class="values">
            <span><div class="v">{fmt(pg.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(pg.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    # Celery Tasks Active
    tasks_active = rs.get("celery_tasks_active", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Celery Active Tasks</div>
          <div class="values">
            <span><div class="v">{fmt(tasks_active.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(tasks_active.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')

    # Celery Task Rate
    task_rate = rs.get("celery_task_rate", {})
    cards.append(f'''
        <div class="resource-card">
          <div class="label">Celery Task Rate (tasks/s)</div>
          <div class="values">
            <span><div class="v">{fmt(task_rate.get("max"))}</div><div class="l">Max</div></span>
            <span><div class="v">{fmt(task_rate.get("avg"))}</div><div class="l">Avg</div></span>
          </div>
        </div>''')
    
    time_range = ""
    if rs.get("time_start") and rs.get("time_end"):
        time_range = f'<div style="font-size:11px;color:var(--muted);margin-top:8px;">Collection window: {rs["time_start"]} → {rs["time_end"]}</div>'
    
    return f'''
    <details open>
      <summary>Resource Metrics Summary ({rs["snapshot_count"]} snapshots)</summary>
      <div class="section-content">
        <div class="resource-grid">{"".join(cards)}</div>
        {time_range}
      </div>
    </details>'''


def _build_throughput_summary_html(
    ros_throughput: list[dict],
    ing_throughput: list[dict],
    proc_throughput: list[dict],
    api_latency: dict,
    concurrent: list[dict],
) -> str:
    """Build the Service Throughput summary section with tables and KPI context."""
    sections = []

    # --- Kruize / ROS Throughput ---
    if ros_throughput:
        rows = []
        for r in ros_throughput:
            status_cls = "pass" if r["passed"] else "fail"
            rows.append(
                f'<tr>'
                f'<td>{r["label"]}</td>'
                f'<td>{r["workloads"]}</td>'
                f'<td>{r["experiments"]}</td>'
                f'<td>{r["time_sec"]}s</td>'
                f'<td><strong>{r["rate_per_min"]}</strong></td>'
                f'<td>{r["per_exp_sec"]}s</td>'
                f'<td><span class="badge {status_cls}">{"PASS" if r["passed"] else "FAIL"}</span></td>'
                f'</tr>'
            )
        sections.append(
            '<h4 style="margin:16px 0 8px;">Kruize Experiment Creation</h4>'
            '<table class="results-table">'
            '<thead><tr><th>Test</th><th>Workloads</th><th>Experiments</th>'
            '<th>Time</th><th>Rate (exp/min)</th><th>Per Experiment</th><th>Status</th></tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody></table>'
        )

    # --- Ingestion Upload Throughput ---
    if ing_throughput:
        rows = []
        for r in ing_throughput:
            status_cls = "pass" if r["passed"] else "fail"
            rows.append(
                f'<tr>'
                f'<td>{r["label"]}</td>'
                f'<td>{r["size_mb"]} MB</td>'
                f'<td><strong>{r["throughput"]}</strong></td>'
                f'<td>{r["upload_s"]}s</td>'
                f'<td>{r["processing_s"]}{"s" if r["processing_s"] < 120 else " min"}</td>'
                f'<td><span class="badge {status_cls}">{"PASS" if r["passed"] else "FAIL"}</span></td>'
                f'</tr>'
            )
        sections.append(
            '<h4 style="margin:16px 0 8px;">Ingestion Upload & Processing</h4>'
            '<table class="results-table">'
            '<thead><tr><th>Test</th><th>Size</th><th>Upload (MB/s)</th>'
            '<th>Upload Time</th><th>Processing</th><th>Status</th></tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody></table>'
        )

    # --- Processing Rate ---
    if proc_throughput:
        rows = []
        for r in proc_throughput:
            status_cls = "pass" if r["passed"] else "fail"
            rows.append(
                f'<tr>'
                f'<td>{r["label"]}</td>'
                f'<td>{r["size_mb"]} MB</td>'
                f'<td>{r["processing_sec"]}s</td>'
                f'<td><strong>{r["processing_rate_mb_min"]}</strong></td>'
                f'<td><span class="badge {status_cls}">{"PASS" if r["passed"] else "FAIL"}</span></td>'
                f'</tr>'
            )
        sections.append(
            '<h4 style="margin:16px 0 8px;">End-to-End Processing Rate</h4>'
            '<table class="results-table">'
            '<thead><tr><th>Test</th><th>Size</th><th>Processing Time</th>'
            '<th>Rate (MB/min)</th><th>Status</th></tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody></table>'
        )

    # --- API Request Rate ---
    if api_latency:
        rows = []
        for label, v in api_latency.items():
            status_cls = "pass" if v["passed"] else "fail"
            rows.append(
                f'<tr>'
                f'<td>{label}</td>'
                f'<td>{v["p50"]}ms</td>'
                f'<td>{v["p95"]}ms</td>'
                f'<td>{v["p99"]}ms</td>'
                f'<td><span class="badge {status_cls}">{"PASS" if v["passed"] else "FAIL"}</span></td>'
                f'</tr>'
            )
        sections.append(
            '<h4 style="margin:16px 0 8px;">API Response Latency</h4>'
            '<table class="results-table">'
            '<thead><tr><th>Endpoint</th><th>P50</th><th>P95</th><th>P99</th><th>Status</th></tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody></table>'
        )

    # --- Concurrent Upload Scaling ---
    if concurrent:
        rows = []
        for r in concurrent:
            status_cls = "pass" if r["passed"] else "fail"
            rows.append(
                f'<tr>'
                f'<td>{r["concurrent"]}</td>'
                f'<td><strong>{r["throughput"]}</strong></td>'
                f'<td>{r["total_mb"]} MB</td>'
                f'<td>{r["upload_s"]}s</td>'
                f'<td>{r["processing_s"]}s</td>'
                f'<td><span class="badge {status_cls}">{"PASS" if r["passed"] else "FAIL"}</span></td>'
                f'</tr>'
            )
        sections.append(
            '<h4 style="margin:16px 0 8px;">Concurrent Upload Scaling</h4>'
            '<table class="results-table">'
            '<thead><tr><th>Sources</th><th>Throughput (MB/s)</th><th>Total Size</th>'
            '<th>Upload Time</th><th>Processing</th><th>Status</th></tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody></table>'
        )

    if not sections:
        return ""

    return (
        '<details open>'
        '<summary>Service Throughput</summary>'
        '<div class="section-content">'
        + "\n".join(sections) +
        '</div></details>'
    )


def _make_row_id(name: str) -> str:
    """Generate a consistent row ID from a test name."""
    return name.replace("[", "_").replace("]", "_").replace(" ", "_").replace("-", "_")


def _build_kpi_scorecard(all_evals: list[dict], per_test: dict[str, list[dict]]) -> str:
    """Build the KPI scorecard HTML table with links to test results."""
    rows = []
    for test_name, evals in per_test.items():
        short = test_name.replace("test_perf_", "").replace("_baseline", "")
        row_id = _make_row_id(short)
        for e in evals:
            fmt_val = f'{e["value"]:.3f}' if isinstance(e["value"], float) else str(e["value"])
            if e.get("unit") == "bool":
                fmt_thresh = "pass/fail"
            else:
                op = e.get("op", "<")
                op_html = {"<": "&lt;", "<=": "≤", ">": "&gt;", ">=": "≥", "==": "="}.get(op, op)
                fmt_thresh = f'G{op_html}{e["green"]} Y{op_html}{e["yellow"]}'
            rows.append(
                f'<tr>'
                f'<td><a href="#test-{row_id}" style="color:inherit;text-decoration:none;border-bottom:1px dotted var(--muted);">{short}</a></td>'
                f'<td>{e["label"]}</td>'
                f'<td>{fmt_val} {e["unit"]}</td>'
                f'<td>{fmt_thresh}</td>'
                f'<td>{_kpi_status_icon(e["status"], e)}</td>'
                f'</tr>'
            )
    return (
        '<table class="results-table" style="margin-bottom:20px;">'
        '<thead><tr><th>Test</th><th>KPI</th><th>Value</th><th>Thresholds</th><th>Status</th></tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody></table>'
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_grafana_links(run_dir: Path, skip: bool = False) -> dict:
    """Load grafana-links.json if present (written by push-grafana-snapshot.py).
    
    Args:
        run_dir: Path to run directory
        skip: If True, return empty dict (skip Grafana links)
    """
    if skip:
        return {}
    for candidate in [run_dir / "reports" / "grafana-links.json",
                      run_dir / "grafana-links.json"]:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text())
            except Exception:
                pass
    return {}


def load_metrics_snapshots(run_dir: Path) -> list[dict]:
    """Load Prometheus metric snapshots from metrics/ directory."""
    snapshots = []
    metrics_dir = run_dir / "metrics"
    if not metrics_dir.exists():
        return snapshots
    for sf in sorted(metrics_dir.glob("snapshot_*.json")):
        try:
            snapshots.append(json.loads(sf.read_text()))
        except Exception:
            pass
    return snapshots



# ---------------------------------------------------------------------------
# Chart data extraction
# ---------------------------------------------------------------------------

def extract_api_latency(results: list[dict]) -> dict:
    """Extract per-endpoint p50/p95/p99 latency data from API tests."""
    endpoints: dict = {}
    for r in results:
        if "api" not in r.get("test_name", ""):
            continue
        m = r.get("metrics") or {}
        # API-001 and similar: metrics.results is a dict keyed by endpoint path
        for ep, ep_data in (m.get("results") or {}).items():
            lat = ep_data.get("latencies") or {}
            if not lat:
                continue
            short_ep = ep.rstrip("/").split("/")[-1] or ep
            label = f'{short_ep} ({r["test_name"].split("[")[-1].rstrip("]")} iter)'
            endpoints[label] = {
                "p50": round(lat.get("p50", 0) * 1000, 1),
                "p95": round(lat.get("p95", 0) * 1000, 1),
                "p99": round(lat.get("p99", 0) * 1000, 1),
                "passed": r.get("passed", True),
            }
        # API-002: latencies dict directly in metrics
        if "latencies" in m and isinstance(m["latencies"], dict):
            lat = m["latencies"]
            users = m.get("concurrent_users", "?")
            label = f'{r["test_name"].replace("test_perf_","").split("[")[0]} {users}u'
            endpoints[label] = {
                "p50": round(lat.get("p50", 0) * 1000, 1),
                "p95": round(lat.get("p95", 0) * 1000, 1),
                "p99": round(lat.get("p99", 0) * 1000, 1),
                "passed": r.get("passed", True),
            }
    return endpoints


def extract_ingestion_throughput(results: list[dict]) -> list[dict]:
    """Extract upload throughput and processing time for ingestion tests."""
    rows = []
    for r in results:
        name = r.get("test_name", "")
        if "ing" not in name:
            continue
        m = r.get("metrics") or {}
        timings = {t["name"]: round(t["duration_seconds"], 1) for t in r.get("timings", [])}

        # ING-001 style
        upload = m.get("upload") or {}
        if upload.get("upload_mb_per_second"):
            rows.append({
                "label": name.replace("test_perf_", "").replace("_baseline", "")[:35],
                "throughput": round(upload["upload_mb_per_second"], 3),
                "size_mb": round(upload.get("package_size_mb", 0), 1),
                "upload_s": round(upload.get("upload_seconds", 0), 1),
                "processing_s": round(timings.get("summary_table_wait", timings.get("processing_wait", 0)), 1),
                "passed": r.get("passed", True),
            })
        # ING-004 style
        if "upload_throughput_mb_s" in m:
            rows.append({
                "label": name.replace("test_perf_", "").replace("_baseline", "")[:35],
                "throughput": round(m["upload_throughput_mb_s"], 3),
                "size_mb": round(m.get("actual_size_mb", 0), 1),
                "upload_s": round(m.get("upload_time_seconds", 0), 1),
                "processing_s": round(m.get("processing_time_seconds", 0) / 60, 2),
                "passed": r.get("passed", True),
            })
        # ING-005 (high frequency)
        if "total_uploads" in m and "test_duration_minutes" in m:
            total_mb = m.get("total_data_mb", 0)
            duration_s = timings.get("high_frequency_test", m["test_duration_minutes"] * 60)
            rows.append({
                "label": name.replace("test_perf_", "").replace("_baseline", "")[:35],
                "throughput": round(total_mb / duration_s, 3) if duration_s > 0 else 0,
                "size_mb": round(total_mb, 1),
                "upload_s": round(duration_s, 1),
                "processing_s": 0,
                "passed": r.get("passed", True),
            })
    return rows


def extract_ros_throughput(results: list[dict]) -> list[dict]:
    """Extract Kruize experiment creation throughput from ROS tests."""
    rows = []
    for r in results:
        name = r.get("test_name", "")
        if "ros" not in name:
            continue
        m = r.get("metrics") or {}
        exp_count = m.get("experiment_count")
        workload_count = m.get("workload_count")
        exp_time = m.get("experiment_creation_time_sec") or m.get("processing_time_sec")
        if exp_count is not None and exp_time and exp_time > 0:
            rate = exp_count / exp_time * 60
            rows.append({
                "label": name.replace("test_perf_", "").replace("_baseline", "")[:30],
                "experiments": exp_count,
                "workloads": workload_count or 0,
                "time_sec": round(exp_time, 1),
                "rate_per_min": round(rate, 1),
                "per_exp_sec": round(exp_time / exp_count, 1) if exp_count > 0 else 0,
                "passed": r.get("passed", True),
            })
    return rows


def extract_processing_throughput(results: list[dict]) -> list[dict]:
    """Extract end-to-end processing throughput for ingestion tests."""
    rows = []
    for r in results:
        name = r.get("test_name", "")
        if "ing" not in name:
            continue
        m = r.get("metrics") or {}
        timings = {t["name"]: t["duration_seconds"] for t in r.get("timings", [])}
        upload = m.get("upload") or {}
        size_mb = upload.get("package_size_mb") or m.get("actual_size_mb") or m.get("total_upload_mb", 0)
        proc_time = timings.get("processing_wait", timings.get("processing_wait_all", timings.get("summary_table_wait", 0)))
        if proc_time and proc_time > 0 and size_mb > 0:
            rows.append({
                "label": name.replace("test_perf_", "").replace("_baseline", "")[:35],
                "size_mb": round(size_mb, 1),
                "processing_sec": round(proc_time, 1),
                "processing_rate_mb_min": round(size_mb / proc_time * 60, 2),
                "passed": r.get("passed", True),
            })
    return rows


def extract_concurrent_scaling(results: list[dict]) -> list[dict]:
    """ING-003: throughput vs concurrent sources."""
    rows = []
    for r in results:
        if "ing_003" not in r.get("test_name", ""):
            continue
        m = r.get("metrics") or {}
        timings = {t["name"]: t["duration_seconds"] for t in r.get("timings", [])}
        up_s = timings.get("concurrent_uploads", 1)
        total_mb = m.get("total_upload_mb", 0)
        rows.append({
            "concurrent": m.get("concurrent_sources", 0),
            "throughput": round(total_mb / up_s, 3) if up_s > 0 else 0,
            "upload_s": round(up_s, 2),
            "total_mb": round(total_mb, 2),
            "processing_s": round(timings.get("processing_wait_all", 0), 1),
            "passed": r.get("passed", True),
        })
    return sorted(rows, key=lambda x: x["concurrent"])


def extract_test_timeline(results: list[dict]) -> list[dict]:
    """All tests with duration and pass/fail for the timeline bar chart."""
    rows = []
    for r in results:
        total_s = sum(t["duration_seconds"] for t in r.get("timings", []))
        if total_s == 0:
            total_s = 1
        rows.append({
            "name": r["test_name"].replace("test_perf_", "").replace("_baseline", "")[:45],
            "duration_s": round(total_s, 1),
            "passed": r.get("passed", True),
            "error": (r.get("error_message") or "")[:80],
        })
    return rows


def extract_test_windows(results: list[dict]) -> list[dict]:
    """Extract test execution windows (start/end times) for resource correlation."""
    from datetime import datetime
    windows = []
    for r in results:
        timings = r.get("timings", [])
        if not timings:
            continue
        # Get earliest start and latest end from all timing phases
        starts = [t.get("start_time") for t in timings if t.get("start_time")]
        ends = [t.get("end_time") for t in timings if t.get("end_time")]
        if not starts or not ends:
            continue
        windows.append({
            "name": r["test_name"].replace("test_perf_", "").replace("_baseline", "")[:25],
            "start": min(starts),
            "end": max(ends),
            "passed": r.get("passed", True),
        })
    return windows


def find_active_test(timestamp: str, test_windows: list[dict]) -> str | None:
    """Find which test was running at a given timestamp."""
    from datetime import datetime
    try:
        # Parse the metrics timestamp (format: "2026-05-31T22:07:38Z" or similar)
        ts = timestamp.replace("Z", "+00:00")
        if "+" not in ts and len(ts) == 19:
            ts += "+00:00"
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    
    for w in test_windows:
        try:
            start = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(w["end"].replace("Z", "+00:00"))
            if start <= dt <= end:
                return w["name"]
        except (ValueError, TypeError):
            continue
    return None


def extract_prometheus_series(snapshots: list[dict], test_windows: list[dict] | None = None) -> dict:
    """Extract time-series data from Prometheus metric snapshots.
    
    If test_windows is provided, includes which test was active at each timestamp.
    """
    series: dict = {
        "timestamps": [],
        "active_tests": [],  # Which test was running at each timestamp
        "listener_cpu": [],
        "celery_cpu": [],
        "postgres_cpu": [],
        "celery_tasks_active": [],
        "celery_task_rate": [],
        "db_connections": [],
        "memory_mb": [],
        "valkey_mb": [],
        "postgres_mb": [],
    }
    for snap in snapshots:
        ts = snap.get("timestamp", "")
        series["timestamps"].append(ts[:19].replace("T", " "))
        
        # Find which test was running at this timestamp
        active = find_active_test(ts, test_windows) if test_windows else None
        series["active_tests"].append(active)
        
        m = snap.get("metrics", {})

        def _scalar(val):
            """Prometheus can return lists; extract first numeric element."""
            if isinstance(val, list):
                val = val[0] if val else None
            return val if isinstance(val, (int, float)) else None

        # CPU - check multiple possible field names (keep 4 decimal places for millicores)
        cpu = _scalar(snap.get("listener_cpu_cores") or
                      m.get("listener_cpu_cores") or
                      m.get("pod_cpu_usage"))
        series["listener_cpu"].append(round(cpu, 4) if cpu is not None else None)

        # Celery CPU (keep 4 decimal places for millicores)
        celery_cpu = _scalar(snap.get("celery_worker_cpu_cores") or 
                             m.get("celery_worker_cpu_cores"))
        series["celery_cpu"].append(round(celery_cpu, 4) if celery_cpu is not None else None)

        # Postgres CPU (keep 4 decimal places for millicores)
        pg_cpu = _scalar(snap.get("postgres_cpu_cores") or 
                         m.get("postgres_cpu_cores"))
        series["postgres_cpu"].append(round(pg_cpu, 4) if pg_cpu is not None else None)

        # Celery task activity from celery-exporter
        tasks_active = m.get("celery_tasks_active")
        if tasks_active is None:
            # Legacy field name fallback
            tasks_active = m.get("celery_queue_depth_total")
        if isinstance(tasks_active, list):
            tasks_active = sum(tasks_active) if tasks_active else None
        series["celery_tasks_active"].append(tasks_active)

        task_rate = m.get("celery_task_rate")
        if isinstance(task_rate, list):
            task_rate = task_rate[0] if task_rate else None
        series["celery_task_rate"].append(round(float(task_rate), 3) if task_rate is not None else None)

        # DB connections
        db_conn = (snap.get("db_connections") or 
                   m.get("db_connections") or 
                   m.get("pg_connections_active"))
        if isinstance(db_conn, list):
            db_conn = db_conn[0] if db_conn else None
        series["db_connections"].append(db_conn)

        # Memory - convert bytes to MB if needed
        mem = _scalar(snap.get("process_memory_mb") or m.get("process_memory_mb"))
        if mem is None:
            mem_bytes = _scalar(m.get("pod_memory_usage_bytes"))
            if mem_bytes is not None:
                mem = mem_bytes / (1024 * 1024)
        series["memory_mb"].append(round(mem, 1) if mem is not None else None)

        # Valkey memory - convert bytes to MB if needed
        valkey = _scalar(snap.get("valkey_memory_mb") or m.get("valkey_memory_mb"))
        if valkey is None:
            valkey_bytes = _scalar(m.get("valkey_memory_used_bytes"))
            if valkey_bytes is not None:
                valkey = valkey_bytes / (1024 * 1024)
        series["valkey_mb"].append(round(valkey, 1) if valkey is not None else None)

        # Postgres memory
        pg = _scalar(snap.get("postgres_memory_mb") or 
                     m.get("postgres_memory_mb"))
        series["postgres_mb"].append(round(pg, 1) if pg is not None else None)

    return series


def compute_resource_summary(prom_series: dict) -> dict:
    """Compute min/max/avg for resource metrics."""
    def stats(values, decimals=2):
        nums = [v for v in values if v is not None]
        if not nums:
            return {"min": None, "max": None, "avg": None}
        return {
            "min": round(min(nums), decimals),
            "max": round(max(nums), decimals),
            "avg": round(sum(nums) / len(nums), decimals),
        }
    return {
        "listener_cpu": stats(prom_series["listener_cpu"], decimals=4),
        "celery_cpu": stats(prom_series["celery_cpu"], decimals=4),
        "postgres_cpu": stats(prom_series["postgres_cpu"], decimals=4),
        "memory_mb": stats(prom_series["memory_mb"]),
        "valkey_mb": stats(prom_series["valkey_mb"]),
        "postgres_mb": stats(prom_series["postgres_mb"]),
        "celery_tasks_active": stats(prom_series["celery_tasks_active"]),
        "celery_task_rate": stats(prom_series["celery_task_rate"], decimals=3),
        "snapshot_count": len(prom_series["timestamps"]),
        "time_start": prom_series["timestamps"][0] if prom_series["timestamps"] else None,
        "time_end": prom_series["timestamps"][-1] if prom_series["timestamps"] else None,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def js_array(values: list) -> str:
    return json.dumps([v if v is not None else "null" for v in values])


def js_colors(values: list[bool], true_color="#27ae60", false_color="#e74c3c") -> str:
    return json.dumps([true_color if v else false_color for v in values])


def render_html(run_dir: Path, output_path: Path, skip_grafana_links: bool = True) -> None:
    session        = load_session(run_dir)
    metadata       = load_metadata(run_dir)
    junit          = parse_junit(run_dir)
    snapshots      = load_metrics_snapshots(run_dir)
    grafana_links  = load_grafana_links(run_dir, skip=skip_grafana_links)

    if not session:
        print(f"[WARN] No session JSON found in {run_dir}/results/ — report will be sparse")
        results = []
    else:
        results = session.get("results", [])

    # Compute derived throughput metrics for any service that has raw timing data
    for r in results:
        m = r.get("metrics") or {}
        exp_count = m.get("experiment_count")
        exp_time = m.get("experiment_creation_time_sec") or m.get("processing_time_sec")
        if exp_count and exp_time and exp_time > 0:
            m["experiment_creation_rate_per_min"] = round(exp_count / exp_time * 60, 1)
            m["seconds_per_experiment"] = round(exp_time / exp_count, 2) if exp_count > 0 else None

    # Extract all chart data
    api_latency     = extract_api_latency(results)
    ing_throughput  = extract_ingestion_throughput(results)
    concurrent      = extract_concurrent_scaling(results)
    ros_throughput  = extract_ros_throughput(results)
    proc_throughput = extract_processing_throughput(results)
    timeline        = extract_test_timeline(results)
    test_windows    = extract_test_windows(results)
    prom_series     = extract_prometheus_series(snapshots, test_windows)

    # KPIs
    run_id       = run_dir.name
    chart_ver    = metadata.get("chart_version", session.get("results", [{}])[0].get("chart_version", "unknown") if results else "unknown")
    profile      = metadata.get("perf_profile", results[0].get("profile", "unknown") if results else "unknown")
    suite        = metadata.get("perf_suite", "all")
    ts_raw       = metadata.get("created_at", session.get("timestamp", "") if session else "")
    ts_str       = ts_raw[:19].replace("T", " ") + " UTC" if ts_raw else "unknown"
    cluster_info = metadata.get("cluster_info", results[0].get("cluster_info", {}) if results else {})
    # Session results are the source of truth for pass/fail because they
    # capture metric-based failures (e.g. 0% success_rate) that may not
    # trigger a pytest assertion.  JUnit is only used for skipped count and
    # duration since it includes tests the session collector never sees.
    session_passed = sum(1 for r in results if r.get("passed"))
    session_failed = sum(1 for r in results if not r.get("passed"))
    if results:
        passed  = session_passed
        failed  = session_failed
        skipped = junit["skipped"] if junit else 0
        # Total should be tests that actually ran (exclude skipped)
        total   = passed + failed
    elif junit:
        skipped = junit["skipped"]
        # Exclude skipped from total for accurate pass rate
        total   = junit["total"] - skipped
        passed  = junit["passed"]
        failed  = junit["failed"]
    else:
        total = passed = failed = skipped = 0
    dur_min = round((junit["duration_s"] if junit else sum(
        sum(t["duration_seconds"] for t in r.get("timings", [])) for r in results
    )) / 60, 1)

    # Avg upload throughput
    avg_throughput = round(
        sum(r["throughput"] for r in ing_throughput if r["throughput"] > 0) /
        max(len([r for r in ing_throughput if r["throughput"] > 0]), 1), 3
    )

    # Avg Kruize experiment throughput (exp/min)
    avg_ros_rate = round(
        sum(r["rate_per_min"] for r in ros_throughput if r["rate_per_min"] > 0) /
        max(len([r for r in ros_throughput if r["rate_per_min"] > 0]), 1), 1
    ) if ros_throughput else 0

    # KPI evaluation (using profile-aware thresholds)
    all_kpi_evals: list[dict] = []
    per_test_kpis: dict[str, list[dict]] = {}
    for r in results:
        evals = evaluate_kpis(r, profile=profile)
        if evals:
            all_kpi_evals.extend(evals)
            per_test_kpis[r["test_name"]] = evals
    overall_kpi = aggregate_kpi_status(all_kpi_evals) if all_kpi_evals else "green"
    kpi_green  = sum(1 for e in all_kpi_evals if e["status"] == "green")
    kpi_yellow = sum(1 for e in all_kpi_evals if e["status"] == "yellow")
    kpi_red    = sum(1 for e in all_kpi_evals if e["status"] == "red")
    kpi_total  = len(all_kpi_evals)

    has_prom   = len(snapshots) > 0
    has_api    = len(api_latency) > 0
    has_ing    = len(ing_throughput) > 0
    has_conc   = len(concurrent) > 0
    has_ros    = len(ros_throughput) > 0
    has_proc   = len(proc_throughput) > 0

    # Resource summary from Prometheus snapshots
    resource_summary = compute_resource_summary(prom_series) if has_prom else {}

    # Conditional chart card HTML blocks (built before the f-string)
    api_chart_html = (
        '<div class="chart-card wide"><h3>API Response Latency (ms)</h3>'
        '<canvas id="latencyChart"></canvas></div>'
    ) if has_api else ""

    throughput_chart_html = (
        '<div class="chart-card"><h3>Upload Throughput (MB/s)</h3>'
        '<canvas id="throughputChart"></canvas></div>'
    ) if has_ing else ""

    proc_chart_html = (
        '<div class="chart-card"><h3>Processing Time per Ingestion Test (min)</h3>'
        '<canvas id="procChart"></canvas></div>'
    ) if has_ing else ""

    conc_chart_html = (
        '<div class="chart-card"><h3>Concurrent Upload Scaling</h3>'
        '<canvas id="concChart"></canvas></div>'
    ) if has_conc else ""

    ros_chart_html = (
        '<div class="chart-card"><h3>Kruize Experiment Throughput</h3>'
        '<canvas id="rosChart"></canvas></div>'
    ) if has_ros else ""

    proc_chart_html2 = (
        '<div class="chart-card"><h3>Processing Rate (MB/min)</h3>'
        '<canvas id="procRateChart"></canvas></div>'
    ) if has_proc else ""

    cpu_chart_html = (
        '<div class="chart-card wide"><h3>CPU Usage (cores) over time — hover for active test</h3>'
        '<canvas id="cpuChart"></canvas></div>'
    ) if has_prom else ""

    memory_chart_html = (
        '<div class="chart-card"><h3>Memory Usage (MB) over time</h3>'
        '<canvas id="memoryChart"></canvas></div>'
    ) if has_prom else ""

    celery_chart_html = (
        '<div class="chart-card"><h3>Celery Task Activity — hover for active test</h3>'
        '<canvas id="celeryChart"></canvas></div>'
    ) if has_prom else ""

    # Chart.js data blobs
    tl_labels  = js_array([r["name"] for r in timeline])
    tl_durations = js_array([r["duration_s"] for r in timeline])
    tl_colors  = js_colors([r["passed"] for r in timeline])

    api_labels = js_array(list(api_latency.keys()))
    api_p50    = js_array([v["p50"] for v in api_latency.values()])
    api_p95    = js_array([v["p95"] for v in api_latency.values()])
    api_p99    = js_array([v["p99"] for v in api_latency.values()])

    ing_labels  = js_array([r["label"] for r in ing_throughput])
    ing_tput    = js_array([r["throughput"] for r in ing_throughput])
    ing_proc    = js_array([r["processing_s"] for r in ing_throughput])
    ing_colors  = js_colors([r["passed"] for r in ing_throughput])

    conc_labels   = js_array([str(r["concurrent"]) for r in concurrent])
    conc_tput     = js_array([r["throughput"] for r in concurrent])
    conc_proc     = js_array([r["processing_s"] for r in concurrent])

    ros_labels    = js_array([r["label"] for r in ros_throughput])
    ros_rates     = js_array([r["rate_per_min"] for r in ros_throughput])
    ros_exps      = js_array([r["experiments"] for r in ros_throughput])
    ros_colors    = js_colors([r["passed"] for r in ros_throughput])

    proc_rate_labels = js_array([r["label"] for r in proc_throughput])
    proc_rate_values = js_array([r["processing_rate_mb_min"] for r in proc_throughput])
    proc_rate_colors = js_colors([r["passed"] for r in proc_throughput])

    prom_ts    = js_array(prom_series["timestamps"])
    prom_cpu   = js_array(prom_series["listener_cpu"])
    prom_celery_cpu = js_array(prom_series["celery_cpu"])
    prom_pg_cpu = js_array(prom_series["postgres_cpu"])
    prom_active_tests = js_array(prom_series["active_tests"])
    prom_mem   = js_array(prom_series["memory_mb"])
    prom_valkey_mb = js_array(prom_series["valkey_mb"])
    prom_pg_mb = js_array(prom_series["postgres_mb"])
    prom_celery_active = js_array(prom_series["celery_tasks_active"])
    prom_celery_rate = js_array(prom_series["celery_task_rate"])

    # Conditional JS blocks built before the f-string
    js_api_chart = (
        f"new Chart(document.getElementById('latencyChart'), {{ type: 'bar', data: {{ labels: {api_labels},"
        f" datasets: [{{ label: 'p50', data: {api_p50}, backgroundColor: 'rgba(41,128,185,0.7)', borderRadius: 2 }},"
        f" {{ label: 'p95', data: {api_p95}, backgroundColor: 'rgba(230,126,34,0.7)', borderRadius: 2 }},"
        f" {{ label: 'p99', data: {api_p99}, backgroundColor: 'rgba(231,76,60,0.7)', borderRadius: 2 }}] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }},"
        f"   annotation: {{ annotations: {{"
        f"     greenLine: {{ type: 'line', yMin: 2000, yMax: 2000, borderColor: 'rgba(39,174,96,0.8)', borderWidth: 2, borderDash: [6,3],"
        f"       label: {{ display: true, content: 'KPI: Green (2s)', position: 'start', backgroundColor: 'rgba(39,174,96,0.8)', font: {{size:10}} }} }},"
        f"     yellowLine: {{ type: 'line', yMin: 5000, yMax: 5000, borderColor: 'rgba(243,156,18,0.8)', borderWidth: 2, borderDash: [6,3],"
        f"       label: {{ display: true, content: 'KPI: Warn (5s)', position: 'start', backgroundColor: 'rgba(243,156,18,0.8)', font: {{size:10}} }} }}"
        f"   }} }} }},"
        f" scales: {{ x: {{ ticks: {{ font: {{ size: 9 }}, maxRotation: 45 }} }},"
        f" y: {{ grid: {{ color: gridColor }}, title: {{ display: true, text: 'ms' }} }} }} }} }});"
    ) if has_api else ""

    js_throughput_chart = (
        f"new Chart(document.getElementById('throughputChart'), {{ type: 'bar', data: {{ labels: {ing_labels},"
        f" datasets: [{{ label: 'MB/s', data: {ing_tput}, backgroundColor: {ing_colors}, borderRadius: 3 }}] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},"
        f" scales: {{ x: {{ ticks: {{ font: {{ size: 9 }} }} }},"
        f" y: {{ grid: {{ color: gridColor }}, title: {{ display: true, text: 'MB/s' }} }} }} }} }});"
    ) if has_ing else ""

    js_proc_chart = (
        f"new Chart(document.getElementById('procChart'), {{ type: 'bar', data: {{ labels: {ing_labels},"
        f" datasets: [{{ label: 'Processing (min)', data: {ing_proc},"
        f" backgroundColor: 'rgba(142,68,173,0.7)', borderRadius: 3 }}] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},"
        f" scales: {{ x: {{ ticks: {{ font: {{ size: 9 }} }} }},"
        f" y: {{ grid: {{ color: gridColor }}, title: {{ display: true, text: 'minutes' }} }} }} }} }});"
    ) if has_ing else ""

    js_conc_chart = (
        f"new Chart(document.getElementById('concChart'), {{ type: 'bar', data: {{ labels: {conc_labels},"
        f" datasets: [{{ label: 'Throughput (MB/s)', data: {conc_tput},"
        f" backgroundColor: 'rgba(39,174,96,0.7)', yAxisID: 'y', borderRadius: 3 }},"
        f" {{ label: 'Processing (s)', data: {conc_proc},"
        f" backgroundColor: 'rgba(231,76,60,0.5)', yAxisID: 'y1', borderRadius: 3 }}] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }},"
        f" scales: {{ x: {{ title: {{ display: true, text: 'concurrent sources' }} }},"
        f" y: {{ position: 'left', title: {{ display: true, text: 'MB/s' }}, grid: {{ color: gridColor }} }},"
        f" y1: {{ position: 'right', title: {{ display: true, text: 'proc seconds' }},"
        f" grid: {{ drawOnChartArea: false }} }} }} }} }});"
    ) if has_conc else ""

    js_ros_chart = (
        f"new Chart(document.getElementById('rosChart'), {{ type: 'bar', data: {{ labels: {ros_labels},"
        f" datasets: ["
        f" {{ label: 'Rate (exp/min)', data: {ros_rates}, backgroundColor: {ros_colors}, borderRadius: 3, yAxisID: 'y' }},"
        f" {{ label: 'Experiments', data: {ros_exps}, type: 'line',"
        f" borderColor: 'rgba(41,128,185,0.9)', backgroundColor: 'rgba(41,128,185,0.1)',"
        f" tension: 0.3, pointRadius: 4, yAxisID: 'y1' }}"
        f" ] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }},"
        f" scales: {{ x: {{ ticks: {{ font: {{ size: 9 }} }} }},"
        f" y: {{ position: 'left', grid: {{ color: gridColor }}, title: {{ display: true, text: 'exp/min' }} }},"
        f" y1: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'count' }} }} }} }} }});"
    ) if has_ros else ""

    js_proc_rate_chart = (
        f"new Chart(document.getElementById('procRateChart'), {{ type: 'bar', data: {{ labels: {proc_rate_labels},"
        f" datasets: [{{ label: 'MB/min', data: {proc_rate_values}, backgroundColor: {proc_rate_colors}, borderRadius: 3 }}] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},"
        f" scales: {{ x: {{ ticks: {{ font: {{ size: 9 }} }} }},"
        f" y: {{ grid: {{ color: gridColor }}, title: {{ display: true, text: 'MB/min' }} }} }} }} }});"
    ) if has_proc else ""

    # CPU chart with all three series and active test tooltip
    js_cpu_chart = (
        f"const activeTests = {prom_active_tests};"
        f"new Chart(document.getElementById('cpuChart'), {{ type: 'line', data: {{ labels: {prom_ts},"
        f" datasets: ["
        f" {{ label: 'Listener', data: {prom_cpu},"
        f" borderColor: 'rgba(41,128,185,0.9)', backgroundColor: 'rgba(41,128,185,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1 }},"
        f" {{ label: 'Celery Workers', data: {prom_celery_cpu},"
        f" borderColor: 'rgba(230,126,34,0.9)', backgroundColor: 'rgba(230,126,34,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1 }},"
        f" {{ label: 'Postgres', data: {prom_pg_cpu},"
        f" borderColor: 'rgba(39,174,96,0.9)', backgroundColor: 'rgba(39,174,96,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1 }}"
        f" ] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }},"
        f" tooltip: {{ callbacks: {{ afterLabel: function(ctx) {{ const test = activeTests[ctx.dataIndex]; return test ? 'Test: ' + test : ''; }} }} }} }},"
        f" scales: {{ x: {{ ticks: {{ maxRotation: 45, font: {{ size: 9 }} }} }},"
        f" y: {{ min: 0, grid: {{ color: gridColor }}, title: {{ display: true, text: 'cores' }} }} }} }} }});"
    ) if has_prom else ""

    # Memory chart with listener, Valkey, and Postgres series
    js_memory_chart = (
        f"new Chart(document.getElementById('memoryChart'), {{ type: 'line', data: {{ labels: {prom_ts},"
        f" datasets: ["
        f" {{ label: 'Listener', data: {prom_mem},"
        f" borderColor: 'rgba(41,128,185,0.9)', backgroundColor: 'rgba(41,128,185,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1 }},"
        f" {{ label: 'Postgres', data: {prom_pg_mb},"
        f" borderColor: 'rgba(39,174,96,0.9)', backgroundColor: 'rgba(39,174,96,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1 }},"
        f" {{ label: 'Valkey', data: {prom_valkey_mb},"
        f" borderColor: 'rgba(155,89,182,0.9)', backgroundColor: 'rgba(155,89,182,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1 }}"
        f" ] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }},"
        f" tooltip: {{ callbacks: {{ afterLabel: function(ctx) {{ const test = activeTests[ctx.dataIndex]; return test ? 'Test: ' + test : ''; }} }} }} }},"
        f" scales: {{ x: {{ ticks: {{ maxRotation: 45, font: {{ size: 9 }} }} }},"
        f" y: {{ min: 0, grid: {{ color: gridColor }}, title: {{ display: true, text: 'MB' }} }} }} }} }});"
    ) if has_prom else ""

    # Celery task activity chart (active tasks + task rate on dual Y axis)
    js_celery_chart = (
        f"new Chart(document.getElementById('celeryChart'), {{ type: 'line', data: {{ labels: {prom_ts},"
        f" datasets: ["
        f" {{ label: 'Active Tasks', data: {prom_celery_active},"
        f" borderColor: 'rgba(231,76,60,0.9)', backgroundColor: 'rgba(231,76,60,0.1)',"
        f" tension: 0.3, fill: true, pointRadius: 1, yAxisID: 'y' }},"
        f" {{ label: 'Task Rate (tasks/s)', data: {prom_celery_rate},"
        f" borderColor: 'rgba(46,204,113,0.9)', backgroundColor: 'rgba(46,204,113,0.1)',"
        f" tension: 0.3, fill: false, pointRadius: 1, borderDash: [4,2], yAxisID: 'y1' }}"
        f" ] }},"
        f" options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }},"
        f" tooltip: {{ callbacks: {{ afterLabel: function(ctx) {{ const test = activeTests[ctx.dataIndex]; return test ? 'Test: ' + test : ''; }} }} }} }},"
        f" scales: {{ x: {{ ticks: {{ maxRotation: 45, font: {{ size: 9 }} }} }},"
        f" y: {{ min: 0, grid: {{ color: gridColor }}, title: {{ display: true, text: 'active tasks' }}, position: 'left' }},"
        f" y1: {{ min: 0, grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'tasks/s' }}, position: 'right' }} }} }} }});"
    ) if has_prom else ""


    ocp_ver    = cluster_info.get("ocp_version", "?")
    nodes      = cluster_info.get("node_count", "?")
    storage_type = cluster_info.get("storage_type", "?")
    s3_backend = cluster_info.get("s3_backend", "")
    storage = f"{storage_type} + {s3_backend}" if s3_backend and s3_backend != "unknown" else storage_type
    pass_color = "#27ae60" if failed == 0 else "#e74c3c"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Pre-compute style attrs that contain quotes (backslashes in f-string
    # expressions are a SyntaxError on Python < 3.12).
    fail_border_style = 'style="border-color:var(--fail)"' if failed > 0 else ""
    fail_color_style = 'style="color:var(--fail)"' if failed > 0 else ""

    # Grafana links banner
    grafana_banner = ""
    snap_url = grafana_links.get("snapshot_url", "")
    live_url = grafana_links.get("live_dashboard_url", "")
    if snap_url or live_url:
        g_parts = []
        if snap_url:
            g_parts.append(f'<a href="{snap_url}" target="_blank" class="g-btn">Grafana Snapshot</a>')
        if live_url:
            g_parts.append(f'<a href="{live_url}" target="_blank" class="g-btn g-btn-live">Live Dashboard</a>')
        grafana_banner = (
            '<div class="grafana-bar" id="grafana-bar">'
            '<span class="g-label">Grafana:</span> '
            + " ".join(g_parts) + '</div>'
        )

    suite_banner = (
        f'<div style="background:var(--yellow-bg,#fff3cd);border:1px solid var(--yellow-border,#ffc107);'
        f'padding:6px 12px;border-radius:4px;font-size:12px;margin:4px 0 8px;">'
        f'Partial run: <strong>{suite}</strong> suite only</div>'
    ) if suite != "all" else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Perf Run: {run_id}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
  :root {{
    --bg:#f4f6f9; --surface:#fff; --border:#dde3ec;
    --text:#2c3e50; --muted:#7f8c8d; --accent:#2980b9;
    --pass:#27ae60; --fail:#e74c3c; --warn:#e67e22;
    --kpi-green:#27ae60; --kpi-yellow:#f39c12; --kpi-red:#e74c3c;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg); color:var(--text); font-size:13px; }}
  .page {{ max-width:1280px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:20px; font-weight:700; }}
  h2 {{ font-size:15px; font-weight:700; margin:28px 0 12px; padding-bottom:6px; border-bottom:2px solid var(--border); }}
  a {{ color:var(--accent); }}

  /* KPI cards */
  .kpi-row {{ display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px 18px; min-width:120px; }}
  .kpi .n {{ font-size:26px; font-weight:700; }}
  .kpi .l {{ font-size:11px; color:var(--muted); margin-top:2px; }}

  /* Meta row */
  .meta-row {{ display:flex; gap:20px; flex-wrap:wrap; font-size:12px; color:var(--muted); margin-bottom:8px; }}
  .meta-row strong {{ color:var(--text); }}

  /* Chart grid */
  .chart-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(480px,1fr)); gap:20px; }}
  .chart-card {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px; }}
  .chart-card h3 {{ font-size:13px; font-weight:600; margin-bottom:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }}
  .chart-card canvas {{ max-height:280px; }}
  .chart-card.wide {{ grid-column:1/-1; }}

  /* Results table */
  .results-table {{ width:100%; border-collapse:collapse; font-size:12px; background:var(--surface); border-radius:8px; overflow:hidden; border:1px solid var(--border); }}
  .results-table th {{ background:#eef2f7; padding:8px 12px; text-align:left; font-weight:600; font-size:11px; }}
  .results-table td {{ padding:7px 12px; border-top:1px solid #f0f0f0; vertical-align:top; }}
  .results-table tr:hover td {{ background:#f9fbfd; }}
  .badge {{ display:inline-block; border-radius:3px; padding:1px 6px; font-size:10px; font-weight:700; color:#fff; }}
  .pass {{ background:var(--pass); }}
  .fail {{ background:var(--fail); }}
  .skip {{ background:var(--muted); }}
  .err-msg {{ font-size:10px; color:var(--fail); max-width:320px; word-break:break-word; }}

  .footer {{ margin-top:32px; font-size:11px; color:var(--muted); text-align:center; }}

  /* Collapsible sections */
  details {{ margin:16px 0; }}
  summary {{ cursor:pointer; font-weight:600; font-size:14px; padding:12px 16px; background:var(--surface); border:1px solid var(--border); border-radius:8px; list-style:none; display:flex; align-items:center; gap:8px; }}
  summary::-webkit-details-marker {{ display:none; }}
  summary::before {{ content:'▶'; font-size:10px; transition:transform 0.2s; }}
  details[open] > summary::before {{ transform:rotate(90deg); }}
  summary:hover {{ background:#f0f4f8; }}
  details > .section-content {{ padding:16px; background:var(--surface); border:1px solid var(--border); border-top:none; border-radius:0 0 8px 8px; }}

  /* Resource summary grid */
  .resource-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:16px 0; }}
  .resource-card {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:14px; }}
  .resource-card .label {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; margin-bottom:8px; }}
  .resource-card .values {{ display:flex; gap:16px; font-size:13px; }}
  .resource-card .values span {{ display:flex; flex-direction:column; }}
  .resource-card .values .v {{ font-size:18px; font-weight:700; color:var(--text); }}
  .resource-card .values .l {{ font-size:10px; color:var(--muted); }}

  /* Expandable test details */
  .test-details {{ margin-top:8px; padding:12px; background:#f8fafc; border-radius:6px; font-size:12px; }}
  .test-details pre {{ background:#1a1a2e; color:#e0e0e0; padding:12px; border-radius:6px; overflow-x:auto; font-size:11px; margin-top:8px; }}
  .metrics-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:8px; }}
  .metric-item {{ padding:6px 10px; background:var(--surface); border:1px solid var(--border); border-radius:4px; }}
  .metric-item .k {{ font-size:10px; color:var(--muted); }}
  .metric-item .v {{ font-size:14px; font-weight:600; }}

  /* Grafana links banner */
  .grafana-bar {{ background:#1a1a2e; color:#eee; padding:10px 24px; border-radius:8px; display:flex; align-items:center; gap:12px; margin-bottom:16px; flex-wrap:wrap; }}
  .g-label {{ font-size:12px; color:#aaa; font-weight:600; text-transform:uppercase; letter-spacing:.5px; }}
  .g-btn {{ display:inline-block; padding:6px 14px; border-radius:5px; text-decoration:none; font-size:12px; font-weight:600; background:#e6521e; color:#fff; }}
  .g-btn:hover {{ background:#c44415; }}
  .g-btn-live {{ background:#2980b9; }}
  .g-btn-live:hover {{ background:#1e6090; }}
</style>
</head>
<body>
<div class="page">

  <h1>Performance Run Report</h1>
  {grafana_banner}
  {suite_banner}
  <div class="meta-row">
    <span><strong>Run:</strong> {run_id}</span>
    <span><strong>Chart:</strong> {chart_ver}</span>
    <span><strong>Profile:</strong> {profile}</span>
    <span><strong>Suite:</strong> {suite}</span>
    <span><strong>Cluster:</strong> OCP {ocp_ver} · {nodes} nodes · {storage}</span>
    <span><strong>Started:</strong> {ts_str}</span>
    <span><strong>Generated:</strong> {generated_at}</span>
  </div>

  <!-- KPI Summary -->
  <div class="kpi-row">
    <div class="kpi" style="border-color:{pass_color}">
      <div class="n" style="color:{pass_color}">{passed}/{total}</div>
      <div class="l">Tests passed</div>
    </div>
    <div class="kpi" {fail_border_style}>
      <div class="n" {fail_color_style}>{failed}</div>
      <div class="l">Failed</div>
    </div>
    <div class="kpi">
      <div class="n">{dur_min} min</div>
      <div class="l">Total duration</div>
    </div>
    <div class="kpi">
      <div class="n">{avg_throughput} MB/s</div>
      <div class="l">Avg upload throughput</div>
    </div>
    {f'<div class="kpi"><div class="n">{avg_ros_rate} exp/min</div><div class="l">Avg Kruize throughput</div></div>' if has_ros else ""}
    {f'<div class="kpi"><div class="n">{len(snapshots)}</div><div class="l">Metrics snapshots</div></div>' if has_prom else ""}
    <div class="kpi" style="border-color:var(--kpi-{overall_kpi})">
      <div class="n" style="color:var(--kpi-{overall_kpi})">{kpi_green}/{kpi_total}</div>
      <div class="l">KPIs passing</div>
    </div>
  </div>

  <!-- Resource Summary (if metrics available) -->
  {_build_resource_summary_html(resource_summary) if has_prom else ""}

  <!-- KPI Scorecard -->
  <details open>
    <summary>KPI Scorecard ({kpi_green}/{kpi_total} passing)</summary>
    <div class="section-content">
      {"<p style='color:var(--muted);font-size:12px;margin-bottom:8px;'>No KPI thresholds matched for this run's tests.</p>" if not all_kpi_evals else ""}
      {_build_kpi_scorecard(all_kpi_evals, per_test_kpis) if all_kpi_evals else ""}
    </div>
  </details>

  <!-- Service Throughput -->
  {_build_throughput_summary_html(ros_throughput, ing_throughput, proc_throughput, api_latency, concurrent)}

  <!-- Test Execution Charts -->
  <details open>
    <summary>Test Execution Charts</summary>
    <div class="section-content">
      <div class="chart-grid">
        <div class="chart-card wide">
          <h3>Test Duration Timeline (seconds)</h3>
          <canvas id="timelineChart"></canvas>
        </div>
        {api_chart_html}
        {throughput_chart_html}
        {proc_chart_html}
        {conc_chart_html}
        {ros_chart_html}
        {proc_chart_html2}
      </div>
    </div>
  </details>

  <!-- Resource Metrics Charts (if available) -->
  {f'''<details open>
    <summary>Resource Metrics Over Time ({len(snapshots)} snapshots)</summary>
    <div class="section-content">
      <div class="chart-grid">
        {cpu_chart_html}
        {memory_chart_html}
        {celery_chart_html}
      </div>
    </div>
  </details>''' if has_prom else ""}

  <!-- All Test Results -->
  <details open>
    <summary>All Test Results ({passed}/{total} passed)</summary>
    <div class="section-content">
      <table class="results-table">
        <thead><tr><th>Test</th><th>Status</th><th>Duration</th><th>Key Metrics</th><th>KPI</th><th style="width:60px">Details</th></tr></thead>
        <tbody>
        {"".join(_result_row_expandable(r, per_test_kpis.get(r["test_name"], [])) for r in results)}
        </tbody>
      </table>
    </div>
  </details>

  <div class="footer">Generated by generate-perf-run-report.py · {generated_at}</div>
</div>

<script>
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 11;
const gridColor = 'rgba(0,0,0,0.06)';

// Timeline
new Chart(document.getElementById('timelineChart'), {{
  type: 'bar',
  data: {{
    labels: {tl_labels},
    datasets: [{{
      label: 'Duration (s)',
      data: {tl_durations},
      backgroundColor: {tl_colors},
      borderRadius: 3,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: gridColor }}, title: {{ display: true, text: 'seconds' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }}
    }}
  }}
}});

{js_api_chart}

{js_throughput_chart}

{js_proc_chart}

{js_conc_chart}

{js_ros_chart}

{js_proc_rate_chart}

{js_cpu_chart}

{js_memory_chart}

{js_celery_chart}

// Toggle expandable test details
function toggleDetails(id) {{
  const row = document.getElementById('details-' + id);
  const btn = event.target;
  if (row.style.display === 'none') {{
    row.style.display = 'table-row';
    btn.textContent = '▼';
  }} else {{
    row.style.display = 'none';
    btn.textContent = '▶';
  }}
}}
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] Run report written to: {output_path}")
    print(f"     {len(results)} tests · {passed}/{total} passed · {dur_min} min")


KPI_NOTES: dict[str, str] = {
    "speedup_ratio": "For small datasets, refresh may not be faster than initial processing due to limited caching benefit.",
}


def _build_kpi_details_html(kpis: list[dict] | None) -> str:
    """Build HTML showing KPI evaluation details with violations highlighted."""
    if not kpis:
        return ""

    violations = [k for k in kpis if k.get("status") in ("red", "yellow")]
    passes = [k for k in kpis if k.get("status") == "green"]

    if not violations:
        return ""

    items = []
    for k in violations:
        color = "var(--kpi-red)" if k["status"] == "red" else "var(--kpi-yellow)"
        op_desc = {"<": "must be <", ">": "must be >", "==": "must equal", ">=": "must be ≥", "<=": "must be ≤"}.get(k.get("op", "<"), "threshold")
        val_fmt = f'{k["value"]:.3f}' if isinstance(k["value"], float) else str(k["value"])
        thresh = k.get("green", "?")
        metric = k.get("metric", "?")
        
        note = ""
        if k["status"] == "yellow" and metric in KPI_NOTES:
            note = f'<div style="font-size:11px;color:var(--muted);margin-top:4px;font-style:italic;">ℹ️ {KPI_NOTES[metric]}</div>'
        
        items.append(
            f'<div style="padding:8px 12px;background:{color}22;border-left:3px solid {color};border-radius:4px;margin-bottom:6px;">'
            f'<strong style="color:{color};">{k["label"]}</strong>: '
            f'<code>{metric}</code> = <strong>{val_fmt}</strong> {k.get("unit", "")}'
            f' ({op_desc} {thresh} for green)'
            f'{note}'
            f'</div>'
        )

    return f'<div style="margin-bottom:12px;"><strong style="color:var(--kpi-red);">KPI Violations:</strong><div style="margin-top:8px;">{"".join(items)}</div></div>'


def _result_row_expandable(r: dict, kpis: list[dict] | None = None) -> str:
    """Build an expandable table row with full metrics in a details element."""
    name   = r["test_name"].replace("test_perf_", "").replace("_baseline", "")
    passed = r.get("passed", True)
    badge  = '<span class="badge pass">PASS</span>' if passed else '<span class="badge fail">FAIL</span>'
    dur_s  = round(sum(t["duration_seconds"] for t in r.get("timings", [])), 1)
    dur    = f"{dur_s}s" if dur_s < 120 else f"{dur_s/60:.1f}min"
    m      = r.get("metrics") or {}
    err    = r.get("error_message") or ""
    
    # Quick metrics summary
    bits = []
    upload = m.get("upload") or {}
    if upload.get("upload_mb_per_second"):
        bits.append(f'{upload["upload_mb_per_second"]:.3f} MB/s')
    if "upload_throughput_mb_s" in m:
        bits.append(f'{m["upload_throughput_mb_s"]:.3f} MB/s')
    if "aggregate_p95" in m:
        bits.append(f'p95={round(m["aggregate_p95"]*1000,1)}ms')
    if "requests_per_second" in m:
        bits.append(f'{m["requests_per_second"]:.1f} req/s')
    # ROS throughput
    exp_count = m.get("experiment_count")
    exp_time = m.get("experiment_creation_time_sec") or m.get("processing_time_sec")
    if exp_count is not None and exp_time and exp_time > 0:
        rate = exp_count / exp_time * 60
        wl = m.get("workload_count", "?")
        bits.append(f'{exp_count}/{wl} exp · {rate:.1f}/min')
    metrics_str = " · ".join(bits) if bits else "—"
    
    kpi_badges = ""
    if kpis:
        kpi_parts = [_kpi_status_icon(e["status"], e) for e in kpis]
        kpi_badges = " ".join(kpi_parts)
    
    # Build KPI violations highlight
    kpi_violations_html = _build_kpi_details_html(kpis)
    
    # Build expandable details with full metrics
    metrics_json = json.dumps(m, indent=2) if m else "{}"
    timings_html = ""
    if r.get("timings"):
        timing_items = [f'<div class="metric-item"><div class="k">{t["name"]}</div><div class="v">{t["duration_seconds"]:.1f}s</div></div>' 
                        for t in r.get("timings", [])]
        timings_html = f'<div style="margin-top:12px;"><strong>Timings:</strong><div class="metrics-grid" style="margin-top:8px;">{"".join(timing_items)}</div></div>'
    
    row_id = _make_row_id(name)
    
    return f'''<tr id="test-{row_id}">
      <td><strong>{name}</strong></td>
      <td>{badge}</td>
      <td>{dur}</td>
      <td>{metrics_str}</td>
      <td>{kpi_badges}</td>
      <td><button onclick="toggleDetails('{row_id}')" style="border:none;background:none;cursor:pointer;font-size:14px;">▶</button></td>
    </tr>
    <tr id="details-{row_id}" style="display:none;">
      <td colspan="6" style="padding:0;border:none;">
        <div class="test-details">
          {f'<div style="color:var(--fail);margin-bottom:8px;"><strong>Error:</strong> {err}</div>' if err else ''}
          {kpi_violations_html}
          {timings_html}
          <div style="margin-top:12px;"><strong>Full Metrics:</strong><pre>{metrics_json}</pre></div>
        </div>
      </td>
    </tr>
'''


def _result_row(r: dict, kpis: list[dict] | None = None) -> str:
    name   = r["test_name"].replace("test_perf_", "").replace("_baseline", "")
    passed = r.get("passed", True)
    badge  = '<span class="badge pass">PASS</span>' if passed else '<span class="badge fail">FAIL</span>'
    dur_s  = round(sum(t["duration_seconds"] for t in r.get("timings", [])), 1)
    dur    = f"{dur_s}s" if dur_s < 120 else f"{dur_s/60:.1f}min"
    m      = r.get("metrics") or {}
    err    = r.get("error_message") or ""

    # Summarize key metric
    bits = []
    upload = m.get("upload") or {}
    if upload.get("upload_mb_per_second"):
        bits.append(f'{upload["upload_mb_per_second"]:.3f} MB/s')
    if "upload_throughput_mb_s" in m:
        bits.append(f'{m["upload_throughput_mb_s"]:.3f} MB/s')
    if "aggregate_p95" in m:
        bits.append(f'p95={round(m["aggregate_p95"]*1000,1)}ms')
    if "requests_per_second" in m:
        bits.append(f'{m["requests_per_second"]:.1f} req/s')
    if "concurrent_sources" in m:
        bits.append(f'{m["concurrent_sources"]} concurrent')
    if "within_window" in m:
        bits.append(f'6h window: {"✅" if m["within_window"] else "❌"}')
    # ROS throughput
    if "experiment_creation_rate_per_min" in m:
        wl = m.get("workload_count", "?")
        bits.append(f'{m.get("experiment_count", "?")}/{wl} exp · {m["experiment_creation_rate_per_min"]:.1f}/min')
    metrics_str = " · ".join(bits) if bits else "—"

    kpi_badges = ""
    if kpis:
        kpi_parts = []
        for e in kpis:
            kpi_parts.append(_kpi_status_icon(e["status"], e))
        kpi_badges = " ".join(kpi_parts)

    err_cell = f'<div class="err-msg">{err}</div>' if err else ""
    return (
        f'<tr><td>{name}</td><td>{badge}</td>'
        f'<td>{dur}</td><td>{metrics_str}</td>'
        f'<td>{kpi_badges}</td>'
        f'<td>{err_cell}</td></tr>\n'
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate visual HTML report for a single perf run")
    parser.add_argument("--run-dir", required=True, help="Path to perf run directory")
    parser.add_argument("--output", default=None, help="Output HTML path (default: <run-dir>/reports/perf-run-report.html)")
    parser.add_argument("--skip-grafana-links", action="store_true", default=True,
                        help="Skip Grafana links in report (default: True)")
    parser.add_argument("--grafana-links", action="store_true",
                        help="Include Grafana links in report (overrides --skip-grafana-links)")
    args = parser.parse_args()
    
    # --grafana-links overrides --skip-grafana-links
    skip_grafana = args.skip_grafana_links and not args.grafana_links

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"[ERROR] Run directory not found: {run_dir}")
        raise SystemExit(1)

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        reports_dir = run_dir / "reports"
        reports_dir.mkdir(exist_ok=True)
        output_path = reports_dir / "perf-run-report.html"

    render_html(run_dir, output_path, skip_grafana_links=skip_grafana)


if __name__ == "__main__":
    main()
