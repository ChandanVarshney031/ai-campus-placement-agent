"""
Unit and Integration Tests for OWASP ZAP Baseline Security Scan (#890).

Acceptance Criteria:
- The scan runs in CI and uploads a report.
- A deliberately added missing header makes the job fail.
- Every ignored rule has a written reason.
"""

from pathlib import Path
import pytest

from utils.zap_scanner import (
    load_zap_rules,
    check_security_headers,
    check_input_handling_and_errors,
    execute_scan,
)


def test_every_ignored_rule_has_written_reason():
    """
    AC 3:
    Every ignored rule in .zap/rules.tsv must have a non-empty, reviewed reason.
    """
    rules_path = Path(".zap/rules.tsv")
    assert rules_path.is_file(), ".zap/rules.tsv rules file must exist."

    rules = load_zap_rules(str(rules_path))
    assert len(rules) > 0, "Rules file should contain rule definitions."

    ignored_rules = [r for r in rules.values() if r.action == "IGNORE"]
    assert len(ignored_rules) >= 4, "Expected common expected ignores to be defined."

    for rule in ignored_rules:
        assert rule.reason is not None, f"Rule {rule.rule_id} must have a reason."
        assert len(rule.reason.strip()) >= 15, (
            f"Rule {rule.rule_id} marked IGNORE must have a substantive written explanation, "
            f"found: '{rule.reason}'"
        )


def test_rules_file_rejects_missing_reason_on_ignore(tmp_path):
    """
    Verifies that load_zap_rules raises ValueError if any rule is marked IGNORE
    without a documented reason.
    """
    bad_rules = tmp_path / "bad_rules.tsv"
    bad_rules.write_text("10035\tIGNORE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="without a written reason"):
        load_zap_rules(str(bad_rules))


def test_deliberately_missing_header_fails_job():
    """
    AC 2:
    A deliberately added missing header makes the job fail.
    Simulates responses missing each critical security header and verifies
    that rule check returns an alert with action 'FAIL'.
    """
    rules = load_zap_rules(".zap/rules.tsv")

    # 1. Deliberately missing X-Content-Type-Options
    bad_headers_no_nosniff = {
        "Content-Security-Policy": "default-src 'self'",
        "X-Frame-Options": "SAMEORIGIN",
        "Permissions-Policy": "camera=()",
    }
    alerts_nosniff = check_security_headers(bad_headers_no_nosniff, "/test", rules)
    nosniff_fail = [a for a in alerts_nosniff if a.rule_id == "10021" and a.action == "FAIL"]
    assert len(nosniff_fail) == 1, (
        "Missing X-Content-Type-Options must trigger a FAIL alert (Rule 10021)."
    )

    # 2. Deliberately missing Content-Security-Policy
    bad_headers_no_csp = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Permissions-Policy": "camera=()",
    }
    alerts_csp = check_security_headers(bad_headers_no_csp, "/test", rules)
    csp_fail = [a for a in alerts_csp if a.rule_id == "10038" and a.action == "FAIL"]
    assert len(csp_fail) == 1, (
        "Missing Content-Security-Policy must trigger a FAIL alert (Rule 10038)."
    )

    # 3. Deliberately missing Anti-Clickjacking (X-Frame-Options & frame-ancestors)
    bad_headers_no_frame = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'",  # lacks frame-ancestors
        "Permissions-Policy": "camera=()",
    }
    alerts_frame = check_security_headers(bad_headers_no_frame, "/test", rules)
    frame_fail = [a for a in alerts_frame if a.rule_id == "10020" and a.action == "FAIL"]
    assert len(frame_fail) == 1, (
        "Missing Anti-clickjacking protection must trigger a FAIL alert (Rule 10020)."
    )

    # 4. Deliberately missing Permissions-Policy
    bad_headers_no_perms = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'",
        "X-Frame-Options": "SAMEORIGIN",
    }
    alerts_perms = check_security_headers(bad_headers_no_perms, "/test", rules)
    perms_fail = [a for a in alerts_perms if a.rule_id == "10063" and a.action == "FAIL"]
    assert len(perms_fail) == 1, (
        "Missing Permissions-Policy must trigger a FAIL alert (Rule 10063)."
    )


def test_compliant_headers_produce_zero_failures():
    """
    Verifies that fully compliant security headers pass with zero FAIL alerts.
    """
    rules = load_zap_rules(".zap/rules.tsv")
    compliant_headers = {
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'self'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Permissions-Policy": "camera=(self), microphone=(self)",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    alerts = check_security_headers(compliant_headers, "/", rules)
    failures = [a for a in alerts if a.action == "FAIL"]
    assert len(failures) == 0, f"Compliant headers should produce 0 FAIL alerts, found: {failures}"


def test_reflected_xss_triggers_fail_alert():
    """
    Verifies that unescaped reflected input triggers Rule 10017 with action FAIL.
    """
    rules = load_zap_rules(".zap/rules.tsv")
    probe = '<script>alert("xss")</script>'
    body_with_xss = f"<html><body>Search query: {probe}</body></html>"

    alerts = check_input_handling_and_errors(
        body=body_with_xss,
        status_code=200,
        url="/search",
        reflection_probe=probe,
        rules=rules,
    )

    xss_fail = [a for a in alerts if a.rule_id == "10017" and a.action == "FAIL"]
    assert len(xss_fail) == 1, "Reflected probe must trigger Rule 10017 with action FAIL."


def test_full_zap_baseline_scan_execution(tmp_path):
    """
    AC 1:
    The scan runs and uploads/creates a report artifact.
    Executes the full scan pipeline with temporary database, seeds test user,
    and produces HTML, JSON, and step summary artifacts.
    """
    html_report = tmp_path / "zap-report.html"
    json_report = tmp_path / "zap-report.json"
    summary_file = tmp_path / "summary.md"

    exit_code = execute_scan(
        rules_path=".zap/rules.tsv",
        report_path=str(html_report),
        json_path=str(json_report),
        summary_file=str(summary_file),
    )

    # Compliant application must pass the scan
    assert exit_code == 0, "Full scan on current application must exit with 0 (PASS)."

    # Verify report files were generated
    assert html_report.is_file(), "HTML report artifact must be generated."
    html_content = html_report.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_content
    assert "OWASP ZAP Baseline Security Scan Report" in html_content
    assert "Documented Reviewed Ignores" in html_content

    assert json_report.is_file(), "JSON report artifact must be generated."
    json_content = json_report.read_text(encoding="utf-8")
    assert '"passed": true' in json_content
    assert '"fail_count": 0' in json_content

    assert summary_file.is_file(), "Step summary file must be generated."
    summary_md = summary_file.read_text(encoding="utf-8")
    assert "## 🛡️ OWASP ZAP Baseline Security Scan" in summary_md
    assert "✅ PASSED" in summary_md
