"""Shared utilities for loading performance run data.

Used by generate-perf-run-report.py, generate-perf-summary.py,
generate-perf-matrix-report.py, and push-grafana-snapshot.py.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional


def load_session(run_dir: Path) -> Optional[dict]:
    """Load the first session_*.json from results/."""
    for sf in sorted((run_dir / "results").glob("session_*.json")):
        try:
            return json.loads(sf.read_text())
        except Exception:
            pass
    return None


def load_metadata(run_dir: Path) -> dict:
    """Load metadata.json from the run directory."""
    p = run_dir / "metadata.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def parse_junit(run_dir: Path, *, include_testcases: bool = False) -> Optional[dict]:
    """Parse junit XML from reports/ subdirectory.

    Args:
        run_dir: Path to the run directory.
        include_testcases: If True, include per-testcase detail in a
            ``perf_tests`` key (used by the matrix report).

    Returns:
        Summary dict with total/passed/failed/skipped/duration_s, or None.
    """
    reports_dir = run_dir / "reports"
    named = reports_dir / "junit.xml"
    candidates = [named] if named.exists() else sorted(reports_dir.glob("*.xml"))
    for xml_path in candidates:
        try:
            root = ET.parse(xml_path).getroot()
            suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
            total = errors = failures = skipped = 0
            duration = 0.0
            perf_tests: list[dict[str, Any]] = []
            for suite in suites:
                total    += int(suite.get("tests",    0))
                errors   += int(suite.get("errors",   0))
                failures += int(suite.get("failures", 0))
                skipped  += int(suite.get("skipped",  0))
                try:
                    duration += float(suite.get("time", 0))
                except ValueError:
                    pass
                if include_testcases:
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
            result: dict[str, Any] = {
                "total": total,
                "passed": total - errors - failures - skipped,
                "failed": failures + errors,
                "skipped": skipped,
                "duration_s": round(duration, 1),
            }
            if include_testcases:
                result["perf_tests"] = perf_tests
                result["xml_path"] = str(xml_path)
            return result
        except Exception:
            continue
    return None
