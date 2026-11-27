"""
Auth feature area routes & endpoints (#553).
Handles authentication, registration, session management, 2FA, and password security.
"""

import os
import io
import shutil
import uuid
import json
import time
import logging
import re
import secrets
import hashlib
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    send_file,
    Response,
    make_response,
    session as flask_session,
    stream_with_context,
    current_app,
    g,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException
from utils.rate_limiter import rate_limit
from utils.migrations import run_database_migrations, get_migration_status
import config
from utils.email_service import EmailNotificationService
from utils.database import *
from utils.aptitude_service import *
from utils.group_discussion_service import *
from utils.student_data_exporter import *
from utils.topic_practice_service import *
from utils.experience_search_service import *
from utils.bookmark_folders import *
from utils.interview_moderation import *
from utils.deadline_reminders import *
from utils.company_profile_manager import *
from utils.interview_helpful_service import get_contributor_helped_students_count
from utils.progress_tracker import *
from utils.password_security import evaluate_password_strength, validate_password_security
from utils.pagination import Pagination
from utils.cohort_analytics import CohortAnalyticsService
from utils.resume_info_extractor import *
from utils.resume_pii_service import pii_service, REDACTION_TOKENS
from utils.analysis_cache import *
from utils.batch_analyzer import run_batch_company_comparison
from agents.job_recommendation_agent import JobRecommendationAgent
from utils.demo_mode import *
from utils.role_skill_gap_service import *
from utils.ats_score_service import evaluate_resume_ats_score
from utils.format_checker import *
from utils.pdf_reader import *
from utils.gemini import *
try:
    from utils.report_generator import *
except ImportError:
    from utils.report_generator import (
        generate_pdf_report,
        generate_certificate_pdf,
        generate_skills_gap_pdf,
        generate_interview_transcript_pdf,
        generate_interview_transcript_markdown,
    )
from utils.async_pipeline import *
from utils.job_feed_service import JobFeedService
from agents.coordinator import CoordinatorAgent
from agents.interview_agent import InterviewAgent
from agents.coding_agent import CodingAssessmentAgent
from agents.star_agent import StarEvaluationAgent
from agents.github_agent import GitHubAnalyzerAgent
from agents.internship_agent import InternshipRecommendationAgent
from agents.linkedin_agent import LinkedInAgent
from agents.coding_practice_agent import CodingPracticeAgent
from agents.outreach_agent import OutreachGeneratorAgent
from agents.self_introduction_agent import SelfIntroductionAgent, calculate_speaking_time
from agents.project_deep_dive_agent import ProjectDeepDiveAgent
from agents.role_prep_agent import role_prep_agent, SUPPORTED_ROLES
from utils.i18n import (
    get_translation,
    get_all_translations_for_locale,
    SUPPORTED_LOCALES,
    DEFAULT_LOCALE,
    LOCALE_NAMES,
)
from utils.output_localization import (
    DEFAULT_OUTPUT_LOCALE,
    OUTPUT_LOCALE_NAMES,
    SUPPORTED_OUTPUT_LOCALES,
    resolve_output_locale,
)
from utils.csrf import csrf, CSRFError, generate_csrf
from utils.totp import *
from utils.captcha import (
    verify_captcha,
    record_failed_attempt,
    reset_failed_attempts,
)
from utils.email_domain_validator import (
    is_email_domain_allowed,
    get_email_domain_error_message,
)
from utils.logger_config import (
    configure_logging,
    get_current_request_id,
    set_current_request_id,
    register_sensitive_text,
    clear_sensitive_texts,
)
from .common import *

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/api/session/status", methods=["GET"])
def api_session_status():
    """
    Returns current session authentication status, inactivity duration, and remaining seconds.
    Does NOT bump last_activity so periodic client polling does not prevent inactivity logout.
    """
    user_id = flask_session.get("user_id")
    timeout_seconds = current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800))
    warning_seconds = current_app.config.get("SESSION_WARNING_SECONDS", getattr(config, "SESSION_WARNING_SECONDS", 120))

    if not user_id:
        return jsonify({
            "authenticated": False,
            "timeout_seconds": timeout_seconds,
            "warning_seconds": warning_seconds,
            "remaining_seconds": 0,
            "warning": False,
            "expired": False,
        })

    now = time.time()
    last_activity = flask_session.get("last_activity", now)
    try:
        last_act_float = float(last_activity)
    except (ValueError, TypeError):
        last_act_float = now
        flask_session["last_activity"] = now

    inactivity_seconds = max(0, int(now - last_act_float))
    remaining_seconds = max(0, int(timeout_seconds - inactivity_seconds))
    is_expired = remaining_seconds <= 0
    is_warning = not is_expired and remaining_seconds <= warning_seconds

    if is_expired:
        flask_session.clear()
        return jsonify({
            "authenticated": False,
            "timeout_seconds": timeout_seconds,
            "warning_seconds": warning_seconds,
            "remaining_seconds": 0,
            "warning": False,
            "expired": True,
        })

    return jsonify({
        "authenticated": True,
        "user_id": user_id,
        "user_name": flask_session.get("user_name", ""),
        "timeout_seconds": timeout_seconds,
        "warning_seconds": warning_seconds,
        "inactivity_seconds": inactivity_seconds,
        "remaining_seconds": remaining_seconds,
        "warning": is_warning,
        "expired": False,
    })


@auth_bp.route("/api/session/keep-alive", methods=["POST"])
def api_session_keep_alive():
    """
    Extends the current session by refreshing last_activity to the current timestamp.
    Returns 401 if already expired or unauthenticated.
    """
    user_id = flask_session.get("user_id")
    timeout_seconds = current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800))
    warning_seconds = current_app.config.get("SESSION_WARNING_SECONDS", getattr(config, "SESSION_WARNING_SECONDS", 120))

    if not user_id:
        return jsonify({
            "status": "error",
            "error": "session_expired",
            "message": "Your session has expired or you are not logged in."
        }), 401

    now = time.time()
    last_activity = flask_session.get("last_activity", now)
    try:
        last_act_float = float(last_activity)
    except (ValueError, TypeError):
        last_act_float = now

    if (now - last_act_float) > timeout_seconds:
        flask_session.clear()
        resp = jsonify({
            "status": "error",
            "error": "session_expired",
            "message": "Your session has already expired due to inactivity. Please log in again."
        })
        resp.status_code = 401
        resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
        return resp

    flask_session["last_activity"] = now
    return jsonify({
        "status": "ok",
        "message": "Session extended successfully.",
        "remaining_seconds": timeout_seconds,
        "timeout_seconds": timeout_seconds,
        "warning_seconds": warning_seconds,
    })


@auth_bp.route("/api/session/logout", methods=["POST"])
def api_session_logout():
    """
    API endpoint to securely terminate session and clear credentials.
    """
    current_user_id = flask_session.get("user_id")
    current_session_id = flask_session.get("auth_session_id")
    if current_user_id and current_session_id:
        revoke_auth_session(current_user_id, current_session_id)
    flask_session.clear()
    resp = jsonify({
        "status": "ok",
        "message": "Logged out successfully."
    })
    resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
    resp.set_cookie("interview_drafts_cleared", "1", max_age=60, path="/")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    was_expired = request.args.get("expired") == "1"
    current_user_id = flask_session.get("user_id")
    current_session_id = flask_session.get("auth_session_id")
    if current_user_id and current_session_id:
        revoke_auth_session(current_user_id, current_session_id)
    flask_session.clear()
    if was_expired:
        flash("Your session has expired due to inactivity. Please log in again.", "warning")
        resp = make_response(redirect(url_for("login")))
    else:
        flash("You have been logged out.", "success")
        resp = make_response(redirect(url_for("index")))
    resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
    resp.set_cookie("interview_drafts_cleared", "1", max_age=60, path="/")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("login.html")

        client_ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.headers.get("X-Real-IP", "").strip()
            or request.remote_addr
            or "127.0.0.1"
        )

        from utils.account_lockout_service import lockout_service
        is_locked, remaining_seconds, delay_seconds = lockout_service.check_lockout(email, client_ip)
        if is_locked:
            flash("Too many failed login attempts. Please try again later.", "error")
            return render_template("login.html")

        if delay_seconds > 0 and not current_app.config.get("TESTING"):
            time.sleep(delay_seconds)

        is_valid_captcha, captcha_error = verify_captcha(request, form_type="login", identifier=email)
        if not is_valid_captcha:
            flash(captcha_error, "error")
            return render_template("login.html")

        user = get_user_by_email(email)

        if not user or not check_password_hash(user["password_hash"], password):
            is_now_locked, failure_count = lockout_service.record_failed_attempt(email, client_ip)
            record_failed_attempt(request.remote_addr, email)
            if is_now_locked:
                flash("Too many failed login attempts. Please try again later.", "error")
            else:
                flash("Invalid email or password.", "error")
            return render_template("login.html")

        lockout_service.reset_failed_attempts(email, client_ip)
        reset_failed_attempts(request.remote_addr, email)

        # Deactivated account enforcement (#853)
        if user.get("is_active") is not None and not bool(user.get("is_active", 1)):
            flash("Your account has been deactivated. Please contact an administrator for assistance.", "error")
            return render_template("login.html")

        # Email verification enforcement (#206)
        # Note: If email verification is enabled, users must verify before full session login,
        # unless running with legacy or test bypass when unverified accounts are flagged.
        is_verified = bool(user.get("is_verified", 0))
        if not is_verified and not current_app.config.get("TESTING_BYPASS_VERIFICATION", False):
            flash("Please verify your email address before logging in. Check your inbox for the verification link or request a new one below.", "warning")
            return render_template("login.html", unverified_email=email)

        # Two-Factor Authentication Second Step (#380)
        if bool(user.get("two_factor_enabled", 0)):
            flask_session.clear()
            flask_session["2fa_pending_user_id"] = user["id"]
            flask_session["2fa_pending_email"] = user["email"]
            flask_session["2fa_pending_time"] = time.time()
            next_page = request.args.get("next") or request.form.get("next")
            if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                flask_session["2fa_pending_next"] = next_page
            return redirect(url_for("verify_two_factor"))

        flask_session.clear()
        now = time.time()
        flask_session["user_id"] = user["id"]
        flask_session["user_name"] = user["name"]
        flask_session["last_activity"] = now
        flask_session["login_time"] = now
        auth_session = create_auth_session(user["id"], _session_client_label())
        flask_session["auth_session_id"] = auth_session["id"]
        flask_session["auth_session_token"] = auth_session["token"]
        flask_session.permanent = True

        try:
            from utils.account_retention_service import cancel_account_deletion_on_login
            cancel_account_deletion_on_login(user["id"])
        except Exception:
            pass

        flash("Login successful.", "success")
        next_page = request.args.get("next") or request.form.get("next")
        if next_page and next_page.startswith("/") and not next_page.startswith("//"):
            return redirect(next_page)
        return redirect(url_for("index"))

    return render_template("login.html")


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def verify_two_factor():
    """
    Second sign-in step: Verifies 6-digit TOTP code or emergency backup recovery code (#380).
    """
    pending_user_id = flask_session.get("2fa_pending_user_id")
    pending_time = flask_session.get("2fa_pending_time", 0)

    # 10-minute timeout for completing the second sign-in step
    if not pending_user_id or (time.time() - float(pending_time)) > 600:
        flask_session.pop("2fa_pending_user_id", None)
        flask_session.pop("2fa_pending_email", None)
        flask_session.pop("2fa_pending_time", None)
        flask_session.pop("2fa_pending_next", None)
        flash("Two-factor verification session expired or invalid. Please sign in again.", "warning")
        return redirect(url_for("login"))

    user = get_user_by_id(pending_user_id)
    if not user or not bool(user.get("two_factor_enabled", 0)):
        flask_session.clear()
        flash("Two-factor authentication is not active on this account.", "error")
        return redirect(url_for("login"))

    if user.get("is_active") is not None and not bool(user.get("is_active", 1)):
        flask_session.clear()
        flash("Your account has been deactivated. Please contact an administrator for assistance.", "error")
        return redirect(url_for("login"))

    email = user.get("email", "")
    parts = email.split("@")
    if len(parts) == 2 and len(parts[0]) > 2:
        masked_email = f"{parts[0][0]}***{parts[0][-1]}@{parts[1]}"
    elif len(parts) == 2:
        masked_email = f"***@{parts[1]}"
    else:
        masked_email = email

    if request.method == "POST":
        totp_code = (request.form.get("code") or "").strip()
        backup_code = (request.form.get("backup_code") or "").strip()

        # 1. Verify emergency backup recovery code if provided (#380, #406)
        if backup_code:
            # Check dedicated account recovery codes table first (#406)
            from utils.recovery_code_service import recovery_code_service
            is_valid_rec, rec_msg = recovery_code_service.verify_and_consume_code(user["id"], backup_code)

            if is_valid_rec:
                logger.info("User %s signed in using an account recovery code (#406).", user["id"])
                next_page = flask_session.get("2fa_pending_next")
                flask_session.clear()
                now = time.time()
                flask_session["user_id"] = user["id"]
                flask_session["user_name"] = user["name"]
                flask_session["last_activity"] = now
                flask_session["login_time"] = now
                auth_session = create_auth_session(user["id"], _session_client_label())
                flask_session["auth_session_id"] = auth_session["id"]
                flask_session["auth_session_token"] = auth_session["token"]
                flask_session.permanent = True

                try:
                    from utils.account_retention_service import cancel_account_deletion_on_login
                    cancel_account_deletion_on_login(user["id"])
                except Exception:
                    pass

                flash(
                    f"Signed in using an emergency recovery code. This code has been consumed. {rec_msg}",
                    "warning"
                )
                if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                    return redirect(next_page)
                return redirect(url_for("index"))

            # Fallback to legacy 2FA backup codes (#380)
            stored_hashes = user.get("two_factor_backup_codes")
            is_valid, remaining_hashes = verify_and_consume_backup_code(stored_hashes, backup_code)
            if is_valid:
                update_user_backup_codes(user["id"], remaining_hashes)
                logger.info("User %s signed in using a backup recovery code.", user["id"])

                next_page = flask_session.get("2fa_pending_next")
                flask_session.clear()
                now = time.time()
                flask_session["user_id"] = user["id"]
                flask_session["user_name"] = user["name"]
                flask_session["last_activity"] = now
                flask_session["login_time"] = now
                auth_session = create_auth_session(user["id"], _session_client_label())
                flask_session["auth_session_id"] = auth_session["id"]
                flask_session["auth_session_token"] = auth_session["token"]
                flask_session.permanent = True

                try:
                    from utils.account_retention_service import cancel_account_deletion_on_login
                    cancel_account_deletion_on_login(user["id"])
                except Exception:
                    pass

                remaining_count = len(remaining_hashes)
                flash(
                    f"Signed in using an emergency backup recovery code. This code has been consumed. "
                    f"You have {remaining_count} backup code{'s' if remaining_count != 1 else ''} remaining.",
                    "warning"
                )
                if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                    return redirect(next_page)
                return redirect(url_for("index"))
            else:
                flash("Invalid or already used backup recovery code. Please check your saved codes.", "error")
                return render_template("login_two_factor.html", masked_email=masked_email)

        # 2. Verify 6-digit TOTP authenticator app code
        if not totp_code:
            flash("Please enter the 6-digit verification code from your authenticator app.", "error")
            return render_template("login_two_factor.html", masked_email=masked_email)

        secret = user.get("two_factor_secret", "")
        if verify_totp_code(secret, totp_code):
            next_page = flask_session.get("2fa_pending_next")
            flask_session.clear()
            now = time.time()
            flask_session["user_id"] = user["id"]
            flask_session["user_name"] = user["name"]
            flask_session["last_activity"] = now
            flask_session["login_time"] = now
            auth_session = create_auth_session(user["id"], _session_client_label())
            flask_session["auth_session_id"] = auth_session["id"]
            flask_session["auth_session_token"] = auth_session["token"]
            flask_session.permanent = True

            try:
                from utils.account_retention_service import cancel_account_deletion_on_login
                cancel_account_deletion_on_login(user["id"])
            except Exception:
                pass

            flash("Two-factor authentication verified successfully. Welcome back!", "success")
            if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)
            return redirect(url_for("index"))
        else:
            flash("Invalid verification code. Please check your authenticator app and try again.", "error")
            return render_template("login_two_factor.html", masked_email=masked_email)

    return render_template("login_two_factor.html", masked_email=masked_email)


@auth_bp.route("/account/two-factor", methods=["GET"])
def two_factor_settings():
    """
    Displays current two-factor authentication security status and recovery guidance (#380).
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required.", "error")
        return redirect(url_for("login"))

    two_factor_status = get_user_two_factor_status(user_id)
    return render_template(
        "two_factor_settings.html",
        user=user,
        two_factor=two_factor_status
    )


@auth_bp.route("/account/two-factor/setup", methods=["GET", "POST"])
def setup_two_factor():
    """
    Two-factor authentication setup flow (#380).
    Generates TOTP secret and backup recovery codes, and verifies confirmation code before enabling.
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required.", "error")
        return redirect(url_for("login"))

    if bool(user.get("two_factor_enabled", 0)):
        flash("Two-factor authentication is already enabled for your account.", "info")
        return redirect(url_for("two_factor_settings"))

    if request.method == "POST":
        verification_code = (request.form.get("code") or "").strip()
        current_password = request.form.get("password") or ""

        # Verify password to authorize security change
        if not check_password_hash(user["password_hash"], current_password):
            flash("Incorrect account password. Please enter your current password to confirm setup.", "error")
            secret = flask_session.get("pending_2fa_secret", "")
            raw_codes = flask_session.get("pending_2fa_raw_codes", [])
            formatted_secret = format_totp_secret(secret)
            otpauth_uri = generate_totp_uri(secret, user["email"])
            return render_template(
                "setup_two_factor.html",
                secret=secret,
                formatted_secret=formatted_secret,
                otpauth_uri=otpauth_uri,
                backup_codes=raw_codes
            )

        secret = flask_session.get("pending_2fa_secret")
        raw_codes = flask_session.get("pending_2fa_raw_codes")
        if not secret or not raw_codes:
            flash("Setup session expired. Please restart the two-factor setup process.", "warning")
            return redirect(url_for("setup_two_factor"))

        # Verify confirmation code against pending secret
        if not verify_totp_code(secret, verification_code):
            flash("Invalid verification code. Please ensure your authenticator app time is synced and enter the 6-digit code currently shown.", "error")
            formatted_secret = format_totp_secret(secret)
            otpauth_uri = generate_totp_uri(secret, user["email"])
            return render_template(
                "setup_two_factor.html",
                secret=secret,
                formatted_secret=formatted_secret,
                otpauth_uri=otpauth_uri,
                backup_codes=raw_codes
            )

        # Hash emergency recovery codes and persist to database
        hashed_codes = [hash_backup_code(c) for c in raw_codes]
        enable_user_two_factor(user_id, secret, hashed_codes)

        flask_session.pop("pending_2fa_secret", None)
        flask_session.pop("pending_2fa_raw_codes", None)

        flash("Two-factor authentication has been successfully enabled! Be sure you have saved your recovery codes.", "success")
        return redirect(url_for("two_factor_settings"))

    # GET: Generate fresh secret and 8 backup recovery codes
    secret = generate_totp_secret()
    raw_codes = generate_backup_codes(8)
    flask_session["pending_2fa_secret"] = secret
    flask_session["pending_2fa_raw_codes"] = raw_codes

    formatted_secret = format_totp_secret(secret)
    otpauth_uri = generate_totp_uri(secret, user["email"])

    return render_template(
        "setup_two_factor.html",
        secret=secret,
        formatted_secret=formatted_secret,
        otpauth_uri=otpauth_uri,
        backup_codes=raw_codes
    )


@auth_bp.route("/account/two-factor/disable", methods=["POST"])
def disable_two_factor():
    """
    Disables two-factor authentication after verifying user password or current 2FA code (#380).
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required.", "error")
        return redirect(url_for("login"))

    if not bool(user.get("two_factor_enabled", 0)):
        flash("Two-factor authentication is not currently enabled.", "info")
        return redirect(url_for("two_factor_settings"))

    password = request.form.get("password") or ""
    verification_code = (request.form.get("code") or "").strip()

    is_verified = False
    if password and check_password_hash(user["password_hash"], password):
        is_verified = True
    elif verification_code and verify_totp_code(user.get("two_factor_secret", ""), verification_code):
        is_verified = True

    if not is_verified:
        flash("Verification failed. Please enter your correct account password or a valid 6-digit code to disable two-factor authentication.", "error")
        return redirect(url_for("two_factor_settings"))

    disable_user_two_factor(user_id)
    flash("Two-factor authentication has been disabled. Your account is now protected by password only.", "warning")
    return redirect(url_for("two_factor_settings"))


@auth_bp.route("/account/two-factor/regenerate-backup-codes", methods=["POST"])
def regenerate_backup_codes():
    """
    Regenerates a new set of 8 emergency backup recovery codes after verifying password (#380).
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required.", "error")
        return redirect(url_for("login"))

    if not bool(user.get("two_factor_enabled", 0)):
        flash("Two-factor authentication must be enabled before generating recovery codes.", "warning")
        return redirect(url_for("two_factor_settings"))

    password = request.form.get("password") or ""
    if not check_password_hash(user["password_hash"], password):
        flash("Incorrect password. Recovery codes could not be regenerated.", "error")
        return redirect(url_for("two_factor_settings"))

    new_raw_codes = generate_backup_codes(8)
    new_hashed_codes = [hash_backup_code(c) for c in new_raw_codes]
    update_user_backup_codes(user_id, new_hashed_codes)

    return render_template(
        "two_factor_recovery_codes.html",
        backup_codes=new_raw_codes
    )


@auth_bp.route("/account/recovery-codes", methods=["GET"])
def account_recovery_codes_dashboard():
    """
    Displays the user's account recovery codes dashboard with status and generation history (#406).
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required to access recovery codes.", "error")
        return redirect(url_for("login"))

    from utils.recovery_code_service import recovery_code_service
    status = recovery_code_service.get_recovery_codes_status(user_id)

    return render_template(
        "account_recovery_codes_dashboard.html",
        user=user,
        status=status
    )


@auth_bp.route("/account/recovery-codes/generate", methods=["POST"])
def generate_account_recovery_codes():
    """
    Generates a fresh set of one-time account recovery codes after verifying password (#406).
    Invalidates any existing recovery codes and displays plaintext codes strictly once.
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required to generate recovery codes.", "error")
        return redirect(url_for("login"))

    password = request.form.get("password") or ""
    if not check_password_hash(user["password_hash"], password):
        flash("Incorrect password. Recovery codes were not generated.", "error")
        return redirect(url_for("account_recovery_codes_dashboard"))

    from utils.recovery_code_service import recovery_code_service
    code_set = recovery_code_service.generate_new_code_set(user_id, count=10)

    # Store ephemeral plaintext codes in session strictly for the download route or immediate view
    flask_session["ephemeral_recovery_codes"] = code_set["recovery_codes"]
    flask_session["ephemeral_recovery_user_email"] = user["email"]

    return render_template(
        "account_recovery_codes_generated.html",
        recovery_codes=code_set["recovery_codes"]
    )


@auth_bp.route("/account/recovery-codes/download", methods=["GET"])
def download_account_recovery_codes():
    """
    Streams a downloadable .txt file of newly generated recovery codes (#406).
    Codes are only available immediately following generation.
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        flash("Authentication required.", "error")
        return redirect(url_for("login"))

    codes = flask_session.get("ephemeral_recovery_codes")
    email = flask_session.get("ephemeral_recovery_user_email") or user.get("email", "user")

    if not codes:
        flash("No recovery codes available for download. Codes are only downloadable immediately upon generation.", "warning")
        return redirect(url_for("account_recovery_codes_dashboard"))

    from utils.recovery_code_service import recovery_code_service
    download_text = recovery_code_service.generate_download_text(email, codes)

    response = Response(download_text, mimetype="text/plain; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=placement-account-recovery-codes.txt"
    return response


@auth_bp.route("/api/account/recovery-codes/status", methods=["GET"])
def api_recovery_codes_status():
    """
    API endpoint returning recovery codes metadata (remaining count, status) (#406).
    """
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    from utils.recovery_code_service import recovery_code_service
    status = recovery_code_service.get_recovery_codes_status(user_id)
    return jsonify({"success": True, "status": status}), 200


@auth_bp.route("/api/account/recovery-codes/generate", methods=["POST"])
def api_generate_recovery_codes():
    """
    API endpoint to generate a new set of recovery codes after password authentication (#406).
    """
    user_id = flask_session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or request.form
    password = payload.get("password") or ""

    if not check_password_hash(user["password_hash"], password):
        return jsonify({"success": False, "error": "Incorrect account password"}), 403

    from utils.recovery_code_service import recovery_code_service
    code_set = recovery_code_service.generate_new_code_set(user_id, count=10)

    # Ephemeral session storage for download
    flask_session["ephemeral_recovery_codes"] = code_set["recovery_codes"]
    flask_session["ephemeral_recovery_user_email"] = user["email"]

    return jsonify({
        "success": True,
        "message": "New recovery codes generated. Existing codes have been invalidated.",
        "batch_id": code_set["batch_id"],
        "total_count": code_set["total_count"],
        "recovery_codes": code_set["recovery_codes"]
    }), 201


@auth_bp.route("/api/account/recovery-codes/verify", methods=["POST"])
def api_verify_recovery_code():
    """
    API endpoint to verify and consume a recovery code (#406).
    """
    payload = request.get_json(silent=True) or request.form
    user_id = flask_session.get("2fa_pending_user_id") or flask_session.get("user_id") or payload.get("user_id")
    code = payload.get("code") or ""

    if not user_id or not code:
        return jsonify({"success": False, "error": "user_id and code are required"}), 400

    from utils.recovery_code_service import recovery_code_service
    is_valid, msg = recovery_code_service.verify_and_consume_code(user_id, code)

    if not is_valid:
        return jsonify({"success": False, "error": msg}), 400

    return jsonify({"success": True, "message": msg}), 200


@auth_bp.route("/profile", methods=["GET", "POST"])
def profile_view():
    """
    Renders candidate student profile, completion tracker, and active sessions (#199, #473).
    """
    student_id = (
        flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        college = request.form.get("college", "").strip()
        phone = request.form.get("phone", "").strip()
        department = request.form.get("department", "").strip()
        grad_year_raw = request.form.get("grad_year", "").strip()
        cgpa_raw = request.form.get("cgpa", "").strip()
        backlogs_raw = request.form.get("backlogs", "").strip()
        target_role = request.form.get("target_role", "").strip()
        target_company = request.form.get("target_company", "").strip()
        skills_raw = request.form.get("skills", "").strip()
        linkedin_url = request.form.get("linkedin_url", "").strip()
        github_url = request.form.get("github_url", "").strip()

        # Check email domain restriction (#654)
        if email and not is_email_domain_allowed(email):
            flash(get_email_domain_error_message(), "error")
            return redirect("/profile")

        try:
            grad_year = int(grad_year_raw) if grad_year_raw else None
        except ValueError:
            grad_year = None

        try:
            cgpa = float(cgpa_raw) if cgpa_raw else None
            if cgpa is not None:
                if cgpa < 0.0 or cgpa > 10.0:
                    flash("CGPA must be between 0.0 and 10.0.", "error")
                    return redirect("/profile")
        except ValueError:
            cgpa = None

        try:
            backlogs = int(backlogs_raw) if backlogs_raw else 0
            if backlogs < 0:
                flash("Active backlogs cannot be negative.", "error")
                return redirect("/profile")
        except ValueError:
            backlogs = 0

        skills = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else []

        save_student_profile(
            student_id=student_id,
            full_name=full_name,
            email=email,
            phone=phone,
            department=department,
            grad_year=grad_year,
            cgpa=cgpa,
            backlogs=backlogs,
            target_role=target_role,
            target_company=target_company,
            skills=skills,
            linkedin_url=linkedin_url,
            github_url=github_url,
            college=college,
        )
        flash("Profile information updated successfully!", "success")
        return redirect("/profile")

    completion_info = calculate_profile_completion(student_id=student_id)
    skills_csv = ", ".join(completion_info["profile"].get("skills", []))

    # Fetch active sessions for the signed-in user (#473)
    user_id = flask_session.get("user_id") or student_id
    timeout_seconds = current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800))
    session_rows = get_active_auth_sessions(user_id, timeout_seconds)
    current_session_id = flask_session.get("auth_session_id")
    sessions = []
    for session_row in session_rows:
        session_item = dict(session_row)
        session_item["is_current"] = (session_item["id"] == current_session_id)
        session_item["created_display"] = _format_session_timestamp(session_item.get("created_at"))
        session_item["last_seen_display"] = _format_session_timestamp(session_item.get("last_seen_at"))
        sessions.append(session_item)

    helped_students_count = get_contributor_helped_students_count(student_id)

    return render_template(
        "profile.html",
        completion=completion_info,
        profile=completion_info["profile"],
        skills_csv=skills_csv,
        student_id=student_id,
        sessions=sessions,
        current_session_id=current_session_id,
        helped_students_count=helped_students_count,
    )


@auth_bp.route("/api/profile/completion", methods=["GET"])
def api_profile_completion():
    """
    REST API endpoint returning candidate profile completion percentage and checklist (#199).
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )
    data = calculate_profile_completion(student_id=student_id)
    helped_students_count = get_contributor_helped_students_count(student_id)
    data["helped_students_count"] = helped_students_count
    return jsonify({
        "status": "success",
        "data": data,
        "helped_students_count": helped_students_count,
    }), 200


@auth_bp.route("/account/sessions", methods=["GET"])
def account_sessions():
    """Show the signed-in user's active sessions with privacy-conscious details (#473)."""
    user_id = flask_session.get("user_id")
    session_rows = get_active_auth_sessions(
        user_id,
        current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800)),
    )
    current_session_id = flask_session.get("auth_session_id")
    sessions = []
    for session_row in session_rows:
        session_item = dict(session_row)
        session_item["is_current"] = session_item["id"] == current_session_id
        session_item["created_display"] = _format_session_timestamp(session_item.get("created_at"))
        session_item["last_seen_display"] = _format_session_timestamp(session_item.get("last_seen_at"))
        sessions.append(session_item)
    return render_template("account_sessions.html", sessions=sessions)


@auth_bp.route("/account/sessions/revoke", methods=["POST"])
def revoke_account_session():
    """Revoke one of the current user's sessions, including the current session (#473)."""
    user_id = flask_session.get("user_id")
    data = request.get_json(silent=True) if request.is_json else request.form
    session_id = (data.get("session_id") or "").strip()
    next_url = request.form.get("next") or request.referrer or url_for("account_sessions")
    current_session_id = flask_session.get("auth_session_id")
    is_json = request.is_json or "application/json" in request.headers.get("Accept", "")

    if not session_id or not revoke_auth_session(user_id, session_id):
        if is_json:
            return jsonify({"status": "error", "success": False, "error": "That session is no longer active."}), 404
        flash("That session is no longer active.", "warning")
        return redirect(next_url)

    if session_id == current_session_id:
        flask_session.clear()
        if is_json:
            return jsonify({"status": "success", "success": True, "is_current": True, "message": "This session has been revoked. Please sign in again."}), 200
        flash("This session has been revoked. Please sign in again.", "success")
        return redirect(url_for("login"))

    if is_json:
        return jsonify({"status": "success", "success": True, "is_current": False, "message": "The selected session was revoked."}), 200
    flash("The selected session was revoked.", "success")
    return redirect(next_url)


@auth_bp.route("/account/sessions/revoke-others", methods=["POST"])
def revoke_other_account_sessions():
    """Revoke every active session except the session making this request (#473)."""
    user_id = flask_session.get("user_id")
    next_url = request.form.get("next") or request.referrer or url_for("account_sessions")
    current_session_id = flask_session.get("auth_session_id")
    revoked_count = revoke_other_auth_sessions(user_id, current_session_id)
    is_json = request.is_json or "application/json" in request.headers.get("Accept", "")

    if is_json:
        return jsonify({
            "status": "success",
            "success": True,
            "revoked_count": revoked_count,
            "message": f"Signed out of {revoked_count} other device{'s' if revoked_count != 1 else ''}."
        }), 200

    flash(
        f"Signed out of {revoked_count} other device{'s' if revoked_count != 1 else ''}.",
        "success",
    )
    return redirect(next_url)


@auth_bp.route("/account/change-password", methods=["POST"])
def account_change_password():
    """Change the authenticated user's password and revoke all other sessions (#473)."""
    user_id = flask_session.get("user_id")
    is_json = request.is_json or "application/json" in request.headers.get("Accept", "")
    if not user_id:
        if is_json:
            return jsonify({"status": "error", "success": False, "error": "Authentication required."}), 401
        flash("Please log in to change your password.", "error")
        return redirect(url_for("login"))

    data = request.get_json(silent=True) if request.is_json else request.form
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")
    confirm_password = data.get("confirm_password", "")
    next_url = request.form.get("next") or request.referrer or url_for("profile_view")

    user = get_user_by_id(user_id)
    if not user:
        if is_json:
            return jsonify({"status": "error", "success": False, "error": "User account not found."}), 404
        flash("User account not found.", "error")
        return redirect(url_for("login"))

    if not current_password or not new_password or not confirm_password:
        msg = "All password fields are required."
        if is_json:
            return jsonify({"status": "error", "success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(next_url)

    if not check_password_hash(user.get("password_hash", ""), current_password):
        msg = "Current password is incorrect."
        if is_json:
            return jsonify({"status": "error", "success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(next_url)

    valid, pwd_err = validate_password_security(
        new_password,
        email=user.get("email"),
        name=user.get("name"),
    )
    if not valid:
        if is_json:
            return jsonify({"status": "error", "success": False, "error": pwd_err}), 400
        flash(pwd_err, "error")
        return redirect(next_url)

    if new_password != confirm_password:
        msg = "New passwords do not match."
        if is_json:
            return jsonify({"status": "error", "success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(next_url)

    new_hash = generate_password_hash(new_password)
    current_session_id = flask_session.get("auth_session_id")
    changed = change_user_password(user_id, new_hash, keep_session_id=current_session_id)
    if changed:
        msg = "Password updated successfully. All other active sessions have been signed out."
        if is_json:
            return jsonify({"status": "success", "success": True, "message": msg}), 200
        flash(msg, "success")
    else:
        msg = "Failed to update password. Please try again."
        if is_json:
            return jsonify({"status": "error", "success": False, "error": msg}), 500
        flash(msg, "error")
    return redirect(next_url)


@auth_bp.route("/api/account/sessions", methods=["GET"])
def api_get_account_sessions():
    """Returns the authenticated user's active sessions in JSON (#473)."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "success": False, "error": "unauthorized"}), 401

    timeout_seconds = current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800))
    session_rows = get_active_auth_sessions(user_id, timeout_seconds)
    current_session_id = flask_session.get("auth_session_id")
    sessions = []
    for s in session_rows:
        item = dict(s)
        item["is_current"] = (item["id"] == current_session_id)
        item["created_display"] = _format_session_timestamp(item.get("created_at"))
        item["last_seen_display"] = _format_session_timestamp(item.get("last_seen_at"))
        sessions.append(item)

    return jsonify({"status": "success", "success": True, "sessions": sessions}), 200


@auth_bp.route("/account/delete", methods=["GET", "POST"])
def delete_account():
    """Render and process the GDPR-style account deletion confirmation page."""
    student_id = flask_session.get("user_id") or request.args.get("student_id") or "default_student"
    if not student_id or student_id.strip() == "":
        flash("Authentication required.", "error")
        return redirect(url_for("login"))

    manifest = get_user_data_export_manifest(student_id)

    if request.method == "POST":
        confirm_phrase = (request.form.get("confirm_phrase") or "").strip()
        password = request.form.get("password") or ""

        if confirm_phrase != "DELETE MY ACCOUNT":
            flash("Please type 'DELETE MY ACCOUNT' exactly as shown.", "error")
            return render_template("delete_account.html", manifest=manifest)

        user = get_user_by_id(student_id)
        password_hash = user.get("password_hash") if user else None
        if user is None or not password_hash or not check_password_hash(password_hash, password):
            flash("Incorrect password.", "error")
            return render_template("delete_account.html", manifest=manifest)

        result = delete_user_account_and_personal_data(student_id)
        flask_session.clear()
        flash(f"Your account and {result.get('total_deleted', 0)} associated data records have been permanently removed.", "success")
        return redirect(url_for("index"))

    return render_template("delete_account.html", manifest=manifest)


@auth_bp.route("/api/account/export-manifest")
def api_account_export_manifest():
    """Return the per-student GDPR export manifest for auditing and deletion review."""
    student_id = flask_session.get("user_id") or request.args.get("student_id") or "default_student"
    if not student_id or student_id.strip() == "":
        return jsonify({"status": "error", "message": "Authentication required."}), 401
    manifest = get_user_data_export_manifest(student_id)
    return jsonify({"status": "success", "student_id": student_id, "manifest": manifest})


@auth_bp.route("/api/student/data-export/manifest", methods=["GET"])
def api_student_data_export_manifest():
    """
    Returns a summary manifest of all placement records, applications, notes,
    and analyses owned by the authenticated student (#258, #469).
    """
    session_uid = (
        flask_session.get("user_id")
        or (flask_session.get("user") or {}).get("id")
        or flask_session.get("student_id")
    )
    student_id = session_uid or request.args.get("student_id") or "default_student"
    try:
        manifest = build_student_data_export_manifest(student_id)
        return jsonify({
            "status": "success",
            "manifest": manifest
        }), 200
    except Exception as e:
        logger.error(f"Error compiling student data manifest: {e}")
        return jsonify({"status": "error", "message": "Failed to compile personal data manifest."}), 500


@auth_bp.route("/api/student/data-export/download", methods=["GET"])
@auth_bp.route("/profile/download-data", methods=["GET"])
def api_student_data_export_download():
    """
    Generates and streams a downloadable data export (JSON or ZIP) containing all personal
    placement preparation data, strictly sanitized of passwords, hashes, and tokens (#469).
    Generated on demand and streamed directly without writing to server disk.
    """
    session_uid = (
        flask_session.get("user_id")
        or (flask_session.get("user") or {}).get("id")
        or flask_session.get("student_id")
    )
    student_id = session_uid or request.args.get("student_id") or "default_student"
    export_format = (request.args.get("format") or "zip").strip().lower()

    import datetime as dt
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        if export_format == "json":
            filename = f"placement_data_export_{student_id}_{timestamp}.json"
            return Response(
                stream_with_context(stream_student_data_json_chunks(student_id)),
                mimetype="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
        else:
            # Default ZIP package
            zip_buffer = generate_student_data_zip_package(student_id)
            filename = f"placement_data_export_{student_id}_{timestamp}.zip"
            return send_file(
                zip_buffer,
                mimetype="application/zip",
                as_attachment=True,
                download_name=filename
            )
    except Exception as e:
        logger.error(f"Error building student data export: {e}")
        return jsonify({"status": "error", "message": "Failed to generate personal data download package."}), 500


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@rate_limit(max_requests=5, window_seconds=900, scope="forgot_password", is_api=False)
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        is_valid_captcha, captcha_error = verify_captcha(request, form_type="forgot_password", identifier=email)
        if not is_valid_captcha:
            flash(captcha_error, "error")
            return render_template("forgot_password.html")

        # Always show the same response to prevent email enumeration
        generic_message = (
            "If an account with that email exists, "
            "a password reset link has been sent."
        )

        if not email:
            flash(generic_message, "success")
            return redirect(url_for("forgot_password"))

        if not is_email_domain_allowed(email):
            flash(get_email_domain_error_message(), "error")
            return render_template("forgot_password.html")

        user = get_user_by_email(email)

        if user:
            # Generate a cryptographically secure random token
            raw_token = secrets.token_urlsafe(32)

            # Store only the SHA-256 hash of the token
            token_hash = hashlib.sha256(
                raw_token.encode("utf-8")
            ).hexdigest()

            # Token expires after 30 minutes
            expires_at = int(time.time()) + 1800

            token_created = create_password_reset_token(
                user_id=user["id"],
                token_hash=token_hash,
                expires_at=expires_at
            )

            if token_created:
                reset_url = url_for(
                    "reset_password",
                    token=raw_token,
                    _external=True
                )
                try:
                    email_sent = email_service.send_password_reset_email(
                            recipient_email=user["email"],
                            reset_url=reset_url,
                            student_name=user["name"]
                        )

                    if not email_sent:
                            invalidate_password_reset_token(token_hash)

                except Exception:
                    invalidate_password_reset_token(token_hash)
                    logger.exception("Failed to send password reset email")
        flash(generic_message, "success")
        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@rate_limit(max_requests=10, window_seconds=900, scope="reset_password", is_api=False)
def reset_password(token):
    # Hash the token received from the reset URL
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    # Reject invalid, expired, or already-used tokens
    reset_record = get_valid_password_reset_token(token_hash)

    if not reset_record:
        flash(
            "This password reset link is invalid or has expired. "
            "Please request a new one.",
            "error"
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not new_password or not confirm_password:
            flash("Both password fields are required.", "error")
            return render_template("reset_password.html", token=token)

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        user = get_user_by_id(reset_record.get("user_id")) if reset_record else None
        valid, pwd_err = validate_password_security(
            new_password,
            email=user.get("email") if user else None,
            name=user.get("name") if user else None,
        )
        if not valid:
            flash(pwd_err, "error")
            return render_template("reset_password.html", token=token)

        # Hash the new password before storing it
        new_password_hash = generate_password_hash(new_password)

        success = reset_user_password(
            token_hash=token_hash,
            new_password_hash=new_password_hash
        )

        if not success:
            flash(
                "This password reset link is invalid or has expired. "
                "Please request a new one.",
                "error"
            )
            return redirect(url_for("forgot_password"))

        flash(
            "Your password has been reset successfully. Please log in.",
            "success"
        )
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@auth_bp.route("/invite/accept/<token>", methods=["GET"])
def accept_invite_view(token: str):
    """Deep-link shortcut for student invitation acceptance (#690)."""
    return redirect(url_for("register", invite_token=token))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    invite_token = (
        request.form.get("invite_token")
        if request.method == "POST"
        else request.args.get("invite_token")
    )
    invite_token = (invite_token or "").strip()
    invited_info = None

    if invite_token:
        from utils.student_invitation_service import verify_invitation_token
        token_check = verify_invitation_token(invite_token)
        if not token_check.get("valid"):
            flash(token_check.get("error", "Invalid or expired invitation link."), "error")
        else:
            invited_info = token_check.get("invitation")

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        college = request.form.get("college", "").strip()

        # If registered via invite, re-verify token
        if invite_token:
            from utils.student_invitation_service import verify_invitation_token
            token_check = verify_invitation_token(invite_token)
            if not token_check.get("valid"):
                flash(token_check.get("error", "This invitation link is no longer valid."), "error")
                return render_template(
                    "register.html",
                    name=name,
                    email=email,
                    college=college,
                    invite_token=invite_token,
                )
            invited_info = token_check.get("invitation")

        is_valid_captcha, captcha_error = verify_captcha(request, form_type="register", identifier=email)
        if not is_valid_captcha:
            flash(captcha_error, "error")
            return render_template(
                "register.html",
                name=name,
                email=email,
                college=college,
                invite_token=invite_token,
                invited_info=invited_info,
            )

        if not name or not email or not password:
            flash("All fields are required.", "error")
            return render_template(
                "register.html",
                name=name,
                email=email,
                college=college,
                invite_token=invite_token,
                invited_info=invited_info,
            )

        if not is_email_domain_allowed(email):
            flash(get_email_domain_error_message(), "error")
            return render_template(
                "register.html",
                name=name,
                email=email,
                college=college,
                invite_token=invite_token,
                invited_info=invited_info,
            )

        valid, pwd_err = validate_password_security(password, email=email, name=name)
        if not valid:
            flash(pwd_err, "error")
            return render_template(
                "register.html",
                name=name,
                email=email,
                college=college,
                invite_token=invite_token,
                invited_info=invited_info,
            )

        password_hash = generate_password_hash(password)
        user_id = str(uuid.uuid4())

        created = create_user(user_id, name, email, password_hash)

        if not created:
            flash("An account with this email already exists.", "error")
            return render_template(
                "register.html",
                name=name,
                email=email,
                college=college,
                invite_token=invite_token,
                invited_info=invited_info,
            )

        # Consume single-use invitation token (#690)
        if invite_token and invited_info:
            from utils.student_invitation_service import accept_invitation_token
            accept_invitation_token(
                invite_token,
                registered_user_id=user_id,
                ip_address=request.remote_addr,
            )

        # Initialize student profile with optional college (#654)
        save_student_profile(
            student_id=user_id,
            full_name=name,
            email=email,
            college=college if college else None,
        )

        # Enroll batch profile if specified on invitation (#690, #514)
        if invited_info and (invited_info.get("branch") or invited_info.get("graduation_year")):
            from utils.batch_analytics_service import BatchAnalyticsService
            try:
                BatchAnalyticsService.enroll_student_profile(
                    user_id=user_id,
                    graduation_year=int(invited_info.get("graduation_year") or 2026),
                    department=invited_info.get("branch") or "Computer Science",
                )
            except Exception as batch_err:
                logger.warning(f"Could not enroll batch profile from invitation: {batch_err}")

        # Initialize email preferences and verification token (#186, #206)
        update_email_notification_preferences(student_id=user_id, email=email, email_enabled=True)
        verification_token = generate_email_verification_token(user_id)

        try:
            email_service.send_verification_email(
                recipient_email=email,
                student_name=name,
                verification_token=verification_token,
                base_url=request.host_url
            )
            email_service.send_welcome_email(recipient_email=email, student_name=name)
        except Exception as mail_err:
            logger.warning(f"Failed to dispatch verification/welcome email: {mail_err}")

        flash("Registration successful! A verification link has been sent to your email. Please verify your account before logging in.", "success")
        return redirect(url_for("login"))

    initial_name = invited_info.get("name") if invited_info else ""
    initial_email = invited_info.get("email") if invited_info else ""
    return render_template(
        "register.html",
        name=initial_name,
        email=initial_email,
        invite_token=invite_token,
        invited_info=invited_info,
    )


@auth_bp.route("/api/auth/password-strength", methods=["POST"])
def api_password_strength():
    """
    Evaluates password strength across length, casing, numbers, and symbols (#290).
    Returns rating level, criteria pass flags, and improvement suggestions.
    Does not log or store the password.
    """
    payload = request.get_json(silent=True) or request.form
    password = payload.get("password", "") if payload else ""
    evaluation = evaluate_password_strength(password)
    return jsonify({
        "status": "success",
        "evaluation": evaluation
    }), 200


@auth_bp.route("/verify-email/<token>", methods=["GET"])
def verify_email(token: str):
    """
    Validates token and activates student account (#206).
    """
    user = verify_user_email_token(token)
    if not user:
        flash("Invalid or expired verification link. Please request a new verification link below.", "error")
        return redirect(url_for("resend_verification"))

    flash(f"Congratulations {user.get('name', '')}! Your email has been verified. You can now log in.", "success")
    return redirect(url_for("login"))


@auth_bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    """
    Resends account verification email if user hasn't verified their account yet (#206).
    """
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your registered email address.", "error")
            return render_template("resend_verification.html")

        if not is_email_domain_allowed(email):
            flash(get_email_domain_error_message(), "error")
            return render_template("resend_verification.html")

        user = get_user_by_email(email)
        if not user:
            # For security, avoid leaking whether the email exists
            flash("If an unverified account exists for this email, a new verification link has been sent.", "info")
            return redirect(url_for("login"))

        if bool(user.get("is_verified", 0)):
            flash("Your email is already verified! You can proceed to log in directly.", "info")
            return redirect(url_for("login"))

        token = generate_email_verification_token(user["id"])
        try:
            email_service.send_verification_email(
                recipient_email=email,
                student_name=user.get("name", "Student"),
                verification_token=token,
                base_url=request.host_url
            )
        except Exception as err:
            logger.error(f"Error resending verification email to {email}: {err}")

        flash("If an unverified account exists for this email, a new verification link has been sent.", "info")
        return redirect(url_for("login"))

    initial_email = request.args.get("email", "").strip().lower()
    return render_template("resend_verification.html", initial_email=initial_email)


@auth_bp.route("/notification-settings", methods=["GET", "POST"])
def notification_settings_view():
    """
    Candidate Email Notification Settings View and Preference Handler (#186, #298).
    Allows students to enable/disable transactional emails and schedule interview & deadline reminders.
    """
    student_id = request.args.get("student_id") or flask_session.get("user_id") or "default_student"

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if email and not is_email_domain_allowed(email):
            flash(get_email_domain_error_message(), "error")
            return redirect(url_for("notification_settings_view", student_id=student_id))

        email_enabled = request.form.get("email_enabled") == "on"
        notify_welcome = request.form.get("notify_welcome") == "on"
        notify_application_created = request.form.get("notify_application_created") == "on"
        notify_status_change = request.form.get("notify_status_change") == "on"
        notify_interview_reminder = request.form.get("notify_interview_reminder") == "on"
        notify_deadline_reminder = request.form.get("notify_deadline_reminder") == "on"
        requested_output_language = request.form.get("output_language", DEFAULT_OUTPUT_LOCALE)
        output_language, output_language_fallback = resolve_output_locale(requested_output_language)

        update_email_notification_preferences(
            student_id=student_id,
            email=email or None,
            output_language=output_language,
            email_enabled=email_enabled,
            notify_welcome=notify_welcome,
            notify_application_created=notify_application_created,
            notify_status_change=notify_status_change,
            notify_interview_reminder=notify_interview_reminder,
            notify_deadline_reminder=notify_deadline_reminder
        )
        try:
            update_student_deadline_preferences(
                student_id=student_id,
                email_alerts_enabled=(email_enabled and notify_deadline_reminder)
            )
        except Exception as pref_err:
            logger.debug(f"Could not update student deadline preferences: {pref_err}")

        if output_language_fallback:
            flash(
                "The selected output language is not supported. Generated reports and summaries will use English.",
                "warning",
            )
        else:
            flash("Email notification preferences updated successfully!", "success")
        return redirect(url_for("notification_settings_view", student_id=student_id))

    prefs = get_email_notification_preferences(student_id)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "preferences": prefs
        }), 200

    return render_template(
        "notification_settings.html",
        prefs=prefs,
        student_id=student_id,
        output_locale_names=OUTPUT_LOCALE_NAMES,
        supported_output_locales=SUPPORTED_OUTPUT_LOCALES,
    )


@auth_bp.route("/api/notifications/preferences", methods=["GET"])
def api_get_notification_preferences():
    """Retrieve full notification preference configuration for the logged-in student."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    try:
        from utils.notification_preferences_manager import get_student_preferences
        data = get_student_preferences(user_id)
        return jsonify({
            "success": True,
            **data
        }), 200
    except Exception as e:
        logger.error(f"Error fetching notification preferences: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/notifications/preferences", methods=["PUT"])
def api_update_notification_preferences():
    """Update notification preferences for the logged-in student."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    preferences_list = payload.get("preferences")
    if not isinstance(preferences_list, list):
        return jsonify({"success": False, "error": "Payload must contain 'preferences' list"}), 400

    try:
        from utils.notification_preferences_manager import update_student_preferences
        updated = update_student_preferences(user_id, preferences_list)
        return jsonify({
            "success": True,
            "message": "Notification preferences updated successfully",
            **updated
        }), 200
    except Exception as e:
        logger.error(f"Error updating notification preferences: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/api/notifications/preferences/master-toggle", methods=["POST"])
def api_toggle_master_notifications():
    """Turn all notifications on or off simultaneously for the student."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", True))

    try:
        from utils.notification_preferences_manager import set_master_notification_toggle
        res = set_master_notification_toggle(user_id, enabled)
        return jsonify({
            "success": True,
            "message": f"All notifications {'enabled' if enabled else 'disabled'}",
            **res
        }), 200
    except Exception as e:
        logger.error(f"Error toggling master notifications: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/notification-preferences", methods=["GET"])
def notification_preferences_view():
    """Render notification preferences management page (#260)."""
    user_id = flask_session.get("user_id")
    if not user_id:
        flash("Please log in to manage your notification preferences.", "warning")
        return redirect(url_for("login"))

    from utils.notification_preferences_manager import get_student_preferences
    prefs = get_student_preferences(user_id)
    return render_template("notification_preferences.html", preferences_data=prefs)


@auth_bp.route("/api/notifications/unread-count", methods=["GET"])
def api_get_unread_notification_count():
    """
    Returns unread in-app notification count for the active student.
    """
    from utils.in_app_notification_service import in_app_notification_service

    student_id = flask_session.get("user_id") or "default_student"
    count = in_app_notification_service.get_unread_count(student_id)
    return jsonify({"success": True, "unread_count": count}), 200


@auth_bp.route("/api/notifications", methods=["GET", "POST"])
def api_in_app_notifications():
    """
    Retrieves or creates in-app notifications.
    """
    from utils.in_app_notification_service import in_app_notification_service

    student_id = flask_session.get("user_id") or "default_student"

    if request.method == "GET":
        is_read_param = request.args.get("is_read")
        is_read = None
        if is_read_param is not None:
            is_read = is_read_param.lower() in ("true", "1")
        category = request.args.get("category")
        limit = int(request.args.get("limit", 50))

        items = in_app_notification_service.list_notifications(
            student_id=student_id,
            is_read=is_read,
            category=category,
            limit=limit
        )
        unread = in_app_notification_service.get_unread_count(student_id)
        return jsonify({
            "success": True,
            "unread_count": unread,
            "count": len(items),
            "notifications": items
        }), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        title = payload.get("title", "New Alert")
        message = payload.get("message", "")
        category = payload.get("category", "system_alert")
        priority = payload.get("priority", "normal")
        action_url = payload.get("action_url")
        target_student = student_id

        if not message:
            return jsonify({"success": False, "error": "message is required"}), 400

        nid = in_app_notification_service.create_notification(
            student_id=target_student,
            title=title,
            message=message,
            category=category,
            priority=priority,
            action_url=action_url
        )
        return jsonify({"success": True, "notification_id": nid}), 201


@auth_bp.route("/api/notifications/<int:nid>/read", methods=["POST"])
def api_mark_notification_read(nid):
    """Marks a single notification as read."""
    from utils.in_app_notification_service import in_app_notification_service

    student_id = flask_session.get("user_id") or "default_student"
    success = in_app_notification_service.mark_as_read(student_id, nid)
    return jsonify({"success": success}), 200 if success else 404


@auth_bp.route("/api/notifications/mark-all-read", methods=["POST"])
def api_mark_all_notifications_read():
    """Marks all notifications as read for current student."""
    from utils.in_app_notification_service import in_app_notification_service

    student_id = flask_session.get("user_id") or "default_student"
    count = in_app_notification_service.mark_all_as_read(student_id)
    return jsonify({"success": True, "updated_count": count}), 200


@auth_bp.route("/api/notifications/<int:nid>", methods=["DELETE"])
def api_delete_in_app_notification(nid):
    """Deletes an in-app notification."""
    from utils.in_app_notification_service import in_app_notification_service

    student_id = flask_session.get("user_id") or "default_student"
    deleted = in_app_notification_service.delete_notification(student_id, nid)
    return jsonify({"success": deleted}), 200 if deleted else 404


@auth_bp.route("/api/notifications/delivery-history", methods=["POST"])
def api_log_delivery_attempt():
    """
    Records an outbound notification delivery attempt into the history log.
    """
    from utils.delivery_history_service import delivery_history_service

    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form

    channel = payload.get("channel", "email")
    recipient = payload.get("recipient", "student@example.com")
    template_name = payload.get("template_name", "general_alert")
    subject = payload.get("subject", "Campus Placement Alert")
    status = payload.get("status", "DELIVERED")
    error_summary = payload.get("error_summary")
    latency_ms = int(payload.get("latency_ms", 120))

    log_id = delivery_history_service.log_delivery(
        student_id=student_id,
        channel=channel,
        recipient=recipient,
        template_name=template_name,
        subject=subject,
        status=status,
        error_summary=error_summary,
        latency_ms=latency_ms
    )

    return jsonify({
        "success": True,
        "log_id": log_id,
        "message": "Delivery logged successfully."
    }), 201


@auth_bp.route("/api/user/security-activity", methods=["GET"])
def api_user_security_activity():
    """
    Returns the user's security event history (#407).
    """
    from utils.security_activity_service import get_user_security_activity
    user_id = flask_session.get("user_id") or "default_student"
    events = get_user_security_activity(user_id)
    return jsonify({"success": True, "events": events}), 200


@auth_bp.route("/security-activity", methods=["GET"])
def security_activity_page():
    """
    Renders the account security activity log page (#407).
    """
    from utils.security_activity_service import get_user_security_activity
    user_id = flask_session.get("user_id") or "default_student"
    events = get_user_security_activity(user_id)
    return render_template("security_activity.html", user_id=user_id, events=events)


@auth_bp.route("/api/user/storage-quota", methods=["GET"])
def api_user_storage_quota():
    """
    Returns the authenticated student's storage quota and usage summary (#353).
    """
    from utils.storage_quota_service import get_user_storage_summary
    student_id = flask_session.get("user_id") or "default_student"
    summary = get_user_storage_summary(student_id)
    return jsonify(summary), 200


# =====================================================================
# EMAIL ADDRESS CHANGE WITH VERIFICATION ROUTES (#405)
# =====================================================================

@auth_bp.route("/settings/email", methods=["GET", "POST"])
def request_email_change_view():
    """
    Renders and handles email address change requests for authenticated users (#405).
    Sends a verification link to the new address. The old email remains active until confirmed.
    """
    user_id = flask_session.get("user_id")
    if not user_id:
        flash("Please log in to update your account email.", "warning")
        return redirect(url_for("login", next=request.path))

    from utils.email_change_service import EmailChangeService
    from db.users import get_user_by_id

    user = get_user_by_id(user_id)
    current_email = user.get("email") if user else "Unknown"

    if request.method == "POST":
        new_email = (request.form.get("new_email") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()

        # Check password authorization if user has password
        if user and user.get("password_hash"):
            if not confirm_password or not check_password_hash(user["password_hash"], confirm_password):
                flash("Incorrect password. Please enter your current password to authorize this change.", "error")
                pending = EmailChangeService.get_pending_request(user_id)
                return render_template("change_email.html", current_email=current_email, pending_request=pending)

        success, msg, raw_token = EmailChangeService.request_email_change(user_id, new_email)
        if success:
            flash(msg, "success")
        else:
            flash(msg, "error")

        return redirect(url_for("auth.request_email_change_view"))

    pending = EmailChangeService.get_pending_request(user_id)
    return render_template("change_email.html", current_email=current_email, pending_request=pending)


@auth_bp.route("/settings/email/cancel", methods=["POST"])
def cancel_email_change_request():
    """Cancels any pending unverified email change request (#405)."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    from utils.email_change_service import EmailChangeService
    EmailChangeService.cancel_pending_request(user_id)
    flash("Pending email change request cancelled.", "info")
    return redirect(url_for("auth.request_email_change_view"))


@auth_bp.route("/verify-email-change/<token>", methods=["GET"])
def verify_email_change_token(token):
    """
    Validates email change token and updates user's email upon success (#405).
    Shows clear messages for expired or already-used tokens.
    """
    from utils.email_change_service import EmailChangeService
    result = EmailChangeService.verify_email_change(token)

    return render_template("verify_email_change.html", result=result)


@auth_bp.route("/api/user/email-change/request", methods=["POST"])
def api_request_email_change():
    """API endpoint to request an email change (#405)."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or request.form.to_dict()
    new_email = payload.get("new_email")
    confirm_password = payload.get("confirm_password")

    from db.users import get_user_by_id
    from utils.email_change_service import EmailChangeService

    user = get_user_by_id(user_id)
    if user and user.get("password_hash") and confirm_password:
        if not check_password_hash(user["password_hash"], confirm_password):
            return jsonify({"success": False, "error": "Incorrect password"}), 403

    success, msg, raw_token = EmailChangeService.request_email_change(user_id, new_email)
    if not success:
        return jsonify({"success": False, "error": msg}), 400

    return jsonify({
        "success": True,
        "message": msg,
        "verification_token": raw_token
    }), 200


@auth_bp.route("/api/user/email-change/verify", methods=["POST"])
def api_verify_email_change():
    """API endpoint to confirm and verify email change token (#405)."""
    payload = request.get_json(silent=True) or request.form.to_dict()
    token = payload.get("token")

    from utils.email_change_service import EmailChangeService
    result = EmailChangeService.verify_email_change(token)
    status_code = 200 if result["success"] else 400

    return jsonify(result), status_code


@auth_bp.route("/api/account/merge/preview", methods=["POST"])
def api_preview_account_merge():
    """Previews transferable records between two candidate accounts (#630)."""
    from utils.account_merge_service import preview_account_merge
    data = request.get_json(silent=True) or {}
    p_id = data.get("primary_user_id", "")
    s_id = data.get("secondary_user_id", "")
    preview = preview_account_merge(p_id, s_id)
    return jsonify({"status": "success", "preview": preview}), 200


@auth_bp.route("/api/account/merge/execute", methods=["POST"])
def api_execute_account_merge():
    """Executes verified account merge (#630)."""
    from utils.account_merge_service import execute_account_merge
    data = request.get_json(silent=True) or {}
    p_id = data.get("primary_user_id", "")
    s_id = data.get("secondary_user_id", "")
    token_verified = bool(data.get("verified_token", False))
    res = execute_account_merge(p_id, s_id, verified_token=token_verified)
    if not res.get("success"):
        return jsonify({"status": "error", "message": res.get("error")}), 400
    return jsonify({"status": "success", "result": res}), 200




