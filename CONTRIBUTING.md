# Contributing to AI Campus Placement Agent

Thank you for your interest in contributing to the AI Campus Placement Agent! We welcome contributions from the community to help improve this open-source project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [System Architecture](#system-architecture)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Contributing Guidelines](#contributing-guidelines)
- [Contributing Company Interview Preparation Guides](#contributing-company-interview-preparation-guides)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## System Architecture

Before submitting code changes or exploring new features, contributors are strongly encouraged to read the [System Architecture Guide](docs/architecture.md). It includes:
- **Request Flow**: From resume upload through parsing, sanitization, AI evaluation, and dashboard rendering.
- **Agent Pipeline**: Specialization of `CoordinatorAgent`, `ResumeAgent`, `RolePrepAgent`, `RoadmapAgent`, and `InterviewAgent`.
- **Interview Lifecycle**: State machine diagram covering session checkpoints, STAR grading, pausing, and transcripts.
- **Relational Schema**: Entity-Relationship (ER) diagram of core database tables (`users`, `analyses`, `interviews`, `job_applications`, `student_profiles`, etc.).

## Getting Started

### Prerequisites

- Python 3.9 or higher
- pip (Python package manager)
- Git

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/deepikagandla74/ai-campus-placement-agent.git
   cd ai-campus-placement-agent
   ```

## Development Setup

1. Install dependencies (including testing and linting tools):
   ```bash
   pip install -r requirements.txt
   pip install flake8 black isort pytest pytest-cov
   ```

2. Set up environment variables:
   - Copy `.env.example` (if available) or create a `.env` file
   - Add your Gemini API key:
     ```
     FLASK_SECRET_KEY=replace_with_a_long_random_secret
     GEMINI_API_KEY=your_gemini_api_key_here
     ```

3. Populate Synthetic Local Development Data (Optional but Recommended):
   Contributors can populate safe synthetic data to test dashboards, job applications, mock interviews, and reports without creating data manually or needing API keys:
   ```bash
   # Seed an isolated local development database
   python utils/seed_data.py
   # or
   flask seed run

   # Test logins:
   # Student: contributor.demo@seed.local / Password123!
   # Admin:   admin.demo@seed.local / AdminPassword123!
   ```

4. Run the application:
   ```bash
   # To use the seeded development database:
   # Linux / macOS:
   export DATABASE_PATH="placement_agent_dev.db" && python app.py
   # Windows (PowerShell):
   $env:DATABASE_PATH="placement_agent_dev.db"; python app.py
   ```

5. Visit `http://127.0.0.1:5000/` in your browser

6. Verify local tests, static typing, and lint checks (matching CI workflow):
   ```bash
   # Run flake8 critical syntax and undefined name checks
   flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics

   # Run automated pytest test suite
   pytest

   # Run mypy static type check on strict utility modules
   mypy --config-file mypy.ini utils/text_cleaner.py utils/score_explanations.py utils/input_validator.py utils/email_domain_validator.py utils/sri_validator.py

   # Run pre-flight diagnostic validators
   python utils/ci_pipeline_validator.py
   python utils/deployment_check.py
   ```

### Static Type Checking Policy (mypy)

We are gradually adopting static type annotations with `mypy` to improve code clarity and catch type bugs early:
1. **New Python Files Must Be Fully Typed**:
   All new functions, classes, and methods introduced in new modules must include complete argument and return type annotations.
2. **Strict Modules List**:
   Cleaned utility modules (`utils/text_cleaner.py`, `utils/score_explanations.py`, `utils/input_validator.py`, `utils/email_domain_validator.py`, `utils/sri_validator.py`) are strictly verified in CI. In these modules, removing type annotations or introducing type mismatches will fail CI.
3. **How to Add a Module to the Strict List**:
   - Annotate all functions, parameters, and return values in the target module.
   - Run `mypy --config-file mypy.ini path/to/module.py` locally and ensure it passes cleanly.
   - Add a strict section for your module in `mypy.ini`:
     ```ini
     [mypy-your_package.your_module]
     disallow_untyped_defs = true
     disallow_incomplete_defs = true
     check_untyped_defs = true
     no_implicit_optional = true
     warn_unused_ignores = true
     ```
   - Add the module path to the `Run Mypy Gradual Static Type Check` step in `.github/workflows/ci.yml` and `CLEAN_MODULES` in `tests/test_mypy_typing.py`.

## Contributing Guidelines

### What Can I Contribute?

We welcome contributions in the following areas:

- **Bug fixes**: Help us squash bugs and improve stability
- **New features**: Propose and implement new placement-related features
- **Documentation**: Improve documentation, add examples, or write tutorials
- **UI/UX improvements**: Enhance the user interface and experience
- **Performance optimizations**: Make the application faster and more efficient
- **Test coverage**: Add tests to improve code reliability

### Working on an Issue

Before starting a substantial change, open or claim an issue and confirm the proposed approach with the maintainers. This helps avoid duplicate work and keeps contributions aligned with the project.

Issues intended for new contributors should explain the problem, expected outcome, relevant files or routes, reproduction steps where applicable, and how the change can be verified. Maintainers should label issues by scope and difficulty only when those labels are meaningful and the task is ready to work on.

For NSoC Project Kernel consideration, the project maintainer is responsible for keeping real, well-scoped issues for different experience levels, reviewing pull requests, and helping contributors understand the codebase. These are responsibilities described on the [official NSoC site](https://www.nsoc.in/); they are not a promise of acceptance into the program.

### Development Workflow

1. Create a new branch for your feature or bugfix:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/your-bugfix-name
   ```

2. Make your changes following our coding standards:
   - Follow PEP 8 style guidelines
   - Write clear, descriptive commit messages
   - Add comments for complex logic
   - Update relevant documentation

3. Test your changes thoroughly:
   - Run linter: `flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics`
   - Run tests: `pytest`
   - Run diagnostics: `python utils/ci_pipeline_validator.py` and `python utils/deployment_check.py`
   - Ensure the automated CI workflow will pass without lint errors or test failures
   - Test the specific feature you modified and check for regressions

4. Commit your changes:
   ```bash
   git add .
   git commit -m "Short description of your changes"
   ```

### Coding Standards

- **Python**: Follow PEP 8 style guide
- **HTML/CSS/JS**: Follow modern best practices
- **Comments**: Add docstrings to functions and classes
- **Variable naming**: Use descriptive, meaningful names
- **Error handling**: Include proper error handling and logging

### Subresource Integrity (SRI) for Frontend Libraries (#648)

To protect users against Content Delivery Network (CDN) tampering or malicious resource manipulation, **all external scripts and stylesheets loaded in `templates/` must use Subresource Integrity (SRI)**.

#### Requirements for External Resources:
1. **Pinned Version**: URLs must reference an exact semantic version (e.g. `@5.3.0` or `@4.6.1`). Floating tags such as `latest`, major-only specifiers like `@5/`, or unversioned URLs are prohibited.
2. **Integrity Attribute**: Tags must define `integrity="sha384-..."` (or `sha256-` / `sha512-`).
3. **Crossorigin Attribute**: Tags must specify `crossorigin="anonymous"`.
4. **CSP Compatibility**: External hostnames must already be permitted in the Content-Security-Policy (CSP) headers in `routes/__init__.py`. Currently allowed origins include `cdn.jsdelivr.net` and `cdnjs.cloudflare.com`.

#### How to Compute an SRI Hash for a New Library:

When introducing a new library or upgrading an existing one, compute the cryptographic SHA-384 digest of the exact file:

- **Using cURL and OpenSSL (Linux / macOS / Git Bash)**:
  ```bash
  curl -sSL "https://cdn.jsdelivr.net/npm/package@1.2.3/dist/file.min.js" | openssl dgst -sha384 -binary | openssl base64 -A
  ```

- **Using Python**:
  ```bash
  python -c "import urllib.request, hashlib, base64; d = urllib.request.urlopen('https://cdn.jsdelivr.net/npm/package@1.2.3/dist/file.min.js').read(); print('sha384-' + base64.b64encode(hashlib.sha384(d).digest()).decode())"
  ```

- **Using Online Generators**:
  You can also use tools like [SRI Hash Generator](https://www.srihash.org/) or the integrity string provided on [cdnjs.com](https://cdnjs.com/).

#### Example Tag Usage:
```html
<!-- Script -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"
        integrity="sha384-geWF76RCwLtnZ8qwWowPQNguL3RmwHVBC9FhGdlKrxdiJJigb/j/68SIy3Te4Bkz"
        crossorigin="anonymous"></script>

<!-- Stylesheet -->
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
      rel="stylesheet"
      integrity="sha384-9ndCyUaIbzAi2FUVXJi0CjmCapSmO7SnpJef0486qhLnuZ2cdeRhO02iuK6FUUVM"
      crossorigin="anonymous">
```

Run the validator locally to confirm compliance:
```bash
python utils/sri_validator.py
```

### Property-Based Testing with Hypothesis (#692)

To ensure pure functions in the platform (parsers, sanitizers, validators, regional formatters, and scoring engines) handle arbitrary user inputs, Unicode artifacts, boundary values, and extreme numbers without crashing or returning invalid results, we use **[Hypothesis](https://hypothesis.readthedocs.io/)** for property-based testing.

Unlike example-based tests that only check inputs anticipated by developers, property-based tests randomly generate hundreds of permutations (including empty strings, unusual Unicode symbols, ligature characters, extreme numeric values, and special file paths) and automatically shrink any failing input down to the minimal reproducible case.

#### Running Property-Based Tests Locally:
```bash
# Run the complete property-based test suite
pytest tests/test_property_based.py -v

# Run with custom verbosity or statistics
pytest tests/test_property_based.py -v --hypothesis-show-statistics
```

#### How to Add or Extend a Property-Based Test:

When writing or modifying pure utility functions, validators, or calculation logic:

1. **Target Pure Functions**: Focus on functions that take inputs and produce outputs without side-effects (e.g. database transactions or network/API calls).
2. **Define Invariants / Properties**: Identify properties that must hold true for *all* valid or arbitrary inputs:
   - **No Unhandled Crashes**: Function always returns the expected type (`str`, `dict`, `int`) without raising unhandled exceptions on arbitrary strings.
   - **Score Bounds**: Numeric scores (e.g. ATS score, match score, bullet impact) must strictly stay within bounds (e.g. `0 <= score <= 100`).
   - **Determinism**: Repeating the function on the exact same input produces identical output.
   - **Idempotence**: `fn(fn(x)) == fn(x)` (e.g. text normalization, filename sanitization, skill canonicalization).
   - **Length & Security Contracts**: Outputs respect maximum length constraints and strip path traversal / injection markers.
3. **Keep Examples Fast for CI**: Use `@settings(max_examples=40, deadline=None)` or `@settings(max_examples=50, deadline=None)` so the full suite runs in under 10 seconds without slowing down CI.
4. **Capture Failing Examples as Regression Tests**: Whenever Hypothesis identifies an edge case bug (such as `decimals=0` or `NaN`/`Infinity` floats in formatters), fix the underlying function and commit the minimal failing example to `TestHypothesisDiscoveredRegressions` in `tests/test_property_based.py`.

#### Example Property-Based Test Pattern:
```python
from hypothesis import given, settings, strategies as st
from utils.file_validation_service import sanitize_filename

@settings(max_examples=50, deadline=None)
@given(
    raw_name=st.one_of(st.text(max_size=300), st.none()),
    max_length=st.integers(min_value=10, max_value=200)
)
def test_prop_sanitize_filename_invariants(raw_name: str | None, max_length: int):
    clean = sanitize_filename(raw_name, max_length=max_length)
    assert isinstance(clean, str)
    assert 0 < len(clean) <= max_length
    assert "/" not in clean and "\\" not in clean and "\x00" not in clean
    # Idempotent
    assert sanitize_filename(clean, max_length=max_length) == clean
```

### End-to-End Browser UI Testing with Playwright (#858)

To guard against integration breakages across HTML templates, frontend JavaScript modules, CSS stylesheets, and backend route workflows, we maintain automated end-to-end (E2E) browser UI smoke tests powered by **[Playwright](https://playwright.dev/python/)**.

The browser test walks through the entire candidate journey without requiring live external network access or real API keys:
1. Student registration with credential validation (`/register`).
2. Authentication and session management (`/login`).
3. Uploading a resume PDF and consenting to AI processing (`/`).
4. Multi-agent evaluation and real-time candidate dashboard rendering (`/dashboard/<analysis_id>`).
5. Launching an interactive mock interview session (`/interview/<analysis_id>`).
6. Session termination and secure logout (`/logout`).

#### Running Playwright Tests Locally:

1. **Install Playwright and Pytest Extension**:
   ```bash
   pip install pytest-playwright playwright
   ```

2. **Install Browser Binaries (Chromium)**:
   ```bash
   playwright install chromium
   ```

3. **Execute the E2E Test Suite**:
   ```bash
   pytest tests/test_playwright_student_journey.py -v
   ```

4. **Capture Traces and Screenshots on Failure**:
   ```bash
   pytest tests/test_playwright_student_journey.py -v --tracing=retain-on-failure --screenshot=only-on-failure --output=test-results
   ```

5. **Inspect Test Traces with Playwright Trace Viewer**:
   If a test fails, launch the interactive trace viewer to inspect the visual DOM, console logs, and network events at every step:
   ```bash
   playwright show-trace test-results/.../trace.zip
   ```

### Route Organization & Where to Add New Routes (#553)

The application has been refactored from a monolithic `app.py` into modular Flask Blueprints grouped by feature area inside the `routes/` package. **`app.py` is a thin entry point** exporting `app = create_app()` and should not have new routes added to it directly.

When adding or modifying routes, place them in the corresponding Blueprint module:

- **`routes/auth.py`**: Authentication, login/logout, registration, two-factor auth (2FA), session lifecycle, password recovery.
- **`routes/analysis.py`**: Resume uploads, ATS evaluation, resume diff/comparisons, dashboards, role skill gaps.
- **`routes/interview.py`**: Mock interviews, speech & STAR evaluation, aptitude practice, group discussions, and interview preparation notes.
- **`routes/coding_practice.py`**: Coding problems, sandbox execution, problem bookmarks, submission tracking.
- **`routes/tracker.py`**: Job application tracking pipelines, placement milestones, calendar deadlines, and reminder preferences.
- **`routes/jobs.py`**: Job recommendations, company profiles, internship feeds, GitHub/LinkedIn profile analyzers.
- **`routes/reports.py`**: PDF reports, certificates, skills gap downloads, watermarking, and read-only report sharing.
- **`routes/admin.py`**: Administrative AI metrics, Gemini usage tracking, database backups, and moderation queue.
- **`routes/api.py`**: Shared/utility endpoints (health checks, system status, i18n translations, walkthroughs).
- **`routes/common.py`**: Shared constants (`APPLICATION_STAGES`, `WALKTHROUGH_STEPS_CATALOG`, caches), agent instances, and helper functions.

Use the Blueprint decorator for your area (e.g. `@interview_bp.route("/path")` rather than `@app.route(...)`). URL endpoints remain backwards-compatible across templates and tests with automatic alias mapping.

## Contributing Company Interview Preparation Guides

We welcome contributions of new company interview guides, hiring process breakdowns, commonly asked questions, and verified campus recruitment criteria.

### Company Data Locations

Company data is stored as JSON in the `data/` directory:
- **`data/companies.json`**: Comprehensive recruitment and hiring guide profiles keyed by canonical company name.
- **`data/company_interview_prep.json`**: Interview preparation guides, typical rounds, and tested topics.
- **`data/company_contribution_template.json`**: A reference template showing the exact JSON format for submitting a new company profile.

### Schema Requirements

When contributing or updating a company entry, make sure all required fields are present and properly typed according to the formal JSON Schema in [`data/schemas/company.schema.json`](data/schemas/company.schema.json) (#855):

#### 1. Hiring Guide Profiles (`data/companies.json`)
- `overview` (`string`, required): Summary of the company, engineering culture, and work domains (minimum 10 characters).
- `hiring_process` (`string`, required): Step-by-step description of recruitment stages (e.g. Online Assessment -> Technical -> Managerial -> HR).
- `technical_rounds` (`string`, required): Breakdown of coding, system design, or domain-specific questions asked.
- `hr_rounds` (`string`, required): Behavioral and cultural round expectations.
- `frequent_topics` (`list` of non-empty `string`s, required): Core topics commonly tested (e.g. `["DSA", "System Design", "SQL"]`). Must not be empty.
- `required_skills` (`list` of non-empty `string`s, required): Essential technical and soft skills. Must not be empty.
- `prep_tips` (`string`, required): Actionable advice for students preparing for this company's hiring drive.
- `eligibility` (`string`, required): CGPA cutoffs, allowed backlogs, and eligible academic branches.
- `last_updated` (`string`, required): Revision date in strict ISO 8601 calendar format (`YYYY-MM-DD`, e.g. `2026-11-20`).
- `source_name` (`string`, required): Name of the reference source or careers portal (e.g. `Google Careers`).
- `source_url` (`string`, required): Canonical URL starting with `http://` or `https://` with a valid domain.
- `min_cgpa` (`number`, optional): Minimum CGPA threshold (e.g. `6.5` or `7.0`).
- `max_backlogs` (`integer`, optional): Maximum allowed active backlogs (e.g. `0`).
- `branches` (`list` of `string`s, optional): Allowed branches (e.g. `["All"]` or `["CS", "IT", "ECE"]`).
- `eligibility_source` (`string`, optional): Source note for the eligibility criteria.

> **Note on Unique Names**: Company names must be unique (case-insensitively). Duplicate keys in the JSON or case-insensitive duplicate names will fail the automated CI schema check.

#### 2. Interview Prep Profiles (`data/company_interview_prep.json`)
- `company_name` (`string`): Display name of the company.
- `typical_rounds` (`list` of non-empty `string`s): Ordered list of interview stages.
- `commonly_tested_topics` (`list` of non-empty `string`s): Key technical domains tested.
- `preparation_tips` (`list` of non-empty `string`s): Specific tips for candidates.
- `last_updated` (`string`): Revision date in strict ISO 8601 calendar format (`YYYY-MM-DD`).

### Running the Schema & Company Data Validator

Before opening a pull request that modifies or adds company data, run the automated CI validation test to ensure there are no syntax errors, missing fields, duplicate company names, or broken URLs:

```bash
# Run the companies.json schema validation test
pytest tests/test_companies_schema.py

# Validate standard company data files in data/
python utils/company_data_validator.py

# Or run using the scripts runner
python scripts/validate_company_data.py

# Or run via Flask CLI
flask validate-company-data
```

#### Advanced Validator Options:

- **Check specific files or custom contributor submissions**:
  ```bash
  python scripts/validate_company_data.py path/to/my_company.json
  ```
- **Perform live URL verification** (confirms that links are reachable over HTTP/HTTPS):
  ```bash
  python scripts/validate_company_data.py --live
  ```
- **Strict mode** (treats warnings as fatal errors):
  ```bash
  python scripts/validate_company_data.py --strict
  ```
- **Structured JSON output** (useful for CI and pre-commit automation):
  ```bash
  python scripts/validate_company_data.py --json
  ```
- **Quiet mode** (only reports errors when validation fails):
  ```bash
  python scripts/validate_company_data.py --quiet
  ```

### Understanding Validator Error Output

The validator pinpoints the exact file, company, and field name for each issue so you can resolve it quickly:

```text
[FAIL] data/companies.json (Errors: 1, Warnings: 0):
  1. File   : data/companies.json
     Company: [ExampleCorp]
     Field  : 'source_url'
     Type   : invalid_url
     Issue  : Invalid URL in 'source_url': Invalid scheme 'ftp'. URL must start with http:// or https://.
     Value  : 'ftp://example.com'
     Remedy : Provide a valid canonical URL starting with http:// or https:// with a valid domain name.
```

## Contributing Learning Resources (#885)<a id="suggest-resource"></a>

To help students learn effectively without losing time searching or paying for resources, the project provides a curated free learning resources catalog in `data/learning_resources.json`.

### How It Works
- Resources are mapped by technical skill (e.g. `Python`, `Docker`, `SQL`, `React`, `System Design`).
- Under each skill gap in the analysis dashboard and inside roadmap weeks, students receive matching curated free resources.
- Students can track progress by marking each resource as **To Do**, **Started**, or **Done**.

### Guidelines for Suggesting a Resource
1. **Free Only**: All suggested resources must be 100% free (no paywalls, required credit cards, or trial-only locks).
2. **HTTPS Only**: Links must use strict `https://` schemes.
3. **High Quality**: Documentation, interactive tutorials, reputable video playlists, or open-source guides.
4. **Required Fields**:
   - `id`: Lowercase identifier with hyphens (e.g., `py-official-tutorial`).
   - `title`: Clear title of the resource.
   - `link`: Direct canonical HTTPS link.
   - `type`: One of `documentation`, `tutorial`, `interactive`, `video`, `book`, or `course`.
   - `estimated_hours`: Positive number indicating estimated study time.
   - `review_note`: Concise peer review note explaining why this resource is valuable.
5. **Schema Validation**: All resources are validated against `data/schemas/learning_resources.schema.json` in automated CI tests (`tests/test_learning_resources_schema.py`).


## Pull Request Process

1. **Update Documentation**: If your changes affect functionality, update the README or relevant documentation

2. **Commit Messages**: Use clear, descriptive commit messages:
   - `feat: add new interview evaluation feature`
   - `fix: resolve PDF parsing error for certain formats`
   - `docs: update installation instructions`
   - `refactor: simplify database initialization`

3. **Push to Your Fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

4. **Create Pull Request**:
   - Go to the original repository on GitHub
   - Click "New Pull Request"
   - Select your branch
   - Fill out the PR template with details about your changes
   - Link related issues using `#issue-number`

5. **Review Process**:
   - Maintainers will review your PR
   - Address any feedback or requested changes
   - Once approved, your PR will be merged

### PR Checklist

Before submitting your PR, ensure:

- [ ] Code follows the project's style guidelines
- [ ] Changes are tested; PR describes what was run and any checks not run
- [ ] Documentation is updated if needed
- [ ] Commit messages are clear and descriptive
- [ ] No merge conflicts with the main branch
- [ ] PR description explains the "why" and "what" of changes
- [ ] PR is linked to a relevant issue or explains why no issue was needed

## Reporting Issues

### Bug Reports

When reporting a bug, please include:

- **Title**: Clear, descriptive bug title
- **Description**: Detailed explanation of the bug
- **Steps to Reproduce**: Clear steps to reproduce the issue
- **Expected Behavior**: What you expected to happen
- **Actual Behavior**: What actually happened
- **Screenshots**: If applicable, add screenshots

### Feature Requests

For feature requests, please include:

- **Title**: Clear feature title
- **Description**: Detailed description of the feature
- **Use Case**: Why this feature would be useful
- **Proposed Solution**: How you envision the feature working
- **Alternatives**: Any alternative solutions considered

## Questions

If you have questions about contributing:

- Check existing [Issues](https://github.com/deepikagandla74/ai-campus-placement-agent/issues)
- Open an [Issue](https://github.com/deepikagandla74/ai-campus-placement-agent/issues) with your question
- Contact maintainers through the repository

## Recognition

Contributors will be recognized in the project's CONTRIBUTORS file.

Thank you for contributing to AI Campus Placement Agent! 🚀
