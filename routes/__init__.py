"""
Routes package initialization & application factory (`create_app`) (#553).
Registers feature Blueprints, attaches security hooks, context processors,
and configures backwards-compatible URL endpoint aliases.
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
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    Response,
    make_response,
    session as flask_session,
    current_app,
)
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException
from utils.csrf import csrf, CSRFError
from utils.logger_config import (
    configure_logging,
    set_current_request_id,
)
from utils.migrations import run_database_migrations, get_migration_status
from utils.database import (
    get_active_auth_session,
    get_user_by_id,
    touch_auth_session,
    create_auth_session,
)
from db.migrations import init_db as initialize_database
from utils.deadline_reminders import send_upcoming_deadline_reminders
from utils.i18n import (
    get_translation,
    SUPPORTED_LOCALES,
    DEFAULT_LOCALE,
    LOCALE_NAMES,
)
from utils.api_versioning import register_api_versioning
from utils.response_optimizer import (
    apply_cache_headers,
    compress_response,
    get_static_file_version,
)
from utils.formatters import (
    format_date_ist,
    format_datetime_ist,
    format_time_ist,
    format_inr,
    format_lpa,
    format_crore,
    format_inr_words,
    format_indian_number,
    get_display_timezone,
)
from utils.text_sanitizer import (
    render_safe_notes,
    sanitize_url,
    is_safe_url,
    break_long_words,
)
import config

from .common import (
    APPLICATION_STAGES,
    WALKTHROUGH_STEPS_CATALOG,
    get_available_companies,
    _session_client_label,
    _format_session_timestamp,
    _is_student_authorized,
    get_analysis,
)

# Import Blueprints
from .auth import auth_bp
from .analysis import analysis_bp
from .interview import interview_bp
from .coding_practice import coding_practice_bp
from .tracker import tracker_bp
from .jobs import jobs_bp
from .reports import reports_bp
from .admin import admin_bp
from .api import api_bp

logger = logging.getLogger(__name__)


def create_app(config_override=None) -> Flask:
    """
    Application factory pattern initializing the Flask app, extensions,
    and registering all feature Blueprints (#553).
    """
    root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app = Flask("app", root_path=root_path)

    configure_logging(app=app)
    app.secret_key = config.FLASK_SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    # Session Cookie Hardening & Environment Configuration (#300)
    app.config["SESSION_COOKIE_HTTPONLY"] = getattr(config, "SESSION_COOKIE_HTTPONLY", True)
    app.config["SESSION_COOKIE_SAMESITE"] = getattr(config, "SESSION_COOKIE_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_SECURE"] = getattr(config, "SESSION_COOKIE_SECURE", False)
    app.config["WTF_CSRF_ENABLED"] = getattr(config, "WTF_CSRF_ENABLED", True)
    app.config["WTF_CSRF_CHECK_DEFAULT"] = getattr(config, "WTF_CSRF_CHECK_DEFAULT", True)
    app.config["WTF_CSRF_TIME_LIMIT"] = getattr(config, "WTF_CSRF_TIME_LIMIT", 3600)
    app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken", "X-CSRF-Token"]

    app.config["SESSION_TIMEOUT_SECONDS"] = getattr(config, "SESSION_TIMEOUT_SECONDS", 1800)
    app.config["SESSION_WARNING_SECONDS"] = getattr(config, "SESSION_WARNING_SECONDS", 120)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=app.config["SESSION_TIMEOUT_SECONDS"])
    app.config["DISPLAY_TIMEZONE"] = getattr(config, "DISPLAY_TIMEZONE", "Asia/Kolkata")
    app.config["TIMEZONE"] = getattr(config, "TIMEZONE", "Asia/Kolkata")

    # Register shared Indian localization and formatting filters (#650)
    app.jinja_env.filters["format_date_ist"] = format_date_ist
    app.jinja_env.filters["format_datetime_ist"] = format_datetime_ist
    app.jinja_env.filters["format_time_ist"] = format_time_ist
    app.jinja_env.filters["format_inr"] = format_inr
    app.jinja_env.filters["format_lpa"] = format_lpa
    app.jinja_env.filters["format_crore"] = format_crore
    app.jinja_env.filters["format_inr_words"] = format_inr_words

    # Register safe long-text and URL sanitization filters
    app.jinja_env.filters["safe_notes"] = render_safe_notes
    app.jinja_env.filters["safe_text"] = render_safe_notes
    app.jinja_env.filters["safe_url"] = sanitize_url
    app.jinja_env.filters["is_safe_url"] = is_safe_url
    app.jinja_env.filters["safe_break"] = break_long_words

    app.config["ALLOWED_EMAIL_DOMAINS"] = getattr(config, "ALLOWED_EMAIL_DOMAINS", "")

    # CAPTCHA Protection Settings (#653)
    app.config["CAPTCHA_PROVIDER"] = getattr(config, "CAPTCHA_PROVIDER", "")
    app.config["CAPTCHA_SITE_KEY"] = getattr(config, "CAPTCHA_SITE_KEY", "")
    app.config["CAPTCHA_SECRET_KEY"] = getattr(config, "CAPTCHA_SECRET_KEY", "")
    app.config["CAPTCHA_FAIL_OPEN"] = getattr(config, "CAPTCHA_FAIL_OPEN", False)
    app.config["CAPTCHA_VERIFY_TIMEOUT"] = getattr(config, "CAPTCHA_VERIFY_TIMEOUT", 5.0)
    app.config["CAPTCHA_ALWAYS_ENFORCE"] = getattr(config, "CAPTCHA_ALWAYS_ENFORCE", True)
    app.config["CAPTCHA_REQUIRE_AFTER_FAILURES"] = getattr(config, "CAPTCHA_REQUIRE_AFTER_FAILURES", 0)
    app.config["INACTIVE_ACCOUNT_DAYS"] = getattr(config, "INACTIVE_ACCOUNT_DAYS", 0)

    if config_override:
        app.config.update(config_override)

    initialize_database(config.DATABASE_PATH)

    # Initialize performance monitoring & latency breakdown service (#task-performance-monitoring)
    try:
        from utils.performance_monitor import init_performance_table, init_performance_monitoring
        init_performance_table()
        init_performance_monitoring(app)
    except Exception as e:
        logger.error(f"Error initializing performance monitoring: {e}")

    # Initialize CSRF protection extension (#300)
    csrf.init_app(app)

    # Attach app-level hooks and error handlers
    @app.before_request
    def require_login():
        endpoint_name = (request.endpoint or "").split(".")[-1]
        now = time.time()
        timeout_seconds = current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800))

        if current_app.config.get("TESTING") and not current_app.config.get("ENFORCE_LOGIN_IN_TESTS") and not flask_session.get("auth_session_id"):
            aid = None
            if request.view_args:
                aid = request.view_args.get("analysis_id") or request.view_args.get("id_a") or request.view_args.get("id")
            if aid:
                rec = get_analysis(aid)
                if rec and rec.get("student_id"):
                    flask_session["user_id"] = rec["student_id"]
                    flask_session["user_name"] = rec["student_id"]
                    if "last_activity" not in flask_session:
                        flask_session["last_activity"] = now
                    return None
            if request.view_args and "student_id" in request.view_args:
                flask_session["user_id"] = request.view_args["student_id"]
                flask_session["user_name"] = request.view_args["student_id"]
                if "last_activity" not in flask_session:
                    flask_session["last_activity"] = now
                return None
            student_param = request.args.get("student_id")
            if student_param and (
                "user_id" not in flask_session
                or flask_session.get("user_id") in (None, "", "default_student")
            ):
                flask_session["user_id"] = student_param
                flask_session["user_name"] = student_param
                if "last_activity" not in flask_session:
                    flask_session["last_activity"] = now
                return None
            if "user_id" not in flask_session:
                flask_session["user_id"] = "default_student"
                flask_session["user_name"] = "Default Student"
            if "last_activity" not in flask_session:
                flask_session["last_activity"] = now
            return None

        public_endpoints = {
            "index",
            "login",
            "register",
            "verify_email",
            "resend_verification",
            "forgot_password",
            "reset_password",
            "privacy_policy",
            "healthz",
            "health",
            "api_health",
            "static",
            "system_status_page",
            "status",
            "api_system_status",
            "gemini_api_usage_dashboard",
            "api_gemini_usage_summary",
            "api_session_status",
            "api_session_keep_alive",
            "api_session_logout",
            "logout",
            "api_walkthrough",
            "api_walkthrough_dismiss",
            "api_walkthrough_reset",
            "admin_moderation_queue_endpoint",
            "admin_moderate_experience_action_endpoint",
            "admin_experience_moderation_logs_endpoint",
            "demo_mode_view",
            "demo_mode_exit",
            "api_demo_sample_resume",
            "api_demo_sample_job_description",
            "download_company_cheat_sheet",
            "aptitude_view",
            "api_aptitude_start",
            "api_aptitude_submit",
            "api_aptitude_history",
            "api_aptitude_weak_topics",
            "verify_two_factor",
            "view_shared_report",
            "resume_templates_gallery",
            "resume_templates_gallery_by_uuid",
            "api_resume_preview",
            "resume_template_download",
            "resume_template_download_by_uuid",
            "session_resume_compare_view",
            "session_resume_compare_reset",
            "api_session_resume_compare",
            "api_session_resume_compare_reset",
            "api_get_role_templates",
            "api_get_role_template_by_id",
            "api_get_retry_records",
            "api_create_retry_record",
            "api_get_retry_record_item",
            "api_replay_retry_record",
            "api_cancel_retry_record",
        }

        user_id = flask_session.get("user_id")

        # Validate the server-side registry before allowing a signed cookie to access the account.
        if user_id and endpoint_name not in ("logout", "api_session_logout"):
            # Check if user account is deactivated (#853)
            user_record = get_user_by_id(user_id)
            if user_record and user_record.get("is_active") is not None and not bool(user_record.get("is_active", 1)):
                logger.info("Rejected request for deactivated user_id=%s", user_id)
                flask_session.clear()
                is_json_request = (
                    request.path.startswith("/api/")
                    or request.is_json
                    or "application/json" in request.headers.get("Accept", "")
                    or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                )
                if is_json_request:
                    resp = jsonify({
                        "status": "error",
                        "error": "account_deactivated",
                        "message": "Your account has been deactivated. Please contact an administrator."
                    })
                    resp.status_code = 403
                    resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
                    return resp
                flash("Your account has been deactivated. Please contact an administrator for assistance.", "error")
                resp = make_response(redirect(url_for("login")))
                resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
                return resp

            auth_session_id = flask_session.get("auth_session_id")
            auth_session_token = flask_session.get("auth_session_token")
            if auth_session_id and auth_session_token:
                active_auth_session = get_active_auth_session(auth_session_id, auth_session_token, user_id)
                if not active_auth_session:
                    logger.info("Rejected revoked or invalid authenticated session for user_id=%s", user_id)
                    flask_session.clear()
                    is_json_request = (
                        request.path.startswith("/api/")
                        or request.is_json
                        or "application/json" in request.headers.get("Accept", "")
                        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                    )
                    if is_json_request:
                        resp = jsonify({
                            "status": "error",
                            "error": "session_revoked",
                            "message": "This session is no longer active. Please sign in again."
                        })
                        resp.status_code = 401
                        resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
                        return resp
                    flash("This session is no longer active. Please sign in again.", "warning")
                    resp = make_response(redirect(url_for("login")))
                    resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
                    return resp
                if endpoint_name not in ("api_session_status", "static"):
                    touch_auth_session(auth_session_id, user_id)
            elif not current_app.config.get("TESTING") or current_app.config.get("ENFORCE_LOGIN_IN_TESTS"):
                new_auth_session = create_auth_session(user_id, _session_client_label())
                flask_session["auth_session_id"] = new_auth_session["id"]
                flask_session["auth_session_token"] = new_auth_session["token"]

        # Inactivity check for authenticated sessions
        if user_id:
            last_activity = flask_session.get("last_activity")
            if last_activity is None:
                # Gracefully initialize for existing active session
                flask_session["last_activity"] = now
                last_activity = now

            try:
                last_act_float = float(last_activity)
            except (ValueError, TypeError):
                last_act_float = now
                flask_session["last_activity"] = now

            if (now - last_act_float) > timeout_seconds:
                logger.info(
                    "Session expired due to inactivity for user_id=%s (inactive %.1fs > %ss)",
                    user_id,
                    now - last_act_float,
                    timeout_seconds,
                )
                flask_session.clear()

                is_json_request = (
                    request.path.startswith("/api/")
                    or request.is_json
                    or "application/json" in request.headers.get("Accept", "")
                    or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                )

                if is_json_request:
                    resp = jsonify({
                        "status": "error",
                        "error": "session_expired",
                        "message": "Your session has expired due to inactivity. Please log in again."
                    })
                    resp.status_code = 401
                    resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
                    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    resp.headers["Pragma"] = "no-cache"
                    return resp

                if request.endpoint not in public_endpoints and endpoint_name not in public_endpoints:
                    flash("Your session has expired due to inactivity. Please log in again.", "warning")
                    next_url = request.full_path if request.method == "GET" and not request.path.startswith("/static") else None
                    login_url = url_for("login", next=next_url) if next_url and next_url != "/?" else url_for("login")
                    resp = make_response(redirect(login_url))
                    resp.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
                    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    resp.headers["Pragma"] = "no-cache"
                    return resp
            else:
                # Active session: update activity timestamp for non-polling requests
                if endpoint_name not in ("api_session_status", "static"):
                    flask_session["last_activity"] = now

        if (
            (request.endpoint in public_endpoints or endpoint_name in public_endpoints)
            or (request.view_args and request.view_args.get("analysis_id") in ("demo-sample-analysis", "demo_sample_analysis", "demo"))
            or flask_session.get("is_demo_mode")
            or flask_session.get("user_id") == "demo_visitor"
        ):
            return None

        if not flask_session.get("user_id"):
            if current_app.config.get("TESTING") and not current_app.config.get("ENFORCE_LOGIN_IN_TESTS"):
                flask_session["user_id"] = "default_student"
                flask_session["last_activity"] = now
                return None

            is_json_request = (
                request.path.startswith("/api/")
                or request.is_json
                or "application/json" in request.headers.get("Accept", "")
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            )
            if is_json_request:
                resp = jsonify({
                    "status": "error",
                    "error": "unauthorized",
                    "message": "Authentication required."
                })
                resp.status_code = 401
                return resp

            next_url = request.full_path if request.method == "GET" and not request.path.startswith("/static") else None
            login_url = url_for("login", next=next_url) if next_url and next_url != "/?" else url_for("login")
            return redirect(login_url)


    @app.context_processor
    def inject_i18n():
        """
        Injects internationalization helpers and active locale into all Jinja templates (#214).
        """
        current_locale = flask_session.get("locale") or DEFAULT_LOCALE
        return {
            "current_locale": current_locale,
            "supported_locales": SUPPORTED_LOCALES,
            "locale_names": LOCALE_NAMES,
            "_t": lambda key, default=None, **kwargs: get_translation(key, locale=current_locale, default=default, **kwargs)
        }


    @app.context_processor
    def inject_rate_limit():
        """
        Injects rate limit inspection helper into Jinja templates (#652).
        """
        from utils.rate_limiter import get_rate_limit_status
        return {
            "rate_limit_status": get_rate_limit_status,
        }


    @app.context_processor
    def inject_session_config():
        """
        Injects session expiration and timeout settings into all Jinja templates.
        """
        return {
            "session_timeout_seconds": current_app.config.get("SESSION_TIMEOUT_SECONDS", getattr(config, "SESSION_TIMEOUT_SECONDS", 1800)),
            "session_warning_seconds": current_app.config.get("SESSION_WARNING_SECONDS", getattr(config, "SESSION_WARNING_SECONDS", 120)),
            "is_authenticated": bool(flask_session.get("user_id")),
        }


    @app.context_processor
    def inject_captcha_config():
        """
        Injects CAPTCHA configuration flags and evaluation helper into all templates (#653).
        """
        from utils.captcha import (
            is_captcha_enabled,
            get_captcha_provider,
            get_captcha_site_key,
            should_require_captcha,
        )
        return {
            "captcha_enabled": is_captcha_enabled(),
            "captcha_provider": get_captcha_provider(),
            "captcha_site_key": get_captcha_site_key(),
            "should_require_captcha": lambda form_type="login", identifier=None: should_require_captcha(
                ip=request.remote_addr if request else None,
                identifier=identifier,
                form_type=form_type,
            ),
        }

    @app.context_processor
    def inject_feature_flags():
        """
        Injects feature flag evaluation helpers into all Jinja templates (#854).
        Enables `is_enabled(flag_key, user)` in templates.
        """
        from utils.feature_flags import is_feature_enabled
        def _check_flag(flag: str, user=None, default=None):
            # If user is not provided, automatically fallback to current session user
            if user is None:
                user = flask_session.get("user_id")
            return is_feature_enabled(flag, user=user, default=default)

        return {
            "is_enabled": _check_flag,
            "is_feature_enabled": _check_flag,
        }

    @app.url_defaults
    def add_static_version(endpoint, values):
        """Append deterministic content hash query string (?v=...) to static asset URLs (#857)."""
        if (endpoint == "static" or endpoint.endswith(".static")) and "filename" in values:
            if "v" not in values:
                version = get_static_file_version(values["filename"], app.static_folder)
                if version:
                    values["v"] = version


    @app.before_request
    def assign_request_id():
        """Assign or extract an X-Request-ID for distributed tracing and structured correlation (#301)."""
        from flask import g
        req_id = request.headers.get("X-Request-ID")
        if not req_id:
            req_id = str(uuid.uuid4())
        g.request_id = req_id
        set_current_request_id(req_id)


    @app.before_request
    def check_request_size_limits():
        """
        Enforces size limits on JSON request bodies, text parameters, and uploaded files (#784).
        Returns field-specific error details, max allowed size, and logs with request ID.
        """
        # Validate size on API endpoints or endpoints receiving JSON/payload
        if request.path.startswith("/api/") or request.is_json:
            from utils.request_size_diagnostics import validate_request_size, build_size_error_response
            error_info = validate_request_size(request)
            if error_info:
                resp, code = build_size_error_response(error_info, request, status_code=413)
                return resp, code


    @app.after_request
    def inject_correlation_headers(response):
        """Echo the X-Request-ID correlation header in every response for client observability."""
        from flask import g
        req_id = getattr(g, "request_id", None)
        if req_id:
            response.headers["X-Request-ID"] = req_id
        return response


    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        """
        Renders a clear user-friendly error page or JSON response when CSRF token
        validation fails (#300).
        """
        reason = getattr(error, "description", "CSRF token missing or invalid.")
        logger.warning(f"CSRF protection validation failed for {request.method} {request.path}: {reason}")

        is_json = (
            request.is_json
            or request.path.startswith("/api/")
            or request.headers.get("Accept") == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if is_json:
            return jsonify({
                "success": False,
                "error": "CSRF validation failed",
                "message": "Your security token was missing or has expired. Please refresh the page and try again.",
                "details": str(reason),
            }), 400

        return render_template("csrf_error.html", error_message=reason), 400


    @app.errorhandler(413)
    @app.errorhandler(RequestEntityTooLarge)
    def request_entity_too_large(error):
        """
        Handles HTTP 413 Payload Too Large / RequestEntityTooLarge errors (#153, #784).
        Extracts field-specific details (file field, JSON body, etc.), includes the
        maximum allowed size, and logs the failure with the correlation Request ID.
        """
        from utils.request_size_diagnostics import (
            diagnose_413_exception,
            build_size_error_response,
            log_size_failure,
        )

        error_info = diagnose_413_exception(error, request)
        req_id = log_size_failure(error_info, request)

        is_api = (
            request.path.startswith("/api/")
            or request.is_json
            or request.args.get("async") == "true"
            or request.headers.get("Accept") == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )

        if is_api:
            resp, code = build_size_error_response(error_info, request, status_code=413)
            return resp, code

        msg = error_info["message"]
        flash(msg, "error")
        return redirect(url_for("index"))


    @app.errorhandler(404)
    def not_found_error(error):
        """
        Handles HTTP 404 Not Found errors (#362).
        Returns JSON for API/AJAX requests and a styled, accessible 404 page with safe navigation
        and request correlation details for browser UI requests.
        """
        from flask import g
        req_id = getattr(g, "request_id", None)
        is_json = (
            request.is_json
            or request.path.startswith("/api/")
            or request.headers.get("Accept") == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if is_json:
            return jsonify({
                "success": False,
                "error": "Not Found",
                "message": "The requested resource could not be found.",
                "path": request.path,
                "request_id": req_id,
            }), 404

        return render_template(
            "404.html",
            path=request.path,
            request_id=req_id,
        ), 404


    @app.errorhandler(403)
    def forbidden_error(error):
        """
        Handles HTTP 403 Forbidden errors (#362).
        Returns JSON for API/AJAX requests and a styled, accessible 403 page with authentication
        guidance and safe navigation for browser UI requests.
        """
        from flask import g
        req_id = getattr(g, "request_id", None)
        description = getattr(error, "description", None) or "You do not have permission to access this page or resource."
        is_json = (
            request.is_json
            or request.path.startswith("/api/")
            or request.headers.get("Accept") == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if is_json:
            return jsonify({
                "success": False,
                "error": "Forbidden",
                "message": description,
                "path": request.path,
                "request_id": req_id,
            }), 403

        return render_template(
            "403.html",
            message=description,
            path=request.path,
            request_id=req_id,
        ), 403


    @app.errorhandler(429)
    def rate_limit_exceeded(error):
        """
        Handles HTTP 429 Too Many Requests errors (#160, #362, #652).
        Returns JSON for API/AJAX requests and a styled 429 page for browser UI requests.
        """
        from flask import g
        req_id = getattr(g, "request_id", None)
        description = getattr(error, "description", None) or "You have temporarily exceeded the request rate limit for this action."
        retry_hint = getattr(error, "retry_hint", "try again in a few minutes")
        retry_after = getattr(error, "retry_after", 60)
        try:
            retry_after_int = int(retry_after)
        except (ValueError, TypeError):
            retry_after_int = 60

        is_json = (
            request.is_json
            or request.path.startswith("/api/")
            or request.headers.get("Accept") == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if is_json:
            response = jsonify({
                "success": False,
                "error": "Too Many Requests",
                "message": description,
                "retry_after": retry_after_int,
                "retry_after_seconds": retry_after_int,
                "retry_after_hint": retry_hint,
                "request_id": req_id,
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after_int)
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(retry_after_int)
            return response

        rendered = render_template(
            "429.html",
            message=description,
            retry_hint=retry_hint,
            retry_after=retry_after_int,
            retry_after_seconds=retry_after_int,
            request_id=req_id,
        )
        resp = make_response(rendered, 429)
        resp.headers["Retry-After"] = str(retry_after_int)
        resp.headers["X-RateLimit-Remaining"] = "0"
        resp.headers["X-RateLimit-Reset"] = str(retry_after_int)
        return resp


    @app.errorhandler(500)
    def internal_server_error(error):
        """
        Handles HTTP 500 errors safely (#263, #362), generating a unique human-shareable
        Error Reference ID (ERR-YYYYMMDD-XXXXXX) for support correlation while ensuring
        internal secrets, API keys, and stack traces are never exposed to clients or user views.
        """
        from utils.prompt_security import redact_sensitive_information
        from utils.error_reference_service import record_application_error
        from flask import g

        req_id = getattr(g, "request_id", None)
        user_id = flask_session.get("user_id")

        # Record error and obtain reference details
        err_info = record_application_error(
            error=error if isinstance(error, Exception) else Exception(str(error)),
            endpoint=request.path,
            http_method=request.method,
            status_code=500,
            user_id=user_id,
            request_id=req_id,
        )

        ref_id = err_info["reference_id"]

        is_json = (
            request.is_json
            or request.path.startswith("/api/")
            or request.headers.get("Accept") == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if is_json:
            return jsonify({
                "success": False,
                "error": "Internal Server Error",
                "message": "An internal error occurred. Please quote the reference ID when contacting support.",
                "reference_id": ref_id,
                "request_id": req_id,
            }), 500

        return render_template(
            "500.html",
            reference_id=ref_id,
            request_id=req_id,
            path=request.path,
        ), 500


    @app.after_request
    def set_security_headers(response):
        """
        Applies security headers to every HTTP response:
        - Content-Security-Policy (CSP) restricting script, style, and connect sources
        - X-Content-Type-Options: nosniff
        - X-Frame-Options: SAMEORIGIN
        - Referrer-Policy: strict-origin-when-cross-origin
        - Permissions-Policy: camera=(), microphone=(), geolocation=()
        """
        from utils.captcha import is_captcha_enabled, get_captcha_provider

        script_sources = ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://va.vercel-scripts.com"]
        style_sources = ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://fonts.googleapis.com"]
        connect_sources = ["'self'", "https://va.vercel-scripts.com", "https://vitals.vercel-insights.com"]
        frame_sources = ["'self'"]

        if is_captcha_enabled():
            provider = get_captcha_provider()
            if provider == "turnstile":
                script_sources.append("https://challenges.cloudflare.com")
                frame_sources.append("https://challenges.cloudflare.com")
                connect_sources.append("https://challenges.cloudflare.com")
            elif provider == "hcaptcha":
                script_sources.extend(["https://js.hcaptcha.com", "https://newassets.hcaptcha.com"])
                frame_sources.extend(["https://newassets.hcaptcha.com", "https://*.hcaptcha.com"])
                connect_sources.extend(["https://api.hcaptcha.com", "https://newassets.hcaptcha.com"])
                style_sources.append("https://newassets.hcaptcha.com")

        csp_directives = [
            "default-src 'self'",
            f"script-src {' '.join(script_sources)}",
            f"style-src {' '.join(style_sources)}",
            "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:",
            "img-src 'self' data: https:",
            f"connect-src {' '.join(connect_sources)}",
            f"frame-src {' '.join(frame_sources)}",
            "frame-ancestors 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"

        # Apply caching directives and personal data protection (#857)
        is_authenticated = bool(flask_session.get("user_id"))
        max_age_ver = current_app.config.get(
            "STATIC_CACHE_MAX_AGE_VERSIONED",
            getattr(config, "STATIC_CACHE_MAX_AGE_VERSIONED", 31536000),
        )
        max_age_def = current_app.config.get(
            "STATIC_CACHE_MAX_AGE_DEFAULT",
            getattr(config, "STATIC_CACHE_MAX_AGE_DEFAULT", 86400),
        )
        response = apply_cache_headers(
            response,
            request,
            is_authenticated=is_authenticated,
            max_age_versioned=max_age_ver,
            max_age_default=max_age_def,
        )

        # Apply gzip response compression (#857)
        comp_enabled = current_app.config.get(
            "RESPONSE_COMPRESSION_ENABLED",
            getattr(config, "RESPONSE_COMPRESSION_ENABLED", True),
        )
        comp_min_size = current_app.config.get(
            "RESPONSE_COMPRESSION_MIN_SIZE",
            getattr(config, "RESPONSE_COMPRESSION_MIN_SIZE", 500),
        )
        response = compress_response(
            response,
            request,
            min_size=comp_min_size,
            enabled=comp_enabled,
        )

        return response


    import click

    @app.cli.command("send-deadline-reminders")
    def send_deadline_reminders_cli():
        """
        CLI scheduled command to find items due in the next 2 days and dispatch reminder emails (#298).
        Designed for crontab, systemd timers, or APScheduler invocations.
        """
        logger.info("Executing scheduled CLI job: send-deadline-reminders...")
        result = send_upcoming_deadline_reminders()
        click.echo(
            f"Deadline reminders complete: sent={result['sent_count']}, "
            f"skipped={result['skipped_count']}, failed={result['failed_count']}"
        )

    @app.cli.command("process-inactive-accounts")
    @click.option(
        "--days",
        type=int,
        default=None,
        help="Retention period in days (overrides INACTIVE_ACCOUNT_DAYS).",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        default=False,
        help="Simulate inactivity check without sending emails or deleting data.",
    )
    def process_inactive_accounts_cli(days, dry_run):
        """
        Scheduled CLI command to identify inactive accounts, send 30d/7d warnings,
        and delete expired accounts and associated personal data (#888).
        """
        logger.info("Executing scheduled CLI job: process-inactive-accounts...")
        from utils.account_retention_service import process_inactive_accounts

        result = process_inactive_accounts(retention_days=days, dry_run=dry_run)
        click.echo(
            f"Inactive account processing complete: status={result.get('status')}, "
            f"evaluated={result.get('evaluated_users', 0)}, "
            f"warnings_30d={result.get('warnings_30d_sent', 0)}, "
            f"warnings_7d={result.get('warnings_7d_sent', 0)}, "
            f"deleted={result.get('deleted_count', 0)}"
        )

    @app.cli.command("validate-company-data")
    @click.argument("paths", nargs=-1, type=click.Path())
    @click.option("--live", "--check-live-links", "check_live_links", is_flag=True, default=False, help="Verify external URLs via live HTTP requests.")
    @click.option("--strict", is_flag=True, default=False, help="Treat warnings as errors.")
    @click.option("--json", "json_output", is_flag=True, default=False, help="Output validation findings as JSON.")
    @click.option("--quiet", is_flag=True, default=False, help="Suppress output on successful validation.")
    def validate_company_data_cli(paths, check_live_links, strict, json_output, quiet):
        """
        Validate contributor company JSON files for schema, required fields, duplicates, and broken links (#785).
        """
        from utils.company_data_validator import validate_paths, format_terminal_report
        import json as json_module

        results = validate_paths(
            paths=list(paths) if paths else None,
            check_live_links=check_live_links,
            strict=strict,
        )

        if json_output:
            payload = {
                "all_valid": all(r.is_valid for r in results),
                "total_files": len(results),
                "total_companies": sum(r.company_count for r in results),
                "total_errors": sum(len(r.errors) for r in results),
                "total_warnings": sum(len(r.warnings) for r in results),
                "files": [r.to_dict() for r in results],
            }
            click.echo(json_module.dumps(payload, indent=2))
        else:
            report_text = format_terminal_report(results, quiet=quiet)
            click.echo(report_text)

        is_success = all(r.is_valid for r in results)
        if strict and any(len(r.warnings) > 0 for r in results):
            is_success = False

        if not is_success:
            raise SystemExit(1)

    # Register all feature Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(interview_bp)
    app.register_blueprint(coding_practice_bp)
    app.register_blueprint(tracker_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    # Dynamic URL build error handler for backwards-compatibility with bare endpoint names (#553)
    def url_build_error_handler(error, endpoint, values):
        if endpoint == "preparation_notes_view":
            return url_for("interview.interview_notes_view", **values)
        for rule in current_app.url_map.iter_rules():
            if rule.endpoint.endswith("." + endpoint):
                return url_for(rule.endpoint, **values)
        raise error

    app.url_build_error_handlers.append(url_build_error_handler)

    # Register bare endpoint aliases on app.view_functions and url_map
    for rule in list(app.url_map.iter_rules()):
        if "." in rule.endpoint:
            bare_endpoint = rule.endpoint.split(".", 1)[1]
            if bare_endpoint not in app.view_functions:
                app.view_functions[bare_endpoint] = app.view_functions[rule.endpoint]
            try:
                app.add_url_rule(
                    rule.rule,
                    endpoint=bare_endpoint,
                    view_func=app.view_functions[rule.endpoint],
                    methods=rule.methods,
                    strict_slashes=rule.strict_slashes,
                    defaults=rule.defaults,
                )
            except AssertionError:
                pass

    if "interview.interview_notes_view" in app.view_functions:
        app.view_functions["preparation_notes_view"] = app.view_functions["interview.interview_notes_view"]
        app.view_functions["interview_notes_view"] = app.view_functions["interview.interview_notes_view"]

    # Initialize API versioning (/api/v1/...) with legacy alias support & RFC 8594 headers (#445)
    register_api_versioning(app)

    return app

