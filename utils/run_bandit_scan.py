"""
Security scan runner for CI and local verification.
Runs Bandit static analysis with baseline and outputs summary.
"""

import json
import os
import subprocess
import sys


def run_bandit_scan(baseline_file=".bandit-baseline.json", config_file=".bandit.yaml", summary_file=None):
    """
    Run Bandit with baseline filtering and high severity threshold (-lll).
    Prints a markdown summary to GitHub Step Summary if available.
    Returns exit code (0 if clean, non-zero if new high-severity findings).
    """
    print(f"[*] Running Bandit with baseline '{baseline_file}' and config '{config_file}'...")

    cmd = [
        sys.executable,
        "-m",
        "bandit",
        "-r",
        ".",
        "-c",
        config_file,
        "-b",
        baseline_file,
        "-lll",
        "-f",
        "json",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    exit_code = proc.returncode

    try:
        report = json.loads(proc.stdout) if proc.stdout else {}
    except Exception:
        report = {}

    results = report.get("results", [])
    metrics = report.get("metrics", {}).get("_totals", {})

    high_count = len([r for r in results if r.get("issue_severity") == "HIGH"])

    summary_lines = [
        "## 🛡️ Bandit Static AST Security Analysis",
        "",
        f"- **Status**: {'❌ FAILED - New High-Severity Issues Found' if high_count > 0 else '✅ PASSED - No New High-Severity Issues'}",
        f"- **Baseline File**: `{baseline_file}`",
        f"- **New High-Severity Findings**: `{high_count}`",
        f"- **Total Scanned Lines**: `{metrics.get('loc', 'N/A')}`",
        "",
    ]

    if high_count > 0:
        summary_lines.append("### 🚨 Detected High-Severity Findings")
        summary_lines.append("| Rule ID | File | Line | Issue Description | Confidence |")
        summary_lines.append("|---|---|---|---|---|")
        for issue in results:
            if issue.get("issue_severity") == "HIGH":
                summary_lines.append(
                    f"| `{issue.get('test_id')}` | `{issue.get('filename')}` | `{issue.get('line_number')}` | {issue.get('issue_text')} | `{issue.get('issue_confidence')}` |"
                )
        summary_lines.append("")
        summary_lines.append("> **Action Required**: Resolve these high-severity findings or justify using `# nosec <test_id>` with a comment.")
    else:
        summary_lines.append("No new high-severity security vulnerabilities were detected in your code against the baseline.")

    summary_text = "\n".join(summary_lines)
    print("\n" + summary_text + "\n")

    step_summary_path = summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write("\n" + summary_text + "\n")
            print(f"[+] Appended Bandit summary to {step_summary_path}")
        except Exception as e:
            print(f"[-] Warning: Failed to write to {step_summary_path}: {e}")

    return 1 if high_count > 0 else 0


if __name__ == "__main__":
    baseline = sys.argv[1] if len(sys.argv) > 1 else ".bandit-baseline.json"
    config = sys.argv[2] if len(sys.argv) > 2 else ".bandit.yaml"
    code = run_bandit_scan(baseline, config)
    sys.exit(code)
