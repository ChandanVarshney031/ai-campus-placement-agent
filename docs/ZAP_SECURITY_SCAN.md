# OWASP ZAP Baseline Security Scanning Guide (#890)

## Overview

To prevent security regressions, missing HTTP headers, insecure cookie configurations, and unescaped input reflection from reaching production, the AI Campus Placement Agent runs automated **OWASP ZAP Baseline Passive Security Scans** in continuous integration and locally.

The baseline scan inspects HTTP responses, cookies, and headers without conducting active attack exploits.

---

## Architecture & Components

1. **Rules Configuration (`.zap/rules.tsv`)**:
   Standard ZAP tab-separated rules file defining actions for specific alert plugins:
   - `FAIL`: Triggers a build failure on detection (e.g. missing `X-Content-Type-Options`, `Content-Security-Policy`, `X-Frame-Options`, `Permissions-Policy`, reflected XSS, or stack trace disclosure).
   - `WARN`: Surfaces a warning in the report without immediately halting execution.
   - `IGNORE`: Ignores the alert. **Every ignored rule must have a written, reviewed justification documented in `.zap/rules.tsv`.**

2. **Scanner Pipeline (`utils/zap_scanner.py`)**:
   - Initializes an isolated temporary SQLite database and applies all migrations.
   - Seeds a dedicated test student account (`zap.security.student@campus.edu`).
   - Audits unauthenticated routes (`/`, `/login`, `/register`, `/privacy-policy`) and authenticated routes (`/dashboard`, `/applications`, `/interview-history`, `/notification-settings`).
   - Evaluates response headers, cookie attributes, and reflected inputs.
   - Generates standalone HTML (`zap-report.html`) and JSON (`zap-report.json`) report artifacts.

3. **CI Automation (`.github/workflows/ci.yml`)**:
   - Runs on every pull request and push to `main`.
   - Executes rules verification tests via `pytest tests/test_zap_baseline_scan.py`.
   - Runs `python utils/zap_scanner.py`.
   - Publishes an itemized summary to `$GITHUB_STEP_SUMMARY`.
   - Uploads `zap-report.html` as a workflow artifact (`zap-baseline-report`).

---

## Running the Scan Locally

### Method 1: Built-in Python Runner (Recommended, No Docker Required)

You can run the full baseline scan directly on your local machine using Python:

```bash
# From the repository root
python utils/zap_scanner.py --rules .zap/rules.tsv --report zap-report.html --json zap-report.json
```

#### Custom Options:
- `--rules <path>`: Path to rules file (default: `.zap/rules.tsv`).
- `--report <path>`: HTML report destination (default: `zap-report.html`).
- `--json <path>`: JSON findings destination (default: `zap-report.json`).
- `--summary-file <path>`: Optional markdown summary destination.

---

### Method 2: Running with Official OWASP ZAP Docker Container

If Docker is installed on your workstation, you can run the official ZAP container:

1. **Start the local application**:
   ```bash
   python app.py
   ```

2. **Execute the ZAP Baseline container**:
   ```bash
   # On Linux / macOS:
   docker run --rm -v $(pwd):/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
     -t http://host.docker.internal:5000 \
     -c .zap/rules.tsv \
     -r zap-report.html \
     -J zap-report.json

   # On Windows (PowerShell):
   docker run --rm -v ${PWD}:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py `
     -t http://host.docker.internal:5000 `
     -c .zap/rules.tsv `
     -r zap-report.html `
     -J zap-report.json
   ```

---

## Verifying Rules and Tests

Automated tests in `tests/test_zap_baseline_scan.py` enforce:
1. Every rule configured with `IGNORE` in `.zap/rules.tsv` has a documented explanation.
2. A deliberately missing security header (`X-Content-Type-Options`, `Content-Security-Policy`, `X-Frame-Options`) triggers an alert with action `FAIL` and fails the job.
3. Unescaped reflected input triggers a `FAIL` action.
4. Fully compliant headers produce zero `FAIL` alerts and generate clean report artifacts.

Run the test suite:
```bash
pytest tests/test_zap_baseline_scan.py -v
```

---

## Documented Ignored Rules Rationale

| Rule ID | Name | Action | Documented Rationale |
| :---: | :--- | :---: | :--- |
| `10035` | Strict-Transport-Security (HSTS) | `IGNORE` | Local test and CI container environments operate over plain HTTP; TLS termination and HSTS enforcement are handled upstream by the reverse proxy/Cloudflare in production. |
| `10015` | Incomplete or No Cache-Control Header | `IGNORE` | Public static assets (Bootstrap CSS, icons, images, JS bundles) and login assets intentionally allow browser caching for performance. Sensitive routes use `Cache-Control: no-store` via `apply_cache_headers`. |
| `10027` | Information Disclosure - Suspicious Comments | `IGNORE` | Non-sensitive HTML template comments and contributor guidance are retained in non-sensitive client templates for developer onboarding. |
| `10096` | Timestamp Disclosure | `IGNORE` | Epoch timestamps are exposed in non-sensitive public session timestamps and cache-busting asset query parameters. |
| `10054` | CSP: style-src unsafe-inline | `IGNORE` | Required for inline styling in Bootstrap components, KaTeX math typesetting, dynamic theme cards, and email preview widgets. |
| `10109` | Modern Web Application | `IGNORE` | Single-page application navigation patterns and AJAX endpoints are expected. |
