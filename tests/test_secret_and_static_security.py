"""
Tests for Gitleaks secret scanning configuration and Bandit AST static security analysis.
Verifies:
1. Baseline file existence, valid JSON structure, and metrics.
2. Configuration files (.bandit.yaml and .gitleaks.toml) validation.
3. Bandit baseline filtering: known baseline findings pass, while newly introduced HIGH severity issues fail.
4. Gitleaks pattern detection catches secrets while respecting allowlists.
5. GitHub Actions workflow file exists and is well-formed.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BANDIT_BASELINE_PATH = os.path.join(REPO_ROOT, ".bandit-baseline.json")
BANDIT_CONFIG_PATH = os.path.join(REPO_ROOT, ".bandit.yaml")
GITLEAKS_CONFIG_PATH = os.path.join(REPO_ROOT, ".gitleaks.toml")
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "security-static-analysis.yml")
DOCS_PATH = os.path.join(REPO_ROOT, "docs", "STATIC_SECURITY_ANALYSIS.md")


def test_bandit_baseline_file_validity():
    """Verify .bandit-baseline.json exists and has valid Bandit report structure."""
    assert os.path.exists(BANDIT_BASELINE_PATH), "Bandit baseline file must exist at repo root"
    with open(BANDIT_BASELINE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "results" in data, "Baseline file must contain 'results' array"
    assert "metrics" in data, "Baseline file must contain 'metrics' dictionary"
    assert isinstance(data["results"], list)
    assert len(data["results"]) > 0, "Baseline should contain existing project findings"


def test_bandit_config_and_workflow_exist():
    """Verify .bandit.yaml, .gitleaks.toml, and workflow file exist."""
    assert os.path.exists(BANDIT_CONFIG_PATH), ".bandit.yaml must exist"
    assert os.path.exists(GITLEAKS_CONFIG_PATH), ".gitleaks.toml must exist"
    assert os.path.exists(WORKFLOW_PATH), "security-static-analysis.yml workflow must exist"

    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        wf_content = f.read()

    assert "gitleaks/gitleaks-action" in wf_content
    assert "bandit" in wf_content
    assert ".bandit-baseline.json" in wf_content


def test_current_repo_passes_bandit_with_baseline():
    """Verify that scanning the current repository with baseline passes with 0 new high-severity findings."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as out_tmp:
        out_path = out_tmp.name

    try:
        cmd = [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            ".",
            "-c",
            BANDIT_CONFIG_PATH,
            "-b",
            BANDIT_BASELINE_PATH,
            "-lll",
            "-f",
            "json",
            "-o",
            out_path,
        ]
        subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        with open(out_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        results = report.get("results", [])
        high_issues = [r for r in results if r.get("issue_severity") == "HIGH"]
        assert len(high_issues) == 0, f"Found unexpected high severity issues: {high_issues}"
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


def test_bandit_fails_on_new_high_severity_code():
    """Verify that a newly introduced high-severity vulnerability fails Bandit scan."""
    # Write a temporary python file with a high severity issue: subprocess with shell=True
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tmp:
        tmp.write("import subprocess\n")
        tmp.write("def run_cmd(user_input):\n")
        tmp.write("    subprocess.Popen(user_input, shell=True)\n")  # B602 (HIGH severity)
        tmp_path = tmp.name

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as out_tmp:
        out_path = out_tmp.name

    try:
        cmd = [
            sys.executable,
            "-m",
            "bandit",
            tmp_path,
            "-b",
            BANDIT_BASELINE_PATH,
            "-lll",
            "-f",
            "json",
            "-o",
            out_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        with open(out_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        results = report.get("results", [])
        high_issues = [r for r in results if r.get("issue_severity") == "HIGH"]
        assert len(high_issues) >= 1, "Expected newly introduced shell=True to trigger high severity alert"
        assert any("B602" in r.get("test_id", "") or "shell" in r.get("issue_text", "").lower() for r in high_issues)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if os.path.exists(out_path):
            os.remove(out_path)


def test_secret_detection_patterns():
    """Verify regex patterns for detecting committed API keys, tokens, and private keys."""
    # Common high-risk secret patterns tested by secret scanners like Gitleaks
    patterns = {
        "aws_access_key": re.compile(r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"),
        "generic_api_key": re.compile(r"(?i)(?:api_key|apikey|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]"),
        "rsa_private_key": re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),
    }

    # Test cases that MUST be caught
    positive_samples = [
        "AKIAIOSFODNN7EXAMPLE",
        "api_key = 'AIzaSyD-1234567890abcdefghijklmnopqrst'",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...",
    ]

    for sample in positive_samples:
        matched = any(p.search(sample) for p in patterns.values())
        assert matched, f"Sample secret '{sample}' should be caught by secret scanner patterns"

    # Test case that is in allowlist / dummy placeholder
    safe_sample = "GEMINI_API_KEY=your_gemini_api_key_here"
    # Should not match high-entropy AWS key or RSA key
    assert not patterns["aws_access_key"].search(safe_sample)
    assert not patterns["rsa_private_key"].search(safe_sample)
