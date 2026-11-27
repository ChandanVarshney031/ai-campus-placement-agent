"""
Placement Readiness Activity Timeline Service
============================================
Tracks and aggregates student placement activities chronologically across:
1. Resume Revisions: Uploads, iterations, ATS scoring progress, and target roles.
2. Practice Sessions: Coding submissions, aptitude tests, and group discussion attempts.
3. Applications: Job applications tracked, stage transitions, and statuses.
4. Interviews: Mock technical and behavioral interview sessions and shared experiences.
5. Feedback: Resume section evaluations, mentor notes, and platform feedback.
6. Achievements: Automatic milestones (streaks, high ATS, practice goals) and custom student accomplishments.

Provides a unified chronological audit trail giving students and coordinators a clear
picture of sustained effort and preparation momentum.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, date, timezone
from typing import Dict, Any, List, Optional, Tuple

from utils.database import get_db_connection

logger = logging.getLogger(__name__)

# Canonical categories
CATEGORY_RESUME_REVISIONS = "resume_revisions"
CATEGORY_PRACTICE_SESSIONS = "practice_sessions"
CATEGORY_APPLICATIONS = "applications"
CATEGORY_INTERVIEWS = "interviews"
CATEGORY_FEEDBACK = "feedback"
CATEGORY_ACHIEVEMENTS = "achievements"

ALL_CATEGORIES = [
    CATEGORY_RESUME_REVISIONS,
    CATEGORY_PRACTICE_SESSIONS,
    CATEGORY_APPLICATIONS,
    CATEGORY_INTERVIEWS,
    CATEGORY_FEEDBACK,
    CATEGORY_ACHIEVEMENTS,
]

CATEGORY_META: Dict[str, Dict[str, str]] = {
    CATEGORY_RESUME_REVISIONS: {
        "label": "Resume Revisions",
        "icon": "fa-file-lines",
        "badge_class": "bg-primary text-white",
        "border_class": "border-primary",
        "color": "#2563eb",
    },
    CATEGORY_PRACTICE_SESSIONS: {
        "label": "Practice Sessions",
        "icon": "fa-laptop-code",
        "badge_class": "bg-success text-white",
        "border_class": "border-success",
        "color": "#16a34a",
    },
    CATEGORY_APPLICATIONS: {
        "label": "Applications",
        "icon": "fa-briefcase",
        "badge_class": "bg-warning text-dark",
        "border_class": "border-warning",
        "color": "#d97706",
    },
    CATEGORY_INTERVIEWS: {
        "label": "Interviews",
        "icon": "fa-comments",
        "badge_class": "bg-info text-dark",
        "border_class": "border-info",
        "color": "#0284c7",
    },
    CATEGORY_FEEDBACK: {
        "label": "Feedback",
        "icon": "fa-thumbs-up",
        "badge_class": "bg-secondary text-white",
        "border_class": "border-secondary",
        "color": "#64748b",
    },
    CATEGORY_ACHIEVEMENTS: {
        "label": "Achievements",
        "icon": "fa-trophy",
        "badge_class": "bg-warning text-dark fw-bold",
        "border_class": "border-warning",
        "color": "#f59e0b",
    },
}


def _normalize_category(cat: Optional[str]) -> Optional[str]:
    """Normalizes category input to canonical representation."""
    if not cat:
        return None
    c = cat.strip().lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "resume": CATEGORY_RESUME_REVISIONS,
        "resumes": CATEGORY_RESUME_REVISIONS,
        "resume_revision": CATEGORY_RESUME_REVISIONS,
        "practice": CATEGORY_PRACTICE_SESSIONS,
        "practice_session": CATEGORY_PRACTICE_SESSIONS,
        "coding": CATEGORY_PRACTICE_SESSIONS,
        "aptitude": CATEGORY_PRACTICE_SESSIONS,
        "gd": CATEGORY_PRACTICE_SESSIONS,
        "application": CATEGORY_APPLICATIONS,
        "job_applications": CATEGORY_APPLICATIONS,
        "interview": CATEGORY_INTERVIEWS,
        "mock_interview": CATEGORY_INTERVIEWS,
        "feedbacks": CATEGORY_FEEDBACK,
        "review": CATEGORY_FEEDBACK,
        "achievement": CATEGORY_ACHIEVEMENTS,
        "milestone": CATEGORY_ACHIEVEMENTS,
        "milestones": CATEGORY_ACHIEVEMENTS,
    }
    return alias_map.get(c, c if c in ALL_CATEGORIES else None)


def _parse_timestamp_to_iso(val: Any) -> Tuple[str, str, str]:
    """
    Parses various timestamp and date formats into:
    (iso_timestamp, event_date_str_yyyy_mm_dd, display_date_str)
    """
    if not val:
        now = datetime.now(timezone.utc)
        return now.isoformat(), now.strftime("%Y-%m-%d"), now.strftime("%b %d, %Y")

    dt = None
    if isinstance(val, (int, float)):
        try:
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
    elif isinstance(val, datetime):
        dt = val
    elif isinstance(val, date):
        dt = datetime.combine(val, datetime.min.time())
    elif isinstance(val, str):
        v = val.strip()
        # Common ISO format with T
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
        ):
            try:
                dt = datetime.strptime(v, fmt)
                break
            except (ValueError, TypeError):
                continue

    if dt is None:
        now = datetime.now(timezone.utc)
        return now.isoformat(), now.strftime("%Y-%m-%d"), now.strftime("%b %d, %Y")

    iso_str = dt.isoformat()
    date_str = dt.strftime("%Y-%m-%d")
    display_str = dt.strftime("%b %d, %Y")
    return iso_str, date_str, display_str


def _compute_time_ago(dt_iso: str) -> str:
    """Computes a human-friendly relative time string e.g. '2 days ago', 'Today'."""
    try:
        dt = datetime.fromisoformat(dt_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "Just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days == 1:
            return "Yesterday"
        if days < 7:
            return f"{days} days ago"
        weeks = days // 7
        if weeks < 5:
            return f"{weeks}w ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"
    except Exception:
        return "Recently"


def ensure_placement_activity_timeline_table(conn: sqlite3.Connection) -> None:
    """Ensures the placement activity timeline table and index exist."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS placement_activity_timeline (
            id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            event_date TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT DEFAULT '{}',
            is_milestone INTEGER DEFAULT 0
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_placement_timeline_student ON placement_activity_timeline (student_id, event_date DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_placement_timeline_category ON placement_activity_timeline (student_id, category, event_date DESC)")
    conn.commit()


def log_placement_activity(
    student_id: str,
    category: str,
    title: str,
    description: Optional[str] = None,
    event_date: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    is_milestone: int = 0,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Persists an activity or milestone event for a student in placement_activity_timeline.
    Validates required fields and returns the created event record dict.
    """
    sid = (student_id or "").strip()
    if not sid:
        raise ValueError("student_id is required.")

    norm_category = _normalize_category(category) or CATEGORY_ACHIEVEMENTS
    t_str = (title or "").strip()
    if not t_str:
        raise ValueError("title is required.")

    desc_str = (description or "").strip()
    now_iso, default_date, display_date = _parse_timestamp_to_iso(event_date or datetime.now(timezone.utc))
    ev_date = event_date.strip() if (event_date and isinstance(event_date, str)) else default_date
    rec_id = str(uuid.uuid4())
    meta_json = json.dumps(metadata or {})
    milestone_flag = 1 if is_milestone else 0

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        ensure_placement_activity_timeline_table(conn)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO placement_activity_timeline (
                id, student_id, category, title, description, event_date, timestamp, metadata_json, is_milestone
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec_id, sid, norm_category, t_str, desc_str, ev_date, now_iso, meta_json, milestone_flag
        ))
        conn.commit()

        meta_info = CATEGORY_META.get(norm_category, CATEGORY_META[CATEGORY_ACHIEVEMENTS])
        return {
            "id": rec_id,
            "student_id": sid,
            "category": norm_category,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": t_str,
            "description": desc_str,
            "event_date": ev_date,
            "display_date": display_date,
            "timestamp": now_iso,
            "time_ago": _compute_time_ago(now_iso),
            "metadata": metadata or {},
            "is_milestone": bool(milestone_flag),
        }
    finally:
        if should_close:
            conn.close()


def _collect_custom_events(student_id: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Retrieves explicitly logged activities & milestones from placement_activity_timeline."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='placement_activity_timeline'
    """)
    if not cursor.fetchone():
        return []

    cursor.execute("""
        SELECT id, category, title, description, event_date, timestamp, metadata_json, is_milestone
        FROM placement_activity_timeline
        WHERE student_id = ?
        ORDER BY event_date DESC, timestamp DESC
    """, (student_id,))

    items = []
    for row in cursor.fetchall():
        cat = _normalize_category(row["category"]) or CATEGORY_ACHIEVEMENTS
        meta_info = CATEGORY_META.get(cat, CATEGORY_META[CATEGORY_ACHIEVEMENTS])
        meta = {}
        try:
            if row["metadata_json"]:
                meta = json.loads(row["metadata_json"])
        except Exception:
            meta = {}

        iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["timestamp"] or row["event_date"])
        items.append({
            "id": row["id"],
            "student_id": student_id,
            "category": cat,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": row["title"],
            "description": row["description"] or "",
            "event_date": row["event_date"] or date_str,
            "display_date": display_date,
            "timestamp": iso_ts,
            "time_ago": _compute_time_ago(iso_ts),
            "metadata": meta,
            "is_milestone": bool(row["is_milestone"]),
            "source": "custom",
        })
    return items


def _collect_resume_revisions(student_id: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Gathers resume revisions, uploads, and ATS score updates from analyses table."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, resume_filename, company_name, job_description, results_json, version_number, target_role, created_at
        FROM analyses
        WHERE student_id = ?
        ORDER BY created_at DESC
    """, (student_id,))

    meta_info = CATEGORY_META[CATEGORY_RESUME_REVISIONS]
    items = []

    for row in cursor.fetchall():
        results = {}
        try:
            if row["results_json"]:
                results = json.loads(row["results_json"])
        except Exception:
            results = {}

        ats_score = results.get("ats_score")
        overall_score = results.get("overall_score")
        role = row["target_role"] or results.get("target_role") or "Software Engineer"
        company = row["company_name"] or "General Preparation"
        version = row["version_number"] or 1
        iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["created_at"])

        score_detail = f"ATS Score: {ats_score}/100" if ats_score is not None else "Resume Analysis"
        desc = f"Version {version} tailored for {role} at {company}. {score_detail}."
        title = f"Resume Revision v{version} ({company})"

        items.append({
            "id": f"resume-{row['id']}",
            "student_id": student_id,
            "category": CATEGORY_RESUME_REVISIONS,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": title,
            "description": desc,
            "event_date": date_str,
            "display_date": display_date,
            "timestamp": iso_ts,
            "time_ago": _compute_time_ago(iso_ts),
            "metadata": {
                "analysis_id": row["id"],
                "version_number": version,
                "company_name": company,
                "target_role": role,
                "ats_score": ats_score,
                "overall_score": overall_score,
                "resume_filename": row["resume_filename"],
            },
            "is_milestone": bool(ats_score is not None and ats_score >= 85),
            "source": "analyses",
        })
    return items


def _collect_practice_sessions(student_id: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Gathers coding submissions, aptitude attempts, and GD attempts."""
    cursor = conn.cursor()
    meta_info = CATEGORY_META[CATEGORY_PRACTICE_SESSIONS]
    items = []

    # 1. Coding Submissions
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='coding_submissions'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT id, question_title, company_name, language, passed, score, tests_passed, total_tests, submitted_at
            FROM coding_submissions
            WHERE student_id = ?
            ORDER BY submitted_at DESC
        """, (student_id,))
        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["submitted_at"])
            passed = bool(row["passed"])
            outcome = "Passed" if passed else "Needs Improvement"
            title = f"Coding Practice: {row['question_title']}"
            desc = (
                f"{outcome} in {row['language'].capitalize()} with {row['tests_passed']}/{row['total_tests']} tests passed "
                f"(Score: {row['score']}%). Target: {row['company_name']}."
            )
            items.append({
                "id": f"coding-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_PRACTICE_SESSIONS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "question_title": row["question_title"],
                    "language": row["language"],
                    "passed": passed,
                    "score": row["score"],
                    "company_name": row["company_name"],
                    "type": "coding",
                },
                "is_milestone": bool(passed and row["score"] == 100),
                "source": "coding_submissions",
            })

    # 2. Aptitude Attempts
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='aptitude_attempts'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT id, test_category, total_questions, correct_count, score_percentage, time_taken_seconds, created_at
            FROM aptitude_attempts
            WHERE student_id = ?
            ORDER BY created_at DESC
        """, (student_id,))
        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["created_at"])
            cat_name = row["test_category"].title()
            score_pct = round(row["score_percentage"], 1)
            duration_mins = round(row["time_taken_seconds"] / 60, 1) if row["time_taken_seconds"] else 0
            title = f"Aptitude Assessment: {cat_name}"
            desc = (
                f"Scored {score_pct}% ({row['correct_count']}/{row['total_questions']} correct) "
                f"in {duration_mins} mins."
            )
            items.append({
                "id": f"aptitude-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_PRACTICE_SESSIONS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "test_category": row["test_category"],
                    "score_percentage": score_pct,
                    "correct_count": row["correct_count"],
                    "total_questions": row["total_questions"],
                    "type": "aptitude",
                },
                "is_milestone": bool(score_pct >= 80.0),
                "source": "aptitude_attempts",
            })

    # 3. Group Discussion Attempts
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='gd_practice_attempts'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT id, topic_title, category, contribution_type, overall_score, word_count, created_at
            FROM gd_practice_attempts
            WHERE student_id = ?
            ORDER BY created_at DESC
        """, (student_id,))
        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["created_at"])
            title = f"GD Simulation: {row['topic_title']}"
            desc = (
                f"{row['contribution_type'].replace('_', ' ').title()} contribution "
                f"({row['word_count']} words, Score: {row['overall_score']}/100)."
            )
            items.append({
                "id": f"gd-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_PRACTICE_SESSIONS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "topic_title": row["topic_title"],
                    "gd_category": row["category"],
                    "contribution_type": row["contribution_type"],
                    "overall_score": row["overall_score"],
                    "type": "group_discussion",
                },
                "is_milestone": bool(row["overall_score"] >= 80),
                "source": "gd_practice_attempts",
            })

    return items


def _collect_applications(student_id: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Gathers job applications and stage updates."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='job_applications'
    """)
    if not cursor.fetchone():
        return []

    cursor.execute("""
        SELECT id, company_name, role_title, status, application_date, location, created_at
        FROM job_applications
        WHERE student_id = ?
        ORDER BY application_date DESC, created_at DESC
    """, (student_id,))

    meta_info = CATEGORY_META[CATEGORY_APPLICATIONS]
    items = []

    for row in cursor.fetchall():
        iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["application_date"] or row["created_at"])
        company = row["company_name"]
        role = row["role_title"]
        status = row["status"]
        loc = row["location"] or "Remote"

        title = f"Application: {company} ({role})"
        desc = f"Current Stage: {status} | Location: {loc} | Applied on {display_date}."

        is_offer = "offer" in status.lower()

        items.append({
            "id": f"app-{row['id']}",
            "student_id": student_id,
            "category": CATEGORY_APPLICATIONS,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": title,
            "description": desc,
            "event_date": date_str,
            "display_date": display_date,
            "timestamp": iso_ts,
            "time_ago": _compute_time_ago(iso_ts),
            "metadata": {
                "application_id": row["id"],
                "company_name": company,
                "role_title": role,
                "status": status,
                "location": loc,
            },
            "is_milestone": is_offer,
            "source": "job_applications",
        })
    return items


def _collect_interviews(student_id: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Gathers mock interviews and shared interview experiences."""
    cursor = conn.cursor()
    meta_info = CATEGORY_META[CATEGORY_INTERVIEWS]
    items = []

    # 1. Mock Interviews linked via analyses
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='interviews'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT i.id, i.analysis_id, i.completed, i.current_question_index,
                   a.company_name, a.target_role, a.results_json, a.created_at
            FROM interviews i
            JOIN analyses a ON i.analysis_id = a.id
            WHERE a.student_id = ?
            ORDER BY a.created_at DESC
        """, (student_id,))

        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["created_at"])
            company = row["company_name"] or "Mock Placement"
            role = row["target_role"] or "Software Engineer"
            completed = bool(row["completed"])
            status_text = "Completed" if completed else f"In Progress (Q#{row['current_question_index'] + 1})"

            title = f"Mock Interview: {company}"
            desc = f"Technical/Behavioral interview for {role}. Status: {status_text}."

            items.append({
                "id": f"interview-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_INTERVIEWS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "interview_id": row["id"],
                    "analysis_id": row["analysis_id"],
                    "company_name": company,
                    "target_role": role,
                    "completed": completed,
                },
                "is_milestone": completed,
                "source": "interviews",
            })

    # 2. Shared Interview Experiences
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='interview_experiences'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT id, company_name, role_title, verdict, difficulty, interview_date, created_at
            FROM interview_experiences
            WHERE student_id = ?
            ORDER BY created_at DESC
        """, (student_id,))
        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["interview_date"] or row["created_at"])
            verdict = row["verdict"] or "Completed"
            diff = row["difficulty"] or "Medium"
            title = f"Interview Debrief: {row['company_name']}"
            desc = f"Logged {diff} difficulty interview for {row['role_title']}. Outcome: {verdict}."
            items.append({
                "id": f"intexp-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_INTERVIEWS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "company_name": row["company_name"],
                    "role_title": row["role_title"],
                    "verdict": verdict,
                    "difficulty": diff,
                },
                "is_milestone": bool(verdict.lower() in ("selected", "offer", "passed")),
                "source": "interview_experiences",
            })

    return items


def _collect_feedback(student_id: str, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Gathers section feedback and user feedback reviews."""
    cursor = conn.cursor()
    meta_info = CATEGORY_META[CATEGORY_FEEDBACK]
    items = []

    # 1. Section Feedback
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_section_feedback'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT id, analysis_id, section, rating, comment, created_at
            FROM analysis_section_feedback
            WHERE user_id = ?
            ORDER BY created_at DESC
        """, (student_id,))

        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["created_at"])
            sec_name = row["section"].replace("_", " ").title()
            rating_text = "Helpful" if row["rating"] == "up" else "Needs Polish"
            comment_snippet = f": \"{row['comment'][:80]}...\"" if row["comment"] else ""
            title = f"Resume Section Review: {sec_name}"
            desc = f"Marked section as {rating_text}{comment_snippet}."

            items.append({
                "id": f"feedback-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_FEEDBACK,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "section": row["section"],
                    "rating": row["rating"],
                    "comment": row["comment"],
                    "analysis_id": row["analysis_id"],
                },
                "is_milestone": False,
                "source": "analysis_section_feedback",
            })

    # 2. User feedback entries
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='user_feedback'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT id, feedback_type, category, message, rating, created_at
            FROM user_feedback
            WHERE student_id = ?
            ORDER BY created_at DESC
        """, (student_id,))
        for row in cursor.fetchall():
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(row["created_at"])
            cat_name = (row["category"] or "General").title()
            msg = row["message"] or ""
            snippet = f": \"{msg[:70]}...\"" if msg else ""
            title = f"Platform Feedback: {cat_name}"
            desc = f"Submitted rating {row['rating']}/5 stars{snippet}."
            items.append({
                "id": f"ufeedback-{row['id']}",
                "student_id": student_id,
                "category": CATEGORY_FEEDBACK,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": title,
                "description": desc,
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": iso_ts,
                "time_ago": _compute_time_ago(iso_ts),
                "metadata": {
                    "feedback_type": row["feedback_type"],
                    "category": row["category"],
                    "rating": row["rating"],
                },
                "is_milestone": False,
                "source": "user_feedback",
            })

    return items


def _collect_dynamic_achievements(
    student_id: str,
    conn: sqlite3.Connection,
    collected_items: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Computes milestones and achievements derived from student activity:
    - Streaks (3, 7, 14, 30 days)
    - High ATS Score (>= 80, >= 90)
    - Coding Milestones (1st solved, 5 solved, 10 solved)
    - Application Milestones (1st applied, 5 applied, offer received)
    - Profile Completion (100% profile completed)
    """
    cursor = conn.cursor()
    meta_info = CATEGORY_META[CATEGORY_ACHIEVEMENTS]
    achievements = []

    # 1. Streak Milestones
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='student_streaks'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT current_streak, longest_streak, last_activity_date, total_active_days, updated_at
            FROM student_streaks
            WHERE student_id = ?
        """, (student_id,))
        streak_row = cursor.fetchone()
        if streak_row:
            longest = streak_row["longest_streak"] or 0
            act_date = streak_row["last_activity_date"] or streak_row["updated_at"]
            iso_ts, date_str, display_date = _parse_timestamp_to_iso(act_date)

            streak_thresholds = [
                (3, "Consistency Kickoff", "Maintained an unbroken 3-day placement preparation streak!"),
                (7, "7-Day Sprint Master", "Achieved a full 7-day daily study and practice streak!"),
                (14, "Fortnight Focus", "Two consecutive weeks of active daily placement prep!"),
                (30, "Monthly Placement Champion", "30 days of consistent, daily interview readiness!"),
            ]
            for thresh, title, desc in streak_thresholds:
                if longest >= thresh:
                    achievements.append({
                        "id": f"achieve-streak-{thresh}",
                        "student_id": student_id,
                        "category": CATEGORY_ACHIEVEMENTS,
                        "category_label": meta_info["label"],
                        "icon": meta_info["icon"],
                        "badge_class": meta_info["badge_class"],
                        "border_class": meta_info["border_class"],
                        "color": meta_info["color"],
                        "title": f"Milestone: {title}",
                        "description": desc,
                        "event_date": date_str,
                        "display_date": display_date,
                        "timestamp": iso_ts,
                        "time_ago": _compute_time_ago(iso_ts),
                        "metadata": {"milestone_type": "streak", "threshold": thresh, "longest_streak": longest},
                        "is_milestone": True,
                        "source": "derived_streak",
                    })

    # 2. High ATS Milestones
    resume_items = collected_items.get(CATEGORY_RESUME_REVISIONS, [])
    high_ats_seen = False
    elite_ats_seen = False
    for r in sorted(resume_items, key=lambda x: x["timestamp"]):
        ats = r["metadata"].get("ats_score")
        if ats and ats >= 80 and not high_ats_seen:
            high_ats_seen = True
            achievements.append({
                "id": f"achieve-high-ats-{r['id']}",
                "student_id": student_id,
                "category": CATEGORY_ACHIEVEMENTS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": f"Milestone: ATS Benchmark Achieved ({ats}/100)",
                "description": f"Targeted resume for {r['metadata'].get('company_name')} scored {ats}/100, unlocking top recruiter visibility.",
                "event_date": r["event_date"],
                "display_date": r["display_date"],
                "timestamp": r["timestamp"],
                "time_ago": r["time_ago"],
                "metadata": {"milestone_type": "ats_score", "score": ats},
                "is_milestone": True,
                "source": "derived_resume",
            })
        if ats and ats >= 90 and not elite_ats_seen:
            elite_ats_seen = True
            achievements.append({
                "id": f"achieve-elite-ats-{r['id']}",
                "student_id": student_id,
                "category": CATEGORY_ACHIEVEMENTS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": f"Milestone: Elite ATS Score ({ats}/100)",
                "description": f"Outstanding resume optimization reaching the top 5% threshold with {ats}/100.",
                "event_date": r["event_date"],
                "display_date": r["display_date"],
                "timestamp": r["timestamp"],
                "time_ago": r["time_ago"],
                "metadata": {"milestone_type": "ats_score_elite", "score": ats},
                "is_milestone": True,
                "source": "derived_resume",
            })

    # 3. Practice Milestones
    practice_items = collected_items.get(CATEGORY_PRACTICE_SESSIONS, [])
    coding_passed = [p for p in practice_items if p["metadata"].get("type") == "coding" and p["metadata"].get("passed")]
    if len(coding_passed) >= 1:
        first = sorted(coding_passed, key=lambda x: x["timestamp"])[0]
        achievements.append({
            "id": "achieve-first-code-solve",
            "student_id": student_id,
            "category": CATEGORY_ACHIEVEMENTS,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": "Milestone: First Coding Challenge Solved",
            "description": f"Successfully cleared all unit tests for '{first['metadata'].get('question_title')}'.",
            "event_date": first["event_date"],
            "display_date": first["display_date"],
            "timestamp": first["timestamp"],
            "time_ago": first["time_ago"],
            "metadata": {"milestone_type": "first_coding_solve"},
            "is_milestone": True,
            "source": "derived_practice",
        })
    if len(coding_passed) >= 5:
        fifth = sorted(coding_passed, key=lambda x: x["timestamp"])[4]
        achievements.append({
            "id": "achieve-5-code-solves",
            "student_id": student_id,
            "category": CATEGORY_ACHIEVEMENTS,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": "Milestone: 5 Coding Challenges Solved",
            "description": "Consistent algorithmic practice milestone with 5 validated problem submissions.",
            "event_date": fifth["event_date"],
            "display_date": fifth["display_date"],
            "timestamp": fifth["timestamp"],
            "time_ago": fifth["time_ago"],
            "metadata": {"milestone_type": "5_coding_solves", "count": len(coding_passed)},
            "is_milestone": True,
            "source": "derived_practice",
        })

    # 4. Job Application Milestones
    app_items = collected_items.get(CATEGORY_APPLICATIONS, [])
    if len(app_items) >= 1:
        first_app = sorted(app_items, key=lambda x: x["timestamp"])[0]
        achievements.append({
            "id": "achieve-first-application",
            "student_id": student_id,
            "category": CATEGORY_ACHIEVEMENTS,
            "category_label": meta_info["label"],
            "icon": meta_info["icon"],
            "badge_class": meta_info["badge_class"],
            "border_class": meta_info["border_class"],
            "color": meta_info["color"],
            "title": "Milestone: Job Hunt Commenced",
            "description": f"Submitted first tracked campus placement application to {first_app['metadata'].get('company_name')}.",
            "event_date": first_app["event_date"],
            "display_date": first_app["display_date"],
            "timestamp": first_app["timestamp"],
            "time_ago": first_app["time_ago"],
            "metadata": {"milestone_type": "first_application"},
            "is_milestone": True,
            "source": "derived_application",
        })

    # 5. Profile Completion Milestone
    cursor.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='student_profiles'
    """)
    if cursor.fetchone():
        cursor.execute("""
            SELECT full_name, email, department, cgpa, target_role, skills
            FROM student_profiles
            WHERE student_id = ?
        """, (student_id,))
        prof = cursor.fetchone()
        if prof and prof["full_name"] and prof["department"] and prof["cgpa"] and prof["target_role"]:
            now_iso, date_str, display_date = _parse_timestamp_to_iso(datetime.now(timezone.utc))
            achievements.append({
                "id": "achieve-profile-complete",
                "student_id": student_id,
                "category": CATEGORY_ACHIEVEMENTS,
                "category_label": meta_info["label"],
                "icon": meta_info["icon"],
                "badge_class": meta_info["badge_class"],
                "border_class": meta_info["border_class"],
                "color": meta_info["color"],
                "title": "Milestone: Placement Profile Completed",
                "description": "Comprehensive student profile configured with target roles, academics, and verified skills.",
                "event_date": date_str,
                "display_date": display_date,
                "timestamp": now_iso,
                "time_ago": "Active",
                "metadata": {"milestone_type": "profile_complete"},
                "is_milestone": True,
                "source": "derived_profile",
            })

    return achievements


def get_placement_activity_timeline(
    student_id: str,
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Returns unified chronological placement readiness activity timeline and metrics.
    Aggregates:
    - Resume Revisions
    - Practice Sessions
    - Applications
    - Interviews
    - Feedback
    - Achievements (custom + automatically derived)
    """
    sid = (student_id or "").strip()
    if not sid:
        sid = "default_student"

    norm_category = _normalize_category(category) if category and category.lower() != "all" else None

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        ensure_placement_activity_timeline_table(conn)

        # 1. Collect across all subsystems
        custom_items = _collect_custom_events(sid, conn)
        resume_items = _collect_resume_revisions(sid, conn)
        practice_items = _collect_practice_sessions(sid, conn)
        app_items = _collect_applications(sid, conn)
        interview_items = _collect_interviews(sid, conn)
        feedback_items = _collect_feedback(sid, conn)

        collected_by_cat = {
            CATEGORY_RESUME_REVISIONS: resume_items,
            CATEGORY_PRACTICE_SESSIONS: practice_items,
            CATEGORY_APPLICATIONS: app_items,
            CATEGORY_INTERVIEWS: interview_items,
            CATEGORY_FEEDBACK: feedback_items,
        }

        # Derive milestones
        derived_achievements = _collect_dynamic_achievements(sid, conn, collected_by_cat)

        # Group all items
        all_raw_items = (
            custom_items
            + resume_items
            + practice_items
            + app_items
            + interview_items
            + feedback_items
            + derived_achievements
        )

        # Deduplicate by item id
        seen_ids = set()
        deduped_items = []
        for it in all_raw_items:
            if it["id"] not in seen_ids:
                seen_ids.add(it["id"])
                deduped_items.append(it)

        # Sort chronologically descending (newest first)
        deduped_items.sort(key=lambda x: (x.get("event_date", ""), x.get("timestamp", "")), reverse=True)

        # Calculate category counts across all items
        category_counts = {cat: 0 for cat in ALL_CATEGORIES}
        milestones_count = 0
        active_dates = set()

        for it in deduped_items:
            c = it["category"]
            if c in category_counts:
                category_counts[c] += 1
            if it.get("is_milestone"):
                milestones_count += 1
            if it.get("event_date"):
                active_dates.add(it["event_date"])

        # Streak info
        current_streak = 0
        longest_streak = 0
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='student_streaks'")
        if cursor.fetchone():
            cursor.execute("SELECT current_streak, longest_streak FROM student_streaks WHERE student_id = ?", (sid,))
            s_row = cursor.fetchone()
            if s_row:
                current_streak = s_row["current_streak"] or 0
                longest_streak = s_row["longest_streak"] or 0

        # Readiness Stage Assessment
        total_acts = len(deduped_items)
        if total_acts == 0:
            stage_name = "New Beginnings"
            stage_desc = "Start by uploading your resume or taking your first practice test."
            stage_level = 1
        elif total_acts < 5:
            stage_name = "Foundation Building"
            stage_desc = "Good start! Keep regular study momentum going with mock questions."
            stage_level = 2
        elif total_acts < 15:
            stage_name = "Active Preparation"
            stage_desc = "Strong pace across practice sessions and resume iterations."
            stage_level = 3
        elif category_counts[CATEGORY_APPLICATIONS] > 0 and category_counts[CATEGORY_INTERVIEWS] > 0:
            stage_name = "Placement Ready"
            stage_desc = "Actively applying and interviewing with proven consistency!"
            stage_level = 5
        else:
            stage_name = "Targeted Acceleration"
            stage_desc = "High volume of preparation; expand mock interviews and applications."
            stage_level = 4

        summary = {
            "total_activities": total_acts,
            "category_counts": category_counts,
            "milestones_count": milestones_count,
            "active_days_count": len(active_dates),
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "readiness_stage": stage_name,
            "readiness_stage_description": stage_desc,
            "readiness_stage_level": stage_level,
            "latest_activity_date": deduped_items[0]["event_date"] if deduped_items else None,
        }

        # Apply category filter
        filtered_items = deduped_items
        if norm_category:
            filtered_items = [it for it in filtered_items if it["category"] == norm_category]

        # Apply date filters
        if start_date:
            filtered_items = [it for it in filtered_items if it.get("event_date", "") >= start_date]
        if end_date:
            filtered_items = [it for it in filtered_items if it.get("event_date", "") <= end_date]

        # Pagination slice
        total_filtered = len(filtered_items)
        page_items = filtered_items[offset: offset + limit] if limit > 0 else filtered_items

        # Build category metadata payload for UI rendering
        categories_payload = []
        for c in ALL_CATEGORIES:
            meta = CATEGORY_META[c]
            categories_payload.append({
                "key": c,
                "label": meta["label"],
                "icon": meta["icon"],
                "badge_class": meta["badge_class"],
                "border_class": meta["border_class"],
                "color": meta["color"],
                "count": category_counts.get(c, 0),
            })

        return {
            "items": page_items,
            "total_count": total_filtered,
            "total_unfiltered_count": total_acts,
            "summary": summary,
            "categories": categories_payload,
            "active_category": norm_category or "all",
            "limit": limit,
            "offset": offset,
        }
    finally:
        if should_close:
            conn.close()


def get_readiness_effort_summary(
    student_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Helper to retrieve summary statistics only for dashboard widgets and quick progress checks."""
    res = get_placement_activity_timeline(student_id=student_id, limit=1, conn=conn)
    return res.get("summary", {})
