#!/usr/bin/env python3
"""
Performance Matrix Report Generator
FLPATH-4061 / FLPATH-4036

Scans perf-runs/ for completed test runs and generates a single self-contained
HTML page showing the listener CPU × load profile matrix with inline results.

Usage:
    python3 scripts/observability/generate-perf-matrix-report.py
    python3 scripts/observability/generate-perf-matrix-report.py --runs-dir ./perf-runs --output perf-matrix.html

The generated report is fully self-contained (no external dependencies).
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CPU_CONFIGS = [
    {"label": "constrained", "limit": "300m",  "color": "#e74c3c"},
    {"label": "moderate",    "limit": "500m",  "color": "#e67e22"},
    {"label": "recommended", "limit": "1000m", "color": "#27ae60"},
    {"label": "uncapped",    "limit": "max",   "color": "#2980b9"},
    {"label": "unknown",     "limit": "?",     "color": "#95a5a6"},
]
CPU_LABELS  = [c["label"] for c in CPU_CONFIGS]
CPU_BY_LABEL = {c["label"]: c for c in CPU_CONFIGS}

PROFILES = ["baseline", "small", "medium", "large", "xlarge"]

# Which ING tests are relevant for each profile (for the matrix legend)
RELEVANT_TESTS = {
    "baseline": ["ING-001"],
    "small":    ["ING-001", "ING-003", "ING-005", "ING-006"],
    "medium":   ["ING-006"],
    "large":    ["ING-004", "ING-006"],
    "xlarge":   ["ING-004"],
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_metadata(run_dir: Path) -> Optional[dict]:
    meta_path = run_dir / "metadata.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            pass
    # Fall back to inferring from directory name: {version}-{profile}-{epoch}
    parts = run_dir.name.split("-")
    return None


def parse_junit(run_dir: Path) -> Optional[dict]:
    """Parse junit XML from reports/ subdirectory. Returns summary dict."""
    # Prefer junit.xml by name, fall back to any *.xml
    reports_dir = run_dir / "reports"
    candidates = []
    named = reports_dir / "junit.xml"
    if named.exists():
        candidates = [named]
    else:
        candidates = sorted(reports_dir.glob("*.xml"))
    for xml_path in candidates:
        try:
            root = ET.parse(xml_path).getroot()
            # Handle both <testsuites> and <testsuite> roots
            suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
            total = errors = failures = skipped = 0
            duration = 0.0
            perf_tests = []
            for suite in suites:
                total    += int(suite.get("tests",    0))
                errors   += int(suite.get("errors",   0))
                failures += int(suite.get("failures", 0))
                skipped  += int(suite.get("skipped",  0))
                try:
                    duration += float(suite.get("time", 0))
                except ValueError:
                    pass
                for tc in suite.findall("testcase"):
                    name = tc.get("name", "")
                    if "perf" in name.lower() or "ing" in name.lower():
                        status = "passed"
                        failure_msg = None
                        if tc.find("failure") is not None:
                            status = "failed"
                            failure_msg = (tc.find("failure").get("message") or "")[:120]
                        elif tc.find("error") is not None:
                            status = "error"
                            failure_msg = (tc.find("error").get("message") or "")[:120]
                        elif tc.find("skipped") is not None:
                            status = "skipped"
                        perf_tests.append({
                            "name": name,
                            "classname": tc.get("classname", ""),
                            "time": float(tc.get("time", 0)),
                            "status": status,
                            "message": failure_msg,
                        })
            passed = total - errors - failures - skipped
            return {
                "total": total,
                "passed": passed,
                "failed": failures + errors,
                "skipped": skipped,
                "duration_s": round(duration, 1),
                "perf_tests": perf_tests,
                "xml_path": str(xml_path),
            }
        except Exception:
            continue
    return None


def parse_metrics_summary(run_dir: Path) -> Optional[dict]:
    """Load metrics/summary.json if present."""
    summary_path = run_dir / "metrics" / "summary.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text())
        except Exception:
            pass
    return None


def _cpu_limit_to_label(cpu: str) -> Optional[str]:
    """Map a CPU limit string (e.g. '300m', 'max', '2000m') to a matrix label."""
    if not cpu:
        return None
    cpu = str(cpu).strip()
    if cpu.lower() in ("max", "none", "unlimited"):
        return "uncapped"
    if cpu.lower() in ("default", ""):
        return None
    try:
        millicores = int(cpu.rstrip("m"))
    except ValueError:
        return None
    if millicores >= 1000:
        return "recommended"
    if millicores >= 500:
        return "moderate"
    return "constrained"


def infer_cpu_label(run_id: str, metadata: Optional[dict],
                    session: Optional[dict] = None) -> str:
    """Best-effort: infer listener CPU config from metadata, session, or run ID."""
    if metadata:
        label = _cpu_limit_to_label(metadata.get("listener_cpu_limit", ""))
        if label:
            return label

    if session:
        for r in session.get("results", []):
            m = r.get("metrics") or {}
            cpu_val = m.get("listener_cpu_limit") or m.get("listener_cpu")
            label = _cpu_limit_to_label(str(cpu_val) if cpu_val else "")
            if label:
                return label
            cores = m.get("listener_cpu_cores", 0)
            if isinstance(cores, (int, float)) and cores > 1.5:
                return "uncapped"

    return "constrained"


def infer_profile(run_id: str, metadata: Optional[dict]) -> str:
    """Best-effort: infer profile from metadata or run ID."""
    if metadata:
        p = metadata.get("perf_profile", "")
        if p in PROFILES:
            return p
    for p in PROFILES:
        if p in run_id.lower():
            return p
    return "baseline"


def load_session(run_dir: Path) -> Optional[dict]:
    """Load session_*.json — the aggregated results file written by the perf collector."""
    for sf in sorted((run_dir / "results").glob("session_*.json")):
        try:
            return json.loads(sf.read_text())
        except Exception:
            pass
    return None


def extract_perf_summary(session: dict) -> list[dict]:
    """Pull key per-test metrics out of the session results list."""
    rows = []
    for r in session.get("results", []):
        name    = r.get("test_name", "")
        passed  = r.get("passed", False)
        err_msg = r.get("error_message") or ""
        m       = r.get("metrics", {}) or {}
        timings = {t["name"]: t["duration_seconds"] for t in r.get("timings", [])}

        # Derive the most useful metrics per test type
        highlights = {}
        if "upload_throughput_mb_s" in m:
            highlights["throughput"] = f'{m["upload_throughput_mb_s"]:.2f} MB/s'
        if "actual_size_mb" in m:
            highlights["size"] = f'{m["actual_size_mb"]:.1f} MB'
        if "processing_time_seconds" in m:
            highlights["proc"] = f'{m["processing_time_seconds"]/60:.1f} min'
        if "total_upload_mb" in m:
            highlights["total_mb"] = f'{m["total_upload_mb"]:.1f} MB'
        if "concurrent_sources" in m:
            highlights["concurrent"] = str(m["concurrent_sources"])
        if "total_elapsed_hours" in m:
            highlights["elapsed"] = f'{m["total_elapsed_hours"]:.2f} hr'
            highlights["window_ok"] = "✅" if m.get("within_window") else "❌"
        # Upload throughput from timings
        up_s = timings.get("concurrent_uploads") or timings.get("data_generation_and_upload")
        if up_s and "total_upload_mb" in m and m["total_upload_mb"] > 0:
            highlights["throughput"] = f'{m["total_upload_mb"] / up_s:.2f} MB/s'

        rows.append({
            "name":       name,
            "passed":     passed,
            "error":      err_msg[:100] if err_msg else "",
            "highlights": highlights,
            "timings":    {k: round(v, 1) for k, v in timings.items()},
        })
    return rows


def load_runs(runs_dir: Path) -> list[dict]:
    runs = []
    if not runs_dir.exists():
        return runs
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        metadata = load_metadata(run_dir)
        junit    = parse_junit(run_dir)
        metrics  = parse_metrics_summary(run_dir)
        session  = load_session(run_dir)

        profile    = infer_profile(run_dir.name, metadata)
        cpu_label  = infer_cpu_label(run_dir.name, metadata, session)

        perf_summary = extract_perf_summary(session) if session else []

        # Build result file links (relative to runs_dir)
        result_links = []
        for rf in sorted((run_dir / "results").glob("*.json")):
            if rf.name.startswith("session_"):
                continue
            result_links.append({
                "name": rf.stem[:60],
                "path": str(rf.relative_to(runs_dir)),
            })

        html_report = None
        # Prefer the visual perf-run-report.html over the generic pytest report
        report_priority = ["perf-run-report.html", "report.html"]
        reports_dir = run_dir / "reports"
        for preferred in report_priority:
            hp = reports_dir / preferred
            if hp.exists():
                html_report = str(hp.relative_to(runs_dir))
                break
        if html_report is None:
            for hp in sorted(reports_dir.glob("*.html")):
                html_report = str(hp.relative_to(runs_dir))
                break

        if junit:
            if junit["failed"] == 0:
                run_status = "passed"
            else:
                run_status = "failed"
        else:
            run_status = "no-results"

        runs.append({
            "run_id":        run_dir.name,
            "run_dir":       run_dir,
            "profile":       profile,
            "cpu_label":     cpu_label,
            "status":        run_status,
            "metadata":      metadata or {},
            "junit":         junit,
            "metrics_summary": metrics,
            "perf_summary":  perf_summary,
            "result_links":  result_links,
            "html_report":   html_report,
        })
    return runs


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

STATUS_ICON = {"passed": "✅", "failed": "❌", "error": "⚠️", "skipped": "⏭️"}
STATUS_COLOR = {"passed": "#27ae60", "failed": "#e74c3c", "error": "#e67e22", "skipped": "#95a5a6"}


def run_summary_html(run: dict) -> str:
    """Compact summary card for a single run — shown inside a matrix cell."""
    meta  = run["metadata"]
    junit = run["junit"]
    cpu_c = CPU_BY_LABEL.get(run["cpu_label"], {})
    color = cpu_c.get("color", "#666")

    if not junit and not meta:
        return '<div class="cell-empty">no data</div>'

    lines = []

    # Chart version + timestamp
    version  = meta.get("chart_version", run["run_id"])
    ts_raw   = meta.get("created_at", "")
    ts_str   = ""
    if ts_raw:
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            ts_str = ts_raw[:16]

    lines.append(f'<div class="run-version">{version}</div>')
    if ts_str:
        lines.append(f'<div class="run-ts">{ts_str}</div>')

    # Test result badge
    if junit:
        total   = junit["total"]
        passed  = junit["passed"]
        failed  = junit["failed"]
        skipped = junit["skipped"]
        dur_min = round(junit["duration_s"] / 60, 1)
        pass_pct = round(passed / total * 100) if total > 0 else 0
        badge_color = "#27ae60" if failed == 0 else "#e74c3c"
        lines.append(
            f'<div class="result-badge" style="background:{badge_color}">'
            f'{passed}/{total} passed ({pass_pct}%) &nbsp;·&nbsp; {dur_min} min'
            f'</div>'
        )
        if failed > 0:
            lines.append(f'<div class="result-fail">{failed} failed, {skipped} skipped</div>')

    # Key perf highlights from session (top 3 interesting tests)
    perf = run.get("perf_summary", [])
    if perf:
        ing_rows = [r for r in perf if "ing" in r["name"].lower() and r.get("highlights")][:3]
        if ing_rows:
            lines.append('<div class="metrics-block">')
            for r in ing_rows:
                icon = "✅" if r["passed"] else "❌"
                h = r["highlights"]
                bits = []
                if "throughput" in h: bits.append(h["throughput"])
                if "proc" in h:       bits.append(f'proc {h["proc"]}')
                if "size" in h:       bits.append(h["size"])
                if "window_ok" in h:  bits.append(f'6h {h["window_ok"]}')
                short_name = r["name"].replace("test_perf_", "").replace("_baseline", "")[:40]
                lines.append(
                    f'<div class="metric-row">'
                    f'<span class="metric-label">{icon} {short_name}</span>'
                    f'<span class="metric-val">{" · ".join(bits)}</span>'
                    f'</div>'
                )
            lines.append('</div>')

    # Links
    links = []
    if run["html_report"]:
        links.append(f'<a href="{run["html_report"]}">📊 report</a>')
    anchor = run["run_id"].replace("[", "").replace("]", "").replace(".", "-")
    links.append(f'<a href="#{anchor}">📋 results</a>')
    if links:
        lines.append('<div class="cell-links">' + " &nbsp;|&nbsp; ".join(links) + '</div>')

    return "\n".join(lines)


def build_matrix(runs: list[dict]) -> dict:
    """Build {(cpu_label, profile): [runs]} lookup."""
    matrix: dict = {}
    for cpu in CPU_LABELS:
        for profile in PROFILES:
            matrix[(cpu, profile)] = []
    for run in runs:
        key = (run["cpu_label"], run["profile"])
        if key in matrix:
            matrix[key].append(run)
    return matrix


def render_html(runs: list[dict], runs_dir: Path, output_path: Path) -> None:
    matrix = build_matrix(runs)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Table header cells
    profile_headers = "".join(
        f'<th class="profile-header">'
        f'{p}<br><span class="profile-tests">'
        f'{"&nbsp;".join(RELEVANT_TESTS.get(p, []))}'
        f'</span></th>'
        for p in PROFILES
    )

    # Table rows (one per CPU config)
    rows_html = []
    for cpu in CPU_CONFIGS:
        label = cpu["label"]
        color = cpu["color"]
        cells = []
        for profile in PROFILES:
            cell_runs = matrix[(label, profile)]
            if not cell_runs:
                cells.append('<td class="cell cell-empty"><span class="no-data">—</span></td>')
            else:
                inner = ""
                for run in cell_runs:
                    inner += f'<div class="run-card" data-status="{run["status"]}" data-run="{run["run_id"]}">{run_summary_html(run)}</div>'
                cells.append(f'<td class="cell">{inner}</td>')

        row_label = (
            f'<td class="cpu-label" style="border-left:4px solid {color}">'
            f'<span class="cpu-name">{label}</span>'
            f'<span class="cpu-limit">{cpu["limit"]}</span>'
            f'</td>'
        )
        rows_html.append(f'<tr>{row_label}{"".join(cells)}</tr>')

    rows = "\n".join(rows_html)

    # Per-run performance detail sections
    detail_sections = []
    for run in sorted(runs, key=lambda r: r["run_id"]):
        perf = run.get("perf_summary", [])
        result_links = run.get("result_links", [])
        if not perf and not result_links:
            continue
        anchor = run["run_id"].replace("[", "").replace("]", "").replace(".", "-")
        cpu_c  = CPU_BY_LABEL.get(run["cpu_label"], {})
        cpu_color = cpu_c.get("color", "#666")

        # Build results table rows
        table_rows = []
        for r in perf:
            icon = "✅" if r["passed"] else "❌"
            h    = r["highlights"]
            bits = []
            if "throughput" in h: bits.append(f'<b>{h["throughput"]}</b>')
            if "size" in h:       bits.append(h["size"])
            if "proc" in h:       bits.append(f'proc {h["proc"]}')
            if "concurrent" in h: bits.append(f'{h["concurrent"]} concurrent')
            if "elapsed" in h:    bits.append(f'{h["elapsed"]}')
            if "window_ok" in h:  bits.append(f'6h window {h["window_ok"]}')
            top_timing = sorted(r["timings"].items(), key=lambda x: x[1], reverse=True)[:2]
            timing_str = ", ".join(f'{k}: {v}s' for k, v in top_timing)
            err_td = f'<span style="color:#e74c3c;font-size:10px">{r["error"]}</span>' if r["error"] else ""
            short = r["name"].replace("test_perf_", "").replace("_baseline", "")
            # find matching result JSON link
            result_href = next((l["path"] for l in result_links if short[:30] in l["name"]), "")
            name_cell = f'<a href="{result_href}">{short}</a>' if result_href else short
            table_rows.append(
                f'<tr>'
                f'<td>{icon} {name_cell}</td>'
                f'<td>{"&nbsp;·&nbsp;".join(bits)}</td>'
                f'<td style="font-size:10px;color:#666">{timing_str}</td>'
                f'<td>{err_td}</td>'
                f'</tr>'
            )

        table_html = ""
        if table_rows:
            table_html = f"""
<table class="detail-table">
  <thead><tr><th>Test</th><th>Key Metrics</th><th>Timings</th><th>Error</th></tr></thead>
  <tbody>{"".join(table_rows)}</tbody>
</table>"""

        meta = run["metadata"]
        cluster = meta.get("cluster_info", {})
        storage_type = cluster.get("storage_type", "?")
        s3_backend = cluster.get("s3_backend", "")
        storage_str = f"{storage_type} + {s3_backend}" if s3_backend and s3_backend != "unknown" else storage_type
        cluster_info = (
            f'OCP {cluster.get("ocp_version","?")} · '
            f'{cluster.get("node_count","?")} nodes · '
            f'{storage_str} storage'
        ) if cluster else ""

        detail_sections.append(f"""
<div class="detail-section" id="{anchor}" data-status="{run["status"]}" data-run="{run["run_id"]}">
  <div class="detail-header" style="border-left:4px solid {cpu_color}">
    <span class="detail-run-id">{run["run_id"]}</span>
    <span class="detail-tags">
      <span class="tag" style="background:{cpu_color}">{run["cpu_label"]}</span>
      <span class="tag tag-profile">{run["profile"]}</span>
    </span>
    {f'<span class="detail-cluster">{cluster_info}</span>' if cluster_info else ""}
    {f'<a href="{run["html_report"]}" class="detail-report-link">📊 full report</a>' if run["html_report"] else ""}
  </div>
  {table_html}
</div>""")

    perf_detail_sections = "\n".join(detail_sections) if detail_sections else ""

    # Run index sidebar
    run_list_items = ""
    for run in sorted(runs, key=lambda r: r["run_id"], reverse=True):
        junit = run["junit"]
        status = ""
        if junit:
            status_color = "#27ae60" if junit["failed"] == 0 else "#e74c3c"
            status = f'<span style="color:{status_color}">●</span> '
        cpu_c = CPU_BY_LABEL.get(run["cpu_label"], {})
        cpu_color = cpu_c.get("color", "#666")
        run_list_items += (
            f'<li data-status="{run["status"]}" data-run="{run["run_id"]}">'
            f'{status}'
            f'<span class="ri-id">{run["run_id"]}</span><br>'
            f'<span class="ri-tags">'
            f'<span class="tag" style="background:{cpu_color}">{run["cpu_label"]}</span>'
            f'<span class="tag tag-profile">{run["profile"]}</span>'
            f'</span>'
            f'</li>'
        )

    total_runs  = len(runs)
    total_pass  = sum(1 for r in runs if r["junit"] and r["junit"]["failed"] == 0)
    total_fail  = sum(1 for r in runs if r["junit"] and r["junit"]["failed"] > 0)
    in_progress = sum(1 for r in runs if not r["junit"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cost On-Prem · Performance Matrix Report</title>
<style>
  :root {{
    --bg: #f4f6f9;
    --surface: #ffffff;
    --border: #dde3ec;
    --text: #2c3e50;
    --muted: #7f8c8d;
    --accent: #2980b9;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}

  /* Layout */
  .layout {{ display: flex; min-height: 100vh; }}
  .sidebar {{ width: 240px; flex-shrink: 0; background: var(--surface); border-right: 1px solid var(--border); padding: 16px; overflow-y: auto; }}
  .main {{ flex: 1; overflow-x: auto; padding: 24px; }}

  /* Header */
  .page-header {{ margin-bottom: 20px; }}
  .page-header h1 {{ font-size: 20px; font-weight: 700; }}
  .page-header .subtitle {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
  .stats-row {{ display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap; }}
  .stat-chip {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; }}
  .stat-chip .n {{ font-size: 18px; font-weight: 700; }}
  .stat-chip .l {{ font-size: 11px; color: var(--muted); }}

  /* Matrix table */
  .matrix-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; min-width: 900px; width: 100%; }}
  th, td {{ border: 1px solid var(--border); padding: 6px 8px; vertical-align: top; }}
  th {{ background: #eef2f7; text-align: center; font-weight: 600; font-size: 12px; position: sticky; top: 0; }}
  .corner-header {{ background: #eef2f7; width: 120px; min-width: 120px; }}
  .profile-header {{ min-width: 180px; text-align: center; }}
  .profile-tests {{ font-size: 10px; color: var(--muted); font-weight: 400; }}

  /* CPU label column */
  .cpu-label {{ background: #fafbfc; width: 120px; min-width: 120px; vertical-align: middle; text-align: center; }}
  .cpu-name {{ display: block; font-weight: 700; font-size: 12px; }}
  .cpu-limit {{ display: block; font-size: 11px; color: var(--muted); }}

  /* Cells */
  .cell {{ min-width: 180px; max-width: 220px; }}
  .cell-empty {{ text-align: center; }}
  .no-data {{ color: #ccc; font-size: 18px; }}
  .run-card {{ background: #f9fbfd; border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 6px; }}
  .run-card:last-child {{ margin-bottom: 0; }}

  /* Run card internals */
  .run-version {{ font-weight: 600; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .run-ts {{ font-size: 10px; color: var(--muted); margin-bottom: 4px; }}
  .result-badge {{ color: #fff; font-size: 11px; border-radius: 4px; padding: 2px 6px; display: inline-block; margin: 3px 0; }}
  .result-fail {{ font-size: 11px; color: #e74c3c; margin-top: 2px; }}
  .metrics-block {{ margin-top: 5px; border-top: 1px solid #eee; padding-top: 4px; }}
  .metric-row {{ display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 2px; gap: 4px; }}
  .metric-label {{ color: var(--muted); flex-shrink: 0; }}
  .metric-val {{ font-family: monospace; font-size: 10px; text-align: right; }}
  .cell-links {{ margin-top: 5px; font-size: 11px; border-top: 1px solid #eee; padding-top: 4px; }}

  /* Detail sections */
  .detail-section {{ margin-top: 28px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  .detail-header {{ display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: #f7f9fc; flex-wrap: wrap; }}
  .detail-run-id {{ font-family: monospace; font-size: 12px; font-weight: 600; flex: 1; }}
  .detail-cluster {{ font-size: 11px; color: var(--muted); }}
  .detail-report-link {{ font-size: 11px; margin-left: auto; }}
  .detail-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .detail-table th {{ background: #eef2f7; text-align: left; padding: 6px 10px; font-size: 11px; font-weight: 600; border-bottom: 1px solid var(--border); }}
  .detail-table td {{ padding: 6px 10px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  .detail-table tr:last-child td {{ border-bottom: none; }}
  .detail-table tr:hover td {{ background: #f9fbfd; }}

  /* Sidebar */
  .sidebar h2 {{ font-size: 13px; font-weight: 700; margin-bottom: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }}
  .sidebar ul {{ list-style: none; }}
  .sidebar li {{ padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 11px; }}
  .ri-id {{ font-family: monospace; font-size: 10px; word-break: break-all; }}
  .ri-tags {{ display: flex; gap: 4px; margin-top: 3px; flex-wrap: wrap; }}
  .tag {{ border-radius: 3px; padding: 1px 5px; font-size: 10px; color: #fff; }}
  .tag-profile {{ background: #7f8c8d; }}

  /* Legend */
  .legend {{ margin-top: 24px; }}
  .legend h2 {{ font-size: 13px; font-weight: 700; margin-bottom: 8px; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; margin-bottom: 5px; font-size: 12px; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}

  /* Filter chips */
  .filter-chip {{ cursor: pointer; transition: box-shadow .15s, opacity .15s; user-select: none; }}
  .filter-chip:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.12); }}
  .filter-chip.active {{ box-shadow: 0 0 0 2px var(--accent); }}
  [data-hidden="true"] {{ display: none !important; }}

  @media (max-width: 768px) {{
    .sidebar {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="layout">

  <!-- Sidebar -->
  <aside class="sidebar">
    <h2>Runs ({total_runs})</h2>
    <ul>{run_list_items if run_list_items else '<li><em>No runs found</em></li>'}</ul>

    <div class="legend" style="margin-top:20px;">
      <h2>CPU Configs</h2>
      {''.join(
        f'<div class="legend-item"><div class="legend-dot" style="background:{c["color"]}"></div>'
        f'<span><strong>{c["label"]}</strong> ({c["limit"]})</span></div>'
        for c in CPU_CONFIGS
      )}
    </div>
  </aside>

  <!-- Main content -->
  <main class="main">
    <div class="page-header">
      <h1>Cost On-Prem · Performance Matrix Report</h1>
      <div class="subtitle">
        Listener CPU × Load Profile &nbsp;·&nbsp;
        Runs dir: <code>{runs_dir}</code> &nbsp;·&nbsp;
        Generated: {generated_at}
      </div>
      <div class="stats-row">
        <div class="stat-chip filter-chip active" data-filter="all"><div class="n">{total_runs}</div><div class="l">Total runs</div></div>
        <div class="stat-chip filter-chip" data-filter="passed" style="border-color:#27ae60"><div class="n" style="color:#27ae60">{total_pass}</div><div class="l">All passed</div></div>
        <div class="stat-chip filter-chip" data-filter="failed" style="border-color:#e74c3c"><div class="n" style="color:#e74c3c">{total_fail}</div><div class="l">Had failures</div></div>
        <div class="stat-chip filter-chip" data-filter="no-results"><div class="n">{in_progress}</div><div class="l">In progress / no results</div></div>
      </div>
    </div>

    <div class="matrix-wrap">
      <table>
        <thead>
          <tr>
            <th class="corner-header">CPU Config</th>
            {profile_headers}
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>

    <p style="margin-top:16px; font-size:11px; color:var(--muted);">
      Each cell represents one or more test runs at that CPU config × load profile combination.
      Click <em>report</em> for full pytest-html output · <em>results</em> to jump to the detail table below.
    </p>

    <!-- Per-run performance detail tables -->
    {perf_detail_sections}
  </main>
</div>
<script>
(function() {{
  const chips = document.querySelectorAll('.filter-chip');
  const filterable = document.querySelectorAll('[data-status]');

  chips.forEach(chip => {{
    chip.addEventListener('click', () => {{
      const filter = chip.dataset.filter;
      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');

      filterable.forEach(el => {{
        if (filter === 'all') {{
          el.removeAttribute('data-hidden');
        }} else {{
          el.setAttribute('data-hidden', el.dataset.status !== filter ? 'true' : 'false');
        }}
      }});

      document.querySelectorAll('.cell').forEach(cell => {{
        const cards = cell.querySelectorAll('.run-card');
        if (cards.length === 0) return;
        const anyVisible = Array.from(cards).some(c => c.getAttribute('data-hidden') !== 'true');
        cell.closest('tr').style.display = '';
      }});
    }});
  }});
}})();
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] Report written to: {output_path}")
    print(f"     {total_runs} run(s) found across {len([k for k,v in build_matrix(runs).items() if v])} matrix cells")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate performance matrix HTML report")
    parser.add_argument(
        "--runs-dir",
        default="perf-runs",
        help="Directory containing perf run subdirectories (default: perf-runs)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML file path (default: <runs-dir>/perf-matrix-report.html)",
    )
    args = parser.parse_args()

    runs_dir    = Path(args.runs_dir).resolve()
    output_path = Path(args.output).resolve() if args.output else runs_dir / "perf-matrix-report.html"

    print(f"Scanning: {runs_dir}")
    runs = load_runs(runs_dir)

    if not runs:
        print(f"[WARN] No runs found in {runs_dir} — generating empty report.")

    render_html(runs, runs_dir, output_path)


if __name__ == "__main__":
    main()
