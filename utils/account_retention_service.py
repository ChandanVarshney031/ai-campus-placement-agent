"""
Inactive Account Retention & Deletion Service (#888).

Enforces data minimization and privacy by automatically identifying, warning,
and permanently deleting accounts with no login or activity for a configurable period:
- Configured via `INACTIVE_ACCOUNT_DAYS` (default 0 / disabled).
- With setting disabled (0 or None), nothing is warned or deleted.
- Sends warning emails 30 days and 7 days prior to deletion.
- A user login resets their activity timestamp and cancels deletion.
- Warnings are tracked per threshold to prevent repetitive notifications.
- Account and cascading personal data removal uses the exact same routine as manual deletion:
  `delete_user_account_and_personal_data(student_id)`
- Records deletion events in an admin audit log with ZERO personal details (PII).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import config
from utils.database import (
    get_db_connection,
    delete_user_account_and_personal_data,
)
from utils.admin_user_service import (
    init_admin_user_management_schema,
    record_admin_user_audit_log,
)
from utils.email_service import EmailNotificationService

logger = logging.getLogger(__name__)


def _parse_timestamp_to_epoch(val: Any) -> Optional[float]:
    """Converts a SQLite timestamp string, integer, float, or datetime into epoch seconds."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.timestamp()
    if isinstance(val, str):
        raw = val.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            pass
        # Common SQLite timestamp formats
        clean_str = raw.replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(clean_str[:19], fmt[:19])
                return dt.replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
    return None


def init_account_retention_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Initializes tables for inactive account warning tracking."""
    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inactive_account_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                threshold_days INTEGER NOT NULL,
                sent_at REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, threshold_days)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_inactive_warnings_user "
            "ON inactive_account_warnings(user_id)"
        )
        conn.commit()
    finally:
        if close_after:
            conn.close()


init_inactive_account_retention_schema = init_account_retention_schema


init_inactive_account_retention_schema = init_account_retention_schema


def get_user_last_activity(
    user_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[float]:
    """
    Determines the most recent epoch timestamp of user activity.
    Checks:
    - users.created_at (registration)
    - auth_sessions (login and last_seen_at)
    - analyses (resume submissions)
    - coding_submissions
    - job_applications
    - interviews
    """
    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True

    try:
        cursor = conn.cursor()
        timestamps: List[float] = []

        # 1. users table registration date
        cursor.execute("SELECT created_at FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            ep = _parse_timestamp_to_epoch(row[0])
            if ep is not None:
                timestamps.append(ep)

        # 2. auth_sessions table (logins & touches)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_sessions'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT MAX(COALESCE(last_seen_at, created_at)) "
                "FROM auth_sessions WHERE user_id = ?",
                (user_id,)
            )
            s_row = cursor.fetchone()
            if s_row and s_row[0]:
                ep = _parse_timestamp_to_epoch(s_row[0])
                if ep is not None:
                    timestamps.append(ep)

        # 3. analyses table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analyses'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT MAX(created_at) FROM analyses WHERE student_id = ?",
                (user_id,)
            )
            a_row = cursor.fetchone()
            if a_row and a_row[0]:
                ep = _parse_timestamp_to_epoch(a_row[0])
                if ep is not None:
                    timestamps.append(ep)

        # 4. coding_submissions table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='coding_submissions'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT MAX(submitted_at) FROM coding_submissions WHERE student_id = ?",
                (user_id,)
            )
            c_row = cursor.fetchone()
            if c_row and c_row[0]:
                ep = _parse_timestamp_to_epoch(c_row[0])
                if ep is not None:
                    timestamps.append(ep)

        # 5. job_applications table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='job_applications'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT MAX(created_at) FROM job_applications WHERE student_id = ?",
                (user_id,)
            )
            j_row = cursor.fetchone()
            if j_row and j_row[0]:
                ep = _parse_timestamp_to_epoch(j_row[0])
                if ep is not None:
                    timestamps.append(ep)

        # 6. interviews table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='interviews'"
        )
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(interviews)")
            int_cols = [c[1] for c in cursor.fetchall()]
            if "student_id" in int_cols:
                cursor.execute(
                    "SELECT MAX(created_at) FROM interviews WHERE student_id = ?",
                    (user_id,)
                )
                i_row = cursor.fetchone()
                if i_row and i_row[0]:
                    ep = _parse_timestamp_to_epoch(i_row[0])
                    if ep is not None:
                        timestamps.append(ep)

        return max(timestamps) if timestamps else None
    finally:
        if close_after:
            conn.close()


def cancel_account_deletion_on_login(
    user_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """
    Cancels any pending inactivity warnings/deletion countdown upon successful user login.
    Removes recorded warnings from `inactive_account_warnings`.
    """
    if not user_id:
        return False

    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True

    init_account_retention_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM inactive_account_warnings WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        logger.debug(
            "Cleared inactive account warnings for user %s following login.",
            user_id
        )
        return True
    finally:
        if close_after:
            conn.close()


def get_inactive_account_audit_logs(
    limit: int = 50,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Retrieves admin audit log entries for inactive account deletions."""
    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, admin_id, action, target_user_id, details, created_at
            FROM admin_user_audit_logs
            WHERE action = 'INACTIVE_ACCOUNT_DELETED'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        logs = []
        for r in rows:
            details_dict = {}
            if r[4]:
                try:
                    details_dict = json.loads(r[4])
                except Exception:
                    details_dict = {"raw": r[4]}
            logs.append({
                "id": r[0],
                "admin_id": r[1],
                "action": r[2],
                "target_user_id": r[3],
                "details": details_dict,
                "created_at": r[5],
            })
        return logs
    finally:
        if close_after:
            conn.close()


def process_inactive_accounts(
    retention_days: Optional[int] = None,
    now: Optional[Union[float, int, datetime]] = None,
    dry_run: bool = False,
    email_service: Optional[Any] = None,
    conn: Optional[sqlite3.Connection] = None,
    base_url: str = "http://localhost:5000",
) -> Dict[str, Any]:
    """
    Core retention execution routine:
    1. Validates retention setting (default off). If disabled, nothing is deleted.
    2. Scans student accounts for login or activity.
    3. Sends 30-day and 7-day warnings prior to deletion.
    4. Purges accounts inactive >= retention_days using existing account deletion routine.
    5. Writes an admin audit log entry without personal details (zero PII).
    """
    if retention_days is None:
        retention_days = int(getattr(config, "INACTIVE_ACCOUNT_DAYS", 0) or 0)

    # Acceptance Criteria 1: With the setting off nothing is deleted
    if retention_days <= 0:
        return {
            "status": "disabled",
            "message": "Inactive account retention is disabled (INACTIVE_ACCOUNT_DAYS <= 0).",
            "retention_days": 0,
            "evaluated_users": 0,
            "warnings_30d_sent": 0,
            "warnings_7d_sent": 0,
            "total_warnings_sent": 0,
            "deleted_count": 0,
            "deleted_accounts": [],
            "deleted_user_ids": [],
        }

    now_ts = _parse_timestamp_to_epoch(now)
    if now_ts is None:
        now_ts = time.time()

    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True

    init_account_retention_schema(conn)
    init_admin_user_management_schema(conn)

    if email_service is None:
        email_service = EmailNotificationService()

    login_url = f"{base_url.rstrip('/')}/login"

    warnings_30d_count = 0
    warnings_7d_count = 0
    deleted_accounts: List[str] = []
    deleted_user_ids: List[str] = []

    try:
        cursor = conn.cursor()

        # Query all student candidate users (strictly exclude admin accounts)
        cursor.execute(
            """
            SELECT id, name, email, role, created_at
            FROM users
            WHERE (role NOT IN ('admin', 'coordinator') OR role IS NULL)
              AND id NOT IN ('admin', 'coordinator', 'admin_user')
            """
        )
        users = cursor.fetchall()
        evaluated_users_count = len(users)

        for user_row in users:
            uid = str(user_row[0]).strip()
            uname = user_row[1] or ""
            uemail = user_row[2] or ""

            last_activity = get_user_last_activity(uid, conn=conn)
            if last_activity is None:
                # Fallback to user creation timestamp
                last_activity = _parse_timestamp_to_epoch(user_row[4]) or now_ts

            inactive_seconds = max(0.0, now_ts - last_activity)
            inactive_days = inactive_seconds / 86400.0

            # If user has recent activity newer than any existing warnings, purge stale warnings
            cursor.execute(
                "DELETE FROM inactive_account_warnings WHERE user_id = ? AND sent_at < ?",
                (uid, last_activity)
            )
            conn.commit()

            # Threshold calculations
            # e.g. for retention_days = 365:
            # - 30-day warning: inactive_days >= 335 (365 - 30)
            # - 7-day warning:  inactive_days >= 358 (365 - 7)
            # - Deletion:       inactive_days >= 365
            thresh_30 = max(0.0, float(retention_days - 30)) if retention_days > 30 else None
            thresh_7 = max(0.0, float(retention_days - 7)) if retention_days > 7 else None

            # Case A: Account Inactivity >= retention_days -> Permanently Delete
            if inactive_days >= retention_days:
                anonymized_id = f"user_hash_{hashlib.sha256(uid.encode('utf-8')).hexdigest()[:12]}"
                if not dry_run:
                    # Acceptance Criteria 3: Deletion uses the same code as manual account deletion
                    res = delete_user_account_and_personal_data(uid)

                    # Remove warning tracking
                    cursor.execute(
                        "DELETE FROM inactive_account_warnings WHERE user_id = ?",
                        (uid,)
                    )
                    conn.commit()

                    # Acceptance Criteria: Record deletions in an admin audit log
                    # without personal details
                    record_admin_user_audit_log(
                        admin_id="system_retention",
                        action="INACTIVE_ACCOUNT_DELETED",
                        target_user_id=anonymized_id,
                        target_email=None,  # Zero PII
                        details={
                            "reason": "inactive_account_retention",
                            "retention_policy_days": retention_days,
                            "retention_days": retention_days,
                            "inactive_days": round(inactive_days, 1),
                            "records_purged": res.get("total_deleted", 0),
                        },
                        ip_address=None,  # Zero PII
                        conn=conn,
                    )

                deleted_accounts.append(anonymized_id)
                deleted_user_ids.append(uid)
                continue

            # Case B: 7-Day Warning Threshold Reached
            if thresh_7 is not None and inactive_days >= thresh_7:
                cursor.execute(
                    "SELECT sent_at FROM inactive_account_warnings "
                    "WHERE user_id = ? AND threshold_days = 7",
                    (uid,)
                )
                w_row = cursor.fetchone()
                if not w_row:
                    days_left = max(1, int(round(retention_days - inactive_days)))
                    if uemail and hasattr(email_service, "send_inactive_account_warning"):
                        email_service.send_inactive_account_warning(
                            recipient_email=uemail,
                            student_name=uname,
                            days_remaining=days_left,
                            login_url=login_url,
                        )
                    if not dry_run:
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO inactive_account_warnings
                                (user_id, threshold_days, sent_at)
                            VALUES (?, 7, ?)
                            """,
                            (uid, now_ts)
                        )
                        conn.commit()
                    warnings_7d_count += 1
                continue

            # Case C: 30-Day Warning Threshold Reached
            if thresh_30 is not None and inactive_days >= thresh_30:
                cursor.execute(
                    "SELECT sent_at FROM inactive_account_warnings "
                    "WHERE user_id = ? AND threshold_days = 30",
                    (uid,)
                )
                w_row = cursor.fetchone()
                if not w_row:
                    days_left = max(1, int(round(retention_days - inactive_days)))
                    if uemail and hasattr(email_service, "send_inactive_account_warning"):
                        email_service.send_inactive_account_warning(
                            recipient_email=uemail,
                            student_name=uname,
                            days_remaining=days_left,
                            login_url=login_url,
                        )
                    if not dry_run:
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO inactive_account_warnings
                                (user_id, threshold_days, sent_at)
                            VALUES (?, 30, ?)
                            """,
                            (uid, now_ts)
                        )
                        conn.commit()
                    warnings_30d_count += 1
                continue

        return {
            "status": "completed" if not dry_run else "dry_run",
            "retention_days": retention_days,
            "evaluated_users": evaluated_users_count,
            "warnings_30d_sent": warnings_30d_count,
            "warnings_7d_sent": warnings_7d_count,
            "total_warnings_sent": warnings_30d_count + warnings_7d_count,
            "deleted_count": len(deleted_accounts),
            "deleted_accounts": deleted_accounts,
            "deleted_user_ids": deleted_user_ids,
        }
    finally:
        if close_after:
            conn.close()
