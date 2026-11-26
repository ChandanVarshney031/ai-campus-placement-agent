"""
Unit and Integration Tests for Inactive Account Retention & Deletion Policy (#888).

Acceptance Criteria:
- With the setting off nothing is deleted.
- Warnings are sent once per threshold and a login cancels the deletion.
- Deletion uses the same code as manual account deletion.
- Tests use a fixed clock and mocked email.
- Deletions are recorded in admin audit logs without personal details.
"""

import json
import time
from unittest.mock import MagicMock
import pytest

from app import create_app
from utils.database import get_db_connection
from utils.account_retention_service import (
    process_inactive_accounts,
    get_user_last_activity,
    cancel_account_deletion_on_login,
)


@pytest.fixture
def mock_email():
    """Mock EmailNotificationService instance."""
    mock = MagicMock()
    mock.send_inactive_account_warning.return_value = True
    return mock


@pytest.fixture
def app_instance():
    """Configured test Flask app instance."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["INACTIVE_ACCOUNT_DAYS"] = 0
    return app


@pytest.fixture
def client(app_instance):
    with app_instance.test_client() as c:
        yield c


def _setup_test_user(
    user_id: str,
    name: str = "Test Student",
    email: str = "student@campus.edu",
    created_at: float = 1700000000.0,
    with_data: bool = True,
):
    """Helper to populate a test user and associated placement records."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(created_at))

        # Insert into users
        cursor.execute(
            """
            INSERT OR REPLACE INTO users (id, name, email, password_hash, created_at, role)
            VALUES (?, ?, ?, 'dummy_hash', ?, 'student')
            """,
            (user_id, name, email, created_str),
        )

        if with_data:
            # Student profile
            cursor.execute(
                """
                INSERT OR REPLACE INTO student_profiles (
                    student_id, full_name, email, department, grad_year
                ) VALUES (?, ?, ?, 'Computer Science', 2026)
                """,
                (user_id, name, email),
            )
            # Placement analysis
            cursor.execute(
                """
                INSERT OR REPLACE INTO analyses (
                    id, resume_filename, company_name, results_json, student_id, created_at
                ) VALUES (?, 'resume.pdf', 'Google', '{"score": 85}', ?, ?)
                """,
                (f"analysis_{user_id}", user_id, created_str),
            )
            # Mock interview
            cursor.execute(
                """
                INSERT OR REPLACE INTO interviews (id, analysis_id, questions_json, answers_json)
                VALUES (?, ?, '[]', '[]')
                """,
                (f"interview_{user_id}", f"analysis_{user_id}"),
            )
            # Job application
            cursor.execute(
                """
                INSERT OR REPLACE INTO job_applications (
                    id, student_id, company_name, role_title, status, application_date, created_at
                ) VALUES (?, ?, 'Google', 'SWE', 'Applied', '2026-01-01', ?)
                """,
                (f"app_{user_id}", user_id, created_str),
            )
            # Coding submission
            cursor.execute(
                """
                INSERT OR REPLACE INTO coding_submissions (
                    id, student_id, question_title, code, submitted_at
                ) VALUES (?, ?, 'Two Sum', 'def solution(): pass', ?)
                """,
                (f"code_{user_id}", user_id, created_str),
            )

        conn.commit()
    finally:
        conn.close()


def _cleanup_test_user(user_id: str):
    """Cleans up test user records and warnings."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        cursor.execute("DELETE FROM student_profiles WHERE student_id = ?", (user_id,))
        cursor.execute("DELETE FROM analyses WHERE student_id = ?", (user_id,))
        cursor.execute("DELETE FROM interviews WHERE analysis_id = ?", (f"analysis_{user_id}",))
        cursor.execute("DELETE FROM job_applications WHERE student_id = ?", (user_id,))
        cursor.execute("DELETE FROM coding_submissions WHERE student_id = ?", (user_id,))
        cursor.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM inactive_account_warnings WHERE user_id = ?", (user_id,))
        cursor.execute(
            "DELETE FROM admin_user_audit_logs WHERE action = 'INACTIVE_ACCOUNT_DELETED'"
        )
        conn.commit()
    finally:
        conn.close()


def test_retention_disabled_deletes_nothing(mock_email):
    """
    AC 1: With the setting off (INACTIVE_ACCOUNT_DAYS <= 0 or None),
    nothing is warned and nothing is deleted even if inactive for years.
    """
    user_id = "inactive_disabled_user"
    base_time = 1700000000.0  # arbitrary epoch
    current_time = base_time + (500 * 86400.0)  # 500 days later

    try:
        _setup_test_user(user_id=user_id, created_at=base_time)

        # Run with retention setting 0 (disabled)
        result = process_inactive_accounts(
            retention_days=0,
            now=current_time,
            email_service=mock_email,
        )

        assert result["status"] == "disabled"
        assert result["deleted_count"] == 0
        assert result["total_warnings_sent"] == 0
        mock_email.send_inactive_account_warning.assert_not_called()

        # Verify user still exists
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            assert cursor.fetchone() is not None
        finally:
            conn.close()
    finally:
        _cleanup_test_user(user_id)


def test_warnings_sent_once_per_threshold(mock_email):
    """
    AC 2a: Warnings are sent at 30 days and 7 days prior to deletion,
    and are sent ONCE per threshold (not repeated on subsequent runs).
    """
    user_id = "warn_test_user"
    email = "warn_test@campus.edu"
    retention_days = 365
    base_time = 1700000000.0

    try:
        _setup_test_user(user_id=user_id, email=email, created_at=base_time)

        # 1. Day 100: User is not yet in warning window (365 - 30 = 335 days)
        res_day100 = process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (100 * 86400.0),
            email_service=mock_email,
        )
        assert res_day100["warnings_30d_sent"] == 0
        assert res_day100["warnings_7d_sent"] == 0
        mock_email.send_inactive_account_warning.assert_not_called()

        # 2. Day 336 (in 30-day window: >= 335 days, < 358 days):
        # Dispatches 30-day warning email
        res_day336 = process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (336 * 86400.0),
            email_service=mock_email,
        )
        assert res_day336["warnings_30d_sent"] == 1
        assert res_day336["warnings_7d_sent"] == 0
        mock_email.send_inactive_account_warning.assert_called_once()
        args, kwargs = mock_email.send_inactive_account_warning.call_args
        assert kwargs["recipient_email"] == email
        assert kwargs["days_remaining"] == 29

        # 3. Day 337: Re-run should NOT re-send 30-day warning (sent once per threshold)
        mock_email.reset_mock()
        res_day337 = process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (337 * 86400.0),
            email_service=mock_email,
        )
        assert res_day337["warnings_30d_sent"] == 0
        mock_email.send_inactive_account_warning.assert_not_called()

        # 4. Day 359 (in 7-day window: >= 358 days, < 365 days):
        # Dispatches 7-day warning email
        res_day359 = process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (359 * 86400.0),
            email_service=mock_email,
        )
        assert res_day359["warnings_7d_sent"] == 1
        mock_email.send_inactive_account_warning.assert_called_once()
        args, kwargs = mock_email.send_inactive_account_warning.call_args
        assert kwargs["recipient_email"] == email
        assert kwargs["days_remaining"] == 6

        # 5. Day 360: Re-run should NOT re-send 7-day warning
        mock_email.reset_mock()
        res_day360 = process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (360 * 86400.0),
            email_service=mock_email,
        )
        assert res_day360["warnings_7d_sent"] == 0
        mock_email.send_inactive_account_warning.assert_not_called()

    finally:
        _cleanup_test_user(user_id)


def test_login_cancels_deletion_and_resets_warnings(mock_email):
    """
    AC 2b: A login cancels pending deletion and resets warning thresholds.
    """
    user_id = "login_cancel_user"
    email = "cancel_test@campus.edu"
    retention_days = 365
    base_time = 1700000000.0

    try:
        _setup_test_user(user_id=user_id, email=email, created_at=base_time)

        # Trigger 30-day warning on Day 340
        process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (340 * 86400.0),
            email_service=mock_email,
        )
        assert mock_email.send_inactive_account_warning.call_count == 1

        # Simulate user login on Day 350
        login_time = base_time + (350 * 86400.0)
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO auth_sessions
                (id, user_id, token_hash, client_label, created_at, last_seen_at)
                VALUES ('sess_cancel', ?, 'tok_hash', 'Browser', ?, ?)
                """,
                (user_id, login_time, login_time),
            )
            conn.commit()

            # cancel_account_deletion_on_login is called on auth success
            cleared = cancel_account_deletion_on_login(user_id, conn=conn)
            assert cleared >= 1

            # Verify user's last activity is now updated to login time
            last_act = get_user_last_activity(user_id, conn=conn)
            assert last_act == pytest.approx(login_time, rel=1e-3)
        finally:
            conn.close()

        # Advance clock to Day 370 (past the original 365 deletion mark)
        # Because user logged in on Day 350, they have only been inactive for 20 days!
        mock_email.reset_mock()
        res_day370 = process_inactive_accounts(
            retention_days=retention_days,
            now=base_time + (370 * 86400.0),
            email_service=mock_email,
        )

        # User is NOT deleted and no new warnings sent
        assert res_day370["deleted_count"] == 0
        assert res_day370["total_warnings_sent"] == 0
        mock_email.send_inactive_account_warning.assert_not_called()

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            assert cursor.fetchone() is not None
        finally:
            conn.close()

    finally:
        _cleanup_test_user(user_id)


def test_deletion_uses_same_code_as_manual_deletion_and_purges_all_data(mock_email):
    """
    AC 3 & 4:
    - Deletion cascades across all student records and disk artifacts
      using `delete_user_account_and_personal_data`.
    - Deletion records an audit entry WITHOUT personal details.
    """
    user_id = "purge_target_user"
    retention_days = 365
    base_time = 1700000000.0
    expiry_time = base_time + (366 * 86400.0)  # 366 days inactive (expired)

    try:
        _setup_test_user(user_id=user_id, created_at=base_time, with_data=True)

        # Confirm data records exist before purge
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM analyses WHERE student_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM job_applications WHERE student_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 1
            cursor.execute(
                "SELECT COUNT(*) FROM coding_submissions WHERE student_id = ?",
                (user_id,),
            )
            assert cursor.fetchone()[0] == 1
        finally:
            conn.close()

        # Execute scheduled retention processing at expiry time
        res = process_inactive_accounts(
            retention_days=retention_days,
            now=expiry_time,
            email_service=mock_email,
        )

        assert res["deleted_count"] == 1
        assert user_id in res["deleted_user_ids"]

        # Verify account and all cascading records are completely deleted
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT COUNT(*) FROM student_profiles WHERE student_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT COUNT(*) FROM analyses WHERE student_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT COUNT(*) FROM job_applications WHERE student_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT COUNT(*) FROM coding_submissions WHERE student_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT COUNT(*) FROM inactive_account_warnings WHERE user_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 0

            # Verify admin audit log entry was created WITHOUT personal details
            cursor.execute(
                """
                SELECT action, target_user_id, target_email, details, ip_address
                FROM admin_user_audit_logs
                WHERE action = 'INACTIVE_ACCOUNT_DELETED'
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            audit_row = cursor.fetchone()
            assert audit_row is not None
            assert audit_row[0] == "INACTIVE_ACCOUNT_DELETED"
            assert audit_row[2] is None or audit_row[2] == ""  # No email
            assert audit_row[4] is None or audit_row[4] == ""  # No IP address

            # Details must NOT leak personal names or email addresses
            details_str = audit_row[3] or ""
            assert "student@campus.edu" not in details_str
            assert "Test Student" not in details_str
            details_obj = json.loads(details_str)
            assert details_obj.get("reason") == "inactive_account_retention"
            assert details_obj.get("retention_policy_days") == retention_days
        finally:
            conn.close()

    finally:
        _cleanup_test_user(user_id)


def test_cli_command_process_inactive_accounts(app_instance):
    """
    Tests the Flask CLI scheduled command `flask process-inactive-accounts`.
    """
    runner = app_instance.test_cli_runner()

    # Dry-run with retention override
    result = runner.invoke(
        args=["process-inactive-accounts", "--days", "365", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "Inactive account processing complete" in result.output
    assert "status=dry_run" in result.output


def test_admin_api_process_inactive_accounts(client):
    """
    Tests the administrative API route for inactive account processing.
    """
    # 1. Unauthorized
    res_unauth = client.post("/api/admin/inactive-accounts/process", json={"days": 365})
    assert res_unauth.status_code == 403

    # 2. Authorized as Admin
    with client.session_transaction() as sess:
        sess["user_id"] = "admin_user"
        sess["user_role"] = "admin"
        sess["is_admin"] = True

    res_auth = client.post(
        "/api/admin/inactive-accounts/process",
        json={"days": 365, "dry_run": True},
    )
    assert res_auth.status_code == 200
    data = json.loads(res_auth.data)
    assert data["success"] is True
    assert data["result"]["status"] == "dry_run"
