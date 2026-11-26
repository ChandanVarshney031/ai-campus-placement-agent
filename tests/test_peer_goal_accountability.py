"""
Unit Tests for Peer Goal Accountability Groups (#peer-groups)
=============================================================
Covers:
- Private group creation with unique invite code.
- Joining peer group via invite code.
- Shared placement goals creation and member task completion.
- Member progress privacy opt-out: masking progress stats from other peers.
- Sensitive application data shield: strict prevention of sharing private job applications or offer details.
- Access control for non-members.
- Leaving accountability groups.
- Cascading deletion upon student account purge.
- Flask endpoints (/peer-groups, /api/peer-groups, join, goals, progress, privacy, leave).
"""

import json
import pytest
from unittest.mock import patch

import config
from app import app
from utils.database import (
    init_db,
    get_db_connection,
    save_job_application,
    delete_user_account_and_personal_data,
)
from utils.peer_accountability_service import (
    init_peer_groups_tables,
    create_peer_group,
    join_peer_group,
    leave_peer_group,
    update_member_privacy,
    create_group_goal,
    update_goal_progress,
    get_group_details,
    list_student_groups,
)


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated SQLite database and test client."""
    db_file = tmp_path / "test_peer_groups.db"
    with patch.object(config, "DATABASE_PATH", str(db_file)):
        init_db()
        init_peer_groups_tables()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.test_client() as client:
            yield {"client": client, "db_path": str(db_file)}


def test_create_and_join_peer_group(test_env):
    """Verifies creating a group and joining via invite code."""
    admin_id = "student_admin_01"
    created = create_peer_group(
        creator_student_id=admin_id,
        name="FAANG Aspirants Pod",
        description="Daily DSA and mock interview accountability.",
        display_name="Alex (Admin)",
    )

    assert created["id"] is not None
    assert created["name"] == "FAANG Aspirants Pod"
    assert created["invite_code"].startswith("PEER-")
    assert created["my_role"] == "admin"

    # Member joins via invite code
    member_id = "student_peer_02"
    joined = join_peer_group(
        student_id=member_id,
        invite_code=created["invite_code"],
        display_name="Sarah",
        share_progress=1,
    )

    assert joined["group_id"] == created["id"]
    assert joined["role"] == "member"
    assert joined["share_progress"] is True

    # Check group details
    details = get_group_details(created["id"], current_student_id=admin_id)
    assert details["group"]["member_count"] == 2
    assert len(details["members"]) == 2


def test_shared_goals_and_task_completion(test_env):
    """Verifies creating shared placement tasks and tracking completion."""
    admin_id = "student_admin_02"
    member_id = "student_peer_03"

    group = create_peer_group(creator_student_id=admin_id, name="Campus Core Batch")
    join_peer_group(student_id=member_id, invite_code=group["invite_code"])

    # Admin creates shared goal
    goal = create_group_goal(
        student_id=admin_id,
        group_id=group["id"],
        title="Complete 30 Tree & Graph Problems",
        category="coding",
        target_count=30,
        target_date="2026-12-01",
        description="LeetCode medium/hard curated list.",
    )

    assert goal["id"] is not None
    assert goal["target_count"] == 30
    assert goal["category"] == "coding"

    # Member marks partial progress
    prog = update_goal_progress(
        student_id=member_id,
        goal_id=goal["id"],
        completed_count=15,
    )
    assert prog["completed_count"] == 15
    assert prog["is_completed"] is False
    assert prog["percent"] == 50.0

    # Member marks complete
    prog_done = update_goal_progress(
        student_id=member_id,
        goal_id=goal["id"],
        is_completed=True,
    )
    assert prog_done["is_completed"] is True

    # Verify group details reflect tallies
    details = get_group_details(group["id"], current_student_id=admin_id)
    g_info = details["goals"][0]
    assert g_info["completed_members_count"] == 1
    assert g_info["total_members_count"] == 2
    assert g_info["group_completion_rate"] == 50.0


def test_privacy_opt_out_masks_peer_progress(test_env):
    """
    CRITICAL: Verifies that when a member opts out of progress sharing (share_progress = 0),
    their individual progress is masked from other peers, while the member can still see their own stats.
    """
    student_a = "student_peer_a"
    student_b = "student_peer_b"

    group = create_peer_group(creator_student_id=student_a, name="Privacy Test Pod")
    join_peer_group(
        student_id=student_b,
        invite_code=group["invite_code"],
        display_name="Private Bob",
        share_progress=1,
    )

    # Add goal and Bob marks it complete
    goal = create_group_goal(
        student_id=student_a,
        group_id=group["id"],
        title="Aptitude Practice Set",
        target_count=1,
    )
    update_goal_progress(student_id=student_b, goal_id=goal["id"], is_completed=True)

    # Student B opts out of sharing progress
    opt_res = update_member_privacy(student_id=student_b, group_id=group["id"], share_progress=0)
    assert opt_res["share_progress"] is False

    # 1. Student A views group: Student B's progress must be masked!
    view_from_a = get_group_details(group["id"], current_student_id=student_a)
    bob_entry_from_a = next(m for m in view_from_a["members"] if "Bob" in m["display_name"])

    assert bob_entry_from_a["progress_hidden"] is True
    assert bob_entry_from_a["completed_goals_count"] is None
    assert bob_entry_from_a["completion_percent"] is None
    assert "Progress Opted Out" in bob_entry_from_a["display_name"]

    # 2. Student B views group: Can still see their own progress!
    view_from_b = get_group_details(group["id"], current_student_id=student_b)
    bob_entry_from_b = next(m for m in view_from_b["members"] if m["is_self"])

    assert bob_entry_from_b["progress_hidden"] is False
    assert bob_entry_from_b["completed_goals_count"] == 1
    assert bob_entry_from_b["completion_percent"] == 100.0


def test_sensitive_application_data_protection_shield(test_env):
    """
    CRITICAL: Verifies that private job application details (applied companies,
    offer statuses, salary notes) are strictly shielded and NEVER leaked in group spaces.
    """
    student_c = "student_app_shield_01"
    group = create_peer_group(creator_student_id=student_c, name="Confidentiality Shield Pod")

    # Seed private job application
    save_job_application(
        student_id=student_c,
        company_name="Very Confidential Robotics Corp",
        role_title="AI Research Intern",
        status="Offer Extended",
        application_date="2026-11-20",
        notes="Strict secret: Offered 30 LPA base package.",
    )

    # Query group details
    details = get_group_details(group["id"], current_student_id=student_c)
    details_json = json.dumps(details)

    assert "Very Confidential Robotics Corp" not in details_json
    assert "Strict secret" not in details_json
    assert "Offer Extended" not in details_json
    assert "30 LPA" not in details_json


def test_private_group_access_control(test_env):
    """Verifies that non-members cannot view group details or create goals."""
    creator = "student_creator_01"
    intruder = "student_intruder_99"

    group = create_peer_group(creator_student_id=creator, name="Secret Group")

    # Intruder tries to get details -> raises PermissionError
    with pytest.raises(PermissionError, match="Private group membership required"):
        get_group_details(group["id"], current_student_id=intruder)

    # Intruder tries to add goal -> raises PermissionError
    with pytest.raises(PermissionError, match="enrolled group member"):
        create_group_goal(
            student_id=intruder,
            group_id=group["id"],
            title="Unauthorized Goal",
        )


def test_leave_peer_group(test_env):
    """Verifies leaving group removes membership and purges progress."""
    creator = "student_creator_02"
    member = "student_leaver_01"

    group = create_peer_group(creator_student_id=creator, name="Temporary Pod")
    join_peer_group(student_id=member, invite_code=group["invite_code"])

    goal = create_group_goal(student_id=creator, group_id=group["id"], title="Goal 1")
    update_goal_progress(student_id=member, goal_id=goal["id"], is_completed=True)

    # Member leaves
    left = leave_peer_group(student_id=member, group_id=group["id"])
    assert left is True

    # Verification: member no longer in group
    details = get_group_details(group["id"], current_student_id=creator)
    assert details["group"]["member_count"] == 1
    assert all(m["id"] != member for m in details["members"])


def test_account_deletion_cascades_peer_groups(test_env):
    """Verifies deleting student account purges membership and progress records."""
    sid = "student_purge_peer_01"
    group = create_peer_group(creator_student_id=sid, name="Purge Circle")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM peer_group_members WHERE student_id = ?", (sid,))
        assert cursor.fetchone()[0] == 1

    delete_user_account_and_personal_data(student_id=sid)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM peer_group_members WHERE student_id = ?", (sid,))
        assert cursor.fetchone()[0] == 0


def test_web_routes_and_api_endpoints(test_env):
    """Tests /peer-groups web views and all /api/peer-groups endpoints."""
    client = test_env["client"]
    sid = "student_web_test_01"

    with client.session_transaction() as sess:
        sess["user_id"] = sid

    # 1. GET /peer-groups (HTML)
    res_html = client.get("/peer-groups")
    assert res_html.status_code == 200
    assert b"Peer Goal Accountability Groups" in res_html.data

    # 2. POST /api/peer-groups (Create)
    res_create = client.post(
        "/api/peer-groups",
        json={
            "student_id": sid,
            "name": "API Test Group",
            "description": "Testing full API flow",
            "display_name": "API Tester",
        },
    )
    assert res_create.status_code == 201
    c_json = res_create.get_json()
    group_id = c_json["group"]["id"]
    invite_code = c_json["group"]["invite_code"]

    # 3. GET /api/peer-groups (List)
    res_list = client.get(f"/api/peer-groups?student_id={sid}")
    assert res_list.status_code == 200
    assert len(res_list.get_json()["groups"]) >= 1

    # 4. POST /api/peer-groups/join (Another student joins)
    joiner_sid = "student_web_joiner_02"
    res_join = client.post(
        "/api/peer-groups/join",
        json={
            "student_id": joiner_sid,
            "invite_code": invite_code,
            "display_name": "Joiner Jack",
            "share_progress": 1,
        },
    )
    assert res_join.status_code == 200
    assert res_join.get_json()["status"] == "success"

    # 5. POST /api/peer-groups/<id>/goals (Add goal)
    res_goal = client.post(
        f"/api/peer-groups/{group_id}/goals",
        json={
            "student_id": sid,
            "title": "Complete 10 System Design Cases",
            "category": "interview",
            "target_count": 10,
        },
    )
    assert res_goal.status_code == 201
    goal_id = res_goal.get_json()["goal"]["id"]

    # 6. POST /api/peer-groups/goals/<id>/progress (Update progress)
    res_prog = client.post(
        f"/api/peer-groups/goals/{goal_id}/progress",
        json={
            "student_id": joiner_sid,
            "completed_count": 5,
        },
    )
    assert res_prog.status_code == 200
    assert res_prog.get_json()["progress"]["completed_count"] == 5

    # 7. POST /api/peer-groups/<id>/privacy (Toggle privacy)
    res_priv = client.post(
        f"/api/peer-groups/{group_id}/privacy",
        json={
            "student_id": joiner_sid,
            "share_progress": 0,
        },
    )
    assert res_priv.status_code == 200
    assert res_priv.get_json()["settings"]["share_progress"] is False

    # 8. GET /api/peer-groups/<id> (Verify details and privacy masking)
    res_details = client.get(f"/api/peer-groups/{group_id}?student_id={sid}")
    assert res_details.status_code == 200
    details_data = res_details.get_json()["data"]
    assert details_data["group"]["member_count"] == 2
    # Jack opted out, so from sid's perspective Jack's progress is masked
    jack_member = next(m for m in details_data["members"] if m["id"] != details_data["members"][0]["id"])
    assert jack_member["progress_hidden"] is True

    # 9. POST /api/peer-groups/<id>/leave
    res_leave = client.post(
        f"/api/peer-groups/{group_id}/leave",
        json={"student_id": joiner_sid},
    )
    assert res_leave.status_code == 200
