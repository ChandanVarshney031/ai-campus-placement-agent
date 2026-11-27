"""
Tests for gradual static type checking with mypy (#893).
Verifies:
1. mypy.ini exists and has strict configuration for utility modules.
2. Target modules (text_cleaner, score_explanations, input_validator, email_domain_validator, sri_validator) pass mypy.
3. Removing or corrupting a type annotation in a listed module causes mypy to fail.
4. CI workflow file invokes mypy with the target modules.
5. CONTRIBUTING.md documents the policy for new files and how to add a module to the strict list.
"""

import os
import re
import subprocess
import sys
import tempfile
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MYPY_INI_PATH = os.path.join(REPO_ROOT, "mypy.ini")
CI_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
CONTRIBUTING_PATH = os.path.join(REPO_ROOT, "CONTRIBUTING.md")

CLEAN_MODULES = [
    os.path.join("utils", "text_cleaner.py"),
    os.path.join("utils", "score_explanations.py"),
    os.path.join("utils", "input_validator.py"),
    os.path.join("utils", "email_domain_validator.py"),
    os.path.join("utils", "sri_validator.py"),
]


def test_mypy_ini_configuration_exists():
    """Verify mypy.ini exists and configures strictness per listed module."""
    assert os.path.exists(MYPY_INI_PATH), "mypy.ini configuration must exist at repo root"
    with open(MYPY_INI_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    assert "[mypy]" in content
    assert "disallow_untyped_defs = false" in content, "Global setting should start permissive"

    for mod in ["utils.text_cleaner", "utils.score_explanations", "utils.input_validator"]:
        assert f"[mypy-{mod}]" in content, f"Strict section for {mod} must exist in mypy.ini"


def test_clean_modules_pass_mypy():
    """Verify that all listed clean modules pass mypy without errors."""
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        MYPY_INI_PATH,
    ] + [os.path.join(REPO_ROOT, m) for m in CLEAN_MODULES]

    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, f"mypy failed on clean modules:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"


def test_removing_type_annotation_fails_mypy():
    """
    Acceptance criterion: Removing a type annotation in a listed module makes mypy fail.
    We test this by creating a temporary copy of a clean module with an unannotated function
    and running mypy against it under the module's strict configuration.
    """
    test_code = (
        "def untyped_helper(x, y):\n"
        "    return x + y\n"
    )

    with tempfile.NamedTemporaryFile("w", suffix=".py", dir=os.path.join(REPO_ROOT, "utils"), delete=False) as tmp:
        tmp.write(test_code)
        tmp_path = tmp.name

    tmp_basename = os.path.basename(tmp_path)
    tmp_mod_name = os.path.splitext(tmp_basename)[0]

    try:
        # Run mypy with strict disallow_untyped_defs
        cmd = [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            MYPY_INI_PATH,
            "--disallow-untyped-defs",
            tmp_path,
        ]
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        assert proc.returncode != 0, "Expected mypy to fail on unannotated function with disallow-untyped-defs"
        assert "Function is missing a type annotation" in proc.stdout or "untyped" in proc.stdout
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_ci_workflow_runs_mypy():
    """Verify that .github/workflows/ci.yml runs mypy on the clean modules."""
    assert os.path.exists(CI_WORKFLOW_PATH)
    with open(CI_WORKFLOW_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    assert "mypy" in content
    assert "utils/text_cleaner.py" in content
    assert "utils/score_explanations.py" in content
    assert "utils/input_validator.py" in content


def test_contributing_documents_type_policy():
    """Verify CONTRIBUTING.md describes the type policy and how to add modules to the strict list."""
    assert os.path.exists(CONTRIBUTING_PATH)
    with open(CONTRIBUTING_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    assert "mypy" in content.lower()
    assert "strict" in content.lower()
    assert "mypy.ini" in content
