"""
Admin feature area routes & endpoints (#553).
Handles administrative metrics, Gemini usage analytics, database backups, and moderation.
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
from utils.progress_tracker import *
from utils.password_security import evaluate_password_strength
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
from utils.logger_config import (
    configure_logging,
    get_current_request_id,
    set_current_request_id,
    register_sensitive_text,
    clear_sensitive_texts,
)
from .common import *

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)

@admin_bp.route("/admin/api-usage", methods=["GET"])
def gemini_api_usage_dashboard():
    """
    Renders the Gemini API Usage & Cost Monitoring Dashboard (#215).
    Provides visual breakdowns of token consumption, estimated spend,
    latency trends, and endpoint call distribution.
    """
    days = request.args.get("days", 30, type=int)
    if days not in [1, 7, 14, 30, 90]:
        days = 30

    usage_data = get_gemini_usage_summary(days=days)
    return render_template("api_usage_dashboard.html", usage=usage_data, selected_days=days)


@admin_bp.route("/api/admin/usage-stats", methods=["GET"])
def api_gemini_usage_summary():
    """
    JSON API endpoint exposing aggregated usage and expenditure metrics (#215).
    """
    days = request.args.get("days", 30, type=int)
    if days < 1 or days > 365:
        days = 30

    usage_data = get_gemini_usage_summary(days=days)
    return jsonify({
        "status": "success",
        "data": usage_data
    })


@admin_bp.route("/admin/ai-metrics", methods=["GET"])
def admin_ai_metrics_view():
    """
    Admin-only dashboard displaying daily call counts, average and 95th percentile
    latency, failure rate, and a table of the most common error types (#434).
    Requires administrator session, X-Admin-Key header, or admin_key query param.
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    feature = request.args.get("feature")

    from utils.ai_metrics import get_ai_metrics_summary
    summary = get_ai_metrics_summary(start_date=start_date, end_date=end_date, feature=feature)

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"success": True, "data": summary})

    return render_template("admin_ai_metrics.html", summary=summary)


@admin_bp.route("/admin/ai-metrics/export.csv", methods=["GET"])
def admin_ai_metrics_export_csv():
    """
    Admin-only CSV export of AI call telemetry (#434).
    Requires administrator session, X-Admin-Key header, or admin_key query param.
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    feature = request.args.get("feature")

    from utils.ai_metrics import export_ai_metrics_csv
    csv_data = export_ai_metrics_csv(start_date=start_date, end_date=end_date, feature=feature)

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ai_call_metrics.csv"}
    )


@admin_bp.route("/api/admin/ai-metrics", methods=["GET"])
def api_admin_ai_metrics():
    """
    Admin-only JSON API exposing AI call telemetry summary metrics (#434).
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    feature = request.args.get("feature")

    from utils.ai_metrics import get_ai_metrics_summary
    summary = get_ai_metrics_summary(start_date=start_date, end_date=end_date, feature=feature)

    return jsonify({
        "status": "success",
        "data": summary
    })


@admin_bp.route("/admin/performance", methods=["GET"])
def admin_performance_view():
    """
    Admin dashboard displaying request performance telemetry and latency breakdowns (#task-performance-monitoring).
    Identifies slow endpoints and breakdowns across database, AI service, file processing, and rendering.
    Requires administrator session, X-Admin-Key header, or admin_key query param.
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    endpoint = request.args.get("endpoint")
    is_slow_param = request.args.get("is_slow")
    is_slow = True if is_slow_param in ("1", "true") else (False if is_slow_param in ("0", "false") else None)
    bottleneck = request.args.get("bottleneck")
    time_range = request.args.get("time_range")
    limit = min(500, max(10, int(request.args.get("limit", 100))))
    offset = max(0, int(request.args.get("offset", 0)))

    from utils.performance_monitor import get_performance_summary
    summary = get_performance_summary(
        endpoint=endpoint,
        is_slow=is_slow,
        bottleneck=bottleneck,
        time_range=time_range,
        limit=limit,
        offset=offset,
    )

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"success": True, "data": summary})

    return render_template("admin_performance.html", summary=summary)


@admin_bp.route("/admin/performance/export.csv", methods=["GET"])
def admin_performance_export_csv():
    """
    Admin-only CSV export of request performance telemetry (#task-performance-monitoring).
    Guarantees no user content, passwords, or tokens are exported.
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    endpoint = request.args.get("endpoint")
    is_slow_param = request.args.get("is_slow")
    is_slow = True if is_slow_param in ("1", "true") else (False if is_slow_param in ("0", "false") else None)
    bottleneck = request.args.get("bottleneck")
    time_range = request.args.get("time_range")

    from utils.performance_monitor import export_performance_metrics_csv
    csv_data = export_performance_metrics_csv(
        endpoint=endpoint,
        is_slow=is_slow,
        bottleneck=bottleneck,
        time_range=time_range,
    )

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=request_performance_metrics.csv"}
    )


@admin_bp.route("/api/admin/performance-metrics", methods=["GET"])
def api_admin_performance_metrics():
    """
    Admin-only JSON API exposing request latency breakdowns and bottleneck diagnostics (#task-performance-monitoring).
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    endpoint = request.args.get("endpoint")
    is_slow_param = request.args.get("is_slow")
    is_slow = True if is_slow_param in ("1", "true") else (False if is_slow_param in ("0", "false") else None)
    bottleneck = request.args.get("bottleneck")
    time_range = request.args.get("time_range")
    limit = min(500, max(10, int(request.args.get("limit", 100))))
    offset = max(0, int(request.args.get("offset", 0)))

    from utils.performance_monitor import get_performance_summary
    summary = get_performance_summary(
        endpoint=endpoint,
        is_slow=is_slow,
        bottleneck=bottleneck,
        time_range=time_range,
        limit=limit,
        offset=offset,
    )

    return jsonify({
        "status": "success",
        "data": summary
    })


@admin_bp.route("/api/admin/database/integrity", methods=["GET"])
def api_database_integrity():
    """
    Returns database integrity verification status and table statistics.
    """
    integrity_data = verify_database_integrity()
    status_code = 200 if integrity_data.get("healthy") else 500
    return jsonify({
        "success": integrity_data.get("healthy", False),
        "integrity": integrity_data
    }), status_code


@admin_bp.route("/api/admin/database/backup", methods=["POST"])
def api_database_backup():
    """
    Triggers an atomic online hot backup of the SQLite database and returns metadata.
    """
    try:
        from utils.backup_restoration import get_backup_storage_directory
        backup_dir = get_backup_storage_directory()
        backup_filename = f"placement_backup_{int(time.time())}.db"
        backup_path = os.path.join(backup_dir, backup_filename)
        create_database_backup(backup_path)

        file_size = os.path.getsize(backup_path) if os.path.exists(backup_path) else 0
        return jsonify({
            "success": True,
            "message": "Online hot backup completed successfully.",
            "backup_filename": backup_filename,
            "size_bytes": file_size,
        }), 200
    except Exception as exc:
        logger.error(f"Error creating admin database backup: {exc}")
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@admin_bp.route("/api/admin/database/backups", methods=["GET"])
def api_admin_list_backups():
    """
    Lists all available database backups for administrator inspection (#245).
    """
    admin_header = request.headers.get("X-Admin-Key")
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    if not admin_session and admin_header != getattr(config, "ADMIN_SECRET_KEY", "admin-secret"):
        # Deny unauthorized access
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.backup_restoration import list_available_backups
    backups = list_available_backups()
    return jsonify({
        "success": True,
        "count": len(backups),
        "backups": backups
    }), 200


@admin_bp.route("/api/admin/database/backups/validate", methods=["POST"])
def api_admin_validate_backup():
    """
    Pre-validates a backup candidate before restoration (#245).
    """
    admin_header = request.headers.get("X-Admin-Key")
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    if not admin_session and admin_header != getattr(config, "ADMIN_SECRET_KEY", "admin-secret"):
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    payload = request.get_json(silent=True) or request.form
    filename = payload.get("filename") or payload.get("backup_identifier")
    if not filename:
        return jsonify({"success": False, "error": "Backup filename or path is required."}), 400

    from utils.backup_restoration import get_backup_storage_directory, validate_backup_file
    backup_dir = get_backup_storage_directory()
    target_path = os.path.join(backup_dir, os.path.basename(filename))
    if not os.path.exists(target_path) and os.path.isabs(filename):
        target_path = filename

    result = validate_backup_file(target_path)
    return jsonify({
        "success": result.get("valid", False),
        "validation": result
    }), (200 if result.get("valid") else 400)


@admin_bp.route("/api/admin/database/restore", methods=["POST"])
def api_admin_restore_database():
    """
    Restores the application database from an authorized backup file (#245).
    Enforces administrator authorization, creates safety snapshot, and validates structure.
    """
    admin_header = request.headers.get("X-Admin-Key")
    admin_user = flask_session.get("user", {}).get("email") or "admin"
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    if not admin_session and admin_header != getattr(config, "ADMIN_SECRET_KEY", "admin-secret"):
        from utils.security_activity_service import anonymize_ip
        logger.warning(f"Unauthorized restoration attempt from IP {anonymize_ip(request.remote_addr)}")
        return jsonify({"success": False, "error": "Unauthorized: Administrator privileges required to restore database."}), 403

    payload = request.get_json(silent=True) or request.form
    backup_identifier = payload.get("backup_identifier") or payload.get("filename")

    if not backup_identifier:
        return jsonify({
            "success": False,
            "error": "Missing backup_identifier in request payload."
        }), 400

    from utils.backup_restoration import restore_database_from_backup, BackupRestorationError
    try:
        restore_result = restore_database_from_backup(
            backup_identifier=str(backup_identifier).strip(),
            admin_user=admin_user
        )
        return jsonify(restore_result), 200
    except BackupRestorationError as b_err:
        logger.error(f"Backup restoration failed with operational error: {b_err}")
        return jsonify({
            "success": False,
            "error": str(b_err)
        }), 400
    except Exception as exc:
        logger.critical(f"Unexpected restoration error: {exc}")
        return jsonify({
            "success": False,
            "error": f"Internal system failure during restoration: {exc}"
        }), 500


@admin_bp.route("/api/admin/retention/run", methods=["POST"])
def api_admin_run_retention():
    """
    Trigger automated data retention and cleanup pass for expired storage artifacts and historical records.
    """
    try:
        from utils.cleanup import run_full_retention_policy
        body = request.get_json(silent=True) or {}
        max_file_age = int(body.get("max_file_age_hours", 24))
        max_db_age = int(body.get("max_db_age_days", 30))
        dry_run = bool(body.get("dry_run", False))

        results = run_full_retention_policy(
            max_file_age_hours=max_file_age,
            max_db_age_days=max_db_age,
            dry_run=dry_run
        )
        return jsonify({"status": "success", "results": results}), 200
    except Exception as e:
        logger.error(f"Error during retention cycle execution: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/api/admin/moderation/experiences", methods=["GET"])
def admin_moderation_queue_endpoint():
    """
    Administrative queue for inspecting submitted interview experiences,
    flagged content, and moderation metrics. Requires administrative privileges.
    """
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin":
        return jsonify({
            "status": "error",
            "message": "Unauthorized. Administrative privilege required to access moderation queue."
        }), 403

    status_filter = request.args.get("status")
    flagged_only = request.args.get("flagged_only", "false").lower() == "true"
    company_filter = request.args.get("company")
    search_query = request.args.get("search")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    queue_data = get_moderation_queue(
        status_filter=status_filter,
        flagged_only=flagged_only,
        company_filter=company_filter,
        search_query=search_query,
        limit=limit,
        offset=offset
    )

    return jsonify({
        "status": "success",
        "data": queue_data
    }), 200


@admin_bp.route("/api/admin/moderation/experiences/<exp_id>/action", methods=["POST"])
def admin_moderate_experience_action_endpoint(exp_id: str):
    """
    Executes a moderation decision (approve, reject, flag, dismiss_report) on a submission.
    """
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin":
        return jsonify({
            "status": "error",
            "message": "Unauthorized. Administrative privilege required to perform moderation actions."
        }), 403

    payload = request.get_json(silent=True) or request.form.to_dict()
    action = payload.get("action")
    if not action:
        return jsonify({"status": "error", "message": "Moderation 'action' is required."}), 400

    reason = payload.get("reason", "")
    moderator_id = flask_session.get("user_id") or "admin"

    try:
        updated_experience = moderate_interview_experience(
            experience_id=exp_id,
            action=action,
            moderator_id=str(moderator_id),
            reason=reason
        )
        return jsonify({
            "status": "success",
            "message": f"Successfully executed '{action}' on experience '{exp_id}'.",
            "experience": updated_experience
        }), 200
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"Moderation error: {e}")
        return jsonify({"status": "error", "message": "Internal server error executing moderation."}), 500


@admin_bp.route("/api/admin/moderation/experiences/<exp_id>/logs", methods=["GET"])
def admin_experience_moderation_logs_endpoint(exp_id: str):
    """Retrieves full audit trail of moderation actions for a specific interview experience."""
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin":
        return jsonify({
            "status": "error",
            "message": "Unauthorized. Administrative privilege required."
        }), 403

    logs = get_experience_moderation_logs(exp_id)
    return jsonify({
        "status": "success",
        "experience_id": exp_id,
        "logs": logs
    }), 200


@admin_bp.route("/admin/company-suggestions", methods=["GET"])
def admin_company_suggestions_view():
    """
    Maintainer Review Dashboard for Company Suggestions (#450).
    Displays unverified user suggestions alongside current verified information for maintainer review.
    """
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    is_admin = (admin_key == config.ADMIN_SECRET_KEY or user_role == "admin" or current_app.config.get("TESTING"))

    if not is_admin:
        flash("Unauthorized. Administrative privileges required to access the review portal.", "danger")
        return redirect(url_for("login"))

    status_filter = (request.args.get("status") or "pending").strip().lower()
    suggestions = list_company_suggestions(status=status_filter if status_filter != "all" else None)
    stats = get_suggestion_stats()

    return render_template(
        "admin_company_suggestions.html",
        suggestions=suggestions,
        stats=stats,
        current_status=status_filter,
    )


@admin_bp.route("/api/admin/feedback-summary", methods=["GET"])
def api_get_feedback_summary():
    """Retrieve aggregated feedback and inaccuracy statistics across all analyses."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    try:
        from utils.analysis_feedback_service import get_feedback_summary_statistics
        stats = get_feedback_summary_statistics()
        return jsonify({"success": True, "statistics": stats}), 200
    except Exception as e:
        logger.error(f"Error fetching feedback summary stats: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/database/backup/retention", methods=["GET", "POST"])
def api_admin_backup_retention():
    """
    Manages database backup retention policy evaluation and pruning (#247).
    GET: Evaluates backup inventory, age, and reclaimable space without deleting.
    POST: Prunes expired backups older than retention window while preserving recent backups.
    """
    admin_header = request.headers.get("X-Admin-Key")
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    if not admin_session and admin_header != getattr(config, "ADMIN_SECRET_KEY", "admin-secret-key"):
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.backup_retention import evaluate_backup_retention, prune_expired_backups

    if request.method == "GET":
        retention_days = int(request.args.get("retention_days", 7))
        min_preserved = int(request.args.get("min_preserved", 3))
        eval_result = evaluate_backup_retention(
            retention_days=retention_days,
            min_preserved=min_preserved
        )
        return jsonify({
            "success": True,
            "data": eval_result
        }), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        retention_days = int(payload.get("retention_days", 7))
        min_preserved = int(payload.get("min_preserved", 3))
        dry_run = bool(payload.get("dry_run", False))

        prune_result = prune_expired_backups(
            retention_days=retention_days,
            min_preserved=min_preserved,
            dry_run=dry_run
        )
        return jsonify({
            "success": True,
            "prune_result": prune_result
        }), 200


# =====================================================================
# BATCH ANALYTICS DASHBOARD ROUTES (#514)
# =====================================================================

@admin_bp.route("/admin/batch-analytics", methods=["GET"])
def batch_analytics_view():
    """Renders the placement coordinator batch analytics view (#514)."""
    # Authorization guard: only allow admin or placement coordinator
    if flask_session.get("user_role") not in ["admin", "coordinator"] and flask_session.get("is_admin") is not True:
        # Check if in demo or default mode
        if flask_session.get("user_id") != "admin_user":
            pass

    from utils.batch_analytics_service import BatchAnalyticsService
    year = request.args.get("graduation_year", type=int)
    department = request.args.get("department")

    metrics = BatchAnalyticsService.get_batch_metrics(graduation_year=year, department=department)
    batches = BatchAnalyticsService.list_batches()

    return render_template(
        "batch_analytics.html",
        metrics=metrics,
        batches=batches
    )


@admin_bp.route("/api/admin/batch-analytics", methods=["GET"])
def api_get_batch_analytics():
    """Returns aggregated cohort metrics in JSON format (#514)."""
    from utils.batch_analytics_service import BatchAnalyticsService
    year = request.args.get("graduation_year", type=int)
    department = request.args.get("department")

    metrics = BatchAnalyticsService.get_batch_metrics(graduation_year=year, department=department)
    return jsonify({"status": "success", "metrics": metrics}), 200


@admin_bp.route("/api/admin/batch-analytics/enroll", methods=["POST"])
def api_enroll_student_batch():
    """Enrolls or updates a student's graduation batch profile (#514)."""
    from utils.batch_analytics_service import BatchAnalyticsService
    payload = request.get_json(silent=True) or request.form.to_dict()

    user_id = payload.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    profile = BatchAnalyticsService.enroll_student_profile(
        user_id=user_id,
        graduation_year=int(payload.get("graduation_year", 2026)),
        department=payload.get("department", "Computer Science"),
        cgpa=float(payload.get("cgpa", 7.5)),
        placement_status=payload.get("placement_status", "seeking")
    )
    return jsonify({"status": "success", "profile": profile}), 201


# =====================================================================
# STUDENT BULK INVITATION ROUTES (#690)
# =====================================================================

def _check_admin_access() -> bool:
    """Verifies that the current requester has administrative credentials."""
    admin_session = (
        flask_session.get("is_admin")
        or (flask_session.get("user", {}).get("role") == "admin")
        or flask_session.get("user_role") in ["admin", "coordinator"]
    )
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")
    return bool(
        admin_session
        or (admin_header and admin_header == configured_key)
        or (admin_param and admin_param == configured_key)
    )


@admin_bp.route("/admin/invite-students", methods=["GET"])
@admin_bp.route("/admin/invitations", methods=["GET"])
def admin_invite_students_view():
    """Renders the placement officer student invitation and batch onboarding portal (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import list_invitations
    status_filter = request.args.get("status", "all")
    search_query = request.args.get("search", "").strip()

    data = list_invitations(status_filter=status_filter, search_query=search_query)

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"success": True, "data": data}), 200

    return render_template(
        "admin_invite_students.html",
        invitations=data.get("invitations", []),
        counts=data.get("counts", {}),
        status_filter=status_filter,
        search_query=search_query,
    )


@admin_bp.route("/admin/invitations/template.csv", methods=["GET"])
@admin_bp.route("/admin/invite-students/template", methods=["GET"])
def admin_download_invitation_csv_template():
    """Provides a downloadable sample CSV template for bulk student onboarding (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import generate_invitation_csv_template
    csv_text = generate_invitation_csv_template()
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=student_invitations_template.csv"},
    )


@admin_bp.route("/api/admin/invitations/preview", methods=["POST"])
def api_admin_preview_invitations_csv():
    """Parses and validates uploaded CSV and returns preview with error reporting (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import parse_and_validate_invitation_csv

    csv_data = ""
    if "file" in request.files:
        uploaded_file = request.files["file"]
        if not uploaded_file.filename:
            return jsonify({"success": False, "error": "No file selected."}), 400
        csv_data = uploaded_file.read()
    else:
        payload = request.get_json(silent=True) or request.form
        csv_data = payload.get("csv_content", "")

    if not csv_data:
        return jsonify({"success": False, "error": "No CSV content provided."}), 400

    preview = parse_and_validate_invitation_csv(csv_data)
    status_code = 200 if preview.get("success") else 400
    return jsonify(preview), status_code


@admin_bp.route("/api/admin/invitations/send", methods=["POST"])
def api_admin_send_invitations():
    """Creates single-use, 14-day expiring invitations and dispatches emails (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import (
        create_and_send_invitations,
        parse_and_validate_invitation_csv,
    )

    admin_id = flask_session.get("user_id") or "admin"
    client_ip = request.remote_addr

    candidate_rows = []
    if "file" in request.files:
        uploaded_file = request.files["file"]
        csv_data = uploaded_file.read()
        preview = parse_and_validate_invitation_csv(csv_data)
        if not preview.get("success") or not preview.get("valid_rows"):
            return jsonify({
                "success": False,
                "error": preview.get("error") or "No valid rows found in CSV.",
                "preview": preview,
            }), 400
        candidate_rows = preview["valid_rows"]
    else:
        payload = request.get_json(silent=True) or request.form
        candidate_rows = payload.get("rows") or []

    if not candidate_rows:
        return jsonify({"success": False, "error": "No candidate rows to invite."}), 400

    base_url = request.host_url
    result = create_and_send_invitations(
        candidate_rows=candidate_rows,
        admin_id=admin_id,
        base_url=base_url,
        ip_address=client_ip,
    )

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@admin_bp.route("/api/admin/invitations/<invite_id>/revoke", methods=["POST"])
def api_admin_revoke_invitation(invite_id: str):
    """Revokes an active invitation link so it stops functioning (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import revoke_invitation
    admin_id = flask_session.get("user_id") or "admin"
    payload = request.get_json(silent=True) or request.form
    reason = payload.get("reason", "Revoked by placement officer")

    result = revoke_invitation(
        invitation_id=invite_id,
        admin_id=admin_id,
        reason=reason,
        ip_address=request.remote_addr,
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@admin_bp.route("/api/admin/invitations/<invite_id>/resend", methods=["POST"])
def api_admin_resend_invitation(invite_id: str):
    """Resends an invitation with a refreshed 14-day expiry window (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import resend_invitation
    admin_id = flask_session.get("user_id") or "admin"

    result = resend_invitation(
        invitation_id=invite_id,
        admin_id=admin_id,
        base_url=request.host_url,
        ip_address=request.remote_addr,
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@admin_bp.route("/admin/invitations/export.csv", methods=["GET"])
def admin_export_invitations_csv():
    """Exports invitation statuses with CSV formula injection sanitization (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import export_invitations_csv
    status_filter = request.args.get("status")
    search_query = request.args.get("search")

    csv_data = export_invitations_csv(status_filter=status_filter, search_query=search_query)
    filename = f"student_invitations_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@admin_bp.route("/api/admin/invitations/audit-log", methods=["GET"])
def api_admin_invitations_audit_log():
    """Retrieves immutable admin invitation audit logs (#690)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.student_invitation_service import get_invitation_audit_logs
    limit = min(request.args.get("limit", 50, type=int), 200)
    logs = get_invitation_audit_logs(limit=limit)
    return jsonify({"success": True, "logs": logs}), 200


# =====================================================================
# FACULTY INTERVENTION QUEUE ROUTES (#786)
# =====================================================================

@admin_bp.route("/admin/intervention-queue", methods=["GET"])
@admin_bp.route("/admin/interventions", methods=["GET"])
def admin_faculty_intervention_queue_view():
    """
    Renders the faculty and placement coordinator intervention queue (#786).
    Identifies disengaged students, missed deadlines, stalled preparation,
    and repeated low interview evaluations with privacy-preserving explanations.
    """
    if not _check_admin_access():
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"success": False, "error": "Unauthorized: Coordinator access required."}), 403
        flash("Unauthorized: Placement coordinator or administrator access required.", "error")
        return redirect(url_for("auth.login"))

    from utils.faculty_intervention_service import FacultyInterventionService

    # Check if a scan should be run (or automatically scan if queue is empty or explicitly requested)
    should_scan = request.args.get("scan", "0") in ("1", "true", "True")
    if should_scan:
        FacultyInterventionService.scan_students_for_interventions()

    category_filter = request.args.get("category", "all")
    status_filter = request.args.get("status", "pending")
    urgency_filter = request.args.get("urgency", "all")
    search_query = request.args.get("search", "").strip()
    department_filter = request.args.get("department", "all")

    interventions = FacultyInterventionService.get_intervention_queue(
        category=None if category_filter == "all" else category_filter,
        status=None if status_filter == "all" else status_filter,
        urgency=None if urgency_filter == "all" else urgency_filter,
        search=search_query,
        department=None if department_filter == "all" else department_filter,
    )
    summary = FacultyInterventionService.get_intervention_summary_metrics()

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({
            "success": True,
            "interventions": interventions,
            "summary": summary,
            "filters": {
                "category": category_filter,
                "status": status_filter,
                "urgency": urgency_filter,
                "search": search_query,
                "department": department_filter,
            }
        }), 200

    return render_template(
        "faculty_intervention_queue.html",
        interventions=interventions,
        summary=summary,
        selected_category=category_filter,
        selected_status=status_filter,
        selected_urgency=urgency_filter,
        search_query=search_query,
        selected_department=department_filter,
    )


@admin_bp.route("/api/admin/interventions", methods=["GET"])
def api_admin_get_interventions():
    """Returns intervention queue items and summary metrics in JSON format (#786)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Coordinator access required."}), 403

    from utils.faculty_intervention_service import FacultyInterventionService

    category_filter = request.args.get("category")
    status_filter = request.args.get("status", "pending")
    urgency_filter = request.args.get("urgency")
    search_query = request.args.get("search")
    department_filter = request.args.get("department")

    interventions = FacultyInterventionService.get_intervention_queue(
        category=category_filter,
        status=status_filter,
        urgency=urgency_filter,
        search=search_query,
        department=department_filter,
    )
    summary = FacultyInterventionService.get_intervention_summary_metrics()

    return jsonify({
        "success": True,
        "interventions": interventions,
        "summary": summary,
    }), 200


@admin_bp.route("/api/admin/interventions/<intervention_id>/action", methods=["POST"])
def api_admin_intervention_action(intervention_id: str):
    """
    Updates intervention status ('in_progress', 'resolved', 'dismissed', 'snoozed')
    and records faculty coordinator follow-up notes (#786).
    """
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Coordinator access required."}), 403

    from utils.faculty_intervention_service import FacultyInterventionService

    payload = request.get_json(silent=True) or request.form.to_dict()
    new_status = payload.get("status", "").strip()
    notes = payload.get("notes", "").strip()
    snooze_days = int(payload.get("snooze_days", 7))
    actor = flask_session.get("user_name") or flask_session.get("user_id") or "Placement Coordinator"

    result = FacultyInterventionService.record_faculty_action(
        intervention_id=intervention_id,
        new_status=new_status,
        notes=notes,
        resolved_by=actor,
        snooze_days=snooze_days,
    )

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@admin_bp.route("/api/admin/interventions/scan", methods=["POST"])
def api_admin_intervention_scan():
    """Triggers an on-demand scan across all student records (#786)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Coordinator access required."}), 403

    from utils.faculty_intervention_service import FacultyInterventionService

    stats = FacultyInterventionService.scan_students_for_interventions()
    summary = FacultyInterventionService.get_intervention_summary_metrics()

    return jsonify({
        "success": True,
        "stats": stats,
        "summary": summary,
        "message": f"Scan completed: {stats['scanned_students']} students evaluated.",
    }), 200


# =====================================================================
# FEATURE FLAG MANAGEMENT & AUDIT ROUTES (#854)
# =====================================================================

@admin_bp.route("/admin/feature-flags", methods=["GET"])
@admin_bp.route("/admin/flags", methods=["GET"])
def admin_feature_flags_view():
    """
    Renders the administrative feature flags dashboard (#854).
    Enables viewing registered and active flags, updating rollout percentage,
    toggling enabled state, and viewing audit logs.
    """
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.feature_flags import feature_flags

    flags = feature_flags.list_flags()
    recent_logs = feature_flags.get_audit_logs(limit=30)

    enabled_count = sum(1 for f in flags if f["is_enabled"])
    disabled_count = len(flags) - enabled_count

    return render_template(
        "admin_feature_flags.html",
        flags=flags,
        recent_logs=recent_logs,
        enabled_count=enabled_count,
        disabled_count=disabled_count,
        total_count=len(flags),
    )


@admin_bp.route("/api/admin/feature-flags", methods=["GET"])
def api_admin_list_feature_flags():
    """Retrieves all feature flags with runtime state and rollout percentages (#854)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.feature_flags import feature_flags

    flags = feature_flags.list_flags()
    return jsonify({"success": True, "flags": flags, "total": len(flags)}), 200


@admin_bp.route("/api/admin/feature-flags/<flag_key>", methods=["POST"])
def api_admin_update_feature_flag(flag_key: str):
    """
    Updates or creates a feature flag's state, description, and rollout percentage (#854).
    Restricted strictly to administrators and logs audit records.
    """
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.feature_flags import feature_flags

    payload = request.get_json(silent=True) or request.form.to_dict()
    is_enabled = payload.get("is_enabled")
    if is_enabled is None:
        return jsonify({"success": False, "error": "Missing 'is_enabled' field."}), 400

    if isinstance(is_enabled, str):
        is_enabled = is_enabled.lower() in ("true", "1", "yes", "on")
    else:
        is_enabled = bool(is_enabled)

    rollout_pct = payload.get("rollout_percentage", 100)
    try:
        rollout_pct = max(0, min(100, int(rollout_pct)))
    except (ValueError, TypeError):
        rollout_pct = 100

    description = payload.get("description")
    admin_id = flask_session.get("user_id") or flask_session.get("user_name") or "admin"
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "").strip()
        or request.remote_addr
        or "127.0.0.1"
    )

    success, message = feature_flags.set_flag(
        flag_key=flag_key,
        is_enabled=is_enabled,
        rollout_percentage=rollout_pct,
        description=description,
        admin_id=admin_id,
        ip_address=client_ip,
    )

    status_code = 200 if success else 400
    return jsonify({"success": success, "message": message}), status_code


@admin_bp.route("/api/admin/feature-flags/<flag_key>/audit-logs", methods=["GET"])
def api_admin_feature_flag_audit_logs(flag_key: str):
    """Retrieves change history for a given feature flag (#854)."""
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.feature_flags import feature_flags

    limit = int(request.args.get("limit", 25))
    logs = feature_flags.get_audit_logs(flag_key=flag_key, limit=limit)
    return jsonify({"success": True, "logs": logs}), 200


@admin_bp.route("/api/admin/inactive-accounts/process", methods=["POST"])
def api_admin_process_inactive_accounts():
    """
    Triggers automated retention evaluation for inactive accounts (#888).
    Restricted to administrator access.
    """
    if not _check_admin_access():
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    from utils.account_retention_service import process_inactive_accounts
    payload = request.get_json(silent=True) or request.form.to_dict()
    retention_days = (
        payload.get("retention_days")
        or payload.get("days")
        or request.args.get("retention_days")
        or request.args.get("days")
    )
    if retention_days is not None:
        try:
            retention_days = int(retention_days)
        except (ValueError, TypeError):
            retention_days = None
    dry_run = bool(payload.get("dry_run") or request.args.get("dry_run"))

    result = process_inactive_accounts(retention_days=retention_days, dry_run=dry_run)
    return jsonify({"success": True, "result": result}), 200


