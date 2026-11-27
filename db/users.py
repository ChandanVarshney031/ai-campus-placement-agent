"""
Users Repository (db/users.py)
==============================
Handles user profiles, credentials, auth sessions, two-factor auth,
email/password tokens, preferences, and GDPR account deletion.
"""

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

import config
from db.connection import get_connection, transaction

logger = logging.getLogger(__name__)


def create_user(
    user_id: str,
    name: str,
    email: str,
    password_hash: str
) -> bool:
    """Create a new user account."""
    try:
        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, name, email, password_hash)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, name.strip(), email.strip().lower(), password_hash)
            )
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as exc:
        logger.error(f"Error creating user: {exc}")
        raise


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Retrieve a user by email address."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.strip().lower(),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a user by their unique ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_auth_session(user_id: str, client_label: str) -> Dict[str, str]:
    """Create an opaque, server-tracked session that can later be revoked."""
    session_id = str(secrets.token_hex(16))
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO auth_sessions
                (id, user_id, token_hash, client_label, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, token_hash, client_label or "Web browser", now, now),
        )
    return {"id": session_id, "token": token}


def get_active_auth_session(
    session_id: str,
    token: str,
    user_id: str
) -> Optional[Dict[str, Any]]:
    """Return a session only when its token is valid and unrevoked."""
    if not session_id or not token or not user_id:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, user_id, client_label, created_at, last_seen_at
            FROM auth_sessions
            WHERE id = ? AND user_id = ? AND token_hash = ? AND revoked_at IS NULL
            """,
            (session_id, user_id, token_hash),
        ).fetchone()
        return dict(row) if row else None


def touch_auth_session(session_id: str, user_id: str) -> None:
    """Update last seen activity timestamp for an active session."""
    with transaction() as cursor:
        cursor.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (time.time(), session_id, user_id),
        )


def get_active_auth_sessions(
    user_id: str,
    timeout_seconds: Optional[int] = None
) -> List[Dict[str, Any]]:
    """List non-revoked sessions active within the inactivity window."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if timeout_seconds:
            cutoff = time.time() - timeout_seconds
            cursor.execute(
                """
                SELECT id, client_label, created_at, last_seen_at
                FROM auth_sessions
                WHERE user_id = ? AND revoked_at IS NULL AND last_seen_at >= ?
                ORDER BY last_seen_at DESC
                """,
                (user_id, cutoff),
            )
        else:
            cursor.execute(
                """
                SELECT id, client_label, created_at, last_seen_at
                FROM auth_sessions
                WHERE user_id = ? AND revoked_at IS NULL
                ORDER BY last_seen_at DESC
                """,
                (user_id,),
            )
        return [dict(r) for r in cursor.fetchall()]


def revoke_auth_session(user_id: str, session_id: str) -> bool:
    """Revoke one session only when it belongs to the requesting user."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (time.time(), session_id, user_id),
        )
        return cursor.rowcount > 0


def revoke_other_auth_sessions(user_id: str, current_session_id: str) -> int:
    """Revoke every active session for a user except the current one."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND id != ? AND revoked_at IS NULL
            """,
            (time.time(), user_id, current_session_id),
        )
        return cursor.rowcount


def revoke_all_auth_sessions(user_id: str) -> int:
    """Revoke every active session for a user (#473)."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (time.time(), user_id),
        )
        return cursor.rowcount


def change_user_password(
    user_id: str,
    new_password_hash: str,
    keep_session_id: Optional[str] = None
) -> bool:
    """Updates password and revokes all other (or all) active sessions."""
    now = time.time()
    with transaction() as cursor:
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_password_hash, user_id)
        )
        if cursor.rowcount == 0:
            return False

        if keep_session_id:
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE user_id = ? AND id != ? AND revoked_at IS NULL
                """,
                (now, user_id, keep_session_id),
            )
        else:
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (now, user_id),
            )
        return True


def enable_user_two_factor(
    user_id: str,
    secret: str,
    hashed_backup_codes: List[str]
) -> bool:
    """Enable two-factor authentication and store secret & recovery codes."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE users
            SET two_factor_enabled = 1,
                two_factor_secret = ?,
                two_factor_backup_codes = ?
            WHERE id = ?
            """,
            (secret.strip(), json.dumps(hashed_backup_codes), user_id)
        )
        return cursor.rowcount > 0


def disable_user_two_factor(user_id: str) -> bool:
    """Disable two-factor authentication and clear secret & recovery codes."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE users
            SET two_factor_enabled = 0,
                two_factor_secret = NULL,
                two_factor_backup_codes = NULL
            WHERE id = ?
            """,
            (user_id,)
        )
        return cursor.rowcount > 0


def update_user_backup_codes(
    user_id: str,
    hashed_backup_codes: List[str]
) -> bool:
    """Update active backup recovery code hashes."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE users
            SET two_factor_backup_codes = ?
            WHERE id = ?
            """,
            (json.dumps(hashed_backup_codes), user_id)
        )
        return cursor.rowcount > 0


def get_user_two_factor_status(user_id: str) -> Dict[str, Any]:
    """Retrieve two-factor status and remaining backup codes count."""
    user = get_user_by_id(user_id)
    if not user:
        return {"enabled": False, "backup_codes_count": 0}
    enabled = bool(user.get("two_factor_enabled", 0))
    raw_codes = user.get("two_factor_backup_codes")
    count = 0
    if raw_codes:
        try:
            parsed = json.loads(raw_codes)
            count = len(parsed) if isinstance(parsed, list) else 0
        except Exception:
            count = 0
    return {
        "enabled": enabled,
        "backup_codes_count": count
    }


def generate_email_verification_token(user_id: str) -> str:
    """Generates and persists a cryptographically secure email verification token."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    expires_at = now + (24 * 3600)  # 24 hours
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO email_verification_tokens
                (id, user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(secrets.token_hex(16)), user_id, token_hash, expires_at, now)
        )
    return token


def verify_user_email_token(token: str) -> Optional[Dict[str, Any]]:
    """Validates email token and marks user verified."""
    if not token or not token.strip():
        return None
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    now = int(time.time())
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT id, user_id, expires_at, used_at
            FROM email_verification_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now)
        )
        row = cursor.fetchone()
        if not row:
            return None
        token_id = row["id"]
        user_id = row["user_id"]
        cursor.execute(
            "UPDATE email_verification_tokens SET used_at = ? WHERE id = ?",
            (now, token_id)
        )
        cursor.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
        return {"user_id": user_id, "token_id": token_id}


def is_user_email_verified(user_id: str) -> bool:
    """Checks whether user's email has been verified."""
    user = get_user_by_id(user_id)
    return bool(user and user.get("is_verified", 0) == 1)


def create_password_reset_token(
    user_id: str,
    token_hash: str,
    expires_at: int
) -> bool:
    """Create a reset token and invalidate previous unused tokens."""
    now = int(time.time())
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
            """,
            (now, user_id)
        )
        cursor.execute(
            """
            INSERT INTO password_reset_tokens
                (id, user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), user_id, token_hash, expires_at, now)
        )
        return True


def get_valid_password_reset_token(token_hash: str) -> Optional[Dict[str, Any]]:
    """Retrieve an unused, non-expired reset token."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, token_hash, expires_at
            FROM password_reset_tokens
            WHERE token_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
            """,
            (token_hash, int(time.time()))
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def reset_user_password(token_hash: str, new_password_hash: str) -> bool:
    """Update password and consume the reset token atomically."""
    now = int(time.time())
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT user_id
            FROM password_reset_tokens
            WHERE token_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
            """,
            (token_hash, now)
        )
        token = cursor.fetchone()
        if not token:
            return False

        user_id = token["user_id"]
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_password_hash, user_id)
        )
        if cursor.rowcount != 1:
            return False

        cursor.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
            """,
            (now, user_id)
        )

        cursor.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (now, user_id)
        )
        return True


def invalidate_password_reset_token(token_hash: str) -> bool:
    """Marks a password reset token as used/invalid."""
    now = int(time.time())
    with transaction() as cursor:
        cursor.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now, token_hash)
        )
        return cursor.rowcount > 0


def get_email_notification_preferences(student_id: str) -> Dict[str, Any]:
    """Retrieve student email notification preferences."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM email_notification_preferences WHERE student_id = ?",
            (student_id,)
        )
        row = cursor.fetchone()
        if row:
            rec = dict(row)
            return {
                "student_id": rec["student_id"],
                "email": rec.get("email") or "",
                "output_language": rec.get("output_language") or "en",
                "email_enabled": bool(rec.get("email_enabled", 1)),
                "notify_welcome": bool(rec.get("notify_welcome", 1)),
                "notify_application_created": bool(rec.get("notify_application_created", 1)),
                "notify_status_change": bool(rec.get("notify_status_change", 1)),
                "notify_interview_reminder": bool(rec.get("notify_interview_reminder", 1)),
                "notify_deadline_reminder": bool(rec.get("notify_deadline_reminder", 1)),
            }
        return {
            "student_id": student_id,
            "email": "",
            "output_language": "en",
            "email_enabled": True,
            "notify_welcome": True,
            "notify_application_created": True,
            "notify_status_change": True,
            "notify_interview_reminder": True,
            "notify_deadline_reminder": True
        }


def update_email_notification_preferences(
    student_id: str,
    email: Optional[str] = None,
    output_language: str = "en",
    email_enabled: bool = True,
    notify_welcome: bool = True,
    notify_application_created: bool = True,
    notify_status_change: bool = True,
    notify_interview_reminder: bool = True,
    notify_deadline_reminder: bool = True,
    db_path: Optional[str] = None,
) -> bool:
    """Saves or updates email notification settings for a student (#186, #298)."""
    try:
        effective_id = (student_id or "default_student").strip()
        with transaction(db_path) as cursor:
            cursor.execute(
                """
                INSERT INTO email_notification_preferences (
                    student_id, email, output_language, email_enabled, notify_welcome,
                    notify_application_created, notify_status_change, notify_interview_reminder,
                    notify_deadline_reminder, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(student_id) DO UPDATE SET
                    email = coalesce(excluded.email, email_notification_preferences.email),
                    output_language = excluded.output_language,
                    email_enabled = excluded.email_enabled,
                    notify_welcome = excluded.notify_welcome,
                    notify_application_created = excluded.notify_application_created,
                    notify_status_change = excluded.notify_status_change,
                    notify_interview_reminder = excluded.notify_interview_reminder,
                    notify_deadline_reminder = excluded.notify_deadline_reminder,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    effective_id,
                    email.strip() if email else None,
                    output_language.strip() if output_language else "en",
                    1 if email_enabled else 0,
                    1 if notify_welcome else 0,
                    1 if notify_application_created else 0,
                    1 if notify_status_change else 0,
                    1 if notify_interview_reminder else 0,
                    1 if notify_deadline_reminder else 0,
                ),
            )
            return True
    except Exception as e:
        logger.error(f"Error updating notification preferences for {student_id}: {e}")
        return False


def save_email_notification_preferences(
    student_id: str,
    prefs: Dict[str, Any],
    db_path: Optional[str] = None,
) -> bool:
    """Save or update student email notification preferences dict."""
    return update_email_notification_preferences(
        student_id=student_id,
        email=prefs.get("email"),
        output_language=prefs.get("output_language", "en"),
        email_enabled=bool(prefs.get("email_enabled", True)),
        notify_welcome=bool(prefs.get("notify_welcome", True)),
        notify_application_created=bool(prefs.get("notify_application_created", True)),
        notify_status_change=bool(prefs.get("notify_status_change", True)),
        notify_interview_reminder=bool(prefs.get("notify_interview_reminder", True)),
        notify_deadline_reminder=bool(prefs.get("notify_deadline_reminder", True)),
        db_path=db_path,
    )


def get_user_data_export_manifest(student_id: str) -> Dict[str, Any]:
    """Collects all counts associated with a student across the database for GDPR audit."""
    with get_connection() as conn:
        cursor = conn.cursor()
        summary: Dict[str, Any] = {"student_id": student_id, "tables": {}}

        tables = [
            ("analyses", "SELECT COUNT(*) FROM analyses WHERE student_id = ?"),
            ("interviews", "SELECT COUNT(*) FROM interviews WHERE analysis_id IN (SELECT id FROM analyses WHERE student_id = ?)"),
            ("job_applications", "SELECT COUNT(*) FROM job_applications WHERE student_id = ?"),
            ("preparation_calendar_events", "SELECT COUNT(*) FROM preparation_calendar_events WHERE student_id = ?"),
            ("company_preparation_checklists", "SELECT COUNT(*) FROM company_preparation_checklists WHERE student_id = ?"),
            ("coding_submissions", "SELECT COUNT(*) FROM coding_submissions WHERE student_id = ?"),
            ("technical_self_assessments", "SELECT COUNT(*) FROM technical_self_assessments WHERE student_id = ?"),
            ("user_feedback", "SELECT COUNT(*) FROM user_feedback WHERE student_id = ?"),
            ("interview_preparation_notes", "SELECT COUNT(*) FROM interview_preparation_notes WHERE student_id = ?"),
            ("analysis_annotations", "SELECT COUNT(*) FROM analysis_annotations WHERE student_id = ?"),
            ("saved_job_descriptions", "SELECT COUNT(*) FROM saved_job_descriptions WHERE student_id = ?"),
            ("users", "SELECT COUNT(*) FROM users WHERE id = ?"),
        ]

        for table_name, query in tables:
            try:
                cursor.execute(query, (student_id,))
                summary["tables"][table_name] = cursor.fetchone()[0]
            except Exception:
                summary["tables"][table_name] = 0

        summary["total_records"] = sum(summary["tables"].values())
        return summary


def delete_user_account_and_personal_data(student_id: str) -> Dict[str, Any]:
    """Permanently deletes a student account and cascades removal across all tables."""
    if not student_id or not student_id.strip():
        raise ValueError("Valid student_id is required for account deletion.")

    deleted_counts: Dict[str, int] = {}
    with transaction() as cursor:
        cursor.execute(
            """
            DELETE FROM interviews
            WHERE analysis_id IN (SELECT id FROM analyses WHERE student_id = ?)
            """,
            (student_id,)
        )
        deleted_counts["interviews"] = cursor.rowcount

        cascade_tables = [
            "analyses", "batch_comparisons", "job_applications",
            "email_notification_preferences", "interview_experiences",
            "preparation_calendar_events", "company_preparation_checklists",
            "coding_submissions", "technical_self_assessments", "user_feedback",
            "interview_preparation_notes", "analysis_annotations",
            "saved_job_descriptions", "student_profiles",
            "bookmarked_coding_questions", "bookmarked_interview_questions",
            "recently_viewed_jobs", "auth_sessions"
        ]

        for table in cascade_tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone():
                try:
                    cursor.execute(f"DELETE FROM {table} WHERE student_id = ?", (student_id,))
                    deleted_counts[table] = cursor.rowcount
                except Exception:
                    pass

        cursor.execute("DELETE FROM users WHERE id = ?", (student_id,))
        deleted_counts["users"] = cursor.rowcount

    # File system cleanup
    try:
        for folder in [getattr(config, "UPLOAD_FOLDER", ""), getattr(config, "REPORT_FOLDER", "")]:
            if folder and os.path.exists(folder):
                for fname in os.listdir(folder):
                    if student_id in fname:
                        fpath = os.path.join(folder, fname)
                        if os.path.isfile(fpath):
                            os.remove(fpath)
    except Exception as f_err:
        logger.warning(f"Disk cleanup non-fatal error during purge: {f_err}")

    return {
        "status": "purged",
        "student_id": student_id,
        "deleted_counts": deleted_counts,
        "total_deleted": sum(deleted_counts.values())
    }
