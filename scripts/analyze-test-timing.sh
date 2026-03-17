#!/bin/bash
# Analyze JUnit XML test results to identify slow tests
#
# Usage:
#   ./scripts/analyze-test-timing.sh [OPTIONS]
#
# Options:
#   --input FILE        JUnit XML file to analyze (default: tests/reports/iqe_junit.xml)
#   --threshold SECS    Minimum duration to report (default: 30)
#   --top N             Show top N slowest tests (default: 50)
#   --output FILE       Write results to file (default: stdout)
#   --format FORMAT     Output format: table, csv, json (default: table)
#   --help              Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Defaults
INPUT_FILE="${PROJECT_ROOT}/tests/reports/iqe_junit.xml"
THRESHOLD=30
TOP_N=50
OUTPUT_FILE=""
FORMAT="table"

show_help() {
    cat << EOF
Analyze JUnit XML test results to identify slow tests

Usage: $(basename "$0") [OPTIONS]

Options:
    --input FILE        JUnit XML file to analyze (default: tests/reports/iqe_junit.xml)
    --threshold SECS    Minimum duration to report (default: 30)
    --top N             Show top N slowest tests (default: 50)
    --output FILE       Write results to file (default: stdout)
    --format FORMAT     Output format: table, csv, json (default: table)
    --help              Show this help message

Examples:
    # Analyze default results file
    ./scripts/analyze-test-timing.sh

    # Show tests taking more than 60 seconds
    ./scripts/analyze-test-timing.sh --threshold 60

    # Export top 100 slowest tests as CSV
    ./scripts/analyze-test-timing.sh --top 100 --format csv --output slow-tests.csv

    # Extract from running pod first
    kubectl cp cost-onprem/iqe-cost-tests:/results/junit.xml tests/reports/iqe_junit.xml
    ./scripts/analyze-test-timing.sh
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --input) INPUT_FILE="$2"; shift 2 ;;
        --threshold) THRESHOLD="$2"; shift 2 ;;
        --top) TOP_N="$2"; shift 2 ;;
        --output) OUTPUT_FILE="$2"; shift 2 ;;
        --format) FORMAT="$2"; shift 2 ;;
        --help) show_help; exit 0 ;;
        *) echo "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Validate input file
if [[ ! -f "${INPUT_FILE}" ]]; then
    echo "ERROR: Input file not found: ${INPUT_FILE}"
    echo ""
    echo "If tests are still running, extract the file first:"
    echo "  kubectl cp cost-onprem/iqe-cost-tests:/results/junit.xml ${INPUT_FILE}"
    exit 1
fi

# Check for required tools
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required but not found"
    exit 1
fi

# Python script to parse JUnit XML and extract timing
analyze_timing() {
    python3 << 'PYTHON_SCRIPT'
import xml.etree.ElementTree as ET
import sys
import json
import os

input_file = os.environ.get('INPUT_FILE')
threshold = float(os.environ.get('THRESHOLD', 30))
top_n = int(os.environ.get('TOP_N', 50))
output_format = os.environ.get('FORMAT', 'table')

try:
    tree = ET.parse(input_file)
    root = tree.getroot()
except ET.ParseError as e:
    print(f"ERROR: Failed to parse XML: {e}", file=sys.stderr)
    sys.exit(1)

# Extract test cases with timing
tests = []
for testcase in root.iter('testcase'):
    name = testcase.get('name', 'unknown')
    classname = testcase.get('classname', '')
    time_str = testcase.get('time', '0')
    
    try:
        duration = float(time_str)
    except ValueError:
        duration = 0.0
    
    # Determine status
    status = 'passed'
    if testcase.find('failure') is not None:
        status = 'failed'
    elif testcase.find('error') is not None:
        status = 'error'
    elif testcase.find('skipped') is not None:
        status = 'skipped'
    
    # Extract short class name (last part)
    short_class = classname.split('.')[-1] if classname else ''
    
    tests.append({
        'name': name,
        'classname': classname,
        'short_class': short_class,
        'duration': duration,
        'status': status,
    })

# Sort by duration descending
tests.sort(key=lambda x: x['duration'], reverse=True)

# Filter by threshold and limit
slow_tests = [t for t in tests if t['duration'] >= threshold][:top_n]

# Calculate statistics
total_tests = len(tests)
total_duration = sum(t['duration'] for t in tests)
slow_count = len([t for t in tests if t['duration'] >= threshold])
slow_duration = sum(t['duration'] for t in tests if t['duration'] >= threshold)

stats = {
    'total_tests': total_tests,
    'total_duration_sec': round(total_duration, 2),
    'total_duration_min': round(total_duration / 60, 2),
    'slow_tests_count': slow_count,
    'slow_tests_duration_sec': round(slow_duration, 2),
    'slow_tests_pct_of_time': round(slow_duration / total_duration * 100, 1) if total_duration > 0 else 0,
    'threshold_sec': threshold,
}

# Output based on format
if output_format == 'json':
    output = {
        'statistics': stats,
        'slow_tests': slow_tests,
    }
    print(json.dumps(output, indent=2))

elif output_format == 'csv':
    print('duration_sec,status,test_name,class_name')
    for t in slow_tests:
        # Escape commas in names
        name = t['name'].replace(',', ';')
        classname = t['classname'].replace(',', ';')
        print(f"{t['duration']:.2f},{t['status']},{name},{classname}")

else:  # table format
    print("=" * 80)
    print("TEST TIMING ANALYSIS")
    print("=" * 80)
    print(f"Input file: {input_file}")
    print(f"Threshold: {threshold}s")
    print("")
    print("STATISTICS:")
    print(f"  Total tests:        {stats['total_tests']}")
    print(f"  Total duration:     {stats['total_duration_min']} min ({stats['total_duration_sec']}s)")
    print(f"  Slow tests (>{threshold}s): {stats['slow_tests_count']}")
    print(f"  Slow tests time:    {stats['slow_tests_duration_sec']}s ({stats['slow_tests_pct_of_time']}% of total)")
    print("")
    print("=" * 80)
    print(f"TOP {len(slow_tests)} SLOWEST TESTS (>{threshold}s)")
    print("=" * 80)
    print("")
    print(f"{'Duration':>10}  {'Status':<8}  Test Name")
    print("-" * 80)
    
    for t in slow_tests:
        duration_str = f"{t['duration']:.1f}s"
        # Truncate long test names
        name = t['name']
        if len(name) > 55:
            name = name[:52] + "..."
        print(f"{duration_str:>10}  {t['status']:<8}  {name}")
    
    print("")
    print("=" * 80)
    print("RECOMMENDATIONS:")
    print("=" * 80)
    
    # Group by test pattern to find common slow patterns
    patterns = {}
    for t in slow_tests:
        # Extract base test name (without parameters)
        base_name = t['name'].split('[')[0] if '[' in t['name'] else t['name']
        if base_name not in patterns:
            patterns[base_name] = {'count': 0, 'total_time': 0}
        patterns[base_name]['count'] += 1
        patterns[base_name]['total_time'] += t['duration']
    
    # Sort patterns by total time
    sorted_patterns = sorted(patterns.items(), key=lambda x: x[1]['total_time'], reverse=True)[:10]
    
    print("")
    print("Slowest test patterns (base test names):")
    print("")
    for pattern, data in sorted_patterns:
        print(f"  {data['total_time']:.0f}s total ({data['count']} variants): {pattern}")
    
    print("")
    print("Consider adding these to the skip filter if not critical:")
    print("")
    filter_suggestions = [p[0] for p in sorted_patterns[:5]]
    print(f"  --filter \"not {' and not '.join(filter_suggestions)}\"")
    print("")

PYTHON_SCRIPT
}

# Run analysis
export INPUT_FILE THRESHOLD TOP_N FORMAT

if [[ -n "${OUTPUT_FILE}" ]]; then
    analyze_timing > "${OUTPUT_FILE}"
    echo "Results written to: ${OUTPUT_FILE}"
else
    analyze_timing
fi
