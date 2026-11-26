# Secret Scanning & Static AST Security Analysis Guide

This document explains how secret scanning (`Gitleaks`) and Python static AST security analysis (`Bandit`) are integrated into the AI Campus Placement Agent repository, how to run them locally, and how to resolve or document findings and false positives.

---

## 1. Overview

To protect user data and prevent regressions:
- **Gitleaks**: Scans pull request commit diffs for accidentally committed credentials, private keys, access tokens, and API secrets.
- **Bandit**: Analyzes the Python code AST for common security anti-patterns (such as `subprocess` with `shell=True`, weak cryptographic hashing without `usedforsecurity=False`, unsafe temp file usage, or `eval`/`exec`).
- **Baseline Protection**: Bandit uses `.bandit-baseline.json` so existing known acceptable findings do not fail CI, but **any newly introduced HIGH-severity finding immediately fails the build**.

---

## 2. Configuration Files

1. **`.bandit.yaml`**:
   Configures directories excluded from Bandit scanning (`tests`, `venv`, `.venv`, `.git`, `instance`, `data`, `test-results`).
2. **`.bandit-baseline.json`**:
   The baseline report containing known acceptable findings. Bandit compares current code against this baseline using `-b .bandit-baseline.json`.
3. **`.gitleaks.toml`**:
   Specifies secret scanning rules, allowlisted paths (e.g., fixtures, documentation examples, tests), and placeholder stopwords (`dummy_key`, `fake_key`, `test_secret`, `sample_api_key`, `your_gemini_api_key_here`).
4. **`.github/workflows/security-static-analysis.yml`**:
   Runs automatically on push and pull requests to `main`.
   - Executes `gitleaks-action` on PR commits/diffs.
   - Executes Bandit with the baseline and prints a formatted summary to `$GITHUB_STEP_SUMMARY`.

---

## 3. Running Locally

### A. Bandit Static Security Scan

Install Bandit:
```bash
pip install bandit
```

Run Bandit with the project configuration and baseline:
```bash
# Report only newly introduced HIGH-severity findings (same as CI):
bandit -r . -c .bandit.yaml -b .bandit-baseline.json -lll

# Or run the CI helper script:
python utils/run_bandit_scan.py .bandit-baseline.json .bandit.yaml
```

Run a full scan across the codebase (including low and medium findings):
```bash
bandit -r . -c .bandit.yaml -ll
```

### B. Gitleaks Secret Scanning

Install Gitleaks:
- **macOS / Linux (Homebrew)**: `brew install gitleaks`
- **Windows (Scoop / Chocolatey)**: `scoop install gitleaks` or `choco install gitleaks`
- **Docker**:
  ```bash
  docker run -v ${PWD}:/path zricethezav/gitleaks:latest detect --source="/path" -c "/path/.gitleaks.toml" -v
  ```

Run Gitleaks locally:
```bash
# Scan uncommitted changes / working tree:
gitleaks detect --source . -c .gitleaks.toml -v

# Scan git commit log:
gitleaks detect --source . -c .gitleaks.toml --log-opts="-1" -v
```

---

## 4. Handling Findings & False Positives

### Handling Bandit Findings

1. **Fix the Vulnerability (Preferred)**:
   - For `subprocess.Popen(..., shell=True)`: pass arguments as a list with `shell=False`.
   - For `hashlib.md5(...)` or `hashlib.sha1(...)` used for non-cryptographic cache keys or hashes: pass `usedforsecurity=False` (in Python 3.9+).
   - For `eval(...)` / `exec(...)`: replace with safe parsers like `ast.literal_eval` or `json.loads`.

2. **Suppressing False Positives Inline (`# nosec`)**:
   If a finding is an intentional, verified design choice that cannot be exploited, append `# nosec <test_id>` with an explanatory comment on the same or preceding line:
   ```python
   # Intended non-cryptographic checksum for cache key
   cache_key = hashlib.md5(data.encode(), usedforsecurity=False).hexdigest()  # nosec B324
   ```

3. **Updating the Baseline**:
   When new code is reviewed and approved by maintainers, or after a major refactoring:
   ```bash
   bandit -r . -c .bandit.yaml -f json -o .bandit-baseline.json
   ```
   Commit the updated `.bandit-baseline.json` with a clear explanation in your pull request description.

### Handling Gitleaks Findings

1. **Rotate Leaked Secrets Immediately**:
   If an actual secret or API token was committed, treat it as compromised and rotate it immediately at the provider.
2. **Remove Secret from Git History**:
   Do not just commit a deletion; use `git filter-repo` or BFG to purge the secret from the git ref history if pushed.
3. **Legitimate Test Placeholders / Docs**:
   Use dummy placeholders specified in `.gitleaks.toml` (`dummy_key`, `your_gemini_api_key_here`, etc.) or add safe files to the `paths` allowlist in `.gitleaks.toml`.

---

## 5. Verification

You can verify the security setup anytime by running the automated pytest suite:
```bash
pytest tests/test_secret_and_static_security.py -v
```
