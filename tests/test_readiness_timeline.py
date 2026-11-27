"""
Unit Tests for Placement Readiness Activity Timeline & Audit Trail Service (#timeline)
=====================================================================================
Covers:
- Database table creation & custom milestone persistence in placement_activity_timeline.
- Multi-source chronological timeline aggregation across:
    1. Resume Revisions
    2. Practice Sessions (coding, aptitude, GD)
    3. Job Applications
    4. Interviews (mock sessions, experiences)
    5. Feedback & Reviews
    6. Achievements & Derived Milestones
- Category filtering and pagination.
- Automatic milestone detection (streaks, high ATS score, practice milestones).
- Effort summary calculation and readiness stage progression.
- Flask endpoints (/timeline, /api/timeline, /api/timeline/summary).
- Account deletion cascade purge.
"""

import json
import uuid
import pytest
from unittest.mock import patch
from datetime import datetime, timezone

import config
from app import app
from utils.database import (
    init_db,
    save_analysis,
    save_student_profile,
    save_coding_submission,
    save_job_application,
    delete_user_account_and_personal_data,
    get_db_connection,
)
from utils.readiness_timeline_service import (
    log_placement_activity,
    get_placement_activity_timeline,
    get_readiness_effort_summary,
    ensure_placement_activity_timeline_table,
    CATEGORY_RESUME_REVISIONS,
    CATEGORY_PRACTICE_SESSIONS,
    CATEGORY_APPLICATIONS,
    CATEGORY_INTERVIEWS,
    CATEGORY_FEEDBACK,
    CATEGORY_ACHIEVEMENTS,
)


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated SQLite database and test client."""
    db_file = tmp_path / "test_readiness_timeline.db"
    with patch.object(config, "DATABASE_PATH", str(db_file)):
        init_db()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.test_client() as client:
            yield {"client": client, "db_path": str(db_file)}


def test_log_custom_placement_activity(test_env):
    """Verifies persisting and retrieving explicit milestones and custom events."""
    sid = "student_test_timeline_01"
    ev = log_placement_activity(
        student_id=sid,
        category="achievements",
        title="Secured 1st Place in Hackathon",
        description="Won inter-college hackathon with AI campus agent project.",
        event_date="2026-11-20",
        is_milestone=1,
    )

    assert ev["id"] is not None
    assert ev["student_id"] == sid
    assert ev["category"] == CATEGORY_ACHIEVEMENTS
    assert ev["title"] == "Secured 1st Place in Hackathon"
    assert ev["is_milestone"] is True
    assert ev["event_date"] == "2026-11-20"


def test_timeline_aggregation_across_all_six_categories(test_env):
    """
    Verifies that get_placement_activity_timeline accurately synthesizes items
    from all 6 categories into a single chronological feed.
    """
    sid = "student_aggregate_all_01"

    # 1. Resume Revision
    aid = str(uuid.uuid4())
    save_analysis(
        analysis_id=aid,
        resume_filename="alex_swe_resume.pdf",
        company_name="Google",
        job_description="Software Engineer, Backend Systems",
        results_dict={"ats_score": 88, "overall_score": 85, "target_role": "Backend Engineer"},
        student_id=sid,
        version_number=1,
        target_role="Backend Engineer",
    )

    # 2. Practice Session (Coding)
    save_coding_submission(
        student_id=sid,
        question_title="Two Sum Problem",
        company_name="Google",
        language="python",
        code="def two_sum(): pass",
        passed=True,
        score=100,
        tests_passed=10,
        total_tests=10,
    )

    # 2b. Practice Session (Aptitude)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO aptitude_attempts (
                id, student_id, test_category, total_questions, correct_count,
                score_percentage, time_taken_seconds, time_limit_seconds,
                topic_accuracy_json, weak_topics_json, results_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), sid, "Quantitative Aptitude", 20, 18,
            90.0, 1200, 1800, "{}", "[]", "{}"
        ))
        conn.commit()

    # 3. Job Application
    save_job_application(
        student_id=sid,
        company_name="Microsoft",
        role_title="Software Development Engineer",
        status="Applied",
        application_date="2026-11-22",
        location="Redmond / Hybrid",
    )

    # 4. Mock Interview Session
    with get_db_connection() as conn:
        cursor = conn.cursor()
        int_aid = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO analyses (id, resume_filename, company_name, job_description, results_json, student_id, version_number)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (int_aid, "interview_resume.pdf", "Amazon", "SDE-1", "{}", sid))
        cursor.execute("""
            INSERT INTO interviews (id, analysis_id, current_question_index, questions_json, answers_json, completed)
            VALUES (?, ?, 3, '[]', '[]', 1)
        """, (str(uuid.uuid4()), int_aid))
        conn.commit()

    # 5. Section Feedback
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_section_feedback (analysis_id, user_id, section, rating, comment)
            VALUES (?, ?, 'experience_bullet_points', 'up', 'Rewritten bullets highlighted metrics well.')
        """, (aid, sid))
        conn.commit()

    # 6. Custom Achievement
    log_placement_activity(
        student_id=sid,
        category="achievements",
        title="Passed AWS Certified Cloud Practitioner",
        description="Scored 880/1000 on cloud foundational exam.",
        is_milestone=1,
    )

    # Fetch aggregated timeline
    res = get_placement_activity_timeline(student_id=sid, limit=50)

    assert res["total_count"] > 0
    items = res["items"]

    # Verify presence of all 6 categories
    cats_found = {it["category"] for it in items}
    assert CATEGORY_RESUME_REVISIONS in cats_found
    assert CATEGORY_PRACTICE_SESSIONS in cats_found
    assert CATEGORY_APPLICATIONS in cats_found
    assert CATEGORY_INTERVIEWS in cats_found
    assert CATEGORY_FEEDBACK in cats_found
    assert CATEGORY_ACHIEVEMENTS in cats_found

    # Verify chronological descending order (newest first)
    for i in range(len(items) - 1):
        assert items[i]["event_date"] >= items[i + 1]["event_date"]


def test_timeline_category_filter(test_env):
    """Verifies filtering by category restricts items appropriately."""
    sid = "student_category_filter_01"

    # Add practice item
    save_coding_submission(
        student_id=sid,
        question_title="Binary Search",
        company_name="General",
        language="python",
        code="def bs(): pass",
        passed=True,
        score=100,
        tests_passed=5,
        total_tests=5,
    )

    # Add application item
    save_job_application(
        student_id=sid,
        company_name="Stripe",
        role_title="Backend Engineer",
        status="Applied",
        application_date="2026-11-25",
    )

    # Query only applications
    apps_res = get_placement_activity_timeline(student_id=sid, category="applications")
    assert apps_res["total_count"] == 1
    assert all(it["category"] == CATEGORY_APPLICATIONS for it in apps_res["items"])

    # Query only practice
    prac_res = get_placement_activity_timeline(student_id=sid, category="practice_sessions")
    assert prac_res["total_count"] >= 1
    assert all(it["category"] == CATEGORY_PRACTICE_SESSIONS for it in prac_res["items"])


def test_automatic_milestone_detection(test_env):
    """Verifies derived achievements: streaks, ATS milestones, and coding solves."""
    sid = "student_milestone_deriv_01"

    # 1. High ATS resume (ATS >= 80)
    aid = str(uuid.uuid4())
    save_analysis(
        analysis_id=aid,
        resume_filename="high_ats_resume.pdf",
        company_name="Meta",
        job_description="Production Engineer",
        results_dict={"ats_score": 92, "overall_score": 90},
        student_id=sid,
        version_number=1,
    )

    # 2. First coding solve
    save_coding_submission(
        student_id=sid,
        question_title="Valid Parentheses",
        company_name="Meta",
        language="python",
        code="def isValid(): pass",
        passed=True,
        score=100,
        tests_passed=12,
        total_tests=12,
    )

    # 3. 7-Day Streak
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO student_streaks (student_id, current_streak, longest_streak, last_activity_date, total_active_days)
            VALUES (?, 7, 7, '2026-11-26', 7)
        """, (sid,))
        conn.commit()

    res = get_placement_activity_timeline(student_id=sid, category="achievements")
    achievement_titles = [it["title"] for it in res["items"]]

    # Both high ATS, 7-day streak, and first coding solve should appear
    assert any("ATS Benchmark" in t or "Elite ATS" in t for t in achievement_titles)
    assert any("First Coding Challenge Solved" in t for t in achievement_titles)
    assert any("7-Day" in t or "Streak" in t for t in achievement_titles)


def test_effort_summary_metrics(test_env):
    """Verifies that get_readiness_effort_summary provides accurate aggregated statistics."""
    sid = "student_effort_summary_01"

    # Empty initially
    summary_empty = get_readiness_effort_summary(student_id=sid)
    assert summary_empty["total_activities"] == 0
    assert summary_empty["milestones_count"] == 0
    assert summary_empty["readiness_stage_level"] == 1

    # Log some activities
    log_placement_activity(
        student_id=sid,
        category="resume_revisions",
        title="Resume Uploaded",
        event_date="2026-11-20",
    )
    log_placement_activity(
        student_id=sid,
        category="achievements",
        title="Milestone Logged",
        event_date="2026-11-21",
        is_milestone=1,
    )

    summary = get_readiness_effort_summary(student_id=sid)
    assert summary["total_activities"] >= 2
    assert summary["milestones_count"] >= 1
    assert summary["active_days_count"] >= 2
    assert summary["readiness_stage_level"] >= 1


def test_timeline_web_and_api_routes(test_env):
    """Tests GET /timeline, GET /api/timeline, POST /api/timeline, and summary endpoint."""
    client = test_env["client"]
    sid = "student_route_test_01"

    with client.session_transaction() as sess:
        sess["user_id"] = sid

    # 1. GET /timeline (HTML)
    res_html = client.get("/timeline")
    assert res_html.status_code == 200
    assert b"Placement Readiness Activity Timeline" in res_html.data

    # 2. GET /api/timeline (JSON)
    res_api = client.get(f"/api/timeline?student_id={sid}")
    assert res_api.status_code == 200
    json_data = res_api.get_json()
    assert json_data["status"] == "success"
    assert "items" in json_data["data"]
    assert "summary" in json_data["data"]

    # 3. POST /api/timeline (Log custom event)
    res_post = client.post(
        "/api/timeline",
        json={
            "student_id": sid,
            "category": "achievements",
            "title": "Selected for Final Technical Round at Cisco",
            "description": "Round scheduled for next Tuesday.",
            "event_date": "2026-11-26",
            "is_milestone": 1,
        },
    )
    assert res_post.status_code == 201
    post_json = res_post.get_json()
    assert post_json["status"] == "success"
    assert post_json["event"]["title"] == "Selected for Final Technical Round at Cisco"

    # 4. POST validation failure (missing title)
    res_bad = client.post("/api/timeline", json={"student_id": sid, "title": ""})
    assert res_bad.status_code == 400

    # 5. GET /api/timeline/summary
    res_sum = client.get(f"/api/timeline/summary?student_id={sid}")
    assert res_sum.status_code == 200
    sum_data = res_sum.get_json()
    assert sum_data["status"] == "success"
    assert sum_data["summary"]["total_activities"] >= 1


def test_delete_user_account_purges_timeline(test_env):
    """Verifies that deleting a student account cascades and purges placement_activity_timeline."""
    sid = "student_purge_timeline_01"

    log_placement_activity(
        student_id=sid,
        category="achievements",
        title="Student Milestone to be Purged",
        is_milestone=1,
    )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM placement_activity_timeline WHERE student_id = ?", (sid,))
        assert cursor.fetchone()[0] == 1

    # Perform cascading account deletion
    delete_user_account_and_personal_data(student_id=sid)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM placement_activity_timeline WHERE student_id = ?", (sid,))
        assert cursor.fetchone()[0] == 0
