"""
Jobs feature area routes & endpoints (#553).
Handles job recommendations, company explorer, internship feeds, and profile analyzers.
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
from utils.interview_faq_service import get_frequently_asked_questions
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

jobs_bp = Blueprint("jobs", __name__)

@jobs_bp.route("/api/company/<company_name>")
def get_company_profile(company_name: str):
    """
    Returns company hiring details from data/companies.json for the preview explorer modal.
    Enriched with source metadata, verification status, freshness indicators,
    and frequently asked interview questions (#694).
    """
    companies_file = Path(config.BASE_DIR) / "data" / "companies.json"
    try:
        faqs = get_frequently_asked_questions(company_name)
        if companies_file.exists():
            with open(companies_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if company_name in data:
                    raw_details = data[company_name]
                    enriched_details = enrich_company_hiring_guide(company_name, raw_details)
                    enriched_details["frequently_asked_questions"] = faqs
                    return jsonify({
                        "success": True,
                        "company": company_name,
                        "details": enriched_details,
                        "frequently_asked_questions": faqs,
                    })
    except Exception as e:
        logger.error(f"Error fetching company details for {company_name}: {e}")
    return jsonify({"success": False, "error": f"Profile for '{company_name}' not found."}), 404


@jobs_bp.route("/api/company/<company_name>/eligibility", methods=["POST", "GET"])
def api_calculate_company_eligibility(company_name: str):
    """
    Calculates candidate eligibility against verified company rules (#603, #844).
    Private to student, supports optional CGPA, backlogs, branch, passing year.
    Defaults to logged in student profile if fields not passed.
    """
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    
    # Check session for default student profile if not fully passed
    student_id = flask_session.get("user_id") or flask_session.get("student_id") or "default_student"
    from db.coding_practice import get_student_profile
    profile_rec = get_student_profile(student_id) or {}

    student_profile = {
        "cgpa": payload.get("cgpa") if payload.get("cgpa") is not None else profile_rec.get("cgpa"),
        "backlogs": payload.get("backlogs") if payload.get("backlogs") is not None else profile_rec.get("backlogs", 0),
        "passing_year": payload.get("passing_year") if payload.get("passing_year") is not None else profile_rec.get("grad_year"),
        "branch": payload.get("branch") if payload.get("branch") is not None else (profile_rec.get("department") or ""),
        "department": payload.get("branch") if payload.get("branch") is not None else (profile_rec.get("department") or "")
    }

    from utils.company_eligibility_service import calculate_company_eligibility
    try:
        eligibility_result = calculate_company_eligibility(
            company_name=company_name,
            student_profile=student_profile
        )
        return jsonify({
            "status": "success",
            "success": True,
            "eligibility": eligibility_result
        }), 200
    except Exception as exc:
        logger.error(f"Error calculating eligibility for {company_name}: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@jobs_bp.route("/api/company/<company_name>/process-timeline", methods=["GET"])
def api_get_company_process_timeline(company_name: str):
    """Returns typical stages, average durations, and official/community split (#624)."""
    from utils.company_timeline_service import get_company_process_timeline
    timeline = get_company_process_timeline(company_name)
    return jsonify({"status": "success", "timeline": timeline}), 200


@jobs_bp.route("/company/<company_name>/cheat-sheet", methods=["GET"])
def download_company_cheat_sheet(company_name: str):
    """
    Produces and downloads a concise, single-page printable interview revision cheat sheet PDF (#433).
    Includes company overview, typical rounds, key topics, top skills to revise,
    and the student's top 3 identified skill gaps for that company (skipped gracefully if no analysis).
    """
    try:
        clean_company = (company_name or "").strip()
        companies_file = Path(config.BASE_DIR) / "data" / "companies.json"
        company_data = None

        if companies_file.exists():
            with open(companies_file, "r", encoding="utf-8") as f:
                all_companies = json.load(f)
                # Exact match first, then case-insensitive lookup
                if clean_company in all_companies:
                    company_data = all_companies[clean_company]
                else:
                    for c_key, c_val in all_companies.items():
                        if c_key.lower() == clean_company.lower():
                            company_data = c_val
                            clean_company = c_key
                            break

        if not company_data:
            flash(f"Company profile for '{company_name}' was not found.", "error")
            return redirect(url_for("companies_search_view"))

        # Identify student and fetch top skill gaps for this company (if any)
        student_id = request.args.get("student_id") or flask_session.get("user_id")
        skill_gaps = []
        if student_id:
            try:
                skill_gaps = get_student_top_skill_gaps_for_company(student_id, clean_company, limit=3)
            except Exception as gap_err:
                logger.warning("Could not fetch skill gaps for student %s: %s", student_id, gap_err)
                skill_gaps = []

        # Create output file in reports directory
        safe_company_slug = "".join(c if c.isalnum() else "_" for c in clean_company)
        student_slug = "".join(c if c.isalnum() else "_" for c in (student_id or "general"))
        pdf_filename = f"Cheat_Sheet_{safe_company_slug}_{student_slug}.pdf"
        os.makedirs(config.REPORT_FOLDER, exist_ok=True)
        pdf_path = os.path.join(config.REPORT_FOLDER, pdf_filename)

        generate_company_cheat_sheet_pdf(
            company_name=clean_company,
            company_data=company_data,
            output_path=pdf_path,
            skill_gaps=skill_gaps,
        )

        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=f"{safe_company_slug}_Interview_Cheat_Sheet.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        logger.error(f"Failed to generate company cheat sheet for {company_name}: {e}")
        flash(f"Error compiling cheat sheet PDF: {e}", "error")
        return redirect(url_for("companies_search_view"))


# ==============================================================================
# Career Preferences & Recommendations Routes (#task-career-preferences)
# ==============================================================================

@jobs_bp.route("/career-preferences", methods=["GET", "POST"])
def career_preferences_view():
    """
    Candidate Career Preferences management view & recommendations hub.
    Allows students to set their target roles, dream companies, locations, work modes,
    and salary expectations, returning tailored placement recommendations.
    """
    from utils.career_preferences_service import (
        get_career_preferences,
        save_career_preferences,
        get_available_preference_options,
        get_career_recommendations,
    )

    student_id = flask_session.get("user_id") or flask_session.get("student_id") or "default_student"

    if request.method == "POST":
        target_roles = request.form.getlist("target_roles")
        custom_roles = (request.form.get("custom_target_roles") or "").strip()
        if custom_roles:
            target_roles.extend([r.strip() for r in custom_roles.split(",") if r.strip()])

        target_companies = request.form.getlist("target_companies")
        custom_companies = (request.form.get("custom_target_companies") or "").strip()
        if custom_companies:
            target_companies.extend([c.strip() for c in custom_companies.split(",") if c.strip()])

        preferred_locations = request.form.getlist("preferred_locations")
        preferred_work_modes = request.form.getlist("preferred_work_modes")
        preferred_role_types = request.form.getlist("preferred_role_types")
        primary_skills = [s.strip() for s in (request.form.get("primary_skills") or "").split(",") if s.strip()]
        expected_salary_min = float(request.form.get("expected_salary_min") or 0.0)

        payload = {
            "target_roles": target_roles,
            "target_companies": target_companies,
            "preferred_locations": preferred_locations,
            "preferred_work_modes": preferred_work_modes,
            "preferred_role_types": preferred_role_types,
            "primary_skills": primary_skills,
            "expected_salary_min": expected_salary_min,
            "notes": (request.form.get("notes") or "").strip()
        }

        try:
            save_career_preferences(student_id=student_id, payload=payload)
            flash("Career preferences updated successfully! Recommendations have been refreshed.", "success")
        except Exception as e:
            logger.error(f"Error saving career preferences: {e}")
            flash(f"Error updating career preferences: {e}", "error")

        return redirect(url_for("jobs.career_preferences_view"))

    prefs = get_career_preferences(student_id)
    options = get_available_preference_options()
    recommendations = get_career_recommendations(student_id, preferences=prefs)

    return render_template(
        "career_preferences.html",
        preferences=prefs,
        options=options,
        recommendations=recommendations,
        student_id=student_id
    )


@jobs_bp.route("/api/career-preferences", methods=["GET", "POST", "PUT"])
def api_career_preferences():
    """
    REST API for reading and updating candidate career preferences.
    GET: Returns current preferences and available taxonomy options.
    POST/PUT: Saves preferences and returns updated preferences + recommendations.
    """
    from utils.career_preferences_service import (
        get_career_preferences,
        save_career_preferences,
        get_available_preference_options,
        get_career_recommendations,
    )

    student_id = (
        request.args.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    if request.method == "GET":
        prefs = get_career_preferences(student_id)
        options = get_available_preference_options()
        return jsonify({
            "status": "success",
            "preferences": prefs,
            "options": options
        }), 200

    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        updated = save_career_preferences(student_id=student_id, payload=payload)
        recs = get_career_recommendations(student_id, preferences=updated)
        return jsonify({
            "status": "success",
            "message": "Career preferences saved successfully.",
            "preferences": updated,
            "recommendations": recs
        }), 200
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as exc:
        logger.error(f"Error updating career preferences: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@jobs_bp.route("/api/career-preferences/recommendations", methods=["GET", "POST"])
def api_career_recommendations():
    """
    REST API returning tailored placement and job recommendations based on candidate career selections.
    """
    from utils.career_preferences_service import (
        get_career_preferences,
        get_career_recommendations,
    )

    student_id = (
        request.args.get("student_id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    payload = request.get_json(silent=True) if request.method == "POST" else None
    limit = int(request.args.get("limit", 12))

    try:
        recs = get_career_recommendations(student_id=student_id, preferences=payload, limit=limit)
        return jsonify(recs), 200
    except Exception as exc:
        logger.error(f"Error computing career recommendations: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@jobs_bp.route("/api/jobs/recommendations/<analysis_id>", methods=["GET"])
def api_job_recommendations(analysis_id: str):
    """
    Retrieves and returns tailored campus recruitment drive recommendations for a candidate,
    scored against verified technical skill overlap, ATS readiness, and hiring requirements.
    Supports query parameters: location, role_type, min_match, search, and force_refresh.
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if (
            not analysis_record
            or not _is_student_authorized(analysis_record)
        ):
            return jsonify({
                "success": False,
                "error": f"Candidate analysis record '{analysis_id}' not found."
            }), 404

        location = request.args.get("location", "all").strip()
        role_type = request.args.get("role_type", "all").strip()
        experience_level = request.args.get("experience_level", "all").strip()
        search_query = request.args.get("search", "").strip()
        force_refresh = request.args.get("force_refresh", "false").lower() in ("true", "1")

        try:
            raw_min = request.args.get("min_match", "0").strip()
            min_match = int(raw_min) if raw_min else 0
        except (ValueError, TypeError):
            min_match = 0

        try:
            raw_limit = request.args.get("limit", "").strip()
            limit = int(raw_limit) if raw_limit else None
        except (ValueError, TypeError):
            limit = None

        matcher_agent = JobRecommendationAgent()
        active_jobs = matcher_agent.data_source.get_jobs()

        recommendations = matcher_agent.recommend_jobs(
            analysis_record=analysis_record,
            jobs=active_jobs,
            location=location,
            role_type=role_type,
            experience_level=experience_level,
            min_match=min_match,
            search_query=search_query
        )

        suggestions_to_broaden = []
        if len(recommendations) == 0:
            suggestions_to_broaden = matcher_agent.get_broaden_suggestions(
                total_available=len(active_jobs),
                filtered_count=len(recommendations),
                location=location,
                role_type=role_type,
                experience_level=experience_level,
                search_query=search_query
            )

        if limit and limit > 0:
            recommendations = recommendations[:limit]

        return jsonify({
            "success": True,
            "analysis_id": analysis_id,
            "company_target": analysis_record.get("company_name", "General"),
            "total_available": len(active_jobs),
            "total_matched": len(recommendations),
            "filters_applied": {
                "location": location,
                "role_type": role_type,
                "experience_level": experience_level,
                "min_match": min_match,
                "search": search_query
            },
            "suggestions_to_broaden": suggestions_to_broaden,
            "jobs": recommendations
        }), 200

    except Exception as e:
        logger.error(f"Error computing job recommendations for {analysis_id}: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Failed to compute job recommendations: {str(e)}"
        }), 500


@jobs_bp.route("/github-analyzer", methods=["GET", "POST"])
def github_analyzer_view():
    """
    Evaluates public candidate developer profiles from GitHub (#141).
    Fetches repositories, star counts, top programming languages, and portfolio recommendations.
    """
    if request.method == "GET":
        username_query = request.args.get("username", "").strip()
        analysis = None
        if username_query:
            analysis = github_analyzer_agent.analyze_profile(username_query)
        return render_template("github_analyzer.html", analysis=analysis, query_username=username_query)

    # POST Submission
    target_username = request.form.get("github_username", "").strip()
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        target_username = payload.get("github_username", "").strip()

    analysis = github_analyzer_agent.analyze_profile(target_username)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(analysis), (200 if analysis.get("success") else 400)

    return render_template("github_analyzer.html", analysis=analysis, query_username=target_username)


@jobs_bp.route("/internships", methods=["GET", "POST"])
def internships_view():
    """
    Interactive student internship recommendation engine (#142, #356).
    Matches candidate skills with real internship listings, computes skill alignments,
    and supports multi-dimensional search filtering (location, work mode, stipend, duration, technology, deadline).
    """
    skills_raw = request.args.get("skills") or request.form.get("skills", "")
    role_filter = request.args.get("role") or request.form.get("role", "All")
    location_filter = request.args.get("location") or request.form.get("location", "All")
    search_query = request.args.get("search") or request.form.get("search", "")
    work_mode_filter = request.args.get("work_mode") or request.form.get("work_mode", "All")
    stipend_filter = request.args.get("stipend") or request.form.get("stipend", "All")
    duration_filter = request.args.get("duration") or request.form.get("duration", "All")
    technology_filter = request.args.get("technology") or request.form.get("technology", "All")
    deadline_filter = request.args.get("deadline") or request.form.get("deadline", "All")

    inc_unspec_param = request.args.get("include_unspecified", "") or request.form.get("include_unspecified", "")
    include_unspecified = False if inc_unspec_param.lower() in ("false", "0", "no") else True

    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    candidate_skills = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else [
        "Python", "SQL", "Git", "Data Structures", "Java"
    ]

    recommendations_data = internship_agent.get_recommendations(
        candidate_skills=candidate_skills,
        preferred_role=None if role_filter == "All" else role_filter,
        location=None if location_filter == "All" else location_filter,
        search_query=search_query,
        work_mode=None if work_mode_filter == "All" else work_mode_filter,
        stipend_type=None if stipend_filter == "All" else stipend_filter,
        duration=None if duration_filter == "All" else duration_filter,
        technology=None if technology_filter == "All" else technology_filter,
        deadline_status=None if deadline_filter == "All" else deadline_filter,
        include_unspecified=include_unspecified
    )

    recently_viewed = get_recently_viewed_jobs(student_id=student_id, limit=6)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        res_data = dict(recommendations_data)
        res_data["recently_viewed"] = recently_viewed
        return jsonify(res_data), 200

    return render_template(
        "internships.html",
        data=recommendations_data,
        recently_viewed=recently_viewed,
        student_id=student_id,
        skills_str=",".join(candidate_skills),
        current_role=role_filter,
        current_location=location_filter,
        current_work_mode=work_mode_filter,
        current_stipend=stipend_filter,
        current_duration=duration_filter,
        current_technology=technology_filter,
        current_deadline=deadline_filter,
        include_unspecified=include_unspecified,
        search_query=search_query
    )


@jobs_bp.route("/linkedin-analyzer", methods=["GET", "POST"])
def linkedin_analyzer():
    """
    LinkedIn Profile Analyzer View and API endpoint (#146).
    Reviews headline, summary/about, experience, and skills to provide ATS-style
    profile strength evaluation, missing keywords, and AI-powered headline rewrites.
    """
    analysis_result = None
    target_role = "Software Engineer"
    headline = ""
    about = ""
    experience = ""
    skills = ""

    if request.method == "POST":
        target_role = request.form.get("target_role", "Software Engineer").strip()
        headline = request.form.get("headline", "").strip()
        about = request.form.get("about", "").strip()
        experience = request.form.get("experience", "").strip()
        skills = request.form.get("skills", "").strip()

        if not headline and not about and not experience and not skills:
            flash("Please enter at least one profile section (Headline, About, Experience, or Skills).", "error")
        else:
            analysis_result = linkedin_agent.analyze_profile(
                headline=headline,
                about=about,
                experience=experience,
                skills=skills,
                target_role=target_role
            )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if not analysis_result:
            json_data = request.get_json(silent=True) or {}
            target_role = json_data.get("target_role", "Software Engineer")
            headline = json_data.get("headline", "")
            about = json_data.get("about", "")
            experience = json_data.get("experience", "")
            skills = json_data.get("skills", "")
            analysis_result = linkedin_agent.analyze_profile(
                headline=headline,
                about=about,
                experience=experience,
                skills=skills,
                target_role=target_role
            )
        return jsonify({
            "success": True,
            "analysis": analysis_result
        }), 200

    return render_template(
        "linkedin_analyzer.html",
        result=analysis_result,
        target_role=target_role,
        headline=headline,
        about=about,
        experience=experience,
        skills=skills
    )


@jobs_bp.route("/api/jobs/viewed", methods=["GET", "POST", "DELETE"])
def api_recently_viewed_jobs():
    """
    REST API endpoints to manage recently viewed jobs for candidates (#201).
    Supports GET (fetch history), POST (track a job view), and DELETE (remove or clear history).
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    if request.method == "GET":
        limit = request.args.get("limit", 10)
        try:
            limit_val = int(limit)
        except (ValueError, TypeError):
            limit_val = 10
        viewed_jobs = get_recently_viewed_jobs(student_id=student_id, limit=limit_val)
        return jsonify({
            "status": "success",
            "student_id": student_id,
            "total": len(viewed_jobs),
            "jobs": viewed_jobs
        }), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        job_id = str(payload.get("job_id", "")).strip()
        company_name = str(payload.get("company_name", "")).strip()
        role_title = str(payload.get("role_title", "")).strip()
        location = str(payload.get("location", "Remote")).strip()
        salary_range = payload.get("salary_range")
        apply_url = payload.get("apply_url")

        if not job_id or not company_name or not role_title:
            return jsonify({
                "status": "error",
                "message": "job_id, company_name, and role_title are required fields."
            }), 400

        record = record_viewed_job(
            student_id=student_id,
            job_id=job_id,
            company_name=company_name,
            role_title=role_title,
            location=location,
            salary_range=salary_range,
            apply_url=apply_url
        )
        return jsonify({
            "status": "success",
            "message": f"Recorded view for job '{role_title}' at '{company_name}'.",
            "viewed_job": record
        }), 201

    if request.method == "DELETE":
        payload = request.get_json(silent=True) or request.args
        job_id = payload.get("job_id")
        if job_id:
            removed = remove_recently_viewed_job(student_id=student_id, job_id=job_id)
            return jsonify({
                "status": "success" if removed else "not_found",
                "message": f"Removed job '{job_id}' from recently viewed list." if removed else "Job not found in history."
            }), (200 if removed else 404)
        else:
            cleared_count = clear_recently_viewed_jobs(student_id=student_id)
            return jsonify({
                "status": "success",
                "message": f"Cleared {cleared_count} recently viewed jobs.",
                "cleared_count": cleared_count
            }), 200


@jobs_bp.route("/companies", methods=["GET"])
def companies_search_view():
    """
    Company Search and Filter Explorer Page (#354).
    Enables candidates to discover and filter campus hiring companies by
    company name, industry, hiring tier, tech stack, and recruitment workflow.
    """
    seed_default_company_profiles()

    search_query = (request.args.get("search") or "").strip()
    tier_filter = (request.args.get("tier") or "All").strip()
    industry_filter = (request.args.get("industry") or "All").strip()
    tech_filter = (request.args.get("tech") or "All").strip()
    process_query = (request.args.get("process") or "").strip()
    eligible_only = request.args.get("eligible_only", "").lower() in ("true", "1", "yes")

    companies = list_company_profiles(
        search_query=search_query or None,
        tier_filter=tier_filter if tier_filter.lower() != "all" else None,
        industry_filter=industry_filter if industry_filter.lower() != "all" else None,
        tech_filter=tech_filter if tech_filter.lower() != "all" else None,
        process_query=process_query or None,
        active_only=True
    )

    available_tiers = get_distinct_hiring_tiers()
    available_industries = get_distinct_industries()
    available_techs = get_distinct_tech_stacks()

    # Fetch current student profile for eligibility evaluations (#844)
    student_id = flask_session.get("user_id") or flask_session.get("student_id") or "default_student"
    from db.coding_practice import get_student_profile
    profile_rec = get_student_profile(student_id) or {}
    student_profile = {
        "cgpa": profile_rec.get("cgpa"),
        "backlogs": profile_rec.get("backlogs", 0),
        "branch": profile_rec.get("department") or "",
        "department": profile_rec.get("department") or "",
        "passing_year": profile_rec.get("grad_year")
    }

    from utils.company_eligibility_service import calculate_company_eligibility

    # Enriched profiles with verification & freshness for modal details and eligibility (#844)
    enriched_companies = []
    for c in companies:
        c_copy = dict(c)
        comp_name = c.get("company_name", "")
        c_copy["enriched"] = enrich_company_hiring_guide(comp_name, c)
        c_copy["frequently_asked_questions"] = get_frequently_asked_questions(comp_name)
        
        # Calculate eligibility
        eligibility_eval = calculate_company_eligibility(comp_name, student_profile)
        c_copy["eligibility"] = eligibility_eval

        if eligible_only and not eligibility_eval.get("eligible"):
            continue

        enriched_companies.append(c_copy)

    # Check if AJAX or JSON response is requested
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify({
            "status": "success",
            "total_count": len(companies),
            "companies": companies,
            "filters": {
                "search": search_query,
                "tier": tier_filter,
                "industry": industry_filter,
                "tech": tech_filter,
                "process": process_query
            }
        }), 200

    return render_template(
        "companies.html",
        companies=enriched_companies,
        total_count=len(enriched_companies),
        search_query=search_query,
        tier_filter=tier_filter,
        industry_filter=industry_filter,
        tech_filter=tech_filter,
        process_query=process_query,
        eligible_only=eligible_only,
        student_profile=student_profile,
        available_tiers=available_tiers,
        available_industries=available_industries,
        available_techs=available_techs,
    )


@jobs_bp.route("/api/companies", methods=["GET", "POST"])
def company_profiles_collection_endpoint():
    """
    GET: Lists enterprise company recruitment profiles with filtering by search and hiring tier.
    POST: Creates a new company profile (requires admin privileges or secret key).
    """
    if request.method == "GET":
        search_query = request.args.get("search")
        tier_filter = request.args.get("tier")
        industry_filter = request.args.get("industry")
        tech_filter = request.args.get("tech")
        process_query = request.args.get("process")
        include_inactive = request.args.get("include_inactive", "false").lower() == "true"
        
        # Auto-seed default directory if completely empty
        seed_default_company_profiles()

        profiles = list_company_profiles(
            search_query=search_query,
            tier_filter=tier_filter,
            industry_filter=industry_filter,
            tech_filter=tech_filter,
            process_query=process_query,
            active_only=not include_inactive
        )
        return jsonify({
            "status": "success",
            "total_count": len(profiles),
            "companies": profiles
        }), 200

    if request.method == "POST":
        admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
        user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
        if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin":
            return jsonify({
                "status": "error",
                "message": "Unauthorized. Administrative privilege required to create company profiles."
            }), 403

        payload = request.get_json(silent=True) or request.form.to_dict()
        comp_name = payload.get("company_name")
        if not comp_name or not str(comp_name).strip():
            return jsonify({"status": "error", "message": "company_name is required."}), 400

        try:
            profile = create_company_profile(
                company_name=comp_name,
                industry=payload.get("industry", "Technology"),
                website_url=payload.get("website_url"),
                careers_url=payload.get("careers_url"),
                logo_url=payload.get("logo_url"),
                overview=payload.get("overview"),
                hiring_tier=payload.get("hiring_tier", "Tier 1"),
                hiring_workflow=payload.get("hiring_workflow", []),
                primary_tech_stack=payload.get("primary_tech_stack", []),
                typical_ctc_range=payload.get("typical_ctc_range"),
                headquarters=payload.get("headquarters")
            )
            return jsonify({
                "status": "success",
                "message": f"Created company profile for '{comp_name}'.",
                "company": profile
            }), 201
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to create profile: {str(e)}"}), 500


@jobs_bp.route("/api/companies/<profile_id>", methods=["GET", "PUT", "PATCH", "DELETE"])
def company_profile_detail_endpoint(profile_id: str):
    """
    GET: Fetches a single company profile by its unique ID.
    PUT/PATCH: Updates company profile fields (admin only).
    DELETE: Archives / soft-deletes the company profile (admin only).
    """
    profile = get_company_profile_by_id(profile_id)
    if not profile:
        return jsonify({"status": "error", "message": f"Company profile '{profile_id}' not found."}), 404

    if request.method == "GET":
        return jsonify({
            "status": "success",
            "company": profile
        }), 200

    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin":
        return jsonify({
            "status": "error",
            "message": "Unauthorized. Administrative privilege required to modify company profiles."
        }), 403

    if request.method in ("PUT", "PATCH"):
        updates = request.get_json(silent=True) or request.form.to_dict()
        try:
            updated_profile = update_company_profile(profile_id, updates)
            return jsonify({
                "status": "success",
                "message": f"Updated profile for '{profile['company_name']}'.",
                "company": updated_profile
            }), 200
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": f"Update failed: {str(e)}"}), 500

    if request.method == "DELETE":
        archived = archive_company_profile(profile_id)
        return jsonify({
            "status": "success" if archived else "error",
            "message": f"Archived company profile '{profile['company_name']}'."
        }), 200


@jobs_bp.route("/companies/suggest", methods=["GET"])
def company_suggest_view():
    """
    Renders the company suggestion form page (#450).
    Allows students and users to propose a new hiring company or request updates to an existing profile.
    """
    seed_default_company_profiles()
    prefill_name = (request.args.get("company_name") or request.args.get("name") or "").strip()
    selected_type = (request.args.get("type") or "new").strip().lower()
    selected_company_id = (request.args.get("company_id") or "").strip()

    existing_companies = list_company_profiles(active_only=True)
    available_tiers = get_distinct_hiring_tiers()
    available_industries = get_distinct_industries()

    # If company_id is provided, look up existing company details
    prefill_company = None
    if selected_company_id:
        prefill_company = get_company_profile_by_id(selected_company_id)
        if prefill_company:
            selected_type = "update"
            prefill_name = prefill_company.get("company_name", prefill_name)

    user_name = flask_session.get("user_name") or flask_session.get("name")
    user_email = flask_session.get("email")

    return render_template(
        "company_suggest.html",
        prefill_name=prefill_name,
        selected_type=selected_type,
        selected_company_id=selected_company_id,
        selected_company_name=prefill_company.get("company_name") if prefill_company else prefill_name,
        prefill_industry=prefill_company.get("industry") if prefill_company else "",
        prefill_tier=prefill_company.get("hiring_tier") if prefill_company else "Tier 1",
        prefill_website=prefill_company.get("website_url") if prefill_company else "",
        prefill_careers=prefill_company.get("careers_url") if prefill_company else "",
        prefill_hq=prefill_company.get("headquarters") if prefill_company else "",
        prefill_ctc=prefill_company.get("typical_ctc_range") if prefill_company else "",
        prefill_tech=", ".join(prefill_company.get("primary_tech_stack", [])) if prefill_company else "",
        prefill_workflow="\n".join(prefill_company.get("hiring_workflow", [])) if prefill_company else "",
        prefill_overview=prefill_company.get("overview") if prefill_company else "",
        existing_companies=existing_companies,
        available_tiers=available_tiers,
        available_industries=available_industries,
        current_user_name=user_name,
        current_user_email=user_email,
    )


@jobs_bp.route("/companies/suggest", methods=["POST"])
@jobs_bp.route("/api/company-suggestions", methods=["POST"])
@csrf.exempt
def api_submit_company_suggestion():
    """
    Submits a user suggestion for a new company profile or profile update (#450).
    Suggestions are initially saved in 'pending' unverified status for maintainer review.
    """
    payload = request.get_json(silent=True) or request.form.to_dict()

    comp_name = (payload.get("company_name") or "").strip()
    if not comp_name:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": "Company name is required."}), 400
        flash("Company name is required.", "danger")
        return redirect(url_for("company_suggest_view"))

    stype = (payload.get("suggestion_type") or "new").strip().lower()
    target_id = (payload.get("target_company_id") or "").strip() or None
    existing_name = (payload.get("existing_company_name") or "").strip() or None

    user_id = flask_session.get("user_id") or "default_student"
    submitter_name = (payload.get("submitter_name") or flask_session.get("user_name") or "Anonymous Student").strip()
    submitter_email = (payload.get("submitter_email") or flask_session.get("email") or "").strip()

    try:
        suggestion = create_company_suggestion(
            company_name=comp_name,
            suggestion_type=stype,
            target_company_id=target_id,
            existing_company_name=existing_name,
            industry=payload.get("industry"),
            website_url=payload.get("website_url"),
            careers_url=payload.get("careers_url"),
            hiring_tier=payload.get("hiring_tier"),
            hiring_workflow=payload.get("hiring_workflow"),
            primary_tech_stack=payload.get("primary_tech_stack"),
            typical_ctc_range=payload.get("typical_ctc_range"),
            headquarters=payload.get("headquarters"),
            overview=payload.get("overview"),
            notes=payload.get("notes"),
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            submitter_id=user_id,
        )

        msg = f"Thank you! Your suggestion for '{comp_name}' has been submitted for maintainer review."
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "status": "success",
                "message": msg,
                "suggestion": suggestion
            }), 201

        flash(msg, "success")
        return redirect(url_for("companies_search_view"))
    except ValueError as ve:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": str(ve)}), 400
        flash(str(ve), "danger")
        return redirect(url_for("company_suggest_view"))
    except Exception as e:
        logger.error(f"Error recording company profile suggestion: {e}")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": "Failed to submit company suggestion."}), 500
        flash("Failed to submit company suggestion. Please try again.", "danger")
        return redirect(url_for("company_suggest_view"))


@jobs_bp.route("/api/admin/company-suggestions", methods=["GET"])
def api_admin_list_company_suggestions():
    """
    API endpoint for maintainers to query company profile suggestions (#450).
    Allows maintainers to distinguish unverified suggestions from approved company information.
    """
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin" and not current_app.config.get("TESTING"):
        return jsonify({"status": "error", "message": "Unauthorized. Administrative privilege required."}), 403

    status_filter = request.args.get("status")
    stype_filter = request.args.get("type")
    limit = int(request.args.get("limit", 100))

    suggestions = list_company_suggestions(status=status_filter, suggestion_type=stype_filter, limit=limit)
    stats = get_suggestion_stats()

    return jsonify({
        "status": "success",
        "stats": stats,
        "total_count": len(suggestions),
        "suggestions": suggestions
    }), 200


@jobs_bp.route("/api/admin/company-suggestions/<suggestion_id>", methods=["GET"])
def api_admin_get_company_suggestion(suggestion_id: str):
    """Fetches details of a single company profile suggestion (#450)."""
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin" and not current_app.config.get("TESTING"):
        return jsonify({"status": "error", "message": "Unauthorized."}), 403

    sugg = get_company_suggestion_by_id(suggestion_id)
    if not sugg:
        return jsonify({"status": "error", "message": "Suggestion not found."}), 404

    return jsonify({"status": "success", "suggestion": sugg}), 200


@jobs_bp.route("/admin/company-suggestions/<suggestion_id>/approve", methods=["POST"])
@jobs_bp.route("/api/admin/company-suggestions/<suggestion_id>/approve", methods=["POST"])
@csrf.exempt
def admin_approve_company_suggestion_endpoint(suggestion_id: str):
    """
    Approves a company suggestion, publishing the new profile or updating verified information (#450).
    """
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin" and not current_app.config.get("TESTING"):
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": "Unauthorized."}), 403
        flash("Unauthorized administrative action.", "danger")
        return redirect(url_for("admin_company_suggestions_view"))

    payload = request.get_json(silent=True) or request.form.to_dict()
    notes = (payload.get("moderator_notes") or "Approved by maintainer").strip()
    moderated_by = payload.get("moderated_by") or (flask_session.get("user_name") if user_role == "admin" else None) or "admin"

    try:
        approved = approve_company_suggestion(
            suggestion_id=suggestion_id,
            moderator_notes=notes,
            moderated_by=moderated_by
        )
        msg = f"Suggestion for '{approved.get('company_name')}' approved and applied to directory."
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "status": "success",
                "message": msg,
                "suggestion": approved
            }), 200

        flash(msg, "success")
        return redirect(url_for("admin_company_suggestions_view"))
    except ValueError as ve:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": str(ve)}), 404
        flash(str(ve), "danger")
        return redirect(url_for("admin_company_suggestions_view"))
    except Exception as e:
        logger.error(f"Error approving company suggestion {suggestion_id}: {e}")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": f"Approval failed: {str(e)}"}), 500
        flash(f"Approval failed: {str(e)}", "danger")
        return redirect(url_for("admin_company_suggestions_view"))


@jobs_bp.route("/admin/company-suggestions/<suggestion_id>/reject", methods=["POST"])
@jobs_bp.route("/api/admin/company-suggestions/<suggestion_id>/reject", methods=["POST"])
@csrf.exempt
def admin_reject_company_suggestion_endpoint(suggestion_id: str):
    """
    Rejects a company suggestion with moderator explanation (#450).
    """
    admin_key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    user_role = flask_session.get("role") or flask_session.get("user", {}).get("role")
    if admin_key != config.ADMIN_SECRET_KEY and user_role != "admin" and not current_app.config.get("TESTING"):
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": "Unauthorized."}), 403
        flash("Unauthorized administrative action.", "danger")
        return redirect(url_for("admin_company_suggestions_view"))

    payload = request.get_json(silent=True) or request.form.to_dict()
    notes = (payload.get("moderator_notes") or "Declined by maintainer").strip()
    moderated_by = payload.get("moderated_by") or (flask_session.get("user_name") if user_role == "admin" else None) or "admin"

    try:
        rejected = reject_company_suggestion(
            suggestion_id=suggestion_id,
            moderator_notes=notes,
            moderated_by=moderated_by
        )
        msg = f"Suggestion for '{rejected.get('company_name')}' was marked as rejected."
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "status": "success",
                "message": msg,
                "suggestion": rejected
            }), 200

        flash(msg, "warning")
        return redirect(url_for("admin_company_suggestions_view"))
    except ValueError as ve:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": str(ve)}), 404
        flash(str(ve), "danger")
        return redirect(url_for("admin_company_suggestions_view"))
    except Exception as e:
        logger.error(f"Error rejecting company suggestion {suggestion_id}: {e}")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": f"Rejection failed: {str(e)}"}), 500
        flash(f"Rejection failed: {str(e)}", "danger")
        return redirect(url_for("admin_company_suggestions_view"))


@jobs_bp.route("/api/internships/saved", methods=["GET", "POST"])
def api_internships_saved():
    """
    List and create saved internships for the logged-in student (#357).
    """
    from utils.saved_internships_service import get_saved_internships, save_internship
    student_id = flask_session.get("user_id") or "default_student"

    if request.method == "GET":
        items = get_saved_internships(student_id)
        return jsonify({"success": True, "saved_internships": items}), 200

    payload = request.get_json(silent=True) or request.form or {}
    try:
        res = save_internship(
            student_id=student_id,
            company_name=payload.get("company_name", ""),
            title=payload.get("title", ""),
            internship_id=payload.get("internship_id"),
            location=payload.get("location", "Remote"),
            stipend=payload.get("stipend"),
            deadline=payload.get("deadline"),
            url=payload.get("url"),
            notes=payload.get("notes")
        )
        return jsonify(res), 201
    except (ValueError, PermissionError) as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error saving internship: {e}")
        return jsonify({"success": False, "error": "Internal error saving internship."}), 500


@jobs_bp.route("/api/internships/saved/<saved_id>", methods=["DELETE", "PUT"])
def api_internships_saved_item(saved_id):
    """
    Delete or update personal note on a saved internship (#357).
    """
    from utils.saved_internships_service import remove_saved_internship, update_saved_internship_note
    student_id = flask_session.get("user_id") or "default_student"

    if request.method == "DELETE":
        deleted = remove_saved_internship(student_id, saved_id)
        if deleted:
            return jsonify({"success": True, "message": "Saved internship removed."}), 200
        return jsonify({"success": False, "error": "Item not found or access denied."}), 404

    payload = request.get_json(silent=True) or request.form or {}
    updated = update_saved_internship_note(student_id, saved_id, payload.get("notes", ""))
    if updated:
        return jsonify({"success": True, "message": "Note updated."}), 200
    return jsonify({"success": False, "error": "Item not found or access denied."}), 404


@jobs_bp.route("/api/internships/saved/<saved_id>/convert", methods=["POST"])
def api_internships_saved_convert(saved_id):
    """
    Converts a saved internship to an active tracked job application (#357).
    """
    from utils.saved_internships_service import convert_saved_internship_to_application
    student_id = flask_session.get("user_id") or "default_student"
    try:
        res = convert_saved_internship_to_application(student_id, saved_id)
        return jsonify(res), 200
    except KeyError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error converting internship to application: {e}")
        return jsonify({"success": False, "error": "Internal error converting internship."}), 500


