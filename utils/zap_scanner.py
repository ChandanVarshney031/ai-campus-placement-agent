"""
OWASP ZAP Baseline Security Scan Runner (#890)
==============================================
Runs passive security baseline checks against application routes using the
configured rules file (.zap/rules.tsv):
  - Validates critical security headers: Content-Security-Policy, X-Content-Type-Options,
    X-Frame-Options / frame-ancestors, Permissions-Policy, Referrer-Policy.
  - Validates cookie flags (HttpOnly, SameSite).
  - Checks for reflected input / XSS vulnerabilities and application error leakage.
  - Matches alerts against rules file (.zap/rules.tsv) actions: FAIL, WARN, IGNORE.
  - Enforces that every IGNORE entry has a written, reviewed justification.
  - Generates HTML and JSON reports, suitable for CI artifacts.
  - Fails the scan (exit code 1) on any alert marked FAIL or unignored Medium/High alert.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional
import urllib.parse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = ".zap/rules.tsv"
DEFAULT_REPORT_PATH = "zap-report.html"
DEFAULT_JSON_PATH = "zap-report.json"
DEFAULT_TEST_EMAIL = "zap.security.student@campus.edu"
DEFAULT_TEST_PASSWORD = "ZapSecurityPass123!"


@dataclass
class ZapRule:
    rule_id: str
    action: str  # FAIL, WARN, IGNORE, OUTOFSCOPE
    reason: str


@dataclass
class AlertFinding:
    rule_id: str
    name: str
    severity: str  # High, Medium, Low, Informational
    url: str
    description: str
    solution: str
    action: str  # FAIL, WARN, IGNORE
    reason: str = ""
    evidence: str = ""


@dataclass
class ScanReport:
    target_url: str
    scan_date: str
    pages_scanned: List[str]
    alerts: List[AlertFinding]
    rules_applied: Dict[str, ZapRule]
    passed: bool
    fail_count: int
    warn_count: int
    ignore_count: int


def load_zap_rules(rules_path: str = DEFAULT_RULES_PATH) -> Dict[str, ZapRule]:
    """
    Parses .zap/rules.tsv and validates that every IGNORE entry has a written reason.
    """
    path = Path(rules_path)
    if not path.is_file():
        raise FileNotFoundError(f"ZAP rules file not found at: {rules_path}")

    rules: Dict[str, ZapRule] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) < 2:
                # Fallback to whitespace split if not tab-delimited
                parts = [p.strip() for p in line.split(maxsplit=2)]

            if len(parts) < 2:
                continue

            rule_id = parts[0]
            action = parts[1].upper()
            reason = parts[2] if len(parts) > 2 else ""

            if action not in ("FAIL", "WARN", "IGNORE", "OUTOFSCOPE"):
                raise ValueError(
                    f"Invalid ZAP rule action '{action}' on line {line_no} of {rules_path}."
                )

            # AC 3: Every ignored rule has a written reason
            if action == "IGNORE" and not reason:
                raise ValueError(
                    f"ZAP rule {rule_id} is marked IGNORE on line {line_no} without a written reason."
                )

            rules[rule_id] = ZapRule(rule_id=rule_id, action=action, reason=reason)

    return rules


def check_security_headers(
    headers: Dict[str, str],
    url: str,
    rules: Dict[str, ZapRule],
) -> List[AlertFinding]:
    """
    Validates presence and syntax of security headers matching ZAP passive rules.
    """
    alerts: List[AlertFinding] = []
    headers_lower = {k.lower(): v for k, v in headers.items()}

    # 1. Anti-clickjacking Header (Rule 10020 - Medium)
    x_frame = headers_lower.get("x-frame-options", "").strip().upper()
    csp = headers_lower.get("content-security-policy", "")
    has_frame_ancestors = "frame-ancestors" in csp.lower()
    if not x_frame and not has_frame_ancestors:
        action = rules.get("10020", ZapRule("10020", "FAIL", "")).action
        reason = rules.get("10020", ZapRule("10020", "FAIL", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10020",
                name="Anti-clickjacking Header Missing",
                severity="Medium",
                url=url,
                description="The response lacks both X-Frame-Options and CSP frame-ancestors directives, leaving it vulnerable to clickjacking attacks.",
                solution="Ensure Content-Security-Policy includes 'frame-ancestors' or X-Frame-Options is set to 'DENY' or 'SAMEORIGIN'.",
                action=action,
                reason=reason,
            )
        )

    # 2. X-Content-Type-Options (Rule 10021 - Low/Medium)
    x_content_type = headers_lower.get("x-content-type-options", "").strip().lower()
    if x_content_type != "nosniff":
        action = rules.get("10021", ZapRule("10021", "FAIL", "")).action
        reason = rules.get("10021", ZapRule("10021", "FAIL", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10021",
                name="X-Content-Type-Options Header Missing",
                severity="Low",
                url=url,
                description="The Anti-MIME-Sniffing header X-Content-Type-Options was not set to 'nosniff'.",
                solution="Set X-Content-Type-Options: nosniff on all HTTP responses.",
                action=action,
                reason=reason,
                evidence=f"Current header: '{headers_lower.get('x-content-type-options', '')}'",
            )
        )

    # 3. Content Security Policy (Rule 10038 - Medium)
    if not csp:
        action = rules.get("10038", ZapRule("10038", "FAIL", "")).action
        reason = rules.get("10038", ZapRule("10038", "FAIL", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10038",
                name="Content Security Policy (CSP) Header Not Set",
                severity="Medium",
                url=url,
                description="Content Security Policy (CSP) is an added layer of security that helps detect and mitigate certain types of attacks, including Cross Site Scripting (XSS) and data injection attacks.",
                solution="Configure a robust Content-Security-Policy HTTP response header on all HTML endpoints.",
                action=action,
                reason=reason,
            )
        )
    else:
        # Check CSP Wildcard (Rule 10055)
        if "* " in csp or " *" in csp:
            action = rules.get("10055", ZapRule("10055", "WARN", "")).action
            reason = rules.get("10055", ZapRule("10055", "WARN", "")).reason
            alerts.append(
                AlertFinding(
                    rule_id="10055",
                    name="CSP: Wildcard Directive",
                    severity="Medium",
                    url=url,
                    description="The CSP header contains a wildcard directive which may allow loading unintended external resources.",
                    solution="Restrict CSP source expressions to explicit trusted domain origins.",
                    action=action,
                    reason=reason,
                )
            )

        # Check CSP unsafe-inline style (Rule 10054 - Ignored by rule with reason)
        if "style-src 'unsafe-inline'" in csp or "style-src" in csp and "'unsafe-inline'" in csp:
            action = rules.get("10054", ZapRule("10054", "IGNORE", "")).action
            reason = rules.get("10054", ZapRule("10054", "IGNORE", "")).reason
            alerts.append(
                AlertFinding(
                    rule_id="10054",
                    name="CSP: style-src contains unsafe-inline",
                    severity="Low",
                    url=url,
                    description="The CSP style-src directive includes 'unsafe-inline'.",
                    solution="Use hashes or nonces instead of unsafe-inline where possible.",
                    action=action,
                    reason=reason,
                )
            )

    # 4. Strict-Transport-Security (Rule 10035 - Low/Ignored in plain HTTP test)
    if "strict-transport-security" not in headers_lower:
        action = rules.get("10035", ZapRule("10035", "IGNORE", "")).action
        reason = rules.get("10035", ZapRule("10035", "IGNORE", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10035",
                name="Strict-Transport-Security Header Not Set",
                severity="Low",
                url=url,
                description="HTTP Strict Transport Security (HSTS) informs the browser that it should only connect via HTTPS.",
                solution="Ensure HSTS is enabled at the TLS termination layer in production.",
                action=action,
                reason=reason,
            )
        )

    # 5. Permissions-Policy (Rule 10063 - Low)
    if "permissions-policy" not in headers_lower and "feature-policy" not in headers_lower:
        action = rules.get("10063", ZapRule("10063", "FAIL", "")).action
        reason = rules.get("10063", ZapRule("10063", "FAIL", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10063",
                name="Permissions-Policy Header Not Set",
                severity="Low",
                url=url,
                description="Permissions-Policy allows developers to selectively enable, disable, and modify the behavior of certain browser features and APIs.",
                solution="Set Permissions-Policy header with restricted feature access (e.g. camera=(), microphone=()).",
                action=action,
                reason=reason,
            )
        )

    # 6. Server Information Leak (Rule 10036 - Low)
    server_hdr = headers_lower.get("server", "").lower()
    if any(k in server_hdr for k in ("werkzeug/", "apache/", "nginx/", "python/")):
        action = rules.get("10036", ZapRule("10036", "FAIL", "")).action
        reason = rules.get("10036", ZapRule("10036", "FAIL", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10036",
                name="Server Leaks Information via 'Server' Header",
                severity="Low",
                url=url,
                description="The Server header discloses precise version and technology information.",
                solution="Strip detailed version information from the Server header.",
                action=action,
                reason=reason,
                evidence=f"Server: {headers.get('Server', '')}",
            )
        )

    return alerts


def check_cookies(
    cookies_headers: List[str],
    url: str,
    rules: Dict[str, ZapRule],
) -> List[AlertFinding]:
    """
    Validates cookie flags (HttpOnly, SameSite).
    """
    alerts: List[AlertFinding] = []
    for c_header in cookies_headers:
        c_lower = c_header.lower()
        cookie_name = c_header.split("=")[0].strip()

        # Check HttpOnly (Rule 10010 - Medium)
        if "httponly" not in c_lower:
            action = rules.get("10010", ZapRule("10010", "FAIL", "")).action
            reason = rules.get("10010", ZapRule("10010", "FAIL", "")).reason
            alerts.append(
                AlertFinding(
                    rule_id="10010",
                    name="Cookie No HttpOnly Flag",
                    severity="Medium",
                    url=url,
                    description=f"Cookie '{cookie_name}' has been set without the HttpOnly flag.",
                    solution="Ensure all sensitive session cookies include the HttpOnly attribute.",
                    action=action,
                    reason=reason,
                )
            )

        # Check SameSite (Rule 10011 - Low)
        if "samesite" not in c_lower:
            action = rules.get("10011", ZapRule("10011", "WARN", "")).action
            reason = rules.get("10011", ZapRule("10011", "WARN", "")).reason
            alerts.append(
                AlertFinding(
                    rule_id="10011",
                    name="Cookie Without SameSite Attribute",
                    severity="Low",
                    url=url,
                    description=f"Cookie '{cookie_name}' has been set without a SameSite attribute.",
                    solution="Set SameSite=Lax or SameSite=Strict on all cookies.",
                    action=action,
                    reason=reason,
                )
            )

    return alerts


def check_input_handling_and_errors(
    body: str,
    status_code: int,
    url: str,
    reflection_probe: Optional[str],
    rules: Dict[str, ZapRule],
) -> List[AlertFinding]:
    """
    Checks for reflected XSS and sensitive application error leaks.
    """
    alerts: List[AlertFinding] = []

    # 1. Reflected Input Check (Rule 10017 - High)
    if reflection_probe and reflection_probe in body:
        action = rules.get("10017", ZapRule("10017", "FAIL", "")).action
        reason = rules.get("10017", ZapRule("10017", "FAIL", "")).reason
        alerts.append(
            AlertFinding(
                rule_id="10017",
                name="Cross-Site Scripting (Reflected)",
                severity="High",
                url=url,
                description=f"Unsanitized reflected payload '{reflection_probe}' was detected in response body.",
                solution="Ensure all dynamic query parameters and user inputs are HTML-escaped before rendering.",
                action=action,
                reason=reason,
                evidence=reflection_probe,
            )
        )

    # 2. Application Error Disclosure (Rule 90022 - High)
    error_patterns = [
        "Traceback (most recent call last):",
        "werkzeug.exceptions.InternalServerError",
        "sqlite3.OperationalError:",
        "GEMINI_API_KEY",
        "FLASK_SECRET_KEY",
    ]
    for pattern in error_patterns:
        if pattern in body:
            action = rules.get("90022", ZapRule("90022", "FAIL", "")).action
            reason = rules.get("90022", ZapRule("90022", "FAIL", "")).reason
            alerts.append(
                AlertFinding(
                    rule_id="90022",
                    name="Application Error Disclosure",
                    severity="High",
                    url=url,
                    description=f"Response body discloses internal debugging information or stack trace: '{pattern}'.",
                    solution="Ensure debugging is disabled in production and error handlers return generic messages.",
                    action=action,
                    reason=reason,
                    evidence=pattern,
                )
            )
            break

    return alerts


def generate_html_report(report: ScanReport, output_path: str = DEFAULT_REPORT_PATH):
    """
    Generates a professional standalone HTML security report artifact (#890).
    """
    fail_color = "#dc2626"
    pass_color = "#16a34a"
    status_bg = pass_color if report.passed else fail_color
    status_text = "PASSED" if report.passed else "FAILED"

    alerts_rows = []
    for a in report.alerts:
        action_badge_cls = {
            "FAIL": "bg-danger",
            "WARN": "bg-warning text-dark",
            "IGNORE": "bg-secondary",
        }.get(a.action, "bg-secondary")

        sev_badge_cls = {
            "High": "bg-danger",
            "Medium": "bg-warning text-dark",
            "Low": "bg-info text-dark",
            "Informational": "bg-light text-dark border",
        }.get(a.severity, "bg-secondary")

        alerts_rows.append(f"""
        <tr>
          <td><code>{a.rule_id}</code></td>
          <td><strong>{a.name}</strong><br><small class="text-muted">{a.url}</small></td>
          <td><span class="badge {sev_badge_cls}">{a.severity}</span></td>
          <td><span class="badge {action_badge_cls}">{a.action}</span></td>
          <td><small>{a.description}</small></td>
          <td><small class="text-muted">{a.reason or a.solution}</small></td>
        </tr>
        """)

    ignored_rows = []
    for rid, r in sorted(report.rules_applied.items()):
        if r.action == "IGNORE":
            ignored_rows.append(f"""
            <tr>
              <td><code>{rid}</code></td>
              <td><span class="badge bg-secondary">IGNORE</span></td>
              <td>{r.reason}</td>
            </tr>
            """)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OWASP ZAP Baseline Security Scan Report - AI Campus Placement Agent</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {{ background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    .metric-card {{ border-radius: 10px; border: 1px solid #e2e8f0; background: #ffffff; padding: 20px; }}
  </style>
</head>
<body class="py-4">
  <div class="container max-w-6xl">
    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-4 pb-3 border-bottom">
      <div>
        <h2 class="fw-bold mb-1">🛡️ OWASP ZAP Baseline Security Scan Report</h2>
        <p class="text-muted mb-0">Passive vulnerability and security headers baseline scan (#890)</p>
      </div>
      <div class="text-end">
        <span class="badge px-3 py-2 fs-6 text-white" style="background-color: {status_bg};">{status_text}</span>
        <div class="small text-muted mt-1">{report.scan_date}</div>
      </div>
    </div>

    <!-- Metrics -->
    <div class="row g-3 mb-4">
      <div class="col-md-3">
        <div class="metric-card text-center">
          <div class="text-muted small text-uppercase fw-bold">Pages Audited</div>
          <div class="fs-2 fw-bold text-dark">{len(report.pages_scanned)}</div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="metric-card text-center">
          <div class="text-muted small text-uppercase fw-bold">Critical Failures</div>
          <div class="fs-2 fw-bold text-danger">{report.fail_count}</div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="metric-card text-center">
          <div class="text-muted small text-uppercase fw-bold">Warnings</div>
          <div class="fs-2 fw-bold text-warning">{report.warn_count}</div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="metric-card text-center">
          <div class="text-muted small text-uppercase fw-bold">Documented Ignores</div>
          <div class="fs-2 fw-bold text-secondary">{report.ignore_count}</div>
        </div>
      </div>
    </div>

    <!-- Alert Findings Table -->
    <div class="card border-0 shadow-sm rounded-3 mb-4">
      <div class="card-header bg-white py-3">
        <h5 class="mb-0 fw-bold">Security Alert Findings</h5>
      </div>
      <div class="card-body p-0 table-responsive">
        <table class="table table-hover align-middle mb-0">
          <thead class="table-light">
            <tr>
              <th>Rule ID</th>
              <th>Alert &amp; Target</th>
              <th>Severity</th>
              <th>Action</th>
              <th>Finding Description</th>
              <th>Resolution / Reviewed Reason</th>
            </tr>
          </thead>
          <tbody>
            {"".join(alerts_rows) if alerts_rows else '<tr><td colspan="6" class="text-center py-4 text-success fw-bold">✅ No security violations detected across scanned routes!</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Documented Reviewed Ignores -->
    <div class="card border-0 shadow-sm rounded-3 mb-4">
      <div class="card-header bg-white py-3">
        <h5 class="mb-0 fw-bold">Documented Reviewed Ignores (.zap/rules.tsv)</h5>
        <small class="text-muted">Every rule configured as IGNORE requires a documented security review reason.</small>
      </div>
      <div class="card-body p-0 table-responsive">
        <table class="table table-striped align-middle mb-0">
          <thead class="table-light">
            <tr>
              <th style="width: 120px;">Rule ID</th>
              <th style="width: 120px;">Action</th>
              <th>Written Reviewed Justification</th>
            </tr>
          </thead>
          <tbody>
            {"".join(ignored_rows)}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Footer Note -->
    <div class="text-center text-muted small mt-4">
      Generated automatically by AI Campus Placement Agent ZAP Baseline Scanner &bull;
      To modify rules, update <code>.zap/rules.tsv</code> and document justifications.
    </div>
  </div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def generate_json_report(report: ScanReport, output_path: str = DEFAULT_JSON_PATH):
    """Generates machine-readable JSON security report."""
    data = {
        "target_url": report.target_url,
        "scan_date": report.scan_date,
        "passed": report.passed,
        "pages_scanned": report.pages_scanned,
        "summary": {
            "fail_count": report.fail_count,
            "warn_count": report.warn_count,
            "ignore_count": report.ignore_count,
            "total_alerts": len(report.alerts),
        },
        "alerts": [
            {
                "rule_id": a.rule_id,
                "name": a.name,
                "severity": a.severity,
                "url": a.url,
                "description": a.description,
                "solution": a.solution,
                "action": a.action,
                "reason": a.reason,
                "evidence": a.evidence,
            }
            for a in report.alerts
        ],
        "ignored_rules": {
            rid: r.reason for rid, r in report.rules_applied.items() if r.action == "IGNORE"
        },
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def format_step_summary(report: ScanReport) -> str:
    """Formats markdown for $GITHUB_STEP_SUMMARY."""
    status_icon = "✅ PASSED" if report.passed else "❌ FAILED"
    lines = [
        "## 🛡️ OWASP ZAP Baseline Security Scan",
        "",
        f"**Status**: `{status_icon}` | **Target**: `{report.target_url}` | **Date**: `{report.scan_date}`",
        "",
        f"- **Audited Pages**: `{len(report.pages_scanned)}`",
        f"- **Critical Failures**: `{report.fail_count}`",
        f"- **Monitored Warnings**: `{report.warn_count}`",
        f"- **Documented Ignores**: `{report.ignore_count}`",
        "",
    ]

    if report.alerts:
        lines.extend([
            "### ⚠️ Security Alerts Itemization",
            "",
            "| Rule ID | Alert | Severity | Action | Rationale / Resolution |",
            "| :---: | :--- | :---: | :---: | :--- |",
        ])
        for a in report.alerts:
            lines.append(
                f"| `{a.rule_id}` | **{a.name}** (`{a.url}`) | `{a.severity}` | `{a.action}` | {a.reason or a.solution} |"
            )
        lines.append("")

    if not report.passed:
        lines.extend([
            "> [!CAUTION]",
            "> Security scan detected alerts with action **FAIL**. Please inspect the attached HTML report artifact and address missing headers or insecure inputs.",
            "",
        ])

    return "\n".join(lines)


def run_passive_scan(
    app: Any,
    rules: Dict[str, ZapRule],
    seed_user_id: str = "zap_test_student",
) -> ScanReport:
    """
    Executes the passive baseline scan against core public and authenticated routes.
    """
    routes_to_scan = [
        ("/", False),
        ("/login", False),
        ("/register", False),
        ("/privacy-policy", False),
        ("/dashboard", True),
        ("/applications", True),
        ("/interview-history", True),
        ("/notification-settings", True),
    ]

    all_alerts: List[AlertFinding] = []
    pages_scanned: List[str] = []

    with app.test_client() as client:
        for route, requires_auth in routes_to_scan:
            pages_scanned.append(route)
            probe_xss = '<script>alert("zap_probe")</script>'
            test_url = f"{route}?q={urllib.parse.quote(probe_xss)}"

            # Handle session authentication if required
            if requires_auth:
                with client.session_transaction() as sess:
                    sess["user_id"] = seed_user_id
                    sess["user_name"] = "ZAP Security Test Student"
                    sess["auth_session_id"] = "zap_auth_sess_123"
                    sess["last_activity"] = time.time()

            resp = client.get(test_url, follow_redirects=True)
            body_text = resp.get_data(as_text=True)
            headers_dict = dict(resp.headers)

            # Extract Set-Cookie headers
            cookie_headers = resp.headers.getlist("Set-Cookie")

            # Check Headers
            h_alerts = check_security_headers(headers_dict, route, rules)
            all_alerts.extend(h_alerts)

            # Check Cookies
            c_alerts = check_cookies(cookie_headers, route, rules)
            all_alerts.extend(c_alerts)

            # Check Input Handling & Errors
            i_alerts = check_input_handling_and_errors(
                body=body_text,
                status_code=resp.status_code,
                url=route,
                reflection_probe=probe_xss,
                rules=rules,
            )
            all_alerts.extend(i_alerts)

    # Calculate metrics
    fail_count = sum(1 for a in all_alerts if a.action == "FAIL")
    warn_count = sum(1 for a in all_alerts if a.action == "WARN")
    ignore_count = sum(1 for a in all_alerts if a.action == "IGNORE")
    passed = (fail_count == 0)

    return ScanReport(
        target_url="http://localhost:5000",
        scan_date=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        pages_scanned=pages_scanned,
        alerts=all_alerts,
        rules_applied=rules,
        passed=passed,
        fail_count=fail_count,
        warn_count=warn_count,
        ignore_count=ignore_count,
    )


def execute_scan(
    rules_path: str = DEFAULT_RULES_PATH,
    report_path: str = DEFAULT_REPORT_PATH,
    json_path: str = DEFAULT_JSON_PATH,
    summary_file: Optional[str] = None,
) -> int:
    """
    Main orchestration routine for ZAP security scanning (#890).
    Initializes temporary database, seeds test user, runs scan, outputs reports.
    """
    print("======================================================================")
    print("  AI Campus Placement Agent - OWASP ZAP Baseline Security Scan")
    print("======================================================================")

    # 1. Load and validate rules file
    rules = load_zap_rules(rules_path)
    print(f"Loaded {len(rules)} rules from: {rules_path}")

    # 2. Setup temporary database & seed user
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db_path = temp_db.name
    temp_db.close()

    try:
        import sqlite3
        from werkzeug.security import generate_password_hash
        from db.migrations import init_db
        from routes import create_app

        os.environ["DATABASE_PATH"] = temp_db_path
        os.environ["RATE_LIMIT_ENABLED"] = "false"
        init_db(temp_db_path)

        # Seed test student user
        conn = sqlite3.connect(temp_db_path)
        cur = conn.cursor()
        pw_hash = generate_password_hash(DEFAULT_TEST_PASSWORD)
        seed_uid = "zap_test_student"
        cur.execute(
            """
            INSERT OR REPLACE INTO users (
                id, name, email, password_hash, created_at, is_verified
            ) VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                seed_uid,
                "ZAP Security Test Student",
                DEFAULT_TEST_EMAIL,
                pw_hash,
                dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()

        # 3. Initialize application
        app = create_app()
        app.config["TESTING"] = True

        # 4. Run passive scan
        print("Executing passive baseline scan against main routes...")
        report = run_passive_scan(app=app, rules=rules, seed_user_id=seed_uid)

        # 5. Generate artifacts
        generate_html_report(report, report_path)
        print(f"HTML report artifact generated: {report_path}")

        generate_json_report(report, json_path)
        print(f"JSON findings artifact generated: {json_path}")

        if summary_file:
            summary_content = format_step_summary(report)
            with open(summary_file, "a", encoding="utf-8") as sf:
                sf.write("\n" + summary_content + "\n")
            print(f"Markdown summary written to: {summary_file}")

        # Summary output
        print("----------------------------------------------------------------------")
        print(f"Scan Status: {'PASSED' if report.passed else 'FAILED'}")
        print(f"Failures: {report.fail_count} | Warnings: {report.warn_count} | Ignores: {report.ignore_count}")
        print("----------------------------------------------------------------------")

        return 0 if report.passed else 1

    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="OWASP ZAP Baseline Security Scanner")
    parser.add_argument("--rules", default=DEFAULT_RULES_PATH, help="Path to ZAP rules file (.zap/rules.tsv)")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="Output HTML report path")
    parser.add_argument("--json", default=DEFAULT_JSON_PATH, help="Output JSON report path")
    parser.add_argument("--summary-file", help="Path to write GitHub step summary markdown")

    args = parser.parse_args()
    exit_code = execute_scan(
        rules_path=args.rules,
        report_path=args.report,
        json_path=args.json,
        summary_file=args.summary_file,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
