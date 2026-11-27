"""
Unit and Integration Tests for Placement Cell Readiness Analytics (#886).

Validates:
- Aggregation by branch and graduation year.
- Strict k-anonymity (cohorts < 5 merged into 'Other').
- Zero exposure of student PII (names, emails, IDs) across outputs.
- Formula-injection sanitization on CSV exports.
- Administrator-only authorization enforcement.
"""

import csv
import io
import json
import sqlite3
import pytest

from app import create_app
from utils.placement_readiness_service import (
    PlacementReadinessService,
    sanitize_csv_field,
    normalize_branch_name,
    categorize_funnel_status,
)


@pytest.fixture
def test_db():
    """Sets up an isolated SQLite database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS student_profiles (
                student_id TEXT PRIMARY KEY,
                full_name TEXT,
                email TEXT,
                phone TEXT,
                department TEXT,
                grad_year INTEGER,
                cgpa REAL,
                target_role TEXT,
                target_company TEXT,
                skills_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS student_batch_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                graduation_year INTEGER NOT NULL DEFAULT 2026,
                department TEXT NOT NULL DEFAULT 'Computer Science',
                cgpa REAL DEFAULT 7.5,
                placement_status TEXT DEFAULT 'seeking',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY,
                resume_filename TEXT NOT NULL,
                company_name TEXT NOT NULL,
                job_description TEXT,
                results_json TEXT NOT NULL,
                student_id TEXT DEFAULT 'default_student',
                version_number INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coding_submissions (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                analysis_id TEXT,
                question_title TEXT NOT NULL,
                company_name TEXT NOT NULL DEFAULT 'General',
                language TEXT DEFAULT 'python',
                code TEXT,
                passed INTEGER DEFAULT 1,
                score REAL DEFAULT 100.0,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interviews (
                id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                student_id TEXT,
                current_question_index INTEGER NOT NULL DEFAULT 0,
                questions_json TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_applications (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                company_name TEXT NOT NULL,
                role_title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Applied',
                application_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    yield conn
    conn.close()


@pytest.fixture
def app_client():
    """Configures test client for web routes and APIs."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client


def test_sanitize_csv_field():
    """Ensures formula injection characters are safely escaped."""
    assert sanitize_csv_field("=CMD('calc')") == "'=CMD('calc')"
    assert sanitize_csv_field("+12345") == "'+12345"
    assert sanitize_csv_field("-98765") == "'-98765"
    assert sanitize_csv_field("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_field("\tTabbed") == "'\tTabbed"
    assert sanitize_csv_field("Computer Science") == "Computer Science"
    assert sanitize_csv_field(85.5) == "85.5"
    assert sanitize_csv_field(None) == ""


def test_normalize_branch_name():
    """Verifies branch name normalization across various aliases."""
    assert normalize_branch_name("cs") == "Computer Science"
    assert normalize_branch_name("CSE") == "Computer Science"
    assert normalize_branch_name("ece") == "Electronics & Communication"
    assert normalize_branch_name("mech") == "Mechanical Engineering"
    assert normalize_branch_name("ai/ds") == "Data Science & AI"
    assert normalize_branch_name("") == "General Engineering"
    assert normalize_branch_name(None) == "General Engineering"


def test_categorize_funnel_status():
    """Verifies categorization of job application stages."""
    assert categorize_funnel_status("Applied") == "Applied"
    assert categorize_funnel_status("Online Assessment") == "Assessment"
    assert categorize_funnel_status("Screening Round") == "Assessment"
    assert categorize_funnel_status("Technical Interview") == "Interview"
    assert categorize_funnel_status("HR Interview") == "Interview"
    assert categorize_funnel_status("Selected") == "Offer"
    assert categorize_funnel_status("Offered") == "Offer"
    assert categorize_funnel_status("Placed") == "Offer"
    assert categorize_funnel_status("Rejected") == "Rejected"
    assert categorize_funnel_status(None) == "Applied"


def test_readiness_aggregation_and_k_anonymity(test_db):
    """
    Validates:
    - 6 students in CS 2026 (>= 5) form a distinct cohort.
    - 2 in ECE 2026 (< 5) and 1 in ME 2025 (< 5) get merged into 'Other'.
    - ATS scores, practice activities, and funnel stages aggregate accurately.
    - ZERO individual student names, emails, or student IDs appear in output.
    """
    with test_db:
        # Cohort 1: CS 2026 (6 students - should remain distinct)
        for i in range(1, 7):
            sid = f"cs_student_{i}"
            test_db.execute(
                "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
                (sid, f"Student {i}", f"cs{i}@test.edu")
            )
            test_db.execute(
                "INSERT INTO student_profiles (student_id, full_name, email, "
                "department, grad_year) VALUES (?, ?, ?, ?, ?)",
                (sid, f"Student {i}", f"cs{i}@test.edu", "Computer Science", 2026)
            )
            results = {
                "ats_score": 80.0 + i,
                "skill_gap": {
                    "missing_skills": [
                        "Docker",
                        "Kubernetes" if i % 2 == 0 else "System Design"
                    ]
                }
            }
            test_db.execute(
                "INSERT INTO analyses (id, student_id, resume_filename, "
                "company_name, results_json) VALUES (?, ?, ?, ?, ?)",
                (f"analysis_cs_{i}", sid, "res.pdf", "Tech Corp", json.dumps(results))
            )
            test_db.execute(
                "INSERT INTO coding_submissions (id, student_id, question_title) "
                "VALUES (?, ?, ?)",
                (f"code_cs_{i}", sid, "Two Sum")
            )
            test_db.execute(
                "INSERT INTO interviews (id, analysis_id, student_id, "
                "questions_json, answers_json) VALUES (?, ?, ?, '[]', '[]')",
                (f"int_cs_{i}", f"analysis_cs_{i}", sid)
            )
            test_db.execute(
                "INSERT INTO job_applications (id, student_id, company_name, "
                "role_title, status, application_date) VALUES (?, ?, ?, ?, ?, ?)",
                (f"app_cs_{i}", sid, "Tech Corp", "SDE",
                 "Offer" if i <= 2 else "Applied", "2026-01-01")
            )

        # Cohort 2: ECE 2026 (2 students - below min group size of 5)
        for i in range(1, 3):
            sid = f"ece_student_{i}"
            test_db.execute(
                "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
                (sid, f"ECE Student {i}", f"ece{i}@test.edu")
            )
            test_db.execute(
                "INSERT INTO student_profiles (student_id, full_name, email, "
                "department, grad_year) VALUES (?, ?, ?, ?, ?)",
                (sid, f"ECE Student {i}", f"ece{i}@test.edu",
                 "Electronics & Communication", 2026)
            )
            results = {
                "ats_score": 70.0,
                "skill_gap": {"missing_skills": ["Verilog", "Embedded C"]}
            }
            test_db.execute(
                "INSERT INTO analyses (id, student_id, resume_filename, "
                "company_name, results_json) VALUES (?, ?, ?, ?, ?)",
                (f"analysis_ece_{i}", sid, "res.pdf", "Chip Corp", json.dumps(results))
            )

        # Cohort 3: ME 2025 (1 student - below min group size of 5)
        sid = "me_student_1"
        test_db.execute(
            "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
            (sid, "ME Student 1", "me1@test.edu")
        )
        test_db.execute(
            "INSERT INTO student_profiles (student_id, full_name, email, "
            "department, grad_year) VALUES (?, ?, ?, ?, ?)",
            (sid, "ME Student 1", "me1@test.edu", "Mechanical Engineering", 2025)
        )

    analytics = PlacementReadinessService.get_readiness_analytics(
        conn=test_db, min_group_size=5
    )

    assert analytics["status"] == "success"
    cohorts = analytics["cohorts"]
    summary = analytics["summary"]

    assert summary["total_students"] == 9  # 6 CS + 2 ECE + 1 ME
    assert summary["total_cohorts"] == 2   # 1 CS (>=5) + 1 'Other' (<5)
    assert summary["suppressed_small_cohorts_count"] == 2

    cs_cohort = next((c for c in cohorts if c["department"] == "Computer Science"), None)
    assert cs_cohort is not None
    assert cs_cohort["active_students"] == 6
    assert cs_cohort["graduation_year"] == 2026
    assert cs_cohort["is_other"] is False
    assert cs_cohort["practice_activity_count"] == 12  # 6 coding + 6 interviews
    assert cs_cohort["funnel"]["offer"] == 2
    assert cs_cohort["funnel"]["applied"] == 4

    skills_in_cs = [s["skill"] for s in cs_cohort["top_missing_skills"]]
    assert "Docker" in skills_in_cs

    other_cohort = next((c for c in cohorts if c["is_other"] is True), None)
    assert other_cohort is not None
    assert other_cohort["active_students"] == 3  # 2 ECE + 1 ME
    assert other_cohort["graduation_year"] == "Combined"
    assert "Cohorts < 5" in other_cohort["department"]
    assert other_cohort["average_ats_score"] == 70.0

    raw_payload_str = json.dumps(analytics)
    forbidden_tokens = [
        "cs_student", "ece_student", "me_student",
        "Student 1", "Student 2", "cs1@test.edu", "ece1@test.edu"
    ]
    for token in forbidden_tokens:
        assert token not in raw_payload_str, (
            f"Privacy violation: Found sensitive token '{token}' in readiness payload!"
        )


def test_csv_export_privacy_and_formula_injection(test_db):
    """
    Validates CSV export:
    - Zero student PII present.
    - Malicious formula prefixes are safely escaped.
    - Correct columns and summary aggregates.
    """
    with test_db:
        for i in range(1, 6):
            sid = f"injection_student_{i}"
            test_db.execute(
                "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
                (sid, f"Student {i}", f"inj{i}@test.edu")
            )
            test_db.execute(
                "INSERT INTO student_profiles (student_id, full_name, email, "
                "department, grad_year) VALUES (?, ?, ?, ?, ?)",
                (sid, f"Student {i}", f"inj{i}@test.edu",
                 "=HYPERLINK('http://evil.com')", 2026)
            )
            results = {
                "ats_score": 85.0,
                "skill_gap": {"missing_skills": ["@SUM(1,2)", "+CMD"]}
            }
            test_db.execute(
                "INSERT INTO analyses (id, student_id, resume_filename, "
                "company_name, results_json) VALUES (?, ?, ?, ?, ?)",
                (f"inj_analysis_{i}", sid, "res.pdf", "Evil Corp", json.dumps(results))
            )

    csv_output = PlacementReadinessService.export_readiness_csv(
        conn=test_db, min_group_size=5
    )

    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) >= 2

    header = rows[0]
    assert "Branch / Department" in header
    assert "Graduation Year" in header
    assert "Average ATS Score" in header
    assert "Top Missing Skills" in header

    dept_cell = rows[1][0]
    assert dept_cell.startswith("'="), f"Expected formula-escaped cell, got: {dept_cell}"

    for forbidden in ["injection_student", "Student 1", "inj1@test.edu"]:
        assert forbidden not in csv_output, f"Privacy leak in CSV: found '{forbidden}'"


def test_admin_readiness_routes_authorization(app_client):
    """
    Validates that:
    - Unauthenticated requests are rejected or redirected.
    - Admin or coordinator sessions can access HTML, JSON, and CSV views.
    - X-Admin-Key header allows authorized programmatic access.
    """
    res_html_unauth = app_client.get("/admin/readiness-analytics")
    assert res_html_unauth.status_code in [302, 403]

    res_api_unauth = app_client.get("/api/admin/readiness-analytics")
    assert res_api_unauth.status_code == 403

    res_csv_unauth = app_client.get("/admin/readiness-analytics/export.csv")
    assert res_csv_unauth.status_code == 403

    with app_client.session_transaction() as sess:
        sess["user_id"] = "admin_user"
        sess["user_role"] = "admin"
        sess["is_admin"] = True

    res_html_auth = app_client.get("/admin/readiness-analytics")
    assert res_html_auth.status_code == 200
    assert b"Placement Cell Readiness Analytics" in res_html_auth.data
    assert b"Privacy-Preserving Cohort Aggregation" in res_html_auth.data

    res_api_auth = app_client.get("/api/admin/readiness-analytics")
    assert res_api_auth.status_code == 200
    data = json.loads(res_api_auth.data)
    assert data["status"] == "success"
    assert "cohorts" in data
    assert "summary" in data

    res_csv_auth = app_client.get("/admin/readiness-analytics/export.csv")
    assert res_csv_auth.status_code == 200
    assert res_csv_auth.mimetype == "text/csv"
    assert b"Branch / Department,Graduation Year" in res_csv_auth.data

    with app_client.session_transaction() as sess:
        sess.clear()

    res_key = app_client.get(
        "/api/admin/readiness-analytics",
        headers={"X-Admin-Key": "admin-default-secret-key-2026"}
    )
    assert res_key.status_code == 200
