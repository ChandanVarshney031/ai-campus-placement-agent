"""
Tracker feature area routes & endpoints (#553).
Handles job application pipelines, calendar integration, reminders, and checklists.
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
    abort,
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
from utils.readiness_timeline_service import (
    get_placement_activity_timeline,
    get_readiness_effort_summary,
    log_placement_activity,
    ALL_CATEGORIES,
    CATEGORY_META,
)
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
from agents.follow_up_note_agent import FollowUpNoteAgent, NOTE_TYPES
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

tracker_bp = Blueprint("tracker", __name__)


def _current_streak_student_id():
    return (
        flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )


@tracker_bp.route("/streak", methods=["GET"])
def streak_tracker_view():
    student_id = _current_streak_student_id()
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    streak = get_student_streak(student_id)
    calendar = get_streak_calendar(student_id, year=year, month=month)
    recent_activities = get_recent_preparation_activities(student_id, limit=15)
    milestones = [
        {"days": 3, "name": "3-Day Spark", "icon": "fa-bolt", "unlocked": streak["longest_streak"] >= 3},
        {"days": 7, "name": "7-Day Flame", "icon": "fa-fire", "unlocked": streak["longest_streak"] >= 7},
        {"days": 14, "name": "14-Day Torch", "icon": "fa-fire-flame-curved", "unlocked": streak["longest_streak"] >= 14},
        {"days": 30, "name": "30-Day Firestorm", "icon": "fa-crown", "unlocked": streak["longest_streak"] >= 30},
    ]
    informative_message = (
        "You haven't started your preparation streak yet! Complete a meaningful activity "
        "— like analyzing your resume, practicing DSA problems, completing a company checklist item, "
        "or taking a mock interview — to ignite your streak today."
    ) if not streak.get("has_started") else None

    return render_template(
        "streak.html",
        streak=streak,
        calendar=calendar,
        recent_activities=recent_activities,
        student_id=student_id,
        informative_message=informative_message,
        milestones=milestones,
    )


@tracker_bp.route("/api/streak", methods=["GET"])
def api_get_student_streak():
    streak = get_student_streak(_current_streak_student_id())
    return jsonify({
        "success": True,
        "streak": streak,
        "message": "Streak retrieved successfully",
    }), 200


@tracker_bp.route("/api/streak/calendar", methods=["GET"])
def api_get_streak_calendar():
    calendar = get_streak_calendar(
        _current_streak_student_id(),
        year=request.args.get("year", type=int),
        month=request.args.get("month", type=int),
    )
    return jsonify({"success": True, "calendar": calendar}), 200


@tracker_bp.route("/api/preparation/streak", methods=["POST"])
def api_calculate_preparation_streak():
    """Calculates a preparation streak with the configured grace period."""
    from utils.streak_grace_service import calculate_streak_with_grace

    data = request.get_json(silent=True) or {}
    streak = calculate_streak_with_grace(
        data.get("activity_dates", []),
        data.get("current_date"),
        grace_days_allowed=int(data.get("grace_days", 1)),
    )
    return jsonify({"status": "success", "streak": streak}), 200


@tracker_bp.route("/leaderboard", methods=["GET"])
def leaderboard():
    """
    Renders the anonymized campus placement leaderboard showcasing
    cohort statistics, top 3 podium, and department/batch standings with pagination (#152).
    """
    branch = request.args.get("branch", "all").strip()
    year = request.args.get("year", "all").strip()
    search = request.args.get("search", "").strip()
    current_analysis_id = request.args.get("analysis_id", "").strip() or None
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    cohort_service = CohortAnalyticsService()
    view_data = cohort_service.get_leaderboard_view(
        department=branch if branch != "all" else None,
        grad_year=int(year) if year.isdigit() else None,
        search=search,
        current_analysis_id=current_analysis_id
    )

    all_leaderboard_rows = view_data.get("leaderboard", [])
    pagination = Pagination(
        items=all_leaderboard_rows,
        page=page,
        per_page=per_page,
        endpoint_args=request.args.to_dict(),
        base_url="/leaderboard"
    )

    candidate_percentile = None
    if current_analysis_id:
        try:
            candidate_percentile = cohort_service.calculate_percentile_rank(current_analysis_id)
        except Exception:
            candidate_percentile = None

    user_id = flask_session.get("user_id")
    user_opt_in = get_user_leaderboard_opt_in(user_id) if user_id else True

    return render_template(
        "leaderboard.html",
        stats=view_data.get("stats", {}),
        podium=view_data.get("podium", []),
        leaderboard=pagination.items,
        pagination=pagination,
        total_ranked=view_data.get("total_ranked", 0),
        filters=view_data.get("filters", {}),
        current_analysis_id=current_analysis_id,
        candidate_percentile=candidate_percentile,
        user_opt_in=user_opt_in
    )


@tracker_bp.route("/api/user/leaderboard-privacy", methods=["GET", "POST"])
def user_leaderboard_privacy():
    """
    Retrieves or updates the candidate's leaderboard participation preference (#360).
    Requires authenticated session; bound strictly to flask_session['user_id'].
    POST requests require CSRF protection.
    """
    user_id = flask_session.get("user_id")
    if not user_id:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": False,
                "error": "Authentication required to manage leaderboard privacy settings."
            }), 401
        flash("Please log in to manage your leaderboard privacy settings.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        opt_in_val = True
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            raw = payload.get("opt_in")
            if raw is not None:
                opt_in_val = bool(raw)
        else:
            raw = request.form.get("opt_in")
            if raw is not None:
                opt_in_val = str(raw).strip().lower() in ("1", "true", "on", "yes")
            else:
                opt_in_val = False

        set_user_leaderboard_opt_in(user_id, opt_in_val)
        status_msg = (
            "You are now included in the campus placement leaderboard."
            if opt_in_val
            else "You have opted out of the campus placement leaderboard. Your scores are now private."
        )

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": True,
                "opt_in": opt_in_val,
                "message": status_msg
            }), 200

        flash(status_msg, "success" if opt_in_val else "info")
        target_analysis_id = request.form.get("analysis_id", "").strip()
        redirect_url = url_for("leaderboard", analysis_id=target_analysis_id) if target_analysis_id else url_for("leaderboard")
        return redirect(redirect_url)

    # GET request
    opt_in = get_user_leaderboard_opt_in(user_id)
    return jsonify({
        "success": True,
        "user_id": user_id,
        "opt_in": opt_in
    }), 200


@tracker_bp.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    """
    Returns JSON payload of anonymized cohort benchmarks and ranking metrics with pagination (#152).
    """
    branch = request.args.get("branch", "all").strip()
    year = request.args.get("year", "all").strip()
    search = request.args.get("search", "").strip()
    current_analysis_id = request.args.get("analysis_id", "").strip() or None
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    cohort_service = CohortAnalyticsService()
    view_data = cohort_service.get_leaderboard_view(
        department=branch if branch != "all" else None,
        grad_year=int(year) if year.isdigit() else None,
        search=search,
        current_analysis_id=current_analysis_id
    )

    all_leaderboard_rows = view_data.get("leaderboard", [])
    pagination = Pagination(
        items=all_leaderboard_rows,
        page=page,
        per_page=per_page,
        endpoint_args=request.args.to_dict(),
        base_url="/api/leaderboard"
    )

    return jsonify({
        "success": True,
        "stats": view_data.get("stats", {}),
        "podium": view_data.get("podium", []),
        "leaderboard": pagination.items,
        "pagination": pagination.to_dict(),
        "total_ranked": view_data.get("total_ranked", 0),
        "filters": view_data.get("filters", {}),
    }), 200


@tracker_bp.route("/skills-assessment", methods=["GET", "POST"])
def skills_assessment_view():
    """
    Renders and manages candidate technical skills self-assessment module.
    Allows students to log and review competencies across languages, frameworks, and CS fundamentals.
    """
    student_id = (flask_session.get("user", {}).get("id") or flask_session.get("user_id") or flask_session.get("student_id") or "default_student")
    catalog = get_default_technical_skills_catalog()

    if request.method == "POST":
        skill_name = request.form.get("skill_name", "").strip()
        category = request.form.get("category", "General").strip()
        proficiency_level = request.form.get("proficiency_level", "Intermediate").strip()
        years_experience = request.form.get("years_experience", "0").strip()
        notes = request.form.get("notes", "").strip()

        if not skill_name:
            flash("Skill name is required.", "danger")
        else:
            try:
                exp_val = float(years_experience) if years_experience else 0.0
            except ValueError:
                exp_val = 0.0

            save_skill_self_assessment(
                student_id=student_id,
                skill_name=skill_name,
                category=category,
                proficiency_level=proficiency_level,
                years_experience=exp_val,
                notes=notes
            )
            flash(f"Self-assessment for '{skill_name}' recorded successfully!", "success")
        return redirect("/skills-assessment")

    # GET request - gather assessments and compute metrics
    assessments = get_student_skill_self_assessments(student_id)
    level_weights = {"Beginner": 25, "Intermediate": 50, "Advanced": 80, "Expert": 100}
    category_summary = {}
    total_score = 0

    for item in assessments:
        cat = item.get("category", "General")
        level = item.get("proficiency_level", "Intermediate")
        pts = level_weights.get(level, 50)
        total_score += pts
        if cat not in category_summary:
            category_summary[cat] = {"count": 0, "total_pts": 0}
        category_summary[cat]["count"] += 1
        category_summary[cat]["total_pts"] += pts

    avg_score = round(total_score / len(assessments)) if assessments else 0

    return render_template(
        "skills_assessment.html",
        catalog=catalog,
        assessments=assessments,
        category_summary=category_summary,
        avg_score=avg_score,
        total_skills=len(assessments),
        student_id=student_id
    )


@tracker_bp.route("/api/skills-assessment", methods=["GET", "POST", "DELETE"])
def api_skills_assessment():
    """
    REST API endpoint for managing student technical skill self-assessments.
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    if request.method == "GET":
        items = get_student_skill_self_assessments(student_id)
        return jsonify({
            "status": "success",
            "student_id": student_id,
            "total": len(items),
            "skills": items
        }), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        skill_name = payload.get("skill_name", "").strip()
        category = payload.get("category", "General").strip()
        proficiency_level = payload.get("proficiency_level", "Intermediate").strip()
        years_experience = payload.get("years_experience", 0.0)
        notes = payload.get("notes", "").strip()

        if not skill_name:
            return jsonify({"status": "error", "message": "skill_name is required"}), 400

        try:
            exp_val = float(years_experience)
        except (ValueError, TypeError):
            exp_val = 0.0

        saved = save_skill_self_assessment(
            student_id=student_id,
            skill_name=skill_name,
            category=category,
            proficiency_level=proficiency_level,
            years_experience=exp_val,
            notes=notes
        )
        return jsonify({
            "status": "success",
            "message": f"Skill '{skill_name}' assessment saved",
            "skill": saved
        }), 201

    if request.method == "DELETE":
        payload = request.get_json(silent=True) or request.args
        skill_name = (payload.get("skill_name") or "").strip()
        if not skill_name:
            return jsonify({"status": "error", "message": "skill_name is required for deletion"}), 400
        deleted = delete_skill_self_assessment(student_id, skill_name)
        return jsonify({
            "status": "success" if deleted else "not_found",
            "message": f"Deleted skill '{skill_name}'" if deleted else "Skill entry not found"
        }), 200 if deleted else 404


@tracker_bp.route("/tracker", methods=["GET"])
@tracker_bp.route("/applications", methods=["GET", "POST"])
@tracker_bp.route("/api/applications", methods=["GET", "POST"])
def job_applications_tracker():
    """
    Candidate Job Application Tracker view and management handler (#143, #193).
    Enforces per-student data segregation and provides advanced multi-parameter search & filtering.
    """
    json_body = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(json_body, dict):
        json_body = {}
    student_id = _application_import_student_id()
    status_filter = request.args.get("status", "All")
    search_query = request.args.get("search", "").strip()
    location_filter = request.args.get("location", "All").strip()
    job_type_filter = request.args.get("job_type", "All").strip()
    exp_filter = request.args.get("experience_level", "All").strip()
    sort_by = request.args.get("sort", "recent").strip()

    is_api_request = (
        request.path == "/api/applications"
        or request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        company = (payload.get("company_name") or payload.get("company") or "").strip()
        role = (payload.get("role_title") or payload.get("role") or "").strip()
        raw_status = (payload.get("status") or "Applied").strip()
        status = "Selected" if raw_status == "Offered" else raw_status
        if status not in APPLICATION_STAGES:
            status = "Applied"

        app_date = (payload.get("application_date") or "").strip()
        job_url = (payload.get("job_url") or payload.get("url") or "").strip()
        job_description = (payload.get("job_description") or "").strip()
        resume_analysis_id = (payload.get("resume_analysis_id") or "").strip()
        notes = (payload.get("notes") or "").strip()
        location = (payload.get("location") or "Remote").strip()
        job_type = (payload.get("job_type") or "Full-Time").strip()
        exp_level = (payload.get("experience_level") or "Entry-Level").strip()
        deadline_date = (payload.get("deadline_date") or "").strip() or None

        if not company or not role:
            if is_api_request:
                return jsonify({
                    "success": False,
                    "error": "Company name and job role are required."
                }), 400
            flash("Company name and job role are required.", "error")
            return redirect(url_for("job_applications_tracker", student_id=student_id))

        resume_snapshot = _resume_link_snapshot(student_id, resume_analysis_id)
        if resume_analysis_id and not resume_snapshot:
            error_message = "The selected resume version is not available for this account."
            if is_api_request:
                return jsonify({"success": False, "error": error_message}), 400
            flash(error_message, "error")
            return redirect(url_for("job_applications_tracker", student_id=student_id))
        if resume_snapshot:
            job_description = resume_snapshot["job_description"]

        app_id = f"app-{uuid.uuid4().hex[:12]}"
        save_job_application(
            app_id=app_id,
            student_id=student_id,
            company_name=company,
            role_title=role,
            status=status,
            application_date=app_date or None,
            job_url=job_url,
            job_description=job_description,
            notes=notes,
            location=location,
            job_type=job_type,
            experience_level=exp_level,
            resume_analysis_id=resume_snapshot["id"] if resume_snapshot else None,
            resume_filename=resume_snapshot["filename"] if resume_snapshot else None,
            resume_version=resume_snapshot["version"] if resume_snapshot else None,
            job_description_snapshot=job_description or None
        )

        # Notify candidate via email if preference enabled (#186)
        try:
            prefs = get_email_notification_preferences(student_id)
            if prefs.get("email_enabled") and prefs.get("notify_application_created"):
                user_email = prefs.get("email") or payload.get("email") or f"{student_id}@student.campus.edu"
                email_service.send_application_created_email(
                    recipient_email=user_email,
                    student_name=student_id,
                    company_name=company,
                    role_title=role,
                    applied_date=app_date or "Today",
                    locale=prefs.get("output_language", DEFAULT_OUTPUT_LOCALE)
                )
        except Exception as mail_err:
            logger.warning(f"Error sending application creation email: {mail_err}")

        if is_api_request:
            created_app = get_job_application_by_id(app_id, student_id)
            return jsonify({
                "success": True,
                "message": f"Application for '{role}' at '{company}' added successfully.",
                "app_id": app_id,
                "application": created_app
            }), 201

        flash("Application record saved successfully!", "success")
        return redirect(url_for("job_applications_tracker", student_id=student_id))

    applications = get_job_applications(
        student_id=student_id,
        status_filter=status_filter,
        search_query=search_query,
        location_filter=location_filter,
        job_type_filter=job_type_filter,
        experience_level_filter=exp_filter,
        sort_by=sort_by
    )

    all_user_apps = get_job_applications(student_id=student_id)
    stage_counts = get_application_stage_counts(student_id)
    funnel_analytics = get_application_funnel_analytics(student_id)

    if is_api_request:
        return jsonify({
            "success": True,
            "student_id": student_id,
            "count": len(applications),
            "total": len(all_user_apps),
            "stage_counts": stage_counts,
            "funnel_analytics": funnel_analytics,
            "stages": APPLICATION_STAGES,
            "applications": applications
        }), 200

    return render_template(
        "applications.html",
        applications=applications,
        all_count=len(all_user_apps),
        stage_counts=stage_counts,
        funnel_analytics=funnel_analytics,
        stages=APPLICATION_STAGES,
        student_id=student_id,
        current_status=status_filter,
        search_query=search_query,
        location_filter=location_filter,
        job_type_filter=job_type_filter,
        exp_filter=exp_filter,
        sort_by=sort_by,
        resume_versions=get_student_versions(student_id)
    )


@tracker_bp.route("/api/applications/<app_id>/status", methods=["POST"])
def api_update_application_status(app_id: str):
    """
    Updates the stage of an application (Applied, Assessment, Interview, Selected, Rejected).
    """
    payload = request.get_json(silent=True) or {}
    raw_status = payload.get("status") or request.form.get("status", "")
    new_status = "Selected" if raw_status == "Offered" else raw_status.strip()
    student_id = (
        request.args.get("student_id")
        or payload.get("student_id")
        or request.form.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    if not new_status:
        return jsonify({"success": False, "error": "New status is required."}), 400

    if new_status not in APPLICATION_STAGES:
        return jsonify({
            "success": False,
            "error": f"Invalid stage '{new_status}'. Allowed stages: {', '.join(APPLICATION_STAGES)}."
        }), 400

    updated = update_job_application_status(app_id=app_id, student_id=student_id, new_status=new_status)
    
    # Notify candidate of application status update via email (#186)
    if updated:
        try:
            prefs = get_email_notification_preferences(student_id)
            if prefs.get("email_enabled") and prefs.get("notify_status_change"):
                apps = get_job_applications(student_id=student_id)
                target_app = next((a for a in apps if a.get("id") == app_id), None)
                comp = target_app.get("company_name", "Target Company") if target_app else "Target Company"
                role = target_app.get("role_title", "Candidate Role") if target_app else "Role"
                user_email = prefs.get("email") or f"{student_id}@student.campus.edu"
                email_service.send_status_change_email(
                    recipient_email=user_email,
                    student_name=student_id,
                    company_name=comp,
                    role_title=role,
                    previous_status="Previous Stage",
                    new_status=new_status,
                    notes=payload.get("notes") or request.form.get("notes"),
                    locale=prefs.get("output_language", DEFAULT_OUTPUT_LOCALE)
                )
        except Exception as mail_err:
            logger.warning(f"Failed to dispatch application status change email: {mail_err}")

    return jsonify({
        "success": updated,
        "status": new_status,
        "message": f"Application status updated to '{new_status}'."
    }), (200 if updated else 404)


@tracker_bp.route("/api/shortcuts", methods=["GET"])
def api_get_keyboard_shortcuts():
    """Returns platform keyboard shortcut definitions and bindings (#634)."""
    from utils.keyboard_shortcuts_service import get_keyboard_shortcuts
    return jsonify({"status": "success", "shortcuts": get_keyboard_shortcuts()}), 200


@tracker_bp.route("/api/applications/<app_id>", methods=["DELETE"])
def api_delete_application(app_id: str):
    """
    Deletes an application record owned by student_id.
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()
    deleted = delete_job_application(app_id=app_id, student_id=student_id)
    return jsonify({"success": deleted}), (200 if deleted else 404)


@tracker_bp.route("/applications/export", methods=["GET"])
@tracker_bp.route("/api/applications/export", methods=["GET"])
def export_job_applications():
    """
    Exports a candidate's job applications data as a downloadable CSV or JSON file (#196).
    Supports format selection via ?format=csv (default) or ?format=json.
    Respects active ?status= filter and candidate data segregation.
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )
    status_filter = request.args.get("status", "All")
    export_format = request.args.get("format", "csv").strip().lower()

    if export_format == "json":
        data = export_job_applications_json(student_id=student_id, status_filter=status_filter)
        res = jsonify(data)
        safe_student = "".join(c for c in student_id if c.isalnum() or c in ("-", "_"))
        res.headers["Content-Disposition"] = f"attachment; filename=job_applications_{safe_student}.json"
        return res

    if export_format in ("md", "markdown"):
        md_content = export_job_applications_markdown(student_id=student_id, status_filter=status_filter)
        safe_student = "".join(c for c in student_id if c.isalnum() or c in ("-", "_"))
        return Response(
            md_content,
            mimetype="text/markdown",
            headers={
                "Content-Disposition": f"attachment; filename=job_applications_{safe_student}.md",
                "Content-Type": "text/markdown; charset=utf-8"
            }
        )

    # Default CSV
    csv_content = export_job_applications_csv(student_id=student_id, status_filter=status_filter)
    safe_student = "".join(c for c in student_id if c.isalnum() or c in ("-", "_"))
    response = Response(
        csv_content,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=job_applications_{safe_student}.csv",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )
    return response


@tracker_bp.route("/api/applications/export-summary", methods=["GET"])
def api_applications_export_summary():
    """
    Returns application export summary and readiness stats for candidate (#196).
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )
    summary = get_job_applications_export_summary(student_id=student_id)
    return jsonify({
        "status": "success",
        "export_metadata": summary
    }), 200


@tracker_bp.route("/api/applications/bulk", methods=["POST"])
def api_bulk_application_operations():
    """
    Performs atomic batch operations on job applications owned by the signed-in user (#848).
    Supported actions: 'status', 'tag', 'delete', 'export'.
    Enforces batch limits and candidate data segregation.
    """
    json_data = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(json_data, dict):
        json_data = {}

    student_id = (
        json_data.get("student_id")
        or request.form.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    action = (json_data.get("action") or request.form.get("action") or "").strip().lower()
    app_ids = json_data.get("application_ids") or request.form.getlist("application_ids")
    if not app_ids:
        raw_ids = (
            json_data.get("app_ids")
            or request.form.get("application_ids")
            or request.form.get("app_ids")
            or request.form.get("ids")
        )
        if isinstance(raw_ids, str):
            app_ids = [aid.strip() for aid in raw_ids.split(",") if aid.strip()]
        elif isinstance(raw_ids, list):
            app_ids = raw_ids

    if not isinstance(app_ids, list) or not app_ids:
        return jsonify({"success": False, "error": "No application IDs provided.", "affected_count": 0}), 400

    if len(app_ids) > MAX_BULK_BATCH_SIZE:
        return jsonify({
            "success": False,
            "error": f"Batch size exceeds maximum limit of {MAX_BULK_BATCH_SIZE} applications.",
            "affected_count": 0
        }), 400

    if action == "status":
        new_status = json_data.get("status") or request.form.get("status")
        if not new_status:
            return jsonify({"success": False, "error": "New status must be provided.", "affected_count": 0}), 400
        result = bulk_update_application_status(app_ids, student_id, new_status)
        return jsonify(result), (200 if result.get("success") else 400)

    elif action == "tag":
        tag = json_data.get("tag") or request.form.get("tag")
        if not tag:
            return jsonify({"success": False, "error": "Tag must be provided.", "affected_count": 0}), 400
        result = bulk_add_application_tag(app_ids, student_id, tag)
        return jsonify(result), (200 if result.get("success") else 400)

    elif action == "delete":
        result = bulk_delete_applications(app_ids, student_id)
        return jsonify(result), (200 if result.get("success") else 400)

    elif action == "export":
        try:
            csv_data = bulk_export_applications_csv(app_ids, student_id)
            safe_student = "".join(c for c in student_id if c.isalnum() or c in ("-", "_"))
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=applications_selected_{safe_student}.csv",
                    "Content-Type": "text/csv; charset=utf-8",
                },
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e), "affected_count": 0}), 400

    else:
        return jsonify({
            "success": False,
            "error": f"Invalid bulk action '{action}'. Supported actions: 'status', 'tag', 'delete', 'export'.",
            "affected_count": 0
        }), 400


@tracker_bp.route("/api/applications/bulk/export", methods=["GET", "POST"])
def api_bulk_export_applications():
    """
    Exports selected job applications to a formula-injection-safe CSV file (#848).
    """
    json_data = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(json_data, dict):
        json_data = {}

    student_id = (
        request.args.get("student_id")
        or json_data.get("student_id")
        or request.form.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    app_ids = (
        json_data.get("application_ids")
        or request.form.getlist("application_ids")
        or request.args.getlist("application_ids")
    )
    if not app_ids:
        raw_ids = (
            request.args.get("ids")
            or request.args.get("application_ids")
            or json_data.get("app_ids")
            or request.form.get("application_ids")
            or request.form.get("ids")
        )
        if isinstance(raw_ids, str):
            app_ids = [aid.strip() for aid in raw_ids.split(",") if aid.strip()]
        elif isinstance(raw_ids, list):
            app_ids = raw_ids

    if not isinstance(app_ids, list) or not app_ids:
        return jsonify({"success": False, "error": "No application IDs provided."}), 400

    if len(app_ids) > MAX_BULK_BATCH_SIZE:
        return jsonify({
            "success": False,
            "error": f"Batch size exceeds maximum limit of {MAX_BULK_BATCH_SIZE} applications."
        }), 400

    try:
        csv_data = bulk_export_applications_csv(app_ids, student_id)
        safe_student = "".join(c for c in student_id if c.isalnum() or c in ("-", "_"))
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=applications_selected_{safe_student}.csv",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@tracker_bp.route("/my-reports")
def user_placement_history_view():
    """
    Save and Retrieve Previous Placement Analysis History View (#149).
    Enforces per-student isolation and allows searching, filtering by company, and retrieving past reports.
    """
    student_id = request.args.get("student_id", "default_student").strip()
    company_filter = request.args.get("company", "").strip()
    sort_by = request.args.get("sort", "date_desc").strip()

    history_data = get_user_placement_history(
        student_id=student_id,
        company_filter=company_filter,
        sort_by=sort_by
    )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "data": history_data
        }), 200

    return render_template(
        "my_reports.html",
        data=history_data,
        student_id=student_id,
        company_filter=company_filter,
        sort_by=sort_by
    )


@tracker_bp.route("/api/user/analyses")
def api_user_placement_analyses():
    """
    API endpoint to retrieve placement analysis history for a specific candidate (#149).
    """
    student_id = request.args.get("student_id", "default_student").strip()
    company_filter = request.args.get("company", "").strip()
    sort_by = request.args.get("sort", "date_desc").strip()

    history_data = get_user_placement_history(
        student_id=student_id,
        company_filter=company_filter,
        sort_by=sort_by
    )
    return jsonify({
        "success": True,
        "data": history_data
    }), 200


@tracker_bp.route("/calendar", methods=["GET", "POST"])
def placement_calendar_view():
    """
    Placement Preparation Calendar View and Event Handler (#190).
    Manages student interview dates, preparation sessions, and application deadlines on a calendar.
    """
    import datetime
    student_id = request.args.get("student_id") or flask_session.get("user_id") or "default_student"
    month_filter = request.args.get("month") or datetime.date.today().strftime("%Y-%m")
    type_filter = request.args.get("type", "All").strip()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        event_type = request.form.get("event_type", "preparation").strip().lower()
        event_date = request.form.get("event_date", "").strip()
        event_time = request.form.get("event_time", "").strip()
        company_name = request.form.get("company_name", "").strip()
        description = request.form.get("description", "").strip()
        priority = request.form.get("priority", "Medium").strip()

        if not title or not event_date:
            flash("Event title and scheduled date are required.", "error")
            return redirect(url_for("placement_calendar_view", month=month_filter))

        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        save_calendar_event(
            event_id=event_id,
            student_id=student_id,
            title=title,
            event_type=event_type,
            event_date=event_date,
            event_time=event_time or None,
            company_name=company_name or None,
            description=description or None,
            priority=priority,
            completed=False
        )
        flash("Placement preparation activity added to your calendar!", "success")
        return redirect(url_for("placement_calendar_view", month=event_date[:7]))

    events = get_calendar_events(student_id=student_id, month=month_filter, event_type=type_filter)
    all_events = get_calendar_events(student_id=student_id)

    # Calculate summary counts
    interviews_count = sum(1 for e in all_events if e.get("event_type") == "interview")
    tasks_count = sum(1 for e in all_events if e.get("event_type") == "preparation")
    deadlines_count = sum(1 for e in all_events if e.get("event_type") == "deadline")

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    pagination = Pagination(
        items=events,
        page=page,
        per_page=per_page,
        endpoint_args=request.args.to_dict(),
        base_url="/calendar"
    )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "month": month_filter,
            "count": len(pagination.items),
            "total": len(events),
            "events": pagination.items,
            "pagination": pagination.to_dict()
        }), 200

    return render_template(
        "calendar.html",
        events=pagination.items,
        all_events=all_events,
        pagination=pagination,
        student_id=student_id,
        current_month=month_filter,
        current_type=type_filter,
        interviews_count=interviews_count,
        tasks_count=tasks_count,
        deadlines_count=deadlines_count,
        today_date=datetime.date.today().isoformat()
    )


@tracker_bp.route("/calendar/export.ics", methods=["GET"])
def export_calendar_ics():
    """Exports selected or all owned preparation events as floating local-time ICS."""
    student_id = flask_session.get("user_id") or flask_session.get("student_id") or "default_student"
    requested_ids = {
        event_id.strip()
        for event_id in request.args.get("event_ids", "").split(",")
        if event_id.strip()
    }
    all_events = get_calendar_events(student_id=student_id)
    if requested_ids:
        events = [event for event in all_events if event.get("id") in requested_ids]
    elif request.args.get("all") == "1":
        events = all_events
    else:
        events = []

    if not events:
        return Response(
            "Select at least one calendar event, or choose export all.",
            status=400,
            mimetype="text/plain"
        )

    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Campus Placement Agent//Preparation Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Placement Preparation",
        "X-WR-CALDESC:Times are floating local times as stored; no timezone conversion is applied.",
    ]
    for event in events:
        event_date = (event.get("event_date") or "").replace("-", "")
        event_time = (event.get("event_time") or "").replace(":", "")
        if len(event_date) != 8:
            continue
        description_parts = []
        if event.get("description"):
            description_parts.append(event["description"])
        if event.get("company_name"):
            description_parts.append(f"Company: {event['company_name']}")
        if event.get("priority"):
            description_parts.append(f"Priority: {event['priority']}")
        description_parts.append("Timezone: floating local time as stored; no timezone conversion.")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{_ics_escape(event.get('id'))}@ai-campus-placement-agent",
            f"DTSTAMP:{now_utc}",
            f"SUMMARY:{_ics_escape(event.get('title'))}",
            f"DESCRIPTION:{_ics_escape(' '.join(description_parts))}",
        ])
        if event_time:
            lines.append(f"DTSTART:{event_date}T{event_time}00")
        else:
            lines.append(f"DTSTART;VALUE=DATE:{event_date}")
        lines.extend([
            f"CATEGORIES:{_ics_escape(event.get('event_type', 'preparation'))}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(
        folded_line
        for line in lines
        for folded_line in _ics_fold_line(line)
    ) + "\r\n"
    response = Response(ics_content, mimetype="text/calendar; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=preparation_calendar.ics"
    return response


@tracker_bp.route("/api/calendar/<event_id>", methods=["PUT", "POST", "DELETE"])
def api_manage_calendar_event(event_id: str):
    """
    API endpoint to update or delete a calendar preparation event (#190).
    """
    student_id = request.args.get("student_id") or flask_session.get("user_id") or "default_student"

    if request.method == "DELETE":
        deleted = delete_calendar_event(event_id=event_id, student_id=student_id)
        return jsonify({"success": deleted}), (200 if deleted else 404)

    payload = request.get_json(silent=True) or {}
    title = payload.get("title") or request.form.get("title", "")
    event_type = payload.get("event_type") or request.form.get("event_type", "preparation")
    event_date = payload.get("event_date") or request.form.get("event_date", "")
    event_time = payload.get("event_time") or request.form.get("event_time", "")
    company_name = payload.get("company_name") or request.form.get("company_name", "")
    description = payload.get("description") or request.form.get("description", "")
    priority = payload.get("priority") or request.form.get("priority", "Medium")
    completed = bool(payload.get("completed", False))

    if not title or not event_date:
        return jsonify({"success": False, "error": "Title and date are required."}), 400

    updated = update_calendar_event(
        event_id=event_id,
        student_id=student_id,
        title=title,
        event_type=event_type,
        event_date=event_date,
        event_time=event_time or None,
        company_name=company_name or None,
        description=description or None,
        priority=priority,
        completed=completed
    )
    return jsonify({"success": updated}), (200 if updated else 404)


@tracker_bp.route("/company-checklist", methods=["GET", "POST"])
def company_checklist_view():
    """
    Company-Wise Placement Preparation Checklist View and Task Updater (#192).
    Provides structured readiness checklists across Aptitude, Coding, Technical, and HR.
    """
    student_id = request.args.get("student_id") or flask_session.get("user_id") or "default_student"
    target_company = request.args.get("company", "Google").strip()

    available_companies = get_available_companies()
    if not available_companies:
        available_companies = ["Google", "Amazon", "Microsoft", "Meta", "TCS", "Infosys", "Wipro", "Cognizant", "Adobe"]

    if target_company not in available_companies and target_company != "Google":
        available_companies.insert(0, target_company)

    checklist_info = get_company_checklist(student_id=student_id, company_name=target_company)

    company_guide = None
    try:
        companies_file = Path(config.BASE_DIR) / "data" / "companies.json"
        if companies_file.exists():
            with open(companies_file, "r", encoding="utf-8") as f:
                comp_data = json.load(f)
                if target_company in comp_data:
                    company_guide = enrich_company_hiring_guide(target_company, comp_data[target_company])
                else:
                    company_guide = enrich_company_hiring_guide(target_company, {})
    except Exception as e:
        logger.warning(f"Error loading company guide for checklist: {e}")
        company_guide = enrich_company_hiring_guide(target_company, {})

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "data": checklist_info,
            "company_guide": company_guide
        }), 200

    return render_template(
        "company_checklist.html",
        data=checklist_info,
        target_company=target_company,
        available_companies=available_companies,
        student_id=student_id,
        company_guide=company_guide
    )


@tracker_bp.route("/api/company-checklist/toggle-task", methods=["POST"])
def api_toggle_checklist_task():
    """
    Toggles a checklist task completed status and updates overall progress (#192).
    """
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    company_name = payload.get("company_name", "Google").strip()
    category = payload.get("category", "").strip()
    task_id = payload.get("task_id", "").strip()
    completed = bool(payload.get("completed", False))

    current_data = get_company_checklist(student_id, company_name)
    checklist = current_data["checklist"]

    if category in checklist:
        for t in checklist[category]:
            if t["id"] == task_id:
                t["completed"] = completed
                break

    
    if completed:
        try:
            record_preparation_activity(
                student_id=student_id,
                activity_type="checklist_task",
                metadata={"company": company_name, "task_id": task_id, "category": category}
            )
        except Exception as e_streak:
            logger.warning(f"Could not record streak for checklist task: {e_streak}")

    save_company_checklist(student_id, company_name, checklist)
    updated_info = get_company_checklist(student_id, company_name)

    return jsonify({
        "success": True,
        "overall_percentage": updated_info["overall_percentage"],
        "completed_tasks": updated_info["completed_tasks"],
        "category_percentage": updated_info["category_stats"].get(category, {}).get("percentage", 0.0)
    }), 200


@tracker_bp.route("/api/company-checklist/reset", methods=["POST"])
def api_reset_checklist():
    """
    Resets the checklist tasks for a specific target company (#192).
    """
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    company_name = payload.get("company_name", "Google").strip()

    reset_company_checklist(student_id, company_name)
    refreshed = get_company_checklist(student_id, company_name)
    return jsonify({
        "success": True,
        "message": f"Checklist for {company_name} reset successfully.",
        "data": refreshed
    }), 200


from utils.drive_day_checklist import (
    get_drive_day_checklist,
    save_drive_day_checklist,
    toggle_checklist_item,
    add_checklist_item,
    rename_checklist_item,
    delete_checklist_item,
    reset_drive_day_checklist,
    switch_drive_mode,
    calculate_checklist_progress,
    get_default_checklist,
    get_dashboard_drive_reminder,
)


def _verify_drive_day_application_ownership(
    student_id: str, application_id: Optional[str]
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Tuple[Dict[str, Any], int]]]:
    """
    Verifies that the target application_id exists and is owned by the student (#651).
    Returns: (is_valid, application_record, error_tuple)
    """
    clean_id = (application_id or "").strip()
    if not clean_id:
        return True, None, None

    # Check if application belongs to the student
    app_record = get_job_application_by_id(clean_id, student_id)
    if app_record:
        return True, app_record, None

    # Check if application exists for another student
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT student_id FROM job_applications WHERE id = ?", (clean_id,))
        row = cursor.fetchone()
        if row:
            if row["student_id"] != student_id:
                return False, None, ({"success": False, "error": "Access denied: application does not belong to you."}, 403)
            return True, None, None
        return False, None, ({"success": False, "error": "Application not found."}, 404)
    finally:
        conn.close()


@tracker_bp.route("/drive-day-checklist", methods=["GET"])
def drive_day_checklist_view():
    """
    Drive Day Practical Readiness Checklist View (#651).
    Displays categorized checklist across Documents, Technical Setup, Logistics, and Mindset
    for Offline and Online recruitment drives with tick-off progress and application binding.
    """
    student_id = request.args.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (request.args.get("application_id") or "").strip()
    drive_mode = request.args.get("mode")

    attached_app = None
    if application_id:
        valid, app_rec, err = _verify_drive_day_application_ownership(student_id, application_id)
        if not valid:
            status_code = err[1] if err else 403
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
                return jsonify(err[0]), status_code
            if status_code == 403:
                abort(403)
            flash("Target application record not found.", "warning")
            return redirect(url_for("tracker.drive_day_checklist_view"))
        attached_app = app_rec

    checklist_info = get_drive_day_checklist(
        student_id=student_id,
        application_id=application_id if attached_app else None,
        drive_mode=drive_mode,
    )
    user_apps = get_job_applications(student_id=student_id)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "success": True,
            "checklist": checklist_info,
            "attached_application": attached_app,
        }), 200

    return render_template(
        "drive_day_checklist.html",
        checklist=checklist_info,
        attached_application=attached_app,
        applications=user_apps,
        selected_application_id=application_id if attached_app else "",
        active_mode=checklist_info.get("drive_mode", "offline"),
        student_id=student_id,
    )


@tracker_bp.route("/api/drive-day-checklist/toggle", methods=["POST"])
def api_toggle_drive_day_checklist():
    """Toggles completion state of a drive day checklist item (#651)."""
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (payload.get("application_id") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()
    completed = bool(payload.get("completed", False))

    if not item_id:
        return jsonify({"success": False, "error": "Item ID is required."}), 400

    valid, _, err = _verify_drive_day_application_ownership(student_id, application_id)
    if not valid:
        return jsonify(err[0]), err[1]

    try:
        updated = toggle_checklist_item(
            student_id=student_id,
            application_id=application_id or None,
            item_id=item_id,
            completed=completed,
        )
        return jsonify({"success": True, "data": updated}), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error toggling drive day checklist item: {e}")
        return jsonify({"success": False, "error": "Internal error toggling item."}), 500


@tracker_bp.route("/api/drive-day-checklist/item", methods=["POST"])
def api_add_drive_day_checklist_item():
    """Adds a new custom item to a checklist group (#651)."""
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (payload.get("application_id") or "").strip()
    category = (payload.get("category") or "documents").strip()
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"success": False, "error": "Item description is required."}), 400

    valid, _, err = _verify_drive_day_application_ownership(student_id, application_id)
    if not valid:
        return jsonify(err[0]), err[1]

    try:
        updated = add_checklist_item(
            student_id=student_id,
            application_id=application_id or None,
            category=category,
            text=text,
        )
        return jsonify({"success": True, "data": updated}), 201
    except Exception as e:
        logger.error(f"Error adding drive day checklist item: {e}")
        return jsonify({"success": False, "error": "Internal error adding item."}), 500


@tracker_bp.route("/api/drive-day-checklist/item", methods=["PUT"])
@tracker_bp.route("/api/drive-day-checklist/rename", methods=["POST"])
def api_rename_drive_day_checklist_item():
    """Renames an existing drive day checklist item (#651)."""
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (payload.get("application_id") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()
    text = (payload.get("text") or "").strip()

    if not item_id or not text:
        return jsonify({"success": False, "error": "Item ID and new description are required."}), 400

    valid, _, err = _verify_drive_day_application_ownership(student_id, application_id)
    if not valid:
        return jsonify(err[0]), err[1]

    try:
        updated = rename_checklist_item(
            student_id=student_id,
            application_id=application_id or None,
            item_id=item_id,
            new_text=text,
        )
        return jsonify({"success": True, "data": updated}), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error renaming drive day checklist item: {e}")
        return jsonify({"success": False, "error": "Internal error renaming item."}), 500


@tracker_bp.route("/api/drive-day-checklist/item", methods=["DELETE"])
@tracker_bp.route("/api/drive-day-checklist/delete", methods=["POST"])
def api_delete_drive_day_checklist_item():
    """Deletes an item from the drive day checklist (#651)."""
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (payload.get("application_id") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()

    if not item_id:
        return jsonify({"success": False, "error": "Item ID is required."}), 400

    valid, _, err = _verify_drive_day_application_ownership(student_id, application_id)
    if not valid:
        return jsonify(err[0]), err[1]

    try:
        updated = delete_checklist_item(
            student_id=student_id,
            application_id=application_id or None,
            item_id=item_id,
        )
        return jsonify({"success": True, "data": updated}), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error deleting drive day checklist item: {e}")
        return jsonify({"success": False, "error": "Internal error deleting item."}), 500


@tracker_bp.route("/api/drive-day-checklist/reset", methods=["POST"])
def api_reset_drive_day_checklist():
    """Resets the drive day checklist to default items for specified drive mode (#651)."""
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (payload.get("application_id") or "").strip()
    mode = (payload.get("mode") or "offline").strip().lower()

    valid, _, err = _verify_drive_day_application_ownership(student_id, application_id)
    if not valid:
        return jsonify(err[0]), err[1]

    try:
        updated = reset_drive_day_checklist(
            student_id=student_id,
            application_id=application_id or None,
            drive_mode=mode,
        )
        return jsonify({"success": True, "message": "Checklist reset to defaults.", "data": updated}), 200
    except Exception as e:
        logger.error(f"Error resetting drive day checklist: {e}")
        return jsonify({"success": False, "error": "Internal error resetting checklist."}), 500


@tracker_bp.route("/api/drive-day-checklist/mode", methods=["POST"])
def api_switch_drive_day_checklist_mode():
    """Switches drive mode (offline or online) for the checklist (#651)."""
    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id") or flask_session.get("user_id") or "default_student"
    application_id = (payload.get("application_id") or "").strip()
    mode = (payload.get("mode") or "offline").strip().lower()
    reload_defaults = bool(payload.get("reload_defaults", False))

    valid, _, err = _verify_drive_day_application_ownership(student_id, application_id)
    if not valid:
        return jsonify(err[0]), err[1]

    try:
        updated = switch_drive_mode(
            student_id=student_id,
            application_id=application_id or None,
            new_drive_mode=mode,
            reload_defaults=reload_defaults,
        )
        return jsonify({"success": True, "data": updated}), 200
    except Exception as e:
        logger.error(f"Error switching drive day checklist mode: {e}")
        return jsonify({"success": False, "error": "Internal error switching mode."}), 500



@tracker_bp.route("/applications/import/preview", methods=["POST"])
def preview_application_import():
    student_id = _application_import_student_id()
    try:
        headers, rows = _read_application_import_csv(request.files.get("csv_file"))
        mapping = _suggest_application_import_mapping(headers)
        preview_rows = _build_application_import_preview(student_id, headers, rows, mapping)
        now = time.time()
        for stale_token, stale_preview in list(_APPLICATION_IMPORT_PREVIEWS.items()):
            if now - stale_preview.get("created_at", now) > 1800:
                _APPLICATION_IMPORT_PREVIEWS.pop(stale_token, None)
        token = uuid.uuid4().hex
        _APPLICATION_IMPORT_PREVIEWS[token] = {
            "student_id": student_id,
            "headers": headers,
            "rows": rows,
            "created_at": now,
        }
        return render_template(
            "application_import.html",
            token=token,
            student_id=student_id,
            headers=headers,
            mapping=mapping,
            preview_rows=preview_rows,
            import_fields=APPLICATION_IMPORT_FIELDS,
            valid_rows=sum(1 for row in preview_rows if not row["errors"]),
            invalid_rows=sum(1 for row in preview_rows if row["errors"]),
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("job_applications_tracker", student_id=student_id))


@tracker_bp.route("/applications/import/confirm", methods=["POST"])
def confirm_application_import():
    student_id = _application_import_student_id()
    token = request.form.get("token", "").strip()
    preview = _APPLICATION_IMPORT_PREVIEWS.pop(token, None)
    if not preview or preview["student_id"] != student_id or time.time() - preview["created_at"] > 1800:
        flash("This import preview has expired. Please upload the CSV again.", "danger")
        return redirect(url_for("job_applications_tracker", student_id=student_id))

    mapping = {field: request.form.get(f"mapping_{field}", "").strip() for field in APPLICATION_IMPORT_FIELDS}
    preview_rows = _build_application_import_preview(student_id, preview["headers"], preview["rows"], mapping)
    existing_apps = get_job_applications(student_id=student_id)
    imported = 0
    skipped = []
    for row in preview_rows:
        if row["errors"]:
            skipped.append({"row_number": row["row_number"], "errors": row["errors"]})
            continue
        values = row["values"]
        save_job_application(
            app_id=f"app-{uuid.uuid4().hex[:12]}",
            student_id=student_id,
            company_name=values["company_name"],
            role_title=values["role_title"],
            status=values["status"] or "Applied",
            application_date=values["application_date"] or None,
            job_url=values["job_url"],
            job_description=values["job_description"],
            notes=values["notes"],
            location=values["location"] or "Remote",
            job_type=values["job_type"] or "Full-Time",
            experience_level=values["experience_level"] or "Entry-Level",
        )
        existing_apps.append(values)
        imported += 1

    return render_template(
        "application_import_result.html",
        imported=imported,
        skipped=skipped,
        student_id=student_id,
    )


@tracker_bp.route("/api/applications/<app_id>", methods=["GET", "PUT", "POST"])
def api_application_detail_operations(app_id: str):
    """
    Retrieves, updates, or deletes a specific candidate application record.
    """
    json_data = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(json_data, dict):
        json_data = {}
    student_id = (
        request.args.get("student_id")
        or json_data.get("student_id")
        or request.form.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    if request.method == "GET":
        app_record = get_job_application_by_id(app_id, student_id)
        if not app_record:
            return jsonify({"success": False, "error": "Application not found or unauthorized."}), 404
        return jsonify({"success": True, "application": app_record}), 200

    if request.method in ["PUT", "POST"]:
        payload = request.get_json(silent=True) or request.form
        existing = get_job_application_by_id(app_id, student_id)
        if not existing:
            return jsonify({"success": False, "error": "Application not found or unauthorized."}), 404

        company = (payload.get("company_name") or payload.get("company") or existing["company_name"]).strip()
        role = (payload.get("role_title") or payload.get("role") or existing["role_title"]).strip()
        raw_status = (payload.get("status") or existing["status"]).strip()
        status = "Selected" if raw_status == "Offered" else raw_status
        if status not in APPLICATION_STAGES:
            status = existing["status"]

        app_date = payload.get("application_date") or existing.get("application_date")
        job_url = payload.get("job_url") if payload.get("job_url") is not None else existing.get("job_url")
        resume_link_supplied = "resume_analysis_id" in payload
        resume_analysis_id = (payload.get("resume_analysis_id") or "").strip()
        resume_snapshot = None
        if resume_link_supplied and resume_analysis_id:
            resume_snapshot = _resume_link_snapshot(student_id, resume_analysis_id)
            if not resume_snapshot:
                return jsonify({
                    "success": False,
                    "error": "The selected resume version is not available for this account."
                }), 400
        job_description = payload.get("job_description")
        if job_description is None:
            job_description = existing.get("job_description_snapshot") or existing.get("job_description") or ""
        if resume_snapshot:
            job_description = resume_snapshot["job_description"]
        notes = payload.get("notes") if payload.get("notes") is not None else existing.get("notes")
        location = payload.get("location") or existing.get("location") or "Remote"
        job_type = payload.get("job_type") or existing.get("job_type") or "Full-Time"
        exp_level = payload.get("experience_level") or existing.get("experience_level") or "Entry-Level"
        deadline_date = payload.get("deadline_date") if "deadline_date" in payload else existing.get("deadline_date")
        if isinstance(deadline_date, str):
            deadline_date = deadline_date.strip() or None

        updated = update_job_application(
            app_id=app_id,
            student_id=student_id,
            company_name=company,
            role_title=role,
            status=status,
            application_date=app_date,
            job_url=job_url,
            job_description=job_description,
            notes=notes,
            location=location,
            job_type=job_type,
            experience_level=exp_level,
            resume_analysis_id=(
                resume_snapshot["id"] if resume_snapshot
                else (None if resume_link_supplied else existing.get("resume_analysis_id"))
            ),
            resume_filename=(
                resume_snapshot["filename"] if resume_snapshot
                else (None if resume_link_supplied else existing.get("resume_filename"))
            ),
            resume_version=(
                resume_snapshot["version"] if resume_snapshot
                else (None if resume_link_supplied else existing.get("resume_version"))
            ),
            job_description_snapshot=job_description or None,
            deadline_date=deadline_date
        )
        if updated:
            refreshed = get_job_application_by_id(app_id, student_id)
            return jsonify({
                "success": True,
                "message": "Application updated successfully.",
                "application": refreshed
            }), 200
        return jsonify({"success": False, "error": "Failed to update application."}), 500

    if request.method == "DELETE":
        deleted = delete_job_application(app_id=app_id, student_id=student_id)
        return jsonify({"success": deleted}), (200 if deleted else 404)


@tracker_bp.route("/api/applications/<app_id>/follow-up-note", methods=["GET", "POST"])
def api_application_follow_up_note(app_id: str):
    """
    Generates a tailored post-interview thank-you, follow-up, or offer acknowledgement note (#847).
    Uses the company name from the tracked application and the single detail provided by the student.
    Drafts are strictly under 150 words and are never sent automatically.
    """
    json_data = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(json_data, dict):
        json_data = {}

    student_id = (
        request.args.get("student_id")
        or json_data.get("student_id")
        or request.form.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    app_record = get_job_application_by_id(app_id, student_id)
    if not app_record:
        return jsonify({"success": False, "error": "Application not found or unauthorized."}), 404

    note_type = (
        request.args.get("type")
        or request.args.get("note_type")
        or json_data.get("type")
        or json_data.get("note_type")
        or request.form.get("type")
        or request.form.get("note_type")
        or "thank_you"
    )

    detail = (
        request.args.get("detail")
        or request.args.get("interview_detail")
        or json_data.get("detail")
        or json_data.get("interview_detail")
        or request.form.get("detail")
        or request.form.get("interview_detail")
        or ""
    )

    company_name = app_record.get("company_name") or "Target Company"
    role_title = app_record.get("role_title") or ""

    agent = FollowUpNoteAgent()
    note_payload = agent.generate_note(
        company_name=company_name,
        note_type=note_type,
        interview_detail=detail,
        role_title=role_title,
    )
    note_payload["application_id"] = app_id
    note_payload["auto_send"] = False  # Strictly no auto-send

    return jsonify(note_payload), 200


@tracker_bp.route("/api/applications/follow-up-note", methods=["POST"])
def api_generic_follow_up_note():
    """
    Direct follow-up note generator by application_id or company_name (#847).
    """
    json_data = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(json_data, dict):
        json_data = {}

    student_id = (
        json_data.get("student_id")
        or request.form.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    app_id = (json_data.get("application_id") or json_data.get("app_id") or request.form.get("application_id") or "").strip()
    company_name = (json_data.get("company_name") or request.form.get("company_name") or "").strip()
    role_title = (json_data.get("role_title") or request.form.get("role_title") or "").strip()

    if app_id:
        app_record = get_job_application_by_id(app_id, student_id)
        if app_record:
            company_name = app_record.get("company_name") or company_name
            role_title = app_record.get("role_title") or role_title
        elif not company_name:
            return jsonify({"success": False, "error": "Application not found or unauthorized."}), 404

    if not company_name:
        return jsonify({"success": False, "error": "Company name or valid application ID is required."}), 400

    note_type = json_data.get("type") or json_data.get("note_type") or request.form.get("type") or "thank_you"
    detail = json_data.get("detail") or json_data.get("interview_detail") or request.form.get("detail") or ""

    agent = FollowUpNoteAgent()
    note_payload = agent.generate_note(
        company_name=company_name,
        note_type=note_type,
        interview_detail=detail,
        role_title=role_title,
    )
    if app_id:
        note_payload["application_id"] = app_id
    note_payload["auto_send"] = False

    return jsonify(note_payload), 200


@tracker_bp.route("/api/export/csv/<data_type>", methods=["GET"])
def api_export_csv(data_type):
    """
    Export student placement preparation data to CSV (#274).
    Supported types: applications, interviews, notes, coding.
    """
    from utils.csv_export_service import get_csv_export
    student_id = flask_session.get("user_id") or "default_student"
    try:
        csv_data, filename = get_csv_export(data_type, student_id)
        response = Response(csv_data, mimetype="text/csv")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error generating CSV export for {data_type}: {e}")
        return jsonify({"success": False, "error": "Internal error generating CSV export."}), 500


@tracker_bp.route("/api/reminders/deadlines", methods=["GET", "POST"])
def student_deadline_reminders_endpoint():
    """
    GET: Scans and retrieves all active upcoming job application and interview deadline reminders.
    POST: Triggers an on-demand scan and synchronization of reminders for the candidate.
    """
    student_id = flask_session.get("user_id") or "default_student"
    urgency_filter = request.args.get("urgency")
    include_acknowledged = request.args.get("include_acknowledged", "false").lower() == "true"

    if request.method == "POST":
        reminders = scan_and_generate_student_reminders(student_id)
        return jsonify({
            "status": "success",
            "message": f"Scanned and updated deadline reminders for student '{student_id}'.",
            "count": len(reminders),
            "reminders": reminders
        }), 200

    # GET: Scan and fetch
    scan_and_generate_student_reminders(student_id)
    reminders = list_student_reminders(
        student_id=student_id,
        include_acknowledged=include_acknowledged,
        urgency_filter=urgency_filter
    )
    return jsonify({
        "status": "success",
        "student_id": student_id,
        "count": len(reminders),
        "reminders": reminders
    }), 200


@tracker_bp.route("/api/reminders/deadlines/<reminder_id>/acknowledge", methods=["POST"])
def acknowledge_deadline_reminder_endpoint(reminder_id: str):
    """Marks an upcoming deadline reminder as acknowledged/dismissed."""
    student_id = flask_session.get("user_id") or "default_student"
    success = acknowledge_deadline_reminder(student_id=student_id, reminder_id=reminder_id)
    if not success:
        return jsonify({"status": "error", "message": "Reminder not found or already acknowledged."}), 404
    return jsonify({
        "status": "success",
        "message": "Deadline reminder acknowledged successfully."
    }), 200


@tracker_bp.route("/api/reminders/preferences", methods=["GET", "PUT", "PATCH"])
def student_reminder_preferences_endpoint():
    """
    GET: Retrieves student's deadline reminder preferences.
    PUT/PATCH: Updates alert lead days, email toggle, dashboard alert settings.
    """
    student_id = flask_session.get("user_id") or "default_student"

    if request.method == "GET":
        prefs = get_student_deadline_preferences(student_id)
        return jsonify({
            "status": "success",
            "preferences": prefs
        }), 200

    payload = request.get_json(silent=True) or request.form.to_dict()
    updated = update_student_deadline_preferences(
        student_id=student_id,
        alert_lead_days=int(payload["alert_lead_days"]) if "alert_lead_days" in payload else None,
        email_alerts_enabled=payload.get("email_alerts_enabled") in (True, "true", "1") if "email_alerts_enabled" in payload else None,
        dashboard_alerts_enabled=payload.get("dashboard_alerts_enabled") in (True, "true", "1") if "dashboard_alerts_enabled" in payload else None,
        urgency_threshold_hours=int(payload["urgency_threshold_hours"]) if "urgency_threshold_hours" in payload else None
    )
    return jsonify({
        "status": "success",
        "message": "Updated deadline reminder preferences.",
        "preferences": updated
    }), 200


@tracker_bp.route("/api/reminders/send-upcoming", methods=["POST"])
@tracker_bp.route("/api/reminders/send", methods=["POST"])
def api_send_upcoming_deadline_reminders():
    """
    API endpoint to trigger upcoming application & interview deadline reminders (#298).
    Can be invoked by external cron schedulers or administrative tasks.
    """
    payload = request.get_json(silent=True) or {}
    student_id = request.args.get("student_id") or payload.get("student_id")
    result = send_upcoming_deadline_reminders(student_id=student_id)
    return jsonify(result), 200


@tracker_bp.route("/api/offers", methods=["GET", "POST"])
def api_offers_list_create():
    """
    List and create private student job offers (#330).
    """
    from utils.offer_comparison_service import get_student_offers, create_job_offer
    student_id = flask_session.get("user_id") or "default_student"

    if request.method == "GET":
        offers = get_student_offers(student_id)
        return jsonify({"success": True, "offers": offers}), 200

    payload = request.get_json(silent=True) or request.form
    try:
        res = create_job_offer(
            student_id=student_id,
            company_name=payload.get("company_name", ""),
            role_title=payload.get("role_title", ""),
            base_salary=float(payload.get("base_salary", 0)),
            bonus=float(payload.get("bonus", 0)),
            equity_annual=float(payload.get("equity_annual", 0)),
            location=payload.get("location", "Remote"),
            work_mode=payload.get("work_mode", "Hybrid"),
            benefits_summary=payload.get("benefits_summary", ""),
            deadline_date=payload.get("deadline_date"),
            notes=payload.get("notes", "")
        )
        return jsonify(res), 201
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error creating job offer: {e}")
        return jsonify({"success": False, "error": "Internal error creating offer."}), 500


@tracker_bp.route("/api/offers/compare", methods=["POST"])
def api_offers_compare():
    """
    Compare selected private student job offers side-by-side (#330).
    """
    from utils.offer_comparison_service import compare_job_offers
    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form
    offer_ids = payload.get("offer_ids") or []
    if isinstance(offer_ids, str):
        offer_ids = [o.strip() for o in offer_ids.split(",") if o.strip()]

    try:
        comparison = compare_job_offers(student_id, offer_ids)
        return jsonify(comparison), 200
    except (KeyError, ValueError) as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error comparing job offers: {e}")
        return jsonify({"success": False, "error": "Internal error comparing offers."}), 500


@tracker_bp.route("/offers/compare", methods=["GET"])
def offers_compare_page():
    """
    Render private job offers comparison view (#330).
    """
    from utils.offer_comparison_service import get_student_offers
    student_id = flask_session.get("user_id") or "default_student"
    offers = get_student_offers(student_id)
    return render_template("offer_comparison.html", student_id=student_id, offers=offers)


@tracker_bp.route("/api/calendar/timezone", methods=["GET", "PUT"])
def api_calendar_timezone():
    """Retrieve or update the configured timezone metadata for the logged-in student."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    if request.method == "GET":
        try:
            from utils.calendar_timezone_service import get_student_timezone_info
            info = get_student_timezone_info(user_id)
            return jsonify({
                "success": True,
                "timezone": info
            }), 200
        except Exception as e:
            logger.error(f"Error fetching timezone settings: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    payload = request.get_json(silent=True) or {}
    tz_id = payload.get("timezone_id")
    auto_detect = bool(payload.get("auto_detect", False))

    if not tz_id or not isinstance(tz_id, str):
        return jsonify({"success": False, "error": "timezone_id must be a non-empty string"}), 400

    from utils.calendar_timezone_service import is_valid_timezone, set_student_timezone
    if not is_valid_timezone(tz_id):
        return jsonify({
            "success": False,
            "error": f"Invalid IANA timezone identifier: {tz_id}"
        }), 400

    try:
        updated_info = set_student_timezone(user_id, tz_id, auto_detect=auto_detect)
        return jsonify({
            "success": True,
            "message": f"Timezone successfully updated to {tz_id}",
            "timezone": updated_info
        }), 200
    except Exception as e:
        logger.error(f"Error updating timezone settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@tracker_bp.route("/api/calendar/convert-time", methods=["POST"])
def api_convert_calendar_time():
    """Utility endpoint to convert local wall-clock event time to UTC or vice versa."""
    payload = request.get_json(silent=True) or {}
    direction = payload.get("direction", "local_to_utc")
    
    from utils.calendar_timezone_service import convert_local_to_utc, convert_utc_to_local
    try:
        if direction == "local_to_utc":
            date_str = payload.get("date", "")
            time_str = payload.get("time", "00:00")
            tz_id = payload.get("timezone_id", "UTC")
            res = convert_local_to_utc(date_str, time_str, tz_id)
            return jsonify({"success": True, "result": res}), 200
        elif direction == "utc_to_local":
            utc_str = payload.get("utc_timestamp", "")
            tz_id = payload.get("timezone_id", "UTC")
            res = convert_utc_to_local(utc_str, tz_id)
            return jsonify({"success": True, "result": res}), 200
        else:
            return jsonify({"success": False, "error": "direction must be 'local_to_utc' or 'utc_to_local'"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@tracker_bp.route("/ctc-calculator", methods=["GET"])
def ctc_calculator_view():
    """
    Renders the CTC to In-Hand Salary and Multi-Offer Comparison Calculator (#428).
    Loads configurable tax slabs, deductions, and assumptions from data/salary_calculator_assumptions.json.
    """
    from utils.ctc_calculator_service import load_calculator_assumptions
    assumptions = load_calculator_assumptions()
    return render_template("ctc_calculator.html", assumptions=assumptions)


@tracker_bp.route("/api/ctc-calculator/compare", methods=["POST"])
def api_ctc_calculator_compare():
    """
    Validates salary components and returns comparative in-hand take-home metrics (#428).
    Rejects negative amounts, invalid data types, and computes side-by-side breakdowns.
    """
    payload = request.get_json(silent=True) or {}
    offers_data = payload.get("offers")

    if not offers_data or not isinstance(offers_data, list):
        return jsonify({
            "success": False,
            "error": "Offers list is required. Please provide up to three offer payloads."
        }), 400

    from utils.ctc_calculator_service import compare_offers
    try:
        comparison_result = compare_offers(offers_data)
        return jsonify(comparison_result), 200
    except ValueError as val_err:
        return jsonify({
            "success": False,
            "error": str(val_err)
        }), 400
    except Exception as exc:
        logger.error(f"Unexpected error in /api/ctc-calculator/compare: {exc}")
        return jsonify({
            "success": False,
            "error": "Failed to calculate salary offer comparison. Please review your inputs."
        }), 500


# =====================================================================
# APPLICATION OFFER DECISION NOTES ROUTES (#515)
# =====================================================================

@tracker_bp.route("/tracker/offer-notes", methods=["GET"])
def offer_decision_notes_view():
    """Renders private offer decision notes comparison matrix (#515)."""
    from utils.offer_decision_notes_service import OfferDecisionNotesService
    student_id = flask_session.get("user_id") or "default_student"

    offers = OfferDecisionNotesService.list_offer_notes(student_id)
    return render_template("offer_decision_notes.html", offers=offers)


@tracker_bp.route("/api/offer-notes", methods=["GET"])
def api_list_offer_notes():
    """Lists all private offer decision records for the student (#515)."""
    from utils.offer_decision_notes_service import OfferDecisionNotesService
    student_id = flask_session.get("user_id") or "default_student"

    offers = OfferDecisionNotesService.list_offer_notes(student_id)
    return jsonify({"status": "success", "count": len(offers), "offers": offers}), 200


@tracker_bp.route("/api/offer-notes", methods=["POST"])
def api_create_offer_note():
    """Creates a new private offer decision note (#515)."""
    from utils.offer_decision_notes_service import OfferDecisionNotesService
    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form.to_dict()

    try:
        note = OfferDecisionNotesService.save_offer_notes(
            user_id=student_id,
            company_name=payload.get("company_name", ""),
            role_title=payload.get("role_title", ""),
            ctc_lpa=float(payload.get("ctc_lpa", 0.0)),
            work_location=payload.get("work_location", "Hybrid"),
            bond_terms=payload.get("bond_terms", "None"),
            bond_duration_months=int(payload.get("bond_duration_months", 0)),
            probation_period_months=int(payload.get("probation_period_months", 3)),
            travel_required_pct=int(payload.get("travel_required_pct", 0)),
            growth_potential_rating=int(payload.get("growth_potential_rating", 3)),
            work_life_balance_rating=int(payload.get("work_life_balance_rating", 3)),
            culture_and_team_notes=payload.get("culture_and_team_notes", ""),
            concerns_and_risks=payload.get("concerns_and_risks", ""),
            hr_contact_info=payload.get("hr_contact_info", ""),
            offer_deadline=payload.get("offer_deadline"),
            joining_date=payload.get("joining_date"),
            decision_status=payload.get("decision_status", "under_review"),
            decision_rationale=payload.get("decision_rationale", ""),
            application_id=payload.get("application_id"),
            note_id=payload.get("note_id")
        )
        return jsonify({"status": "success", "offer_note": note}), 201
    except ValueError as ve:
        return jsonify({"success": False, "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error in bulk application action: {e}")
        return jsonify({"success": False, "message": "Bulk action failed."}), 500


@tracker_bp.route("/api/offer-notes/<int:note_id>", methods=["DELETE"])
def api_delete_offer_note(note_id):
    """Deletes an offer decision note (#515)."""
    from utils.offer_decision_notes_service import OfferDecisionNotesService
    student_id = flask_session.get("user_id") or "default_student"

    deleted = OfferDecisionNotesService.delete_offer_note(student_id, note_id)
    if deleted:
        return jsonify({"status": "success", "message": "Offer decision note removed"}), 200
    return jsonify({"status": "not_found", "message": "Offer note not found"}), 404


# ==============================================================================
# Placement Readiness Activity Timeline & Audit Trail (#timeline)
# ==============================================================================

@tracker_bp.route("/timeline", methods=["GET"])
def placement_activity_timeline_view():
    """
    Candidate Placement Readiness Activity Timeline view (#timeline).
    Presents an interactive chronological timeline across:
    - Resume Revisions
    - Practice Sessions
    - Applications
    - Interviews
    - Feedback
    - Achievements & Milestones
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    category_filter = request.args.get("category", "all").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (ValueError, TypeError):
        limit = 50

    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    timeline_data = get_placement_activity_timeline(
        student_id=student_id,
        category=category_filter,
        limit=limit,
        offset=offset,
        start_date=start_date,
        end_date=end_date,
    )

    is_api = (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )
    if is_api:
        return jsonify({"status": "success", "data": timeline_data}), 200

    return render_template(
        "timeline.html",
        timeline_data=timeline_data,
        student_id=student_id,
        active_category=category_filter,
    )


@tracker_bp.route("/api/timeline", methods=["GET", "POST"])
def api_placement_activity_timeline():
    """
    REST API endpoint for placement readiness activity timeline.
    GET: Retrieve filtered chronological activity items and effort summary.
    POST: Log a new custom activity or milestone event.
    """
    if request.method == "GET":
        student_id = (
            request.args.get("student_id")
            or flask_session.get("user_id")
            or flask_session.get("student_id")
            or "default_student"
        ).strip()

        category = request.args.get("category", "all").strip()
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 200))
        except (ValueError, TypeError):
            limit = 50

        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except (ValueError, TypeError):
            offset = 0

        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        data = get_placement_activity_timeline(
            student_id=student_id,
            category=category,
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
        )
        return jsonify({"status": "success", "data": data}), 200

    # POST: Log milestone or custom activity
    payload = request.get_json(silent=True) or request.form or {}
    student_id = (
        payload.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"status": "error", "message": "Activity title is required."}), 400

    category = (payload.get("category") or "achievements").strip()
    description = (payload.get("description") or "").strip()
    event_date = payload.get("event_date")
    is_milestone = payload.get("is_milestone")
    if is_milestone is None:
        is_milestone = 1 if category in ("achievements", "milestone", "milestones") else 0
    else:
        is_milestone = 1 if str(is_milestone).lower() in ("1", "true", "yes") else 0

    metadata = payload.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    elif not isinstance(metadata, dict):
        metadata = {}

    try:
        new_event = log_placement_activity(
            student_id=student_id,
            category=category,
            title=title,
            description=description,
            event_date=event_date,
            metadata=metadata,
            is_milestone=is_milestone,
        )
        return jsonify({
            "status": "success",
            "message": "Placement activity logged successfully.",
            "event": new_event,
        }), 201
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"Failed to log placement activity for student {student_id}: {e}")
        return jsonify({"status": "error", "message": "Failed to log activity."}), 500


@tracker_bp.route("/api/timeline/summary", methods=["GET"])
def api_placement_timeline_summary():
    """Returns effort summary metrics across categories and readiness stages."""
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    ).strip()

    summary = get_readiness_effort_summary(student_id=student_id)
    return jsonify({"status": "success", "summary": summary}), 200




