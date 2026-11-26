"""
Peer Goal Accountability Groups Service
=======================================
Enables students to form private peer accountability circles for campus placement prep:
- Create private groups with unique invite codes (e.g. PEER-A1B2C3D4).
- Join via invite code without public exposure.
- Set shared placement milestones: coding goals, mock interview targets, aptitude tests, and general prep tasks.
- Mark tasks and milestones as complete with real-time peer progress tallies.
- Privacy-Preserving Opt-Out: Members can opt out of sharing their progress with group peers at any time.
- Sensitive Data Protection Shield: Strictly prevents leaking confidential job application details,
  applied company names, offer details, or personal candidate notes into group spaces.
"""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
import json
import logging
import secrets
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

from utils.database import get_db_connection

logger = logging.getLogger(__name__)

SUPPORTED_GOAL_CATEGORIES = [
    "coding",
    "interview",
    "resume",
    "aptitude",
    "general",
]

CATEGORY_META: Dict[str, Dict[str, str]] = {
    "coding": {
        "label": "Coding Practice",
        "icon": "fa-laptop-code",
        "badge_class": "bg-success text-white",
        "color": "#16a34a",
    },
    "interview": {
        "label": "Mock Interview",
        "icon": "fa-comments",
        "badge_class": "bg-info text-dark",
        "color": "#0284c7",
    },
    "resume": {
        "label": "Resume Polish",
        "icon": "fa-file-lines",
        "badge_class": "bg-primary text-white",
        "color": "#2563eb",
    },
    "aptitude": {
        "label": "Aptitude Prep",
        "icon": "fa-calculator",
        "badge_class": "bg-warning text-dark",
        "color": "#d97706",
    },
    "general": {
        "label": "General Milestone",
        "icon": "fa-bullseye",
        "badge_class": "bg-secondary text-white",
        "color": "#64748b",
    },
}


def _generate_invite_code() -> str:
    """Generates an 8-character uppercase invite code like PEER-4A8F9C12."""
    code_part = secrets.token_hex(4).upper()
    return f"PEER-{code_part}"


def init_peer_groups_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    """Ensures peer accountability tables and indices exist."""
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS peer_accountability_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                creator_student_id TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                is_private INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_groups_invite ON peer_accountability_groups (invite_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_groups_creator ON peer_accountability_groups (creator_student_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS peer_group_members (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                share_progress INTEGER DEFAULT 1,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, student_id),
                FOREIGN KEY (group_id) REFERENCES peer_accountability_groups(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_members_student ON peer_group_members (student_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_members_group ON peer_group_members (group_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS peer_group_goals (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                target_count INTEGER DEFAULT 1,
                target_date TEXT,
                description TEXT,
                created_by_student_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES peer_accountability_groups(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_goals_group ON peer_group_goals (group_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS peer_group_member_progress (
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                completed_count INTEGER DEFAULT 0,
                is_completed INTEGER DEFAULT 0,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(goal_id, student_id),
                FOREIGN KEY (goal_id) REFERENCES peer_group_goals(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_progress_goal_student ON peer_group_member_progress (goal_id, student_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_peer_progress_student ON peer_group_member_progress (student_id)")
        conn.commit()
    finally:
        if should_close:
            conn.close()


def create_peer_group(
    creator_student_id: str,
    name: str,
    description: Optional[str] = None,
    display_name: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Creates a new private peer accountability circle and enrolls the creator as admin.
    """
    sid = (creator_student_id or "").strip()
    if not sid:
        raise ValueError("creator_student_id is required.")

    g_name = (name or "").strip()
    if not g_name:
        raise ValueError("Group name is required.")

    desc = (description or "").strip()
    d_name = (display_name or "").strip() or sid

    group_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())
    invite_code = _generate_invite_code()
    now_iso = datetime.now(timezone.utc).isoformat()

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        # Retry if invite code collision (rare)
        for _ in range(5):
            cursor.execute("SELECT id FROM peer_accountability_groups WHERE invite_code = ?", (invite_code,))
            if not cursor.fetchone():
                break
            invite_code = _generate_invite_code()

        cursor.execute(
            """
            INSERT INTO peer_accountability_groups (
                id, name, description, creator_student_id, invite_code, is_private, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (group_id, g_name, desc, sid, invite_code, now_iso, now_iso),
        )

        cursor.execute(
            """
            INSERT INTO peer_group_members (
                id, group_id, student_id, display_name, role, share_progress, joined_at
            ) VALUES (?, ?, ?, ?, 'admin', 1, ?)
            """,
            (member_id, group_id, sid, d_name, now_iso),
        )
        conn.commit()

        return {
            "id": group_id,
            "name": g_name,
            "description": desc,
            "creator_student_id": sid,
            "invite_code": invite_code,
            "is_private": True,
            "member_count": 1,
            "my_role": "admin",
            "share_progress": True,
            "created_at": now_iso,
        }
    finally:
        if should_close:
            conn.close()


def join_peer_group(
    student_id: str,
    invite_code: str,
    display_name: Optional[str] = None,
    share_progress: int = 1,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Enrolls a student into a private accountability group via its invite code.
    Auto-initializes the member's progress tracking rows for existing group goals.
    """
    sid = (student_id or "").strip()
    if not sid:
        raise ValueError("student_id is required.")

    inv_code = (invite_code or "").strip().upper()
    if not inv_code:
        raise ValueError("Invite code is required to join a private group.")

    d_name = (display_name or "").strip() or sid
    share_flag = 1 if share_progress else 0

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, name, description, creator_student_id, invite_code, created_at
            FROM peer_accountability_groups
            WHERE invite_code = ?
            """,
            (inv_code,),
        )
        group_row = cursor.fetchone()
        if not group_row:
            raise ValueError("Invalid or expired invite code.")

        group_id = group_row["id"]

        # Check existing membership
        cursor.execute(
            "SELECT id, role, share_progress, display_name FROM peer_group_members WHERE group_id = ? AND student_id = ?",
            (group_id, sid),
        )
        existing = cursor.fetchone()
        if existing:
            return {
                "group_id": group_id,
                "name": group_row["name"],
                "description": group_row["description"],
                "already_member": True,
                "role": existing["role"],
                "share_progress": bool(existing["share_progress"]),
                "display_name": existing["display_name"],
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        member_id = str(uuid.uuid4())

        cursor.execute(
            """
            INSERT INTO peer_group_members (
                id, group_id, student_id, display_name, role, share_progress, joined_at
            ) VALUES (?, ?, ?, ?, 'member', ?, ?)
            """,
            (member_id, group_id, sid, d_name, share_flag, now_iso),
        )

        # Initialize progress rows for all existing group goals
        cursor.execute("SELECT id FROM peer_group_goals WHERE group_id = ?", (group_id,))
        goal_rows = cursor.fetchall()
        for g in goal_rows:
            prog_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT OR IGNORE INTO peer_group_member_progress (
                    id, goal_id, student_id, completed_count, is_completed, updated_at
                ) VALUES (?, ?, ?, 0, 0, ?)
                """,
                (prog_id, g["id"], sid, now_iso),
            )

        conn.commit()

        return {
            "group_id": group_id,
            "name": group_row["name"],
            "description": group_row["description"],
            "already_member": False,
            "role": "member",
            "share_progress": bool(share_flag),
            "display_name": d_name,
        }
    finally:
        if should_close:
            conn.close()


def leave_peer_group(
    student_id: str,
    group_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> bool:
    """Removes student from peer accountability group and purges progress records."""
    sid = (student_id or "").strip()
    gid = (group_id or "").strip()
    if not sid or not gid:
        raise ValueError("student_id and group_id are required.")

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        # Delete goal progress rows
        cursor.execute(
            """
            DELETE FROM peer_group_member_progress
            WHERE student_id = ? AND goal_id IN (SELECT id FROM peer_group_goals WHERE group_id = ?)
            """,
            (sid, gid),
        )

        # Remove from members
        cursor.execute(
            "DELETE FROM peer_group_members WHERE group_id = ? AND student_id = ?",
            (gid, sid),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        if should_close:
            conn.close()


def update_member_privacy(
    student_id: str,
    group_id: str,
    share_progress: int,
    display_name: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Updates a member's progress visibility opt-in/opt-out status and display name.
    """
    sid = (student_id or "").strip()
    gid = (group_id or "").strip()
    if not sid or not gid:
        raise ValueError("student_id and group_id are required.")

    share_flag = 1 if share_progress else 0

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, display_name FROM peer_group_members WHERE group_id = ? AND student_id = ?",
            (gid, sid),
        )
        row = cursor.fetchone()
        if not row:
            raise PermissionError("Student is not a member of this accountability group.")

        effective_name = (display_name or "").strip() or row["display_name"]

        cursor.execute(
            """
            UPDATE peer_group_members
            SET share_progress = ?, display_name = ?
            WHERE group_id = ? AND student_id = ?
            """,
            (share_flag, effective_name, gid, sid),
        )
        conn.commit()

        return {
            "group_id": gid,
            "student_id": sid,
            "share_progress": bool(share_flag),
            "display_name": effective_name,
            "status": "opted_in" if share_flag else "opted_out",
        }
    finally:
        if should_close:
            conn.close()


def create_group_goal(
    student_id: str,
    group_id: str,
    title: str,
    category: str = "general",
    target_count: int = 1,
    target_date: Optional[str] = None,
    description: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Adds a new shared placement preparation goal or task to the group.
    Initializes tracking entries for all current members.
    """
    sid = (student_id or "").strip()
    gid = (group_id or "").strip()
    t_str = (title or "").strip()

    if not sid or not gid:
        raise ValueError("student_id and group_id are required.")
    if not t_str:
        raise ValueError("Goal title is required.")

    cat = (category or "general").strip().lower()
    if cat not in SUPPORTED_GOAL_CATEGORIES:
        cat = "general"

    t_count = max(1, int(target_count or 1))
    desc = (description or "").strip()
    t_date = (target_date or "").strip() or None

    goal_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        # Check membership
        cursor.execute(
            "SELECT id FROM peer_group_members WHERE group_id = ? AND student_id = ?",
            (gid, sid),
        )
        if not cursor.fetchone():
            raise PermissionError("You must be an enrolled group member to create shared goals.")

        cursor.execute(
            """
            INSERT INTO peer_group_goals (
                id, group_id, title, category, target_count, target_date, description,
                created_by_student_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (goal_id, gid, t_str, cat, t_count, t_date, desc, sid, now_iso),
        )

        # Initialize progress rows for all members in group
        cursor.execute("SELECT student_id FROM peer_group_members WHERE group_id = ?", (gid,))
        members = cursor.fetchall()
        for m in members:
            prog_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT OR IGNORE INTO peer_group_member_progress (
                    id, goal_id, student_id, completed_count, is_completed, updated_at
                ) VALUES (?, ?, ?, 0, 0, ?)
                """,
                (prog_id, goal_id, m["student_id"], now_iso),
            )

        conn.commit()

        meta = CATEGORY_META.get(cat, CATEGORY_META["general"])
        return {
            "id": goal_id,
            "group_id": gid,
            "title": t_str,
            "category": cat,
            "category_label": meta["label"],
            "icon": meta["icon"],
            "target_count": t_count,
            "target_date": t_date,
            "description": desc,
            "created_by_student_id": sid,
            "created_at": now_iso,
        }
    finally:
        if should_close:
            conn.close()


def update_goal_progress(
    student_id: str,
    goal_id: str,
    completed_count: Optional[int] = None,
    is_completed: Optional[bool] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Updates a student's completion progress on a shared group task/goal.
    Auto-marks complete if completed_count >= target_count.
    """
    sid = (student_id or "").strip()
    gid = (goal_id or "").strip()
    if not sid or not gid:
        raise ValueError("student_id and goal_id are required.")

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        # Find goal and verify student is in group
        cursor.execute(
            """
            SELECT g.id, g.group_id, g.target_count, g.title
            FROM peer_group_goals g
            JOIN peer_group_members m ON g.group_id = m.group_id
            WHERE g.id = ? AND m.student_id = ?
            """,
            (gid, sid),
        )
        goal_row = cursor.fetchone()
        if not goal_row:
            raise PermissionError("Goal not found or student is not an enrolled member of this group.")

        target = goal_row["target_count"]

        # Find existing progress row
        cursor.execute(
            "SELECT id, completed_count, is_completed FROM peer_group_member_progress WHERE goal_id = ? AND student_id = ?",
            (gid, sid),
        )
        prog_row = cursor.fetchone()

        curr_count = prog_row["completed_count"] if prog_row else 0
        curr_done = bool(prog_row["is_completed"]) if prog_row else False

        new_count = int(completed_count) if completed_count is not None else curr_count
        new_count = max(0, new_count)

        if is_completed is not None:
            new_done = bool(is_completed)
            if new_done and new_count == 0:
                new_count = target
        else:
            new_done = new_count >= target

        now_iso = datetime.now(timezone.utc).isoformat()
        comp_time = now_iso if new_done else None

        if prog_row:
            cursor.execute(
                """
                UPDATE peer_group_member_progress
                SET completed_count = ?, is_completed = ?, completed_at = ?, updated_at = ?
                WHERE goal_id = ? AND student_id = ?
                """,
                (new_count, 1 if new_done else 0, comp_time, now_iso, gid, sid),
            )
        else:
            prog_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO peer_group_member_progress (
                    id, goal_id, student_id, completed_count, is_completed, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (prog_id, gid, sid, new_count, 1 if new_done else 0, comp_time, now_iso),
            )

        conn.commit()

        return {
            "goal_id": gid,
            "student_id": sid,
            "completed_count": new_count,
            "target_count": target,
            "is_completed": new_done,
            "completed_at": comp_time,
            "percent": min(100.0, round((new_count / max(1, target)) * 100, 1)),
        }
    finally:
        if should_close:
            conn.close()


def get_group_details(
    group_id: str,
    current_student_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Retrieves full private group dashboard:
    - Group info & invite code
    - Shared goals with progress metrics
    - Member list with STRICT PRIVACY ENFORCEMENT:
        * Members who opted out (share_progress = 0) have their progress masked from peers.
        * Sensitive application information (job applications, applied companies, offer details)
          is strictly excluded by design.
    """
    gid = (group_id or "").strip()
    sid = (current_student_id or "").strip() if current_student_id else None

    if not gid:
        raise ValueError("group_id is required.")

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, name, description, creator_student_id, invite_code, created_at
            FROM peer_accountability_groups
            WHERE id = ?
            """,
            (gid,),
        )
        group_row = cursor.fetchone()
        if not group_row:
            raise KeyError("Accountability group not found.")

        # Check membership if current_student_id is specified
        current_member = None
        if sid:
            cursor.execute(
                "SELECT id, role, display_name, share_progress FROM peer_group_members WHERE group_id = ? AND student_id = ?",
                (gid, sid),
            )
            current_member = cursor.fetchone()
            if not current_member:
                raise PermissionError("Access denied: Private group membership required.")

        # Fetch all members
        cursor.execute(
            """
            SELECT id, student_id, display_name, role, share_progress, joined_at
            FROM peer_group_members
            WHERE group_id = ?
            ORDER BY joined_at ASC
            """,
            (gid,),
        )
        member_rows = cursor.fetchall()

        # Fetch all goals
        cursor.execute(
            """
            SELECT id, title, category, target_count, target_date, description,
                   created_by_student_id, created_at
            FROM peer_group_goals
            WHERE group_id = ?
            ORDER BY created_at ASC
            """,
            (gid,),
        )
        goal_rows = cursor.fetchall()
        goal_ids = [g["id"] for g in goal_rows]

        # Fetch progress across all goals
        progress_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if goal_ids:
            placeholders = ",".join("?" for _ in goal_ids)
            cursor.execute(
                f"""
                SELECT goal_id, student_id, completed_count, is_completed, completed_at
                FROM peer_group_member_progress
                WHERE goal_id IN ({placeholders})
                """,
                tuple(goal_ids),
            )
            for p in cursor.fetchall():
                progress_map[(p["goal_id"], p["student_id"])] = dict(p)

        # Assemble sanitized members list (enforcing opt-out privacy)
        total_group_goals = len(goal_rows)
        sanitized_members: List[Dict[str, Any]] = []

        for m in member_rows:
            m_sid = m["student_id"]
            is_self = (sid is not None and m_sid == sid)
            opted_out = (m["share_progress"] == 0)

            # Count completed goals for this member
            member_completed_count = 0
            for g in goal_rows:
                p_item = progress_map.get((g["id"], m_sid))
                if p_item and p_item["is_completed"]:
                    member_completed_count += 1

            completion_pct = (
                round((member_completed_count / total_group_goals) * 100, 1)
                if total_group_goals > 0
                else 0.0
            )

            # Enforce opt-out privacy:
            # If opted out and not self, mask progress stats and display label
            if opted_out and not is_self:
                sanitized_members.append({
                    "id": m["id"],
                    "display_name": f"{m['display_name']} (Progress Opted Out)",
                    "role": m["role"],
                    "is_self": False,
                    "share_progress": False,
                    "progress_hidden": True,
                    "completed_goals_count": None,
                    "completion_percent": None,
                    "joined_at": m["joined_at"],
                })
            else:
                sanitized_members.append({
                    "id": m["id"],
                    "display_name": m["display_name"],
                    "role": m["role"],
                    "is_self": is_self,
                    "share_progress": bool(m["share_progress"]),
                    "progress_hidden": False,
                    "completed_goals_count": member_completed_count,
                    "completion_percent": completion_pct,
                    "joined_at": m["joined_at"],
                })

        # Assemble goals list with group aggregate completion tallies
        total_member_count = len(member_rows)
        sanitized_goals: List[Dict[str, Any]] = []

        for g in goal_rows:
            gid_str = g["id"]
            meta = CATEGORY_META.get(g["category"], CATEGORY_META["general"])

            # Count how many members completed this goal
            completed_members_tally = 0
            for m in member_rows:
                p_item = progress_map.get((gid_str, m["student_id"]))
                if p_item and p_item["is_completed"]:
                    completed_members_tally += 1

            group_comp_rate = (
                round((completed_members_tally / total_member_count) * 100, 1)
                if total_member_count > 0
                else 0.0
            )

            # Current student's personal progress on this goal
            my_prog = None
            if sid:
                p_self = progress_map.get((gid_str, sid), {})
                my_done = bool(p_self.get("is_completed", False))
                my_count = p_self.get("completed_count", 0)
                my_prog = {
                    "is_completed": my_done,
                    "completed_count": my_count,
                    "target_count": g["target_count"],
                    "percent": min(100.0, round((my_count / max(1, g["target_count"])) * 100, 1)),
                }

            sanitized_goals.append({
                "id": gid_str,
                "group_id": g["group_id"],
                "title": g["title"],
                "category": g["category"],
                "category_label": meta["label"],
                "icon": meta["icon"],
                "color": meta["color"],
                "badge_class": meta["badge_class"],
                "target_count": g["target_count"],
                "target_date": g["target_date"],
                "description": g["description"] or "",
                "completed_members_count": completed_members_tally,
                "total_members_count": total_member_count,
                "group_completion_rate": group_comp_rate,
                "my_progress": my_prog,
                "created_at": g["created_at"],
            })

        return {
            "group": {
                "id": group_row["id"],
                "name": group_row["name"],
                "description": group_row["description"] or "",
                "creator_student_id": group_row["creator_student_id"],
                "invite_code": group_row["invite_code"],
                "is_private": True,
                "member_count": total_member_count,
                "goal_count": total_group_goals,
                "created_at": group_row["created_at"],
            },
            "my_membership": {
                "is_member": current_member is not None,
                "role": current_member["role"] if current_member else None,
                "display_name": current_member["display_name"] if current_member else None,
                "share_progress": bool(current_member["share_progress"]) if current_member else False,
            } if current_member else None,
            "members": sanitized_members,
            "goals": sanitized_goals,
            "privacy_notice": (
                "Privacy Guarantee: Peer accountability groups only share placement goal tasks. "
                "Personal job applications, target companies, and confidential notes are never shared."
            ),
        }
    finally:
        if should_close:
            conn.close()


def list_student_groups(
    student_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """
    Lists all peer accountability groups the student is currently enrolled in.
    """
    sid = (student_id or "").strip()
    if not sid:
        return []

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        init_peer_groups_tables(conn)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT g.id, g.name, g.description, g.invite_code, g.creator_student_id, g.created_at,
                   m.role, m.share_progress, m.display_name, m.joined_at,
                   (SELECT COUNT(*) FROM peer_group_members WHERE group_id = g.id) AS member_count,
                   (SELECT COUNT(*) FROM peer_group_goals WHERE group_id = g.id) AS goal_count
            FROM peer_accountability_groups g
            JOIN peer_group_members m ON g.id = m.group_id
            WHERE m.student_id = ?
            ORDER BY m.joined_at DESC
            """,
            (sid,),
        )

        groups: List[Dict[str, Any]] = []
        for r in cursor.fetchall():
            gid = r["id"]

            # Count student's completed goals in this group
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM peer_group_member_progress p
                JOIN peer_group_goals g ON p.goal_id = g.id
                WHERE g.group_id = ? AND p.student_id = ? AND p.is_completed = 1
                """,
                (gid, sid),
            )
            my_completed = cursor.fetchone()[0]

            groups.append({
                "id": gid,
                "name": r["name"],
                "description": r["description"] or "",
                "invite_code": r["invite_code"],
                "is_admin": (r["role"] == "admin"),
                "role": r["role"],
                "share_progress": bool(r["share_progress"]),
                "display_name": r["display_name"],
                "member_count": r["member_count"],
                "goal_count": r["goal_count"],
                "my_completed_goals": my_completed,
                "created_at": r["created_at"],
            })

        return groups
    finally:
        if should_close:
            conn.close()
