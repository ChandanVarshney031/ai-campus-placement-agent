"""
Unit Tests for Mentor Preparation Plan Sharing Service (#mentor-share)
======================================================================
Covers:
- Secure read-only link creation for mentors and faculty.
- Selected goals filtering and ownership enforcement.
- Strict privacy redaction: personal notes and private job application details are hidden.
- Link expiration and view counting.
- Instant link revocation by owner (and protection against unauthorized revocation).
- Web route /shared/plan/<token> with noindex headers.
- REST APIs (/api/preparation-plans/share, /api/preparation-plans/shares, revoke, /api/shared/plan/<token>).
- Cascading deletion when student account is purged.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import config
from app import app
from utils.database import (
    init_db,
    get_db_connection,
    save_job_application,
    delete_user_account_and_personal_data,
)
from utils.goal_planner_service import PreparationGoalPlannerService
from utils.mentor_plan_share_service import (
    init_shared_plans_table,
    create_shared_plan_link,
    get_shared_plan_by_token,
    revoke_shared_plan_link,
    list_student_shared_plans,
    PlanSharePermissionError,
)


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated SQLite database and test client."""
    db_file = tmp_path / "test_mentor_share.db"
    with patch.object(config, "DATABASE_PATH", str(db_file)):
        init_db()
        init_shared_plans_table()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.test_client() as client:
            yield {"client": client, "db_path": str(db_file)}


def _seed_sample_goals(student_id: str) -> list:
    """Helper to seed sample preparation goals with private notes."""
    planner = PreparationGoalPlannerService()
    g1 = planner.create_goal(
        student_id=student_id,
        goal_title="Google Summer Internship Preparation",
        target_role="Software Engineer",
        target_date_str="2026-12-15",
        target_coding_problems=60,
        target_mock_interviews=6,
        resume_completed=True,
        notes="Confidential student note: Need to review DP graphs and avoid mentioning low 2nd sem GPA.",
    )
    g2 = planner.create_goal(
        student_id=student_id,
        goal_title="Microsoft Placement Drive",
        target_role="Cloud Solution Architect",
        target_date_str="2026-11-30",
        target_coding_problems=40,
        target_mock_interviews=4,
        resume_completed=False,
        notes="Private personal note: Ask faculty mentor about system design interview rounds.",
    )
    g3 = planner.create_goal(
        student_id=student_id,
        goal_title="Off-Campus Startup Roadmap",
        target_role="Full Stack Developer",
        target_date_str="2027-01-20",
        target_coding_problems=30,
        target_mock_interviews=3,
        resume_completed=False,
        notes="Private note: Target salary min 18 LPA.",
    )
    return [g1, g2, g3]


def test_create_and_view_shared_plan(test_env):
    """Verifies creating a shared plan link and retrieving it by token."""
    sid = "student_share_01"
    g_ids = _seed_sample_goals(sid)

    res = create_shared_plan_link(
        student_id=sid,
        goal_ids=g_ids[:2],
        mentor_name="Dr. Aris Thorne",
        mentor_email="thorne@campus.edu",
        plan_title="Target Tech Giants 2026",
        expiration="30d",
    )

    assert res["success"] is True
    token = res["token"]
    assert len(token) > 20
    assert res["goal_count"] == 2
    assert res["share_url"] == f"/shared/plan/{token}"

    # Retrieve via token
    valid, plan, err = get_shared_plan_by_token(token)
    assert valid is True
    assert err is None
    assert plan["plan_title"] == "Target Tech Giants 2026"
    assert plan["mentor_name"] == "Dr. Aris Thorne"
    assert len(plan["goals"]) == 2
    assert plan["view_count"] == 1
    assert plan["is_read_only_mentor_view"] is True


def test_privacy_enforcement_redacts_notes_and_private_applications(test_env):
    """
    CRITICAL: Verifies that personal notes and private job applications
    are strictly excluded from the mentor read-only view.
    """
    sid = "student_privacy_01"
    g_ids = _seed_sample_goals(sid)

    # Seed private job application with confidential candidate data
    save_job_application(
        student_id=sid,
        company_name="Confidential Stealth Corp",
        role_title="Backend Intern",
        status="Offer Extended",
        application_date="2026-11-20",
        notes="Secret negotiation note: Offered 25 LPA base plus stock.",
    )

    res = create_shared_plan_link(
        student_id=sid,
        goal_ids=[g_ids[0]],
        mentor_name="Prof. Sharma",
        plan_title="Mentored DSA Track",
    )
    token = res["token"]

    valid, plan, err = get_shared_plan_by_token(token)
    assert valid is True

    # 1. Verify personal notes are redacted / null
    for g in plan["goals"]:
        assert g.get("notes") is None, "Student personal notes must not be leaked to mentors!"
        assert "Confidential student note" not in json.dumps(plan)
        assert "low 2nd sem GPA" not in json.dumps(plan)

    # 2. Verify no private job application details leaked
    plan_json = json.dumps(plan)
    assert "Confidential Stealth Corp" not in plan_json
    assert "Secret negotiation note" not in plan_json
    assert "Offer Extended" not in plan_json
    assert "student_privacy_01" not in plan_json


def test_selected_goals_filtering_and_ownership(test_env):
    """Verifies that only selected goals are shared and foreign goals cannot be shared."""
    student_a = "student_a_01"
    student_b = "student_b_01"

    goals_a = _seed_sample_goals(student_a)
    goals_b = _seed_sample_goals(student_b)

    # Student A shares only goals_a[1]
    res = create_shared_plan_link(
        student_id=student_a,
        goal_ids=[goals_a[1]],
        plan_title="Microsoft Prep Only",
    )
    valid, plan, _ = get_shared_plan_by_token(res["token"])
    assert valid is True
    assert len(plan["goals"]) == 1
    assert plan["goals"][0]["id"] == goals_a[1]

    # Student A attempts to share Student B's goal ID -> raises ValueError
    with pytest.raises(ValueError, match="None of the specified goal IDs belong"):
        create_shared_plan_link(
            student_id=student_a,
            goal_ids=[goals_b[0]],
            plan_title="Malicious Share Attempt",
        )


def test_link_revocation(test_env):
    """Verifies owner can revoke link immediately and unauthorized student cannot revoke."""
    sid = "student_revoke_01"
    g_ids = _seed_sample_goals(sid)

    res = create_shared_plan_link(student_id=sid, goal_ids=g_ids)
    share_id = res["share_id"]
    token = res["token"]

    # Unauthorized student attempts to revoke
    with pytest.raises(PlanSharePermissionError):
        revoke_shared_plan_link(student_id="imposter_student", share_id=share_id)

    # Owner revokes
    success = revoke_shared_plan_link(student_id=sid, share_id=share_id)
    assert success is True

    # Subsequent access fails
    valid, plan, err = get_shared_plan_by_token(token)
    assert valid is False
    assert "revoked" in err.lower()


def test_expired_link_returns_error(test_env):
    """Verifies expired link cannot be accessed."""
    sid = "student_exp_01"
    g_ids = _seed_sample_goals(sid)

    res = create_shared_plan_link(student_id=sid, goal_ids=g_ids)
    token = res["token"]

    # Manually expire the record in database
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE shared_preparation_plans SET expires_at = ? WHERE token = ?",
            (past_iso, token),
        )
        conn.commit()

    valid, plan, err = get_shared_plan_by_token(token)
    assert valid is False
    assert "expired" in err.lower()


def test_list_student_shared_plans(test_env):
    """Verifies listing student's active, expired, and revoked share links."""
    sid = "student_list_01"
    g_ids = _seed_sample_goals(sid)

    res1 = create_shared_plan_link(student_id=sid, goal_ids=[g_ids[0]], plan_title="Track 1")
    res2 = create_shared_plan_link(student_id=sid, goal_ids=[g_ids[1]], plan_title="Track 2")

    # Revoke res2
    revoke_shared_plan_link(student_id=sid, share_id=res2["share_id"])

    shares = list_student_shared_plans(sid)
    assert len(shares) == 2
    status_map = {s["share_id"]: s["status"] for s in shares}
    assert status_map[res1["share_id"]] == "active"
    assert status_map[res2["share_id"]] == "revoked"


def test_web_routes_and_api_endpoints(test_env):
    """Verifies Flask routes: creation, listing, revoking, and public read-only rendering."""
    client = test_env["client"]
    sid = "student_routes_01"
    g_ids = _seed_sample_goals(sid)

    with client.session_transaction() as sess:
        sess["user_id"] = sid

    # 1. POST /api/preparation-plans/share
    post_res = client.post(
        "/api/preparation-plans/share",
        json={
            "student_id": sid,
            "goal_ids": [g_ids[0]],
            "mentor_name": "Faculty Advisor Dr. Rao",
            "plan_title": "Semester Placement Plan",
            "expiration": "30d",
        },
    )
    assert post_res.status_code == 201
    post_data = post_res.get_json()
    assert post_data["success"] is True
    token = post_data["token"]
    share_id = post_data["share_id"]

    # 2. GET /api/preparation-plans/shares
    list_res = client.get(f"/api/preparation-plans/shares?student_id={sid}")
    assert list_res.status_code == 200
    list_data = list_res.get_json()
    assert list_data["success"] is True
    assert len(list_data["shares"]) >= 1

    # 3. GET /shared/plan/<token> (HTML Mentor View)
    html_res = client.get(f"/shared/plan/{token}")
    assert html_res.status_code == 200
    assert "noindex, nofollow, noarchive" in html_res.headers.get("X-Robots-Tag", "")
    assert b"Mentor Read-Only View" in html_res.data
    assert b"Semester Placement Plan" in html_res.data
    # Notes must NOT be in the HTML output
    assert b"Confidential student note" not in html_res.data

    # 4. GET /api/shared/plan/<token> (JSON API)
    api_res = client.get(f"/api/shared/plan/{token}")
    assert api_res.status_code == 200
    api_data = api_res.get_json()
    assert api_data["success"] is True
    assert api_data["plan"]["summary"]["total_goals"] == 1

    # 5. POST /api/preparation-plans/share/<id>/revoke
    rev_res = client.post(f"/api/preparation-plans/share/{share_id}/revoke")
    assert rev_res.status_code == 200
    assert rev_res.get_json()["success"] is True

    # 6. GET /shared/plan/<token> after revocation returns 404
    revoked_html_res = client.get(f"/shared/plan/{token}")
    assert revoked_html_res.status_code == 404
    assert b"Link No Longer Valid" in revoked_html_res.data


def test_account_deletion_cascades_shared_plans(test_env):
    """Verifies deleting student account purges all shared_preparation_plans."""
    sid = "student_purge_share_01"
    g_ids = _seed_sample_goals(sid)
    create_shared_plan_link(student_id=sid, goal_ids=g_ids)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM shared_preparation_plans WHERE student_id = ?", (sid,))
        assert cursor.fetchone()[0] == 1

    # Cascade delete
    delete_user_account_and_personal_data(student_id=sid)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM shared_preparation_plans WHERE student_id = ?", (sid,))
        assert cursor.fetchone()[0] == 0
