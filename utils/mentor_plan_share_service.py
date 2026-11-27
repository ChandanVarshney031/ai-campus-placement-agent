"""
Mentor Preparation Plan Sharing Service
=======================================
Enables students to generate secure, read-only preparation-plan links for mentors
or faculty advisors (#mentor-share):
- Cryptographically secure, unguessable URL tokens (secrets.token_urlsafe).
- Configurable expiration periods (24h, 7d, 30d, 90d, 1y).
- Selective sharing: students choose specific goals or their full roadmap.
- Privacy & Redaction by Design:
    * Strictly hides student personal notes (`notes` column in preparation_goals).
    * Hides all private job applications, offers, and company interview notes.
    * Hides student account credentials, email, phone, and internal database IDs.
- Tracks mentor engagement: view counts and last-viewed timestamps.
- Instant revocation: owner can deactivate any link at any time.
"""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone, timedelta
import json
import logging
import secrets
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

from utils.database import get_db_connection
from utils.goal_planner_service import PreparationGoalPlannerService

logger = logging.getLogger(__name__)

ALLOWED_EXPIRATION_CHOICES = {
    "24h": 24,
    "7d": 168,
    "30d": 720,
    "90d": 2160,
    "1y": 8760,
    "never": 87600,  # ~10 years
}


class PlanSharePermissionError(PermissionError):
    """Raised when an unauthorized student attempts to modify or revoke another student's share link."""
    pass


def init_shared_plans_table(conn: Optional[sqlite3.Connection] = None) -> None:
    """Ensures the shared_preparation_plans table and indices exist."""
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_preparation_plans (
                id TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                student_id TEXT NOT NULL,
                plan_title TEXT NOT NULL DEFAULT 'Placement Preparation Plan',
                mentor_name TEXT,
                mentor_email TEXT,
                selected_goal_ids_json TEXT NOT NULL DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                view_count INTEGER DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP,
                last_viewed_at TIMESTAMP
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_prep_token ON shared_preparation_plans (token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_prep_student ON shared_preparation_plans (student_id, is_active, created_at DESC)")
        conn.commit()
    finally:
        if should_close:
            conn.close()


def create_shared_plan_link(
    student_id: str,
    goal_ids: Optional[List[int]] = None,
    mentor_name: Optional[str] = None,
    mentor_email: Optional[str] = None,
    plan_title: Optional[str] = None,
    expiration: str = "30d",
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Creates a secure, read-only mentor share link for selected student goals.
    Validates goal ownership and enforces privacy boundaries.
    """
    sid = (student_id or "").strip()
    if not sid:
        raise ValueError("student_id is required to create a share link.")

    hours = ALLOWED_EXPIRATION_CHOICES.get(expiration.lower(), 720)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=hours)

    title = (plan_title or "").strip() or "Placement Preparation Plan"
    m_name = (mentor_name or "").strip() or None
    m_email = (mentor_email or "").strip() or None

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_shared_plans_table(conn)
        cursor = conn.cursor()

        # Query all goals belonging to this student
        cursor.execute(
            """
            SELECT id, goal_title, target_role, status
            FROM preparation_goals
            WHERE student_id = ?
            ORDER BY id ASC
            """,
            (sid,),
        )
        owned_goals = cursor.fetchall()
        owned_goal_ids = [g["id"] for g in owned_goals]

        selected_ids: List[int] = []
        if goal_ids:
            # Filter to goals actually owned by student
            for gid in goal_ids:
                try:
                    int_gid = int(gid)
                    if int_gid in owned_goal_ids:
                        selected_ids.append(int_gid)
                except (ValueError, TypeError):
                    continue
            if not selected_ids:
                raise ValueError("None of the specified goal IDs belong to this student.")
        else:
            # Default to all owned goals
            selected_ids = owned_goal_ids

        token = secrets.token_urlsafe(32)
        share_id = str(uuid.uuid4())
        selected_json = json.dumps(selected_ids)

        cursor.execute(
            """
            INSERT INTO shared_preparation_plans (
                id, token, student_id, plan_title, mentor_name, mentor_email,
                selected_goal_ids_json, is_active, view_count, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
            """,
            (
                share_id,
                token,
                sid,
                title,
                m_name,
                m_email,
                selected_json,
                expires_at.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()

        share_url = f"/shared/plan/{token}"

        return {
            "success": True,
            "share_id": share_id,
            "token": token,
            "share_url": share_url,
            "plan_title": title,
            "mentor_name": m_name,
            "mentor_email": m_email,
            "goal_count": len(selected_ids),
            "selected_goal_ids": selected_ids,
            "expires_at": expires_at.isoformat(),
            "created_at": now.isoformat(),
        }
    finally:
        if should_close:
            conn.close()


def get_shared_plan_by_token(
    token: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Validates token and returns sanitized, read-only preparation plan data for mentor view.
    Deeply redacts student personal notes and hides all private application details.
    """
    t_clean = (token or "").strip()
    if not t_clean:
        return False, None, "Invalid preparation plan token."

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_shared_plans_table(conn)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, token, student_id, plan_title, mentor_name, mentor_email,
                   selected_goal_ids_json, is_active, view_count, expires_at,
                   created_at, revoked_at, last_viewed_at
            FROM shared_preparation_plans
            WHERE token = ?
            """,
            (t_clean,),
        )
        share_row = cursor.fetchone()
        if not share_row:
            return False, None, "Shared preparation plan not found."

        # Check revocation
        if not share_row["is_active"]:
            return False, None, "This preparation plan share link has been revoked by the student."

        # Check expiration
        now = datetime.now(timezone.utc)
        expires_str = share_row["expires_at"]
        try:
            exp_dt = datetime.fromisoformat(expires_str)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if now > exp_dt:
                return False, None, "This preparation plan share link has expired."
        except Exception:
            pass

        # Update view stats
        new_view_count = (share_row["view_count"] or 0) + 1
        now_iso = now.isoformat()
        cursor.execute(
            """
            UPDATE shared_preparation_plans
            SET view_count = ?, last_viewed_at = ?
            WHERE token = ?
            """,
            (new_view_count, now_iso, t_clean),
        )
        conn.commit()

        # Parse selected goal IDs
        selected_ids: List[int] = []
        try:
            selected_ids = json.loads(share_row["selected_goal_ids_json"] or "[]")
        except Exception:
            selected_ids = []

        sid = share_row["student_id"]

        # Fetch goals
        planner = PreparationGoalPlannerService()
        goals_query = """
            SELECT id, goal_title, target_role, target_date, target_coding_problems,
                   completed_coding_problems, target_mock_interviews, completed_mock_interviews,
                   resume_completed, status, created_at
            FROM preparation_goals
            WHERE student_id = ?
        """
        # NOTE: 'notes' column is intentionally NOT selected to strictly protect student privacy!

        params: List[Any] = [sid]
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            goals_query += f" AND id IN ({placeholders})"
            params.extend(selected_ids)

        goals_query += " ORDER BY target_date ASC, id ASC"

        cursor.execute(goals_query, tuple(params))
        goal_rows = cursor.fetchall()

        sanitized_goals: List[Dict[str, Any]] = []
        for r in goal_rows:
            r_dict = dict(r)
            progress = planner.calculate_goal_progress(r_dict)
            sanitized_goals.append({
                "id": r_dict["id"],
                "goal_title": r_dict["goal_title"],
                "target_role": r_dict["target_role"],
                "target_date": r_dict["target_date"],
                "target_coding_problems": r_dict["target_coding_problems"],
                "completed_coding_problems": r_dict["completed_coding_problems"],
                "target_mock_interviews": r_dict["target_mock_interviews"],
                "completed_mock_interviews": r_dict["completed_mock_interviews"],
                "resume_completed": bool(r_dict["resume_completed"]),
                "status": r_dict["status"],
                "created_at": r_dict["created_at"],
                "progress_metrics": progress,
                # Explicit privacy guarantee: notes is omitted/null
                "notes": None,
            })

        # Calculate high-level summary
        total_goals = len(sanitized_goals)
        active_goals = len([g for g in sanitized_goals if g["status"] == "ACTIVE"])
        completed_goals = len([g for g in sanitized_goals if g["status"] == "COMPLETED"])
        avg_progress = (
            round(sum(g["progress_metrics"]["overall_progress_percent"] for g in sanitized_goals) / total_goals, 1)
            if total_goals > 0
            else 0.0
        )

        # Retrieve streak for momentum indicator if table exists
        streak_days = 0
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='student_streaks'")
        if cursor.fetchone():
            cursor.execute("SELECT current_streak FROM student_streaks WHERE student_id = ?", (sid,))
            s_row = cursor.fetchone()
            if s_row:
                streak_days = s_row["current_streak"] or 0

        # Candidate display handle (e.g. from student_profiles, redacting email/phone)
        candidate_name = "Candidate"
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='student_profiles'")
        if cursor.fetchone():
            cursor.execute("SELECT full_name, department FROM student_profiles WHERE student_id = ?", (sid,))
            p_row = cursor.fetchone()
            if p_row and p_row["full_name"]:
                candidate_name = p_row["full_name"]

        target_roles = sorted(list(set(g["target_role"] for g in sanitized_goals if g["target_role"])))

        summary = {
            "candidate_name": candidate_name,
            "total_goals": total_goals,
            "active_goals": active_goals,
            "completed_goals": completed_goals,
            "avg_progress_percent": avg_progress,
            "streak_days": streak_days,
            "target_roles": target_roles,
        }

        plan_data = {
            "share_id": share_row["id"],
            "token": share_row["token"],
            "plan_title": share_row["plan_title"],
            "mentor_name": share_row["mentor_name"],
            "mentor_email": share_row["mentor_email"],
            "created_at": share_row["created_at"],
            "expires_at": share_row["expires_at"],
            "view_count": new_view_count,
            "last_viewed_at": now_iso,
            "goals": sanitized_goals,
            "summary": summary,
            "is_read_only_mentor_view": True,
            "privacy_notice": (
                "Privacy Guarantee: Candidate personal notes and confidential job application "
                "details are hidden from this mentor view."
            ),
        }

        return True, plan_data, None
    finally:
        if should_close:
            conn.close()


def revoke_shared_plan_link(
    student_id: str,
    share_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> bool:
    """
    Revokes an active preparation plan share link.
    Guarantees that only the student owner can revoke the link.
    """
    sid = (student_id or "").strip()
    sh_id = (share_id or "").strip()

    if not sid or not sh_id:
        raise ValueError("student_id and share_id are required.")

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_shared_plans_table(conn)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT student_id, is_active FROM shared_preparation_plans WHERE id = ?",
            (sh_id,),
        )
        row = cursor.fetchone()
        if not row:
            return False

        if row["student_id"] != sid:
            raise PlanSharePermissionError("You do not have permission to revoke this share link.")

        now_iso = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE shared_preparation_plans
            SET is_active = 0, revoked_at = ?
            WHERE id = ? AND student_id = ?
            """,
            (now_iso, sh_id, sid),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        if should_close:
            conn.close()


def list_student_shared_plans(
    student_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """
    Lists all preparation plan share links created by the student,
    including active/expired status and view metrics.
    """
    sid = (student_id or "").strip()
    if not sid:
        return []

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_shared_plans_table(conn)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, token, plan_title, mentor_name, mentor_email,
                   selected_goal_ids_json, is_active, view_count, expires_at,
                   created_at, revoked_at, last_viewed_at
            FROM shared_preparation_plans
            WHERE student_id = ?
            ORDER BY created_at DESC
            """,
            (sid,),
        )

        now = datetime.now(timezone.utc)
        shares: List[Dict[str, Any]] = []

        for row in cursor.fetchall():
            goal_ids = []
            try:
                goal_ids = json.loads(row["selected_goal_ids_json"] or "[]")
            except Exception:
                goal_ids = []

            is_expired = False
            try:
                exp_dt = datetime.fromisoformat(row["expires_at"])
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                is_expired = now > exp_dt
            except Exception:
                pass

            status = "revoked" if not row["is_active"] else ("expired" if is_expired else "active")

            shares.append({
                "share_id": row["id"],
                "token": row["token"],
                "share_url": f"/shared/plan/{row['token']}",
                "plan_title": row["plan_title"],
                "mentor_name": row["mentor_name"],
                "mentor_email": row["mentor_email"],
                "goal_count": len(goal_ids),
                "is_active": bool(row["is_active"]),
                "is_expired": is_expired,
                "status": status,
                "view_count": row["view_count"] or 0,
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "last_viewed_at": row["last_viewed_at"],
                "revoked_at": row["revoked_at"],
            })

        return shares
    finally:
        if should_close:
            conn.close()
