"""
Analysis feature area routes & endpoints (#553).
Handles resume upload, ATS evaluation, skill gap extraction, comparison, and dashboards.
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
from utils.weak_topics_service import get_student_weak_topics, update_weak_topic_status
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
from agents.roadmap_agent import generate_learning_roadmap
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
from utils.role_template_service import (
    load_role_templates,
    get_role_template_by_id,
    format_role_template_as_job_description,
)
from utils.resume_generator import (
    get_available_templates,
    generate_resume_docx,
    generate_resume_pdf,
    generate_resume_preview_html,
    DEFAULT_SAMPLE_RESUME,
    TEMPLATES as RESUME_TEMPLATES,
)
from utils.session_resume_comparator import (
    store_session_comparison,
    get_session_comparison,
    clear_session_comparison,
    compare_resume_versions,
)
from utils.job_url_extractor import (
    extract_job_description_from_url,
    JobURLExtractionError,
    SSRFSecurityError,
)
from utils.pdf_metadata_service import (
    read_pdf_metadata,
    analyze_pdf_metadata_hygiene,
    strip_pdf_metadata,
    get_or_compute_pdf_metadata_hygiene,
    generate_suggested_filename,
)
from .common import *

logger = logging.getLogger(__name__)

analysis_bp = Blueprint("analysis", __name__)


def _require_authenticated_weak_topic_student():
    student_id = flask_session.get("user_id") or (flask_session.get("user") or {}).get("id")
    if student_id:
        return student_id, None
    if request.is_json or request.path.startswith("/api/"):
        return None, (jsonify({"success": False, "error": "Authentication required."}), 401)
    flash("Please log in to view your weak topics notebook.", "warning")
    return None, redirect(url_for("login", next=request.path))


@analysis_bp.route("/weak-topics", methods=["GET"])
def weak_topics_view():
    student_id, auth_response = _require_authenticated_weak_topic_student()
    if auth_response:
        return auth_response

    show_resolved = request.args.get("show_resolved", "").strip().lower() in ("1", "true", "yes")
    data = get_student_weak_topics(student_id=student_id, include_resolved=show_resolved)

    return render_template(
        "weak_topics.html",
        student_id=student_id,
        weak_topics=data["topics"],
        summary=data["summary"],
        show_resolved=show_resolved,
    )


@analysis_bp.route("/weak-topics/<topic_key>/status", methods=["POST"])
def update_weak_topic_status_view(topic_key: str):
    student_id, auth_response = _require_authenticated_weak_topic_student()
    if auth_response:
        return auth_response

    status = request.form.get("status", "active").strip().lower()
    notes = request.form.get("notes", "").strip()
    show_resolved = request.form.get("show_resolved", request.args.get("show_resolved", ""))
    success, error = update_weak_topic_status(
        student_id=student_id,
        topic_key=topic_key,
        status=status,
        notes=notes,
    )
    if not success:
        flash(error or "Failed to update topic status.", "danger")
    else:
        status_label = "resolved and hidden" if status == "resolved" else status
        flash(f"Topic status updated to '{status_label}'.", "success")

    redirect_kwargs = {"show_resolved": show_resolved} if show_resolved else {}
    return redirect(url_for("weak_topics_view", **redirect_kwargs))


@analysis_bp.route("/api/weak-topics", methods=["GET"])
def api_get_weak_topics():
    student_id, auth_response = _require_authenticated_weak_topic_student()
    if auth_response:
        return auth_response

    include_resolved = request.args.get("include_resolved", "").strip().lower() in ("1", "true", "yes")
    data = get_student_weak_topics(student_id=student_id, include_resolved=include_resolved)
    return jsonify({"success": True, "data": data}), 200


@analysis_bp.route("/api/weak-topics/<topic_key>/status", methods=["POST", "PUT", "PATCH"])
def api_update_weak_topic_status(topic_key: str):
    student_id, auth_response = _require_authenticated_weak_topic_student()
    if auth_response:
        return auth_response

    payload = request.get_json(silent=True) or request.form.to_dict()
    status = (payload.get("status") or "").strip().lower()
    notes = (payload.get("notes") or "").strip()
    if not status:
        return jsonify({"success": False, "error": "Field 'status' is required."}), 400

    success, error = update_weak_topic_status(
        student_id=student_id,
        topic_key=topic_key,
        status=status,
        notes=notes,
    )
    if not success:
        return jsonify({"success": False, "error": error}), 400

    return jsonify({
        "success": True,
        "topic_key": topic_key,
        "status": status,
        "notes": notes,
        "message": f"Topic status updated to '{status}'."
    }), 200


@analysis_bp.route("/")
@analysis_bp.route("/dashboard")
def index() -> str:
    """
    Renders the placement landing page containing the configuration forms.
    """
    companies_list = get_available_companies()
    student_id = flask_session.get("user_id")
    role_templates = load_role_templates()

    stored_resumes = []
    if student_id:
        try:
            from utils.resume_library_service import resume_library_service
            stored_resumes = resume_library_service.list_resumes(student_id)
        except Exception as e:
            logger.warning(f"Could not load stored resumes for index view: {e}")

    return render_template(
        "index.html",
        companies=companies_list,
        role_templates=role_templates,
        saved_job_descriptions=get_saved_job_descriptions(student_id) if student_id else [],
        stored_resumes=stored_resumes,
        walkthrough_steps=WALKTHROUGH_STEPS_CATALOG,
        walkthrough_dismissed=bool(flask_session.get("walkthrough_dismissed", False))
    )


@analysis_bp.route("/privacy", methods=["GET"])
def privacy_policy():
    """
    Renders the plain-language privacy notice detailing data collection,
    Gemini AI processing, local database storage, retention, and data deletion.
    """
    return render_template("privacy.html")


@analysis_bp.route("/analyze", methods=["POST"])
@rate_limit(scope="analyze_submission", is_api=False)
def analyze():
    """
    Handles PDF resume uploads, processes files, calls coordinator,
    persists result states, and redirects to candidate dashboards.
    Supports optional ?async=true query parameter or AJAX calls for background execution.
    """
    file_uuid = None
    try:
        # Validate privacy & third-party AI processing consent (#304)
        if not _has_privacy_consent(request):
            consent_err = "Please agree to the privacy notice and consent to data processing before analyzing your resume."
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": consent_err}), 400
            flash(consent_err, "error")
            return redirect(url_for("index"))

        extracted_resume_text, resume_filename, file_uuid, validation_error = _extract_and_validate_resume(request)
        if validation_error:
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": validation_error}), 400
            flash(validation_error, "error")
            return redirect(url_for("index"))

        # Section-by-section approval support (#601)
        # If user approved or corrected individual sections, reassemble them into the analysis input
        approved_sections_json = request.form.get("approved_sections")
        if approved_sections_json:
            try:
                approved_dict = json.loads(approved_sections_json)
                if isinstance(approved_dict, dict) and approved_dict:
                    from utils.resume_section_approval_service import reassemble_approved_resume
                    extracted_resume_text = reassemble_approved_resume(approved_dict, extracted_resume_text)
            except Exception as e_appr:
                logger.warning(f"Error parsing approved sections payload: {e_appr}")

        selected_company = request.form.get("company", "General").strip()
        job_description = request.form.get("job_description", "").strip()
        student_id = flask_session["user_id"]

        # Support selecting a role template instead of pasting a job description (#646)
        selected_role_id = (
            request.form.get("role_template")
            or request.form.get("selected_role")
            or request.form.get("role_id")
        )
        if selected_role_id:
            role_template = get_role_template_by_id(selected_role_id)
            if role_template:
                flask_session["active_target_role"] = role_template["name"]
                if (
                    not job_description
                    or request.form.get("input_mode") == "role"
                    or request.form.get("analysis_mode") == "role"
                ):
                    job_description = format_role_template_as_job_description(role_template)

        if len(job_description) > 15000:
            job_description = job_description[:15000]

        # ATS Layout Format Compatibility Check (#474)
        format_compatibility = None
        pdf_metadata_hygiene = None
        try:
            target_upload_path = None
            if file_uuid and resume_filename:
                cand_path = os.path.join(config.UPLOAD_FOLDER, secure_filename(f"{file_uuid}_{resume_filename}"))
                if os.path.exists(cand_path):
                    target_upload_path = cand_path
            if target_upload_path:
                format_compatibility = check_resume_format_compatibility(target_upload_path, extracted_resume_text)
                if resume_filename.lower().endswith(".pdf"):
                    raw_meta = read_pdf_metadata(target_upload_path)
                    pdf_metadata_hygiene = analyze_pdf_metadata_hygiene(
                        raw_meta,
                        filename=resume_filename,
                        target_role=selected_company
                    )
            else:
                format_compatibility = check_resume_format_compatibility(extracted_resume_text, extracted_resume_text)
                if resume_filename.lower().endswith(".pdf"):
                    pdf_metadata_hygiene = analyze_pdf_metadata_hygiene(
                        {},
                        filename=resume_filename,
                        target_role=selected_company
                    )
        except Exception as fc_err:
            logger.warning(f"Non-blocking format compatibility or metadata hygiene check error in analyze: {fc_err}")
            format_compatibility = None

        # PII Detection and Redaction Option (#249)
        redact_pii_flag = request.form.get("redact_pii") in ("true", "1", "on", "yes")
        if redact_pii_flag:
            redaction_res = pii_service.redact_pii(
                extracted_resume_text,
                redact_emails=request.form.get("pii_emails", "true").lower() != "false",
                redact_phones=request.form.get("pii_phones", "true").lower() != "false",
                redact_addresses=request.form.get("pii_addresses", "true").lower() != "false",
                redact_gov_ids=request.form.get("pii_gov_ids", "true").lower() != "false",
            )
            extracted_resume_text = redaction_res["redacted_text"]
            logger.info(f"Resume PII sanitized for student {student_id}: {redaction_res['total_redactions']} entities replaced.")

        # Resume Information Review & Correction Step
        review_step = request.form.get("review_step") in ("true", "1", "on", "yes")
        if review_step:
            review_data = extract_resume_information(extracted_resume_text, filename=resume_filename, file_uuid=file_uuid)
            review_data["target_company"] = selected_company
            review_data["job_description"] = job_description
            save_resume_review_cache(file_uuid, review_data)
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({
                    "success": True,
                    "review_required": True,
                    "file_uuid": file_uuid,
                    "redirect_url": url_for("resume_review_by_uuid", file_uuid=file_uuid)
                }), 200
            return redirect(url_for("resume_review_by_uuid", file_uuid=file_uuid))

        # Multi-company batch comparison request
        batch_companies = request.form.getlist("batch_companies")
        if not batch_companies:
            raw_comp = request.form.get("companies", "")
            if raw_comp:
                batch_companies = [c.strip() for c in raw_comp.split(",") if c.strip()]

        if request.form.get("batch_mode") == "true" or (batch_companies and len(batch_companies) > 1):
            if not batch_companies:
                batch_companies = [selected_company] if selected_company != "General" else ["Google", "Microsoft", "Amazon", "TCS"]
            batch_payload = run_batch_company_comparison(
                resume_text=extracted_resume_text,
                companies=batch_companies,
                job_description=job_description,
                resume_filename=resume_filename,
                student_id=student_id,
                coordinator_agent=coordinator_agent,
                save_to_db=True
            )
            redirect_url = url_for("view_batch_compare", batch_id=batch_payload["batch_id"])
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({
                    "success": True,
                    "batch_id": batch_payload["batch_id"],
                    "redirect_url": redirect_url,
                    "data": batch_payload
                }), 200
            return redirect(redirect_url)

        # Check input hash caching (#296)
        force_rerun = (
            request.form.get("rerun") in ("true", "1", "yes", "on") or
            request.args.get("rerun") in ("true", "1", "yes", "on") or
            request.form.get("force_rerun") in ("true", "1", "yes", "on") or
            request.args.get("force_rerun") in ("true", "1", "yes", "on") or
            request.args.get("force") in ("true", "1", "yes", "on")
        )
        input_hash = compute_analysis_hash(extracted_resume_text, job_description, selected_company)

        if not force_rerun:
            cached_record = get_cached_analysis(input_hash, student_id=student_id)
            if cached_record:
                logger.info(f"Analysis cache hit for hash {input_hash[:12]}... (analysis_id={cached_record['id']})")
                if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({
                        "success": True,
                        "cached": True,
                        "status": "cached",
                        "analysis_id": cached_record["id"],
                        "redirect_url": url_for("dashboard", analysis_id=cached_record["id"], from_cache="1")
                    }), 200
                return redirect(url_for("dashboard", analysis_id=cached_record["id"], from_cache="1"))

        # Check for asynchronous execution request
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            task = create_analysis_task(
                company_name=selected_company,
                resume_filename=resume_filename
            )
            stored_resume_id = flask_session.pop("active_stored_resume_id", None) or request.form.get("stored_resume_id")
            stored_resume_label = flask_session.pop("active_stored_resume_label", None) or request.form.get("stored_resume_label")
            start_async_analysis(
                task=task,
                coordinator_agent=coordinator_agent,
                save_analysis_func=save_analysis,
                resume_text=extracted_resume_text,
                job_description=job_description,
                company_name=selected_company,
                resume_filename=resume_filename,
                analysis_id=file_uuid,
                student_id=student_id,
                format_compatibility=format_compatibility,
                pdf_metadata_hygiene=pdf_metadata_hygiene
            )
            return jsonify({
                "success": True,
                "status": "started",
                "task_id": task.task_id,
                "analysis_id": file_uuid,
                "stream_url": f"/api/analysis/stream/{task.task_id}",
                "status_url": f"/api/analysis/status/{task.task_id}",
                "cancel_url": f"/api/analysis/cancel/{task.task_id}"
            }), 202

        # Synchronous execution fallback (preserves 100% test compatibility)
        is_basic_analysis = False
        try:
            analysis_payload = coordinator_agent.analyze(
                resume_text=extracted_resume_text,
                job_description=job_description,
                company_name=selected_company
            )
        except (AIAnalysisError, Exception) as aie:
            from utils.gemini import format_ai_error_for_user
            friendly_err = format_ai_error_for_user(aie)
            logger.warning(f"AI failure in analysis workflow: {scrub_api_key(str(aie))}. Activating rule-based basic fallback.")
            from utils.basic_analysis_fallback import generate_basic_fallback_analysis
            analysis_payload = generate_basic_fallback_analysis(
                resume_text=extracted_resume_text,
                job_description=job_description,
                company_name=selected_company,
                error_reason=scrub_api_key(friendly_err)
            )
            is_basic_analysis = True

        stored_resume_id = flask_session.pop("active_stored_resume_id", None) or request.form.get("stored_resume_id")
        stored_resume_label = flask_session.pop("active_stored_resume_label", None) or request.form.get("stored_resume_label")

        if isinstance(analysis_payload, dict):
            analysis_payload["extracted_resume_text"] = extracted_resume_text
            if format_compatibility:
                analysis_payload["format_compatibility"] = format_compatibility
            if pdf_metadata_hygiene:
                analysis_payload["pdf_metadata_hygiene"] = pdf_metadata_hygiene

        save_analysis(
            analysis_id=file_uuid,
            resume_filename=resume_filename,
            company_name=selected_company,
            job_description=job_description,
            results_dict=analysis_payload,
            student_id=student_id,
            stored_resume_id=stored_resume_id,
            stored_resume_label=stored_resume_label
        )

        # Do not cache basic fallback analyses (#849)
        if not is_basic_analysis:
            set_cached_analysis(
                input_hash=input_hash,
                analysis_id=file_uuid,
                student_id=student_id
            )

        if is_basic_analysis:
            flash("AI services are temporarily unavailable. Displaying basic rule-based analysis.", "info")

        if isinstance(analysis_payload, dict) and analysis_payload.get("security_warning"):
            flash(analysis_payload["security_warning"], "warning")

        if request.is_json:
            return jsonify({
                "success": True,
                "is_basic_analysis": is_basic_analysis,
                "analysis_id": file_uuid,
                "analysis_label": analysis_payload.get("analysis_label", "basic analysis (AI unavailable)") if is_basic_analysis else "full",
                "redirect_url": url_for("dashboard", analysis_id=file_uuid)
            }), 200

        return redirect(url_for("dashboard", analysis_id=file_uuid))

    except (RequestEntityTooLarge, ValueError) as ve:
        if isinstance(ve, RequestEntityTooLarge):
            from utils.request_size_diagnostics import diagnose_413_exception, build_size_error_response
            err_info = diagnose_413_exception(ve, request)
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                resp, code = build_size_error_response(err_info, request, status_code=413)
                return resp, code
            flash(err_info["message"], "error")
            return redirect(url_for("index"))
        logger.warning(f"Validation error in analysis workflow: {str(ve)}")
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": str(ve)}), 400
        flash(str(ve), "error")
        return redirect(url_for("index"))
    except AIAnalysisError as aie:
        logger.warning(f"AIAnalysisError in analysis workflow: {scrub_api_key(str(aie))}")
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": scrub_api_key(aie.user_friendly_message)}), 500
        flash(scrub_api_key(aie.user_friendly_message), "error")
        return redirect(url_for("index"))
    except ValueError as ve:
        logger.warning(f"Validation error in analysis workflow: {scrub_api_key(str(ve))}")
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": scrub_api_key(str(ve))}), 400
        flash(scrub_api_key(str(ve)), "error")
        return redirect(url_for("index"))
    except Exception as e:
        logger.error(f"Analysis workflow crashed: {scrub_api_key(str(e))}")
    except Exception as e:
        if isinstance(e, HTTPException) and e.code == 413:
            max_mb = getattr(config, "MAX_UPLOAD_SIZE_MB", 5)
            msg = f"The uploaded file exceeds the maximum allowed size of {max_mb} MB. Please upload a smaller file."
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                return jsonify({"success": False, "error": msg}), 413
            flash(msg, "error")
            return redirect(url_for("index"))
        logger.error(f"Analysis workflow crashed: {str(e)}")
        friendly_message = format_ai_error_for_user(e)
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": friendly_message}), 500
        flash(friendly_message, "error")
        return redirect(url_for("index"))
    finally:
        # Delete temporary uploaded files after analysis finishes or fails (#500)
        if file_uuid:
            try:
                from utils.cleanup import cleanup_temporary_upload
                cleanup_temporary_upload(file_uuid=file_uuid)
            except Exception as clean_err:
                logger.warning(f"Error during temporary upload cleanup for {file_uuid}: {clean_err}")


@analysis_bp.route("/api/analysis/start", methods=["POST"])
@rate_limit(scope="analyze", is_api=True)
def start_analysis_async():
    """
    Dedicated REST endpoint initiating asynchronous placement analysis.
    Validates resume text/file and schedules multi-agent processing in ThreadPoolExecutor.
    Returns task_id, stream_url (SSE), and status_url.
    """
    try:
        # Validate privacy & third-party AI processing consent (#304)
        if not _has_privacy_consent(request):
            return jsonify({
                "success": False,
                "error": "Please agree to the privacy notice and consent to data processing before analyzing your resume."
            }), 400

        extracted_resume_text, resume_filename, file_uuid, validation_error = _extract_and_validate_resume(request)
        if validation_error:
            return jsonify({"success": False, "error": validation_error}), 400

        selected_company = request.form.get("company", "General").strip()
        job_description = request.form.get("job_description", "").strip()
        student_id = flask_session["user_id"]

        # Support selecting a role template instead of pasting a job description (#646)
        selected_role_id = (
            request.form.get("role_template")
            or request.form.get("selected_role")
            or request.form.get("role_id")
        )
        if selected_role_id:
            role_template = get_role_template_by_id(selected_role_id)
            if role_template:
                flask_session["active_target_role"] = role_template["name"]
                if (
                    not job_description
                    or request.form.get("input_mode") == "role"
                    or request.form.get("analysis_mode") == "role"
                ):
                    job_description = format_role_template_as_job_description(role_template)

        if len(job_description) > 15000:
            job_description = job_description[:15000]

        # ATS Layout Format Compatibility Check (#474)
        format_compatibility = None
        pdf_metadata_hygiene = None
        try:
            target_upload_path = None
            if file_uuid and resume_filename:
                cand_path = os.path.join(config.UPLOAD_FOLDER, secure_filename(f"{file_uuid}_{resume_filename}"))
                if os.path.exists(cand_path):
                    target_upload_path = cand_path
            if target_upload_path:
                format_compatibility = check_resume_format_compatibility(target_upload_path, extracted_resume_text)
                if resume_filename.lower().endswith(".pdf"):
                    raw_meta = read_pdf_metadata(target_upload_path)
                    pdf_metadata_hygiene = analyze_pdf_metadata_hygiene(
                        raw_meta,
                        filename=resume_filename,
                        target_role=selected_company
                    )
            else:
                format_compatibility = check_resume_format_compatibility(extracted_resume_text, extracted_resume_text)
                if resume_filename.lower().endswith(".pdf"):
                    pdf_metadata_hygiene = analyze_pdf_metadata_hygiene(
                        {},
                        filename=resume_filename,
                        target_role=selected_company
                    )
        except Exception as fc_err:
            logger.warning(f"Non-blocking format compatibility or metadata hygiene check error in analyze: {fc_err}")
            format_compatibility = None

        # PII Detection and Redaction Option (#249)
        redact_pii_flag = request.form.get("redact_pii") in ("true", "1", "on", "yes")
        if redact_pii_flag:
            redaction_res = pii_service.redact_pii(
                extracted_resume_text,
                redact_emails=request.form.get("pii_emails", "true").lower() != "false",
                redact_phones=request.form.get("pii_phones", "true").lower() != "false",
                redact_addresses=request.form.get("pii_addresses", "true").lower() != "false",
                redact_gov_ids=request.form.get("pii_gov_ids", "true").lower() != "false",
            )
            extracted_resume_text = redaction_res["redacted_text"]
            logger.info(f"Resume PII sanitized for student {student_id}: {redaction_res['total_redactions']} entities replaced.")

        # Resume Information Review & Correction Step
        review_step = request.form.get("review_step") in ("true", "1", "on", "yes")
        if review_step:
            review_data = extract_resume_information(extracted_resume_text, filename=resume_filename, file_uuid=file_uuid)
            review_data["target_company"] = selected_company
            review_data["job_description"] = job_description
            save_resume_review_cache(file_uuid, review_data)
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({
                    "success": True,
                    "review_required": True,
                    "file_uuid": file_uuid,
                    "redirect_url": url_for("resume_review_by_uuid", file_uuid=file_uuid)
                }), 200
            return redirect(url_for("resume_review_by_uuid", file_uuid=file_uuid))

        # Check input hash caching (#296)
        force_rerun = (
            request.form.get("rerun") in ("true", "1", "yes", "on") or
            request.args.get("rerun") in ("true", "1", "yes", "on") or
            request.form.get("force_rerun") in ("true", "1", "yes", "on") or
            request.args.get("force_rerun") in ("true", "1", "yes", "on") or
            request.args.get("force") in ("true", "1", "yes", "on")
        )
        input_hash = compute_analysis_hash(extracted_resume_text, job_description, selected_company)

        if not force_rerun:
            cached_record = get_cached_analysis(input_hash, student_id=student_id)
            if cached_record:
                logger.info(f"API start cache hit for hash {input_hash[:12]}... (analysis_id={cached_record['id']})")
                return jsonify({
                    "success": True,
                    "cached": True,
                    "status": "cached",
                    "task_id": None,
                    "analysis_id": cached_record["id"],
                    "redirect_url": url_for("dashboard", analysis_id=cached_record["id"], from_cache="1")
                }), 200

        task = create_analysis_task(
            company_name=selected_company,
            resume_filename=resume_filename
        )

        stored_resume_id = flask_session.pop("active_stored_resume_id", None) or request.form.get("stored_resume_id")
        stored_resume_label = flask_session.pop("active_stored_resume_label", None) or request.form.get("stored_resume_label")

        start_async_analysis(
            task=task,
            coordinator_agent=coordinator_agent,
            save_analysis_func=save_analysis,
            resume_text=extracted_resume_text,
            job_description=job_description,
            company_name=selected_company,
            resume_filename=resume_filename,
            analysis_id=file_uuid,
            student_id=student_id,
            format_compatibility=format_compatibility,
            pdf_metadata_hygiene=pdf_metadata_hygiene
        )

        return jsonify({
            "success": True,
            "status": "started",
            "task_id": task.task_id,
            "analysis_id": file_uuid,
            "stream_url": f"/api/analysis/stream/{task.task_id}",
            "status_url": f"/api/analysis/status/{task.task_id}",
            "cancel_url": f"/api/analysis/cancel/{task.task_id}",
            "format_compatibility": format_compatibility
        }), 202

    except (RequestEntityTooLarge, ValueError) as ve:
        if isinstance(ve, RequestEntityTooLarge):
            from utils.request_size_diagnostics import diagnose_413_exception, build_size_error_response
            err_info = diagnose_413_exception(ve, request)
            resp, code = build_size_error_response(err_info, request, status_code=413)
            return resp, code
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        if isinstance(e, HTTPException) and e.code == 413:
            from utils.request_size_diagnostics import diagnose_413_exception, build_size_error_response
            err_info = diagnose_413_exception(e, request)
            resp, code = build_size_error_response(err_info, request, status_code=413)
            return resp, code
        logger.error(f"Failed to start async analysis: {str(e)}")
        friendly_message = format_ai_error_for_user(e)
        return jsonify({"success": False, "error": friendly_message}), 500


@analysis_bp.route("/api/analysis/stream/<task_id>")
def stream_analysis_progress(task_id: str):
    """
    Server-Sent Events (SSE) endpoint emitting real-time multi-agent milestones.
    Emits data: <json>\\n\\n for each agent milestone, ending upon completed or error state.
    """
    task = get_analysis_task(task_id)
    if not task:
        return jsonify({"success": False, "error": f"Analysis task '{task_id}' not found"}), 404

    def generate_events():
        last_index = 0
        while True:
            events = task.get_events_since(last_index, timeout=15.0)
            if events:
                for ev in events:
                    yield f"data: {json.dumps(ev)}\n\n"
                    last_index += 1
                    if ev.get("status") in ("completed", "error", "cancelled"):
                        return
            else:
                if task.status in ("completed", "error", "cancelled") or task.is_cancelled:
                    return
                # SSE heartbeat comment to keep HTTP connection alive
                yield ": keep-alive\n\n"

    response = Response(generate_events(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@analysis_bp.route("/api/analysis/status/<task_id>")
def get_analysis_task_status(task_id: str):
    """
    Polling fallback endpoint returning current analysis task status and milestones.
    """
    task = get_analysis_task(task_id)
    if not task:
        return jsonify({"success": False, "error": f"Analysis task '{task_id}' not found"}), 404

    return jsonify({"success": True, **task.to_dict()}), 200


@analysis_bp.route("/api/analysis/cancel", methods=["POST"])
@analysis_bp.route("/api/analysis/cancel/<task_id>", methods=["POST"])
def cancel_analysis(task_id: Optional[str] = None):
    """
    Cancels an in-progress or queued background placement analysis task (#465, #764).
    Accepts task_id either as URL route param or in JSON request body {"task_id": "..."}.
    """
    effective_task_id = task_id
    if not effective_task_id:
        req_json = request.get_json(silent=True) or {}
        effective_task_id = req_json.get("task_id") or request.form.get("task_id")

    if not effective_task_id:
        return jsonify({"success": False, "error": "Missing task_id parameter."}), 400

    student_id = flask_session.get("student_id") or flask_session.get("user_id")
    success, msg = cancel_analysis_task(effective_task_id, student_id=student_id)
    if not success:
        status_code = 404 if "not found" in msg.lower() else 400
        return jsonify({"success": False, "error": msg}), status_code

    return jsonify({
        "success": True,
        "status": "cancelled",
        "task_id": effective_task_id,
        "message": msg
    }), 200


@analysis_bp.route("/analyze/rerun/<analysis_id>", methods=["GET", "POST"])
def rerun_analysis(analysis_id: str):
    """
    Forces a fresh placement analysis for an existing analysis, bypassing cache (#296).
    Re-invokes coordinator_agent.analyze(), saves new analysis record, updates cache,
    and redirects to the new dashboard without the cached notice banner.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    # Ensure required nested dictionary structure in results
    results_raw = analysis_record.get("results")
    results = results_raw if isinstance(results_raw, dict) else {}
    if "resume_analysis" not in results or not isinstance(results.get("resume_analysis"), dict):
        results["resume_analysis"] = {
            "ats_score": 0,
            "strengths": [],
            "weaknesses": [],
            "formatting_suggestions": [],
            "grammar_suggestions": [],
            "project_feedback": "",
            "experience_feedback": "",
        }
    if "job_match" not in results or not isinstance(results.get("job_match"), dict):
        results["job_match"] = {
            "match_percentage": 0,
            "matched_skills": [],
            "missing_skills": [],
        }
    analysis_record["results"] = results

    student_id = flask_session.get("user_id") or analysis_record.get("student_id") or "default_student"
    resume_text = (request.form.get("resume_text") or analysis_record.get("resume_text") or "").strip()
    job_description = (request.form.get("job_description") or analysis_record.get("job_description") or "").strip()
    company_name = (request.form.get("company") or analysis_record.get("company_name") or "General").strip()
    resume_filename = (request.form.get("resume_filename") or analysis_record.get("resume_filename") or "resume.pdf").strip()

    if not resume_text:
        # 1. Check for stored text, pdf, or docx files
        for candidate_ext in ("_resume.txt", ".txt", ".pdf", ".docx"):
            candidate_path = os.path.join(config.UPLOAD_FOLDER, f"{analysis_id}{candidate_ext}")
            if os.path.exists(candidate_path):
                try:
                    from utils.pdf_reader import extract_text_from_file
                    resume_text = extract_text_from_file(candidate_path)
                    if resume_text:
                        break
                except Exception as e:
                    logger.warning(f"Could not read stored file {candidate_path}: {e}")

    if not resume_text and os.path.exists(config.UPLOAD_FOLDER):
        # 2. Check for files starting with {analysis_id}_
        for fname in os.listdir(config.UPLOAD_FOLDER):
            if fname.startswith(f"{analysis_id}_") or fname.startswith(f"{analysis_id}."):
                candidate_path = os.path.join(config.UPLOAD_FOLDER, fname)
                try:
                    from utils.pdf_reader import extract_text_from_file
                    resume_text = extract_text_from_file(candidate_path)
                    if resume_text:
                        break
                except Exception as e:
                    logger.warning(f"Could not extract text from {candidate_path}: {e}")

    if not resume_text:
        # 3. Fallback: Reconstruct resume text representation from analysis results
        results = analysis_record.get("results") or {}
        resume_analysis = results.get("resume_analysis") or {}
        strengths = resume_analysis.get("strengths") or []
        summary = resume_analysis.get("summary") or ""
        if summary or strengths:
            resume_text = f"Summary: {summary}\nKey Skills & Strengths: {', '.join(strengths)}"
        else:
            resume_text = f"Candidate profile evaluated for {company_name}."

    new_analysis_id = str(uuid.uuid4())

    # Copy any associated resume files to the new analysis ID
    if os.path.exists(config.UPLOAD_FOLDER):
        for fname in os.listdir(config.UPLOAD_FOLDER):
            if fname.startswith(f"{analysis_id}"):
                suffix = fname[len(analysis_id):]
                new_fname = f"{new_analysis_id}{suffix}"
                try:
                    shutil.copyfile(
                        os.path.join(config.UPLOAD_FOLDER, fname),
                        os.path.join(config.UPLOAD_FOLDER, new_fname)
                    )
                except Exception as e:
                    logger.warning(f"Could not copy file {fname} for re-run: {e}")

    try:
        is_basic = False
        try:
            analysis_payload = coordinator_agent.analyze(
                resume_text=resume_text,
                job_description=job_description,
                company_name=company_name
            )
        except (AIAnalysisError, Exception) as aie:
            from utils.gemini import format_ai_error_for_user
            friendly_err = format_ai_error_for_user(aie)
            logger.warning(f"AI failure on re-run for {analysis_id}: {scrub_api_key(str(aie))}. Using basic fallback.")
            from utils.basic_analysis_fallback import generate_basic_fallback_analysis
            analysis_payload = generate_basic_fallback_analysis(
                resume_text=resume_text,
                job_description=job_description,
                company_name=company_name,
                error_reason=scrub_api_key(friendly_err)
            )
            is_basic = True

        save_analysis(
            analysis_id=new_analysis_id,
            resume_filename=resume_filename,
            company_name=company_name,
            job_description=job_description,
            results_dict=analysis_payload,
            student_id=student_id
        )

        # Do not cache basic fallback analyses (#849)
        if not is_basic:
            input_hash = compute_analysis_hash(resume_text, job_description, company_name)
            set_cached_analysis(
                input_hash=input_hash,
                analysis_id=new_analysis_id,
                student_id=student_id
            )

        if is_basic:
            flash("AI services are still unavailable. Displaying basic rule-based analysis.", "info")
        else:
            flash("Analysis re-run complete! Fresh evaluation generated.", "success")

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": True,
                "is_basic_analysis": is_basic,
                "analysis_id": new_analysis_id,
                "redirect_url": url_for("dashboard", analysis_id=new_analysis_id)
            }), 200

        return redirect(url_for("dashboard", analysis_id=new_analysis_id))

    except Exception as e:
        logger.error(f"Failed to re-run analysis for {analysis_id}: {e}")
        friendly_message = format_ai_error_for_user(e)
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": friendly_message}), 500
        flash(f"Re-run failed: {friendly_message}", "error")
        return redirect(url_for("dashboard", analysis_id=analysis_id))


@analysis_bp.route("/api/analysis/batch-compare", methods=["POST"])
@analysis_bp.route("/batch-compare", methods=["POST"])
@rate_limit(scope="batch_compare", is_api=True)
def api_batch_compare():
    """
    Dedicated endpoint for multi-company batch placement evaluation.
    Orchestrates concurrent evaluations, builds offer likelihood probability metrics,
    and returns comparison rankings and Chart.js radar multi-dataset data.
    """
    try:
        # Validate privacy & third-party AI processing consent (#304)
        if not _has_privacy_consent(request):
            consent_err = "Please agree to the privacy notice and consent to data processing before analyzing your resume."
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": consent_err}), 400
            flash(consent_err, "error")
            return redirect(url_for("index"))

        if request.is_json:
            json_data = request.get_json(silent=True) or {}
            resume_text = (json_data.get("resume_text") or "").strip()
            companies = json_data.get("companies") or json_data.get("batch_companies") or []
            if isinstance(companies, str):
                companies = [c.strip() for c in companies.split(",") if c.strip()]
            job_description = (json_data.get("job_description") or "").strip()
            student_id = flask_session["user_id"]
            resume_filename = json_data.get("resume_filename") or "pasted_resume.txt"
            validation_error = None
            if not resume_text:
                validation_error = "Please provide resume text for batch comparison."
        else:
            resume_text, resume_filename, file_uuid, validation_error = _extract_and_validate_resume(request)
            companies = request.form.getlist("batch_companies") or request.form.getlist("companies")
            if not companies:
                raw_comp = request.form.get("companies", "")
                if raw_comp:
                    companies = [c.strip() for c in raw_comp.split(",") if c.strip()]
                else:
                    sel = request.form.get("company", "Google").strip()
                    companies = [sel] if sel != "General" else ["Google", "Microsoft", "Amazon", "TCS"]
            job_description = request.form.get("job_description", "").strip()
            student_id = flask_session["user_id"]

        if validation_error:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": validation_error}), 400
            flash(validation_error, "error")
            return redirect(url_for("index"))

        if len(job_description) > 15000:
            job_description = job_description[:15000]

        batch_result = run_batch_company_comparison(
            resume_text=resume_text,
            companies=companies,
            job_description=job_description,
            resume_filename=resume_filename,
            student_id=student_id,
            coordinator_agent=coordinator_agent,
            save_to_db=True
        )

        redirect_url = url_for("view_batch_compare", batch_id=batch_result["batch_id"])

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("async") == "true":
            return jsonify({
                "success": True,
                "batch_id": batch_result["batch_id"],
                "redirect_url": redirect_url,
                "data": batch_result
            }), 200

        return redirect(redirect_url)

    except Exception as exc:
        logger.error(f"Batch comparison failed: {exc}")
        friendly_error = format_ai_error_for_user(exc)
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": friendly_error}), 500
        flash(friendly_error, "error")
        return redirect(url_for("index"))


@analysis_bp.route("/batch-compare/<batch_id>", methods=["GET"])
def view_batch_compare(batch_id: str):
    """
    Renders the Company Comparison Matrix view featuring Chart.js Multi-Dataset
    Radar charts and hiring probability rankings.
    """
    record = get_batch_comparison(batch_id)
    if not record or not _is_student_authorized(record):
        flash("Batch comparison record not found.", "error")
        return redirect(url_for("index"))

    batch_payload = record.get("results", {})
    return render_template(
        "batch_compare.html",
        batch_id=batch_id,
        batch=batch_payload,
        comparisons=batch_payload.get("comparisons", []),
        best_match=batch_payload.get("best_match", {}),
        quickest_entry=batch_payload.get("quickest_entry", {}),
        summary_insights=batch_payload.get("summary_insights", {}),
        radar_data_json=json.dumps(batch_payload.get("radar_data", {}))
    )


@analysis_bp.route("/api/analysis/batch-compare/<batch_id>", methods=["GET"])
def api_get_batch_compare(batch_id: str):
    """
    Retrieves stored batch comparison results JSON for a given batch_id.
    """
    record = get_batch_comparison(batch_id)
    if not record or not _is_student_authorized(record):
        return jsonify({"success": False, "error": "Batch not found."}), 404
    return jsonify({
        "success": True,
        "batch_id": batch_id,
        "data": record.get("results", {})
    }), 200


@analysis_bp.route("/resume/review", methods=["GET", "POST"])
def resume_review_view():
    """
    Renders the interactive Resume Information Review and Correction step.
    Allows candidate to verify extracted details, edit incorrect information,
    add missing information, and see visual highlights for low-confidence/missing fields.
    Preserves original uploaded documents without modification.
    """
    if request.method == "POST":
        # Validate privacy & third-party AI processing consent (#304)
        if not _has_privacy_consent(request):
            flash("Please agree to the privacy notice and consent to data processing before analyzing your resume.", "error")
            return redirect(url_for("index"))

        extracted_text, filename, file_uuid, val_err = _extract_and_validate_resume(request)
        if val_err:
            flash(val_err, "error")
            return redirect(url_for("index"))

        target_company = request.form.get("company", "General").strip()
        job_description = request.form.get("job_description", "").strip()

        review_data = extract_resume_information(extracted_text, filename=filename, file_uuid=file_uuid)
        review_data["target_company"] = target_company
        review_data["job_description"] = job_description

        save_resume_review_cache(file_uuid, review_data)
        return render_template(
            "resume_review.html",
            review_data=review_data,
            target_company=target_company,
            job_description=job_description
        )

    # GET request
    file_uuid = request.args.get("file_uuid")
    if file_uuid:
        review_data = get_resume_review_cache(file_uuid)
        if review_data:
            return render_template(
                "resume_review.html",
                review_data=review_data,
                target_company=review_data.get("target_company", "General"),
                job_description=review_data.get("job_description", "")
            )

    flash("Please upload a resume first to review extracted details.", "info")
    return redirect(url_for("index"))


@analysis_bp.route("/resume/review/<file_uuid>", methods=["GET"])
def resume_review_by_uuid(file_uuid: str):
    """
    Direct route to review and correct extracted details for a specific uploaded resume.
    """
    review_data = get_resume_review_cache(file_uuid)
    if not review_data:
        flash("Resume review session expired or not found. Please re-upload.", "warning")
        return redirect(url_for("index"))

    return render_template(
        "resume_review.html",
        review_data=review_data,
        target_company=review_data.get("target_company", "General"),
        job_description=review_data.get("job_description", "")
    )


@analysis_bp.route("/api/resume/parse-preview", methods=["POST"])
def api_resume_parse_preview():
    """
    REST endpoint to upload/submit a resume, extract structured sections,
    and return review details with extraction status flags and review URL.
    Original document is safely preserved in uploads/.
    """
    extracted_text, filename, file_uuid, val_err = _extract_and_validate_resume(request)
    if val_err:
        return jsonify({"success": False, "error": val_err}), 400

    target_company = request.form.get("company", "General").strip()
    job_description = request.form.get("job_description", "").strip()

    review_data = extract_resume_information(extracted_text, filename=filename, file_uuid=file_uuid)
    review_data["target_company"] = target_company
    review_data["job_description"] = job_description
    save_resume_review_cache(file_uuid, review_data)

    return jsonify({
        "success": True,
        "file_uuid": file_uuid,
        "review_data": review_data,
        "review_url": url_for("resume_review_by_uuid", file_uuid=file_uuid),
        "total_review_items": review_data.get("total_review_items", 0),
        "original_filename": filename
    }), 200


@analysis_bp.route("/api/resume/validate-info", methods=["POST"])
def api_validate_resume_info():
    """
    Validates candidate-reviewed information before submission.
    Returns status and dictionary of field validation errors.
    """
    payload = request.get_json(silent=True) or request.form.to_dict()
    is_valid, errors = validate_reviewed_information(payload)
    return jsonify({
        "success": True,
        "is_valid": is_valid,
        "errors": errors
    }), 200


@analysis_bp.route("/api/resume/confirm-analysis", methods=["POST"])
def api_confirm_resume_analysis():
    """
    Validates corrected resume information, reconstructs standardized markdown,
    feeds the verified resume text to the coordinator agent, and persists the analysis.
    The original uploaded document is preserved unaltered.
    """
    payload = request.get_json(silent=True) or request.form.to_dict()
    if not payload:
        return jsonify({"success": False, "error": "No review data submitted."}), 400

    is_valid, errors = validate_reviewed_information(payload)
    if not is_valid:
        return jsonify({
            "success": False,
            "error": "Validation failed on reviewed resume data.",
            "errors": errors
        }), 400

    file_uuid = payload.get("file_uuid") or str(uuid.uuid4())
    original_filename = payload.get("original_filename") or "Resume.pdf"
    target_company = payload.get("target_company") or "General"
    job_description = payload.get("job_description") or ""
    student_id = flask_session.get("user_id") or "default_student"

    # Reconstruct standardized, clean markdown from student-verified data
    reconstructed_resume_text = reconstruct_resume_markdown(payload)

    # Trigger multi-agent placement analysis using the reviewed information
    try:
        analysis_payload = coordinator_agent.analyze(
            resume_text=reconstructed_resume_text,
            job_description=job_description,
            company_name=target_company
        )

        save_analysis(
            analysis_id=file_uuid,
            resume_filename=original_filename,
            company_name=target_company,
            job_description=job_description,
            results_dict=analysis_payload,
            student_id=student_id
        )

        # Update cache to indicate review completed
        payload["status"] = "reviewed_and_analyzed"
        save_resume_review_cache(file_uuid, payload)

        return jsonify({
            "success": True,
            "analysis_id": file_uuid,
            "redirect_url": url_for("dashboard", analysis_id=file_uuid),
            "message": "Placement analysis completed using verified resume profile."
        }), 200

    except Exception as e:
        logger.error(f"Error analyzing verified resume: {str(e)}")
        friendly_message = format_ai_error_for_user(e)
        return jsonify({"success": False, "error": friendly_message}), 500
    finally:
        if file_uuid:
            try:
                from utils.cleanup import cleanup_temporary_upload
                cleanup_temporary_upload(file_uuid=file_uuid)
            except Exception:
                pass


@analysis_bp.route("/dashboard/<analysis_id>")
def dashboard(analysis_id: str) -> str:
    """
    Loads and renders the candidate evaluation dashboard with longitudinal
    progress trends and multi-version delta analysis.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    # Ensure required nested dictionary structure in results
    results_raw = analysis_record.get("results")
    results = results_raw if isinstance(results_raw, dict) else {}
    if "resume_analysis" not in results or not isinstance(results.get("resume_analysis"), dict):
        results["resume_analysis"] = {
            "ats_score": 0,
            "strengths": [],
            "weaknesses": [],
            "formatting_suggestions": [],
            "grammar_suggestions": [],
            "project_feedback": "",
            "experience_feedback": "",
        }
    if "job_match" not in results or not isinstance(results.get("job_match"), dict):
        results["job_match"] = {
            "match_percentage": 0,
            "matched_skills": [],
            "missing_skills": [],
        }
    analysis_record["results"] = results

    student_id = analysis_record.get("student_id") or "default_student"
    all_versions = get_student_versions(student_id)
    if not all_versions:
        all_versions = [analysis_record]

    current_version = analysis_record.get("version_number", 1)
    previous_record = get_previous_analysis(student_id, current_version=current_version)
    delta_data = calculate_version_delta(analysis_record, previous_record)
    trend_series = build_longitudinal_series(all_versions)

    # Demo mode detection (#435)
    is_demo = bool(
        analysis_record.get("is_demo")
        or analysis_id in ("demo-sample-analysis", "demo_sample_analysis", "demo")
        or student_id == "demo_visitor"
        or flask_session.get("is_demo_mode")
    )

    if is_demo:
        initial_outreach = {
            "tone": "formal",
            "role_title": "Software Development Engineer (SDE 1)",
            "company_name": "Google",
            "cover_letter": (
                "Dear University Recruiting Team at Google,\n\n"
                "I am writing to express my enthusiastic interest in the Software Development Engineer (SDE 1) position at Google. "
                "As a Computer Science student at UC Berkeley with hands-on experience developing asynchronous distributed task queues "
                "and scalable microservices in Python, Redis, and Docker, I am eager to contribute to Google's engineering excellence.\n\n"
                "Throughout my academic and project initiatives, I have focused on building performant, scalable applications. "
                "In my Distributed Task Queue project, I architected background workers processing 5,000+ jobs per minute with priority scheduling "
                "and exponential backoff retries, reducing failed job dropouts by 40%. During my software engineering internship at CloudTech Solutions, "
                "I optimized RESTful microservices in Python, improving reporting pipeline throughput by 25%.\n\n"
                "Google's culture of technical innovation, distributed systems leadership, and planetary-scale infrastructure deeply resonates with me. "
                "I am confident that my strong foundations in data structures, algorithms, and practical backend engineering will enable me to make immediate "
                "contributions to your team.\n\n"
                "Thank you for your time and consideration. I welcome the opportunity to discuss how my background aligns with Google's mission.\n\n"
                "Sincerely,\nAlex Chen\nSan Francisco, CA | (555) 019-2834 | alex.chen.demo@example.com"
            ),
            "linkedin_inmail": (
                "Hi [Recruiter Name],\n\n"
                "I hope you're having a great week! I follow Google's systems engineering work with great enthusiasm and noticed the campus Software Development Engineer (SDE 1) opening.\n\n"
                "I am graduating in CS from UC Berkeley with practical experience in Python, distributed task queues, and microservices architecture. "
                "I would love to connect and learn more about Google's campus recruiting timeline and software engineering teams.\n\n"
                "Best regards,\nAlex Chen"
            ),
            "cold_email": (
                "Subject: Application: Software Development Engineer (SDE 1) - Alex Chen\n\n"
                "Dear Google Engineering Team,\n\n"
                "I am reaching out regarding the campus Software Development Engineer (SDE 1) role. "
                "With deep experience in Python backend engineering, distributed systems, and algorithmic problem solving (350+ LeetCode problems solved), "
                "I believe my background would be an excellent fit for your engineering teams.\n\n"
                "Key Highlights:\n"
                "- UC Berkeley Computer Science (GPA: 3.82/4.0)\n"
                "- Built distributed task queues with Redis handling 5,000+ jobs/min\n"
                "- CloudTech Solutions SWE Intern: boosted microservice throughput by 25%\n\n"
                "I've attached my resume and would welcome 10 minutes to discuss how my skill set can support Google's upcoming engineering goals.\n\n"
                "Warm regards,\nAlex Chen"
            ),
            "subject_lines": [
                "Software Development Engineer (SDE 1) Candidate - Python & Distributed Systems | Alex Chen",
                "UC Berkeley CS Graduate - Application for SDE 1 - Alex Chen",
                "Passionate about Google Engineering - Software Engineer Candidate Inquiry"
            ],
            "key_highlights": [
                "Bachelor of Science in CS at UC Berkeley (GPA: 3.82 / 4.0)",
                "Architected distributed task queue in Python/Redis processing 5,000+ jobs/min",
                "Software Engineering Intern at CloudTech Solutions (improved throughput by 25%)",
                "Practical exposure to Docker, REST APIs, and PostgreSQL with sub-50ms query latency"
            ],
            "is_demo": True
        }
    else:
        initial_outreach = outreach_agent.generate_outreach(analysis_record, tone="formal")

    # Generate initial Self-Introduction scripts ('Tell Me About Yourself') (#432)
    initial_self_intro = self_introduction_agent.generate_scripts(
        analysis_record=analysis_record,
        target_role=analysis_record.get("target_role") or "Software Engineer",
        target_company=analysis_record.get("company_name", "General"),
    )

    # Target-scoped longitudinal comparison (#297)
    target_company = analysis_record.get("company_name", "General")
    target_role = analysis_record.get("target_role")
    if not target_role:
        res = analysis_record.get("results") or {}
        target_role = (
            res.get("target_role") or
            res.get("job_match", {}).get("target_role") or
            res.get("role_preparation", {}).get("target_role") or
            "Software Engineer"
        )
    target_previous_record = get_previous_analysis_for_target(
        student_id=student_id,
        current_analysis_id=analysis_id,
        current_version=current_version,
        company_name=target_company,
        target_role=target_role
    )
    target_versions = get_student_versions_for_target(
        student_id=student_id,
        company_name=target_company,
        target_role=target_role
    )
    comparison = compare_analysis_versions(
        current_record=analysis_record,
        previous_record=target_previous_record,
        versions_history=target_versions
    )

    # Fetch initial recommended campus drives for seamless SSR
    try:
        feed_service = JobFeedService()
        active_jobs = feed_service.get_all_jobs()
        job_matcher = JobRecommendationAgent()
        initial_recommended_jobs = job_matcher.recommend_jobs(
            analysis_record=analysis_record,
            jobs=active_jobs,
            min_match=0
        )
    except Exception as job_err:
        logger.error(f"Error fetching initial recommended campus drives: {job_err}")
        initial_recommended_jobs = []

    # Prepare initial Role-Based Preparation Mode data
    results = analysis_record.get("results") or {}
    job_match = results.get("job_match") or {}
    skill_gap_items = results.get("skill_gap") or []
    existing_skills = job_match.get("matched_skills") or []
    gap_names = [g.get("missing_skill") for g in skill_gap_items if isinstance(g, dict) and g.get("missing_skill")] or job_match.get("missing_skills", [])

    if is_demo:
        initial_role_prep = get_synthetic_sample_role_prep()
    else:
        initial_role_prep = role_prep_agent.generate_role_preparation(
            target_role="Software Engineer",
            existing_skills=existing_skills,
            skill_gaps=gap_names,
            company_name=analysis_record.get("company_name", "General"),
            resume_text=analysis_record.get("resume_text", ""),
            analysis_record=analysis_record
        )
    # Prepare verified Placement Readiness Summary data
    results = analysis_record.get("results") or {}
    resume_analysis = results.get("resume_analysis") or {}
    job_match = results.get("job_match") or results.get("job_compatibility") or {}
    skill_gap = results.get("skill_gap") or []

    # Prepare Deterministic ATS Resume Score & Section Breakdown (#494)
    raw_resume = (
        analysis_record.get("resume_text")
        or results.get("extracted_resume_text")
        or results.get("resume_text")
        or ""
    )
    ats_breakdown = results.get("ats_breakdown")
    try:
        if not ats_breakdown and raw_resume:
            ats_breakdown = evaluate_resume_ats_score(raw_resume)
        elif not ats_breakdown:
            candidate_skills = resume_analysis.get("skills", [])
            candidate_summary = resume_analysis.get("summary", "")
            synthetic_text = f"Summary: {candidate_summary}\nSkills: {', '.join(candidate_skills)}"
            ats_breakdown = evaluate_resume_ats_score(synthetic_text)

        if ats_breakdown and int(resume_analysis.get("ats_score", 0)) == 0:
            ats_score = ats_breakdown["total_score"]
        else:
            ats_score = int(resume_analysis.get("ats_score", 0)) or (ats_breakdown["total_score"] if ats_breakdown else 0)
    except Exception as ats_err:
        logger.warning(f"Error computing ATS section breakdown for dashboard: {ats_err}")
        ats_breakdown = None
        ats_score = int(resume_analysis.get("ats_score", 0))

    match_percentage = int(job_match.get("match_percentage", 0))
    skill_gaps_count = len(skill_gap) if isinstance(skill_gap, list) else 0
    from utils.score_explanations import build_score_explanations
    score_explanations = build_score_explanations(
        resume_analysis=resume_analysis,
        job_match=job_match,
        resume_text=analysis_record.get("resume_text", ""),
    )

    raw_jd = analysis_record.get("job_description", "") or ""
    has_custom_jd = bool(raw_jd.strip() and raw_jd.strip().lower() != "none provided. assume general software development engineer role.")

    company_benchmark = job_match.get("company_benchmark")
    if not isinstance(company_benchmark, dict):
        company_benchmark = None

    target_ats = company_benchmark.get("target_ats", 75) if company_benchmark else 75
    target_match = company_benchmark.get("target_match", 65) if company_benchmark else 65

    if ats_score >= target_ats and match_percentage >= target_match:
        readiness_status = "Placement Ready"
        readiness_badge = "success"
        readiness_icon = "fa-circle-check"
    elif ats_score >= (target_ats - 8) and match_percentage >= (target_match - 8):
        readiness_status = "Targeted Prep Needed"
        readiness_badge = "primary"
        readiness_icon = "fa-chart-line"
    else:
        readiness_status = "Foundation Building Required"
        readiness_badge = "warning"
        readiness_icon = "fa-triangle-exclamation"

    if company_benchmark and company_benchmark.get("assessment"):
        status_summary = company_benchmark["assessment"]
    else:
        status_summary = f"Profile evaluated against typical campus recruitment benchmarks for {analysis_record.get('company_name', 'General')}."

    top_recommendations = []
    if results.get("overall_recommendation_summary"):
        top_recommendations.append({
            "category": "Overall Assessment",
            "text": results["overall_recommendation_summary"],
            "icon": "fa-quote-left text-primary"
        })
    if company_benchmark and company_benchmark.get("guidance"):
        top_recommendations.append({
            "category": "Preparation Guidance",
            "text": company_benchmark["guidance"],
            "icon": "fa-lightbulb text-warning"
        })
    if job_match.get("top_5_resume_changes") and len(job_match["top_5_resume_changes"]) > 0:
        top_recommendations.append({
            "category": "Priority Action Item",
            "text": job_match["top_5_resume_changes"][0],
            "icon": "fa-wand-magic-sparkles text-info"
        })

    readiness_summary = {
        "ats_score": ats_score,
        "match_percentage": match_percentage,
        "has_custom_jd": has_custom_jd,
        "skill_gaps_count": skill_gaps_count,
        "readiness_status": readiness_status,
        "readiness_badge": readiness_badge,
        "readiness_icon": readiness_icon,
        "status_summary": status_summary,
        "target_ats": target_ats,
        "target_match": target_match,
        "top_recommendations": top_recommendations,
        "score_explanations": score_explanations,
        "ats_breakdown": ats_breakdown,
    }
    # Prepare verified skills comparison metrics for Visual Skills Comparison Chart
    results = analysis_record.get("results") or {}
    job_match = results.get("job_match") or results.get("job_compatibility") or {}

    raw_matched = job_match.get("matched_skills") or job_match.get("matching_skills") or []
    if isinstance(raw_matched, str):
        raw_matched = [s.strip() for s in raw_matched.split(",") if s.strip()]
    elif not isinstance(raw_matched, list):
        raw_matched = []

    raw_missing = job_match.get("missing_skills") or []
    if not raw_missing and results.get("skill_gap"):
        for gap in results["skill_gap"]:
            if isinstance(gap, dict) and gap.get("skill"):
                raw_missing.append(gap["skill"])
            elif isinstance(gap, str) and gap.strip():
                raw_missing.append(gap.strip())
    if isinstance(raw_missing, str):
        raw_missing = [s.strip() for s in raw_missing.split(",") if s.strip()]
    elif not isinstance(raw_missing, list):
        raw_missing = []

    # Normalize skill synonyms and aliases before matching (#843)
    from utils.skill_alias_service import reconcile_matched_and_missing_skills
    clean_matched, clean_missing = reconcile_matched_and_missing_skills(raw_matched, raw_missing)

    matched_count = len(clean_matched)
    missing_count = len(clean_missing)
    total_skills = matched_count + missing_count

    if total_skills > 0:
        match_pct = round((matched_count / total_skills) * 100)
        missing_pct = 100 - match_pct
    else:
        match_pct = int(job_match.get("match_percentage", 0))
        missing_pct = 100 - match_pct if match_pct > 0 else 0

    target_match = 65
    company_bench = job_match.get("company_benchmark")
    if isinstance(company_bench, dict) and company_bench.get("target_match"):
        target_match = company_bench["target_match"]

    skills_comparison = {
        "matched_skills": clean_matched,
        "missing_skills": clean_missing,
        "matched_count": matched_count,
        "missing_count": missing_count,
        "total_skills": total_skills,
        "match_percentage": match_pct,
        "missing_percentage": missing_pct,
        "target_match": target_match
    }

    streak_data = get_student_streak(student_id)

    # Prepare Skill Evidence Mapping (#250)
    results = analysis_record.get("results") or {}
    skill_evidence = results.get("skill_evidence_mapping")
    from utils.link_checker import check_learning_resources
    skill_gap_resources = check_learning_resources(
        results.get("skill_gap") if isinstance(results.get("skill_gap"), list) else []
    )
    from utils.skill_certification_service import skill_certification_service
    skill_gap_resources = skill_certification_service.enrich_skill_gap_items(skill_gap_resources)
    from utils.learning_resource_service import enrich_items_with_resources
    current_uid = flask_session.get("user_id") or getattr(g, "user_id", None)
    skill_gap_resources = enrich_items_with_resources(skill_gap_resources, user_id=current_uid)
    if not skill_evidence or not skill_evidence.get("skills"):
        try:
            from utils.skill_evidence_mapper import map_skill_evidence
            skill_evidence = map_skill_evidence(
                resume_text=analysis_record.get("resume_text", ""),
                analysis_record=analysis_record,
                target_skills=clean_matched or None
            )
        except Exception as sem_err:
            logger.warning(f"Error generating skill evidence mapping for dashboard: {sem_err}")
            skill_evidence = {
                "total_skills": 0,
                "supported_skills_count": 0,
                "unsupported_skills_count": 0,
                "partial_skills_count": 0,
                "evidence_score": 100,
                "summary": "Skill evidence mapping could not be evaluated.",
                "skills": []
            }

    analysis_annotations = get_analysis_annotations(student_id, analysis_id)

    is_cached = (
        request.args.get("from_cache") in ("1", "true", "yes") or
        request.args.get("cached") in ("1", "true", "yes") or
        bool(analysis_record.get("from_cache")) or
        bool(analysis_record.get("is_cached"))
    )

    # Prepare Resume Keyword Highlights (#430)
    try:
        from utils.resume_keyword_highlighter import prepare_keyword_highlight_data
        keyword_highlighter = prepare_keyword_highlight_data(analysis_record)
    except Exception as kh_err:
        logger.warning(f"Error preparing keyword highlight data for dashboard: {kh_err}")
        keyword_highlighter = {
            "has_resume_text": False,
            "raw_resume_text": "",
            "highlighted_resume_html": "",
            "plain_escaped_resume_html": "",
            "matched_keywords": [],
            "missing_keywords": [],
            "missing_keywords_with_sections": [],
            "matched_count": 0,
            "missing_count": 0,
            "total_keywords": 0,
        }

    # Prepare Aptitude Readiness & Weak Topics Summary (#426)
    try:
        aptitude_summary = get_student_aptitude_summary(student_id)
    except Exception as apt_err:
        logger.warning(f"Error preparing aptitude summary for dashboard: {apt_err}")
        aptitude_summary = {
            "has_attempts": False,
            "total_attempts": 0,
            "average_score": 0.0,
            "weak_topics": [],
            "recent_score": None
        }

    # Prepare Weak Bullet Point Rewrites (#429)
    try:
        from utils.bullet_rewriter import get_bullet_rewrites_for_analysis
        bullet_rewrites = get_bullet_rewrites_for_analysis(analysis_record)
    except Exception as br_err:
        logger.warning(f"Error preparing bullet rewrites for dashboard: {br_err}")
        bullet_rewrites = {
            "has_bullets": False,
            "bullets": [],
            "total_detected": 0,
            "empty_message": "Resume bullet rewrites could not be generated."
        }

    # Prepare Role-Targeted Skill Gap Benchmark (#495)
    try:
        available_roles = get_available_roles()
        selected_role_id = (
            request.args.get("role")
            or request.args.get("role_id")
            or (available_roles[0]["id"] if available_roles else "software_developer")
        )
        role_gap_analysis = analyze_role_skill_gap(analysis_record, selected_role_id)
        if role_gap_analysis and isinstance(role_gap_analysis.get("missing_skills"), list):
            role_gap_analysis["missing_skills"] = skill_certification_service.enrich_skill_gap_items(
                role_gap_analysis["missing_skills"]
            )
    except Exception as rsg_err:
        logger.warning(f"Error preparing role-targeted skill gap benchmark for dashboard: {rsg_err}")
        available_roles = []
        selected_role_id = "software_developer"
        role_gap_analysis = {
            "role_id": "software_developer",
            "role_name": "Software Developer",
            "match_percentage": 0,
            "readiness_verdict": "Unknown",
            "summary": "Skill gap benchmark unavailable.",
            "matched_skills": [],
            "missing_skills": [],
        }

    # Prepare ATS Format Compatibility (#474)
    try:
        format_compatibility = get_or_compute_format_compatibility(analysis_record)
    except Exception as fc_err:
        logger.warning(f"Error evaluating ATS format compatibility for dashboard: {fc_err}")
        format_compatibility = {
            "overall_status": "pass",
            "passed_count": 5,
            "warn_count": 0,
            "total_checks": 5,
            "score_percentage": 100,
            "checks": []
        }

    # Prepare Timeline-Driven Learning Roadmap for Missing Skills (#536)
    try:
        initial_timeline_roadmap = generate_learning_roadmap(
            missing_skills=clean_missing,
            weeks=6,
            hours_per_week=10,
            target_role=analysis_record.get("target_role") or "Target Role"
        )
    except Exception as tl_err:
        logger.warning(f"Error generating initial timeline learning roadmap: {tl_err}")
        initial_timeline_roadmap = {
            "success": True,
            "weeks": 6,
            "hours_per_week": 10,
            "is_zero_missing": len(clean_missing) == 0,
            "congratulatory_message": "Congratulations! You have zero missing skills for this target role." if len(clean_missing) == 0 else None,
            "plan": [],
            "deferred_skills": []
        }

    # Attach learning resource progress to initial timeline roadmap (#885)
    if current_uid and initial_timeline_roadmap.get("plan"):
        try:
            from utils.learning_resource_service import get_user_resource_progress
            u_prog = get_user_resource_progress(current_uid)
            for wk in initial_timeline_roadmap["plan"]:
                for cr in wk.get("curated_resources", []):
                    cr["status"] = u_prog.get(cr["id"], "not_started")
        except Exception as prog_err:
            logger.warning(f"Error attaching resource progress: {prog_err}")

        # Drive Day Checklist Reminder (#651)
    drive_reminder = None
    try:
        from utils.drive_day_checklist import get_dashboard_drive_reminder
        drive_reminder = get_dashboard_drive_reminder(student_id)
    except Exception as dr_err:
        logger.warning(f"Error checking drive day reminder for dashboard: {dr_err}")

    # Prepare ATS Score Confidence Indicator (#598)
    try:
        from utils.ats_confidence_service import calculate_ats_score_confidence
        ats_confidence = calculate_ats_score_confidence(
            resume_text=raw_resume,
            format_compat=format_compatibility,
            ats_breakdown=ats_breakdown
        )
    except Exception as conf_err:
        logger.warning(f"Error computing ATS score confidence: {conf_err}")
        ats_confidence = {
            "confidence_level": "High",
            "confidence_score": 85,
            "is_low_confidence": False,
            "badge_class": "bg-success-subtle text-success border border-success-subtle",
            "plain_explanation": "ATS analysis completed with standard confidence.",
            "factors": [],
            "recommendations": [],
            "recheck_url": "/resume/review"
        }

        # Prepare Resume Evidence Cards (#599)
    try:
        from utils.resume_evidence_service import build_resume_evidence_cards
        resume_evidence = build_resume_evidence_cards(analysis_record)
    except Exception as ev_err:
        logger.warning(f"Error building resume evidence cards: {ev_err}")
        resume_evidence = {
            "has_evidence_cards": False,
            "total_cards": 0,
            "direct_evidence_count": 0,
            "missing_info_count": 0,
            "grouped_cards": [],
            "summary": "Evidence cards could not be loaded."
        }

    # Prepare Resume Timeline & Date Consistency Review (#840)
    try:
        from utils.resume_timeline_service import analyze_resume_timeline
        dismissed_timeline_ids = flask_session.get(f"dismissed_timeline_flags_{analysis_id}", [])
        resume_timeline = analyze_resume_timeline(
            raw_resume,
            gap_threshold_months=6,
            dismissed_flag_ids=dismissed_timeline_ids
        )
    except Exception as tl_err:
        logger.warning(f"Error computing resume timeline review for dashboard: {tl_err}")
        resume_timeline = {
            "status": "error",
            "has_flags": False,
            "has_active_flags": False,
            "total_flags": 0,
            "active_flags_count": 0,
            "dismissed_flags_count": 0,
            "gap_threshold_months": 6,
            "summary": {"gaps_count": 0, "overlaps_count": 0, "inverted_count": 0, "future_count": 0},
            "flags": [],
            "timeline_items": []
        }

    # Prepare PDF Metadata & Filename Hygiene (#841)
    try:
        pdf_metadata_hygiene = get_or_compute_pdf_metadata_hygiene(analysis_record)
    except Exception as meta_err:
        logger.warning(f"Error evaluating PDF metadata hygiene for dashboard: {meta_err}")
        pdf_metadata_hygiene = {
            "has_warnings": False,
            "warnings": [],
            "metadata": {},
            "filename": analysis_record.get("resume_filename") or "resume.pdf",
            "clean_filename_pattern": "FirstName_LastName_Role.pdf",
            "suggested_filename": "FirstName_LastName_Resume.pdf",
            "has_any_metadata": False,
            "is_clean": True,
        }

    # Prepare Resume Links Health Check Card (#845)
    try:
        from utils.resume_link_service import analyze_resume_links
        resume_links = analyze_resume_links(
            resume_text=raw_resume,
            check_reachability=True,
            user_id=student_id
        )
    except Exception as link_err:
        logger.warning(f"Error checking resume links for dashboard: {link_err}")
        resume_links = {
            "total_links": 0,
            "working_count": 0,
            "broken_count": 0,
            "malformed_count": 0,
            "unverified_count": 0,
            "has_issues": False,
            "links": [],
            "summary": "Could not analyze resume links."
        }

    return render_template(
        "dashboard.html",
        drive_reminder=drive_reminder,
        analysis=analysis_record,
        analysis_id=analysis_id,
        is_cached=is_cached,
        versions=all_versions,
        current_version=current_version,
        previous_record=previous_record,
        delta_data=delta_data,
        trend_series=trend_series,
        trend_series_json=json.dumps(trend_series),
        initial_outreach=initial_outreach,
        initial_outreach_json=json.dumps(initial_outreach),
        initial_role_prep=initial_role_prep,
        initial_role_prep_json=json.dumps(initial_role_prep),
        initial_timeline_roadmap=initial_timeline_roadmap,
        initial_timeline_roadmap_json=json.dumps(initial_timeline_roadmap),
        supported_roles=SUPPORTED_ROLES,
        available_roles=available_roles,
        selected_role_id=selected_role_id,
        role_gap_analysis=role_gap_analysis,
        role_gap_analysis_json=json.dumps(role_gap_analysis),
        readiness_summary=readiness_summary,
        skills_comparison=skills_comparison,
        skills_comparison_json=json.dumps(skills_comparison),
        skill_evidence=skill_evidence,
        skill_evidence_json=json.dumps(skill_evidence),
        skill_gap_resources=skill_gap_resources,
        analysis_annotations=analysis_annotations,
        keyword_highlighter=keyword_highlighter,
        score_explanations=score_explanations,
        streak=streak_data,
        comparison=comparison,
        comparison_json=json.dumps(comparison),
        initial_self_intro=initial_self_intro,
        initial_self_intro_json=json.dumps(initial_self_intro),
        aptitude_summary=aptitude_summary,
        is_demo=is_demo,
        bullet_rewrites=bullet_rewrites,
        format_compatibility=format_compatibility,
        format_compatibility_json=json.dumps(format_compatibility),
        ats_confidence=ats_confidence,
        ats_confidence_json=json.dumps(ats_confidence),
        resume_evidence=resume_evidence,
        resume_evidence_json=json.dumps(resume_evidence),
        resume_timeline=resume_timeline,
        resume_timeline_json=json.dumps(resume_timeline),
        initial_recommended_jobs=initial_recommended_jobs,
        initial_recommended_jobs_json=json.dumps(initial_recommended_jobs),
        pdf_metadata_hygiene=pdf_metadata_hygiene,
        pdf_metadata_hygiene_json=json.dumps(pdf_metadata_hygiene),
        resume_links=resume_links,
        resume_links_json=json.dumps(resume_links)
    )


@analysis_bp.route("/api/analysis/<analysis_id>/resume-links", methods=["GET"])
def api_get_resume_links(analysis_id: str):
    """
    Returns profile and external links extracted from resume with format
    and reachability health check statuses (#845).
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    results = analysis_record.get("results") or {}
    raw_resume = (
        analysis_record.get("resume_text")
        or results.get("extracted_resume_text")
        or results.get("resume_text")
        or ""
    )

    from utils.resume_link_service import analyze_resume_links
    student_id = flask_session.get("user_id") or flask_session.get("student_id") or analysis_record.get("student_id")
    check_reachability = request.args.get("check_reachability", "true").lower() in ("true", "1", "yes")

    links_data = analyze_resume_links(
        resume_text=raw_resume,
        check_reachability=check_reachability,
        user_id=student_id
    )

    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "resume_links": links_data
    }), 200


@analysis_bp.route("/api/resume/check-links", methods=["POST"])
def api_check_resume_links_stateless():
    """
    Stateless endpoint to extract and evaluate links from resume text (#845).
    Body: {"resume_text": "...", "check_reachability": true}
    """
    data = request.get_json(silent=True) or request.form or {}
    resume_text = data.get("resume_text", "")
    check_reachability = str(data.get("check_reachability", "true")).lower() in ("true", "1", "yes")
    student_id = flask_session.get("user_id") or flask_session.get("student_id") or "anonymous"

    from utils.resume_link_service import analyze_resume_links
    try:
        links_data = analyze_resume_links(
            resume_text=resume_text,
            check_reachability=check_reachability,
            user_id=student_id
        )
        return jsonify({
            "status": "success",
            "success": True,
            "resume_links": links_data
        }), 200
    except Exception as exc:
        logger.error(f"Error checking resume links: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/analysis/<analysis_id>/linkedin-suggestions", methods=["GET", "POST"])
def api_get_analysis_linkedin_suggestions(analysis_id: str):
    """
    Returns LinkedIn headline options, first-person About draft, and recruiter keywords (#538).
    Strictly uses facts verified in candidate's resume, respects 220 char limit on headlines,
    and handles sparse/minimal resumes gracefully.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    results = analysis_record.get("results") or {}
    raw_resume = (
        analysis_record.get("resume_text")
        or results.get("extracted_resume_text")
        or results.get("resume_text")
        or ""
    )

    # Allow overriding target_role from query param or request JSON/form
    target_role = (
        request.args.get("target_role")
        or (request.get_json(silent=True) or {}).get("target_role")
        or request.form.get("target_role")
        or analysis_record.get("target_role")
        or results.get("target_role")
        or "Software Engineer"
    ).strip()

    suggestions = linkedin_agent.generate_linkedin_suggestions(
        resume_text=raw_resume,
        target_role=target_role
    )

    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "suggestions": suggestions
    }), 200


@analysis_bp.route("/cover-letter", methods=["GET"])
@analysis_bp.route("/cover-letter/<analysis_id>", methods=["GET"])
def cover_letter_view(analysis_id: str = None):
    """
    Renders the dedicated Tailored Cover Letter Generator page (#537).
    Allows students to generate 3-4 paragraph letters tailored to a pasted job description,
    choose between formal and friendly tones, edit the letter, and copy/download it.
    """
    resume_text = ""
    company_name = ""
    target_role = "Software Engineer"

    if analysis_id:
        analysis_record = get_analysis(analysis_id)
        if analysis_record and _is_student_authorized(analysis_record):
            results = analysis_record.get("results") or {}
            resume_text = (
                analysis_record.get("resume_text")
                or results.get("extracted_resume_text")
                or results.get("resume_text")
                or ""
            )
            company_name = analysis_record.get("company_name", "") or ""
            target_role = analysis_record.get("target_role", "Software Engineer") or "Software Engineer"

    return render_template(
        "cover_letter.html",
        resume_text=resume_text,
        company_name=company_name,
        target_role=target_role,
        analysis_id=analysis_id or ""
    )


@analysis_bp.route("/api/cover-letter/generate", methods=["POST"])
def api_generate_tailored_cover_letter():
    """
    Direct API endpoint to generate a tailored 3-to-4 paragraph cover letter from
    a resume, pasted job description, and optional company name (#537).
    """
    payload = request.get_json(silent=True) or {}
    resume_text = (payload.get("resume_text") or request.form.get("resume_text") or "").strip()
    job_description = (payload.get("job_description") or request.form.get("job_description") or "").strip()
    company_name = (payload.get("company_name") or request.form.get("company_name") or "").strip()
    tone = (payload.get("tone") or request.form.get("tone") or "formal").strip()

    if not job_description:
        return jsonify({
            "success": False,
            "error": "A job description is required to generate a tailored cover letter. Please paste the target job description.",
            "missing_field": "job_description"
        }), 400

    if not resume_text:
        return jsonify({
            "success": False,
            "error": "Resume text is missing. Please provide your resume to tailor the cover letter.",
            "missing_field": "resume_text"
        }), 400

    result = outreach_agent.generate_tailored_cover_letter(
        resume_text=resume_text,
        job_description=job_description,
        company_name=company_name,
        tone=tone
    )

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@analysis_bp.route("/api/analysis/<analysis_id>/cover-letter", methods=["POST"])
def api_analysis_tailored_cover_letter(analysis_id: str):
    """
    Generates a tailored 3-to-4 paragraph cover letter for a saved placement analysis (#537).
    Loads the candidate's resume, takes the pasted job description, optional company name, and tone.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({
            "success": False,
            "error": "Analysis not found or unauthorized."
        }), 404

    results = analysis_record.get("results") or {}
    raw_resume = (
        analysis_record.get("resume_text")
        or results.get("extracted_resume_text")
        or results.get("resume_text")
        or ""
    )

    payload = request.get_json(silent=True) or {}
    job_description = (payload.get("job_description") or request.form.get("job_description") or "").strip()
    company_name = (
        payload.get("company_name")
        or request.form.get("company_name")
        or analysis_record.get("company_name")
        or ""
    ).strip()
    tone = (payload.get("tone") or request.form.get("tone") or "formal").strip()

    if not job_description:
        return jsonify({
            "success": False,
            "error": "A job description is required to generate a tailored cover letter. Please paste the target job description.",
            "missing_field": "job_description"
        }), 400

    result = outreach_agent.generate_tailored_cover_letter(
        resume_text=raw_resume,
        job_description=job_description,
        company_name=company_name,
        tone=tone
    )

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@analysis_bp.route("/api/linkedin/suggestions", methods=["POST"])
def api_generate_linkedin_suggestions():
    """
    Direct API endpoint to generate LinkedIn suggestions from raw resume text (#538).
    Accepts JSON: { "resume_text": "...", "target_role": "..." }
    """
    payload = request.get_json(silent=True) or {}
    resume_text = (payload.get("resume_text") or request.form.get("resume_text") or "").strip()
    target_role = (payload.get("target_role") or request.form.get("target_role") or "Software Engineer").strip()

    suggestions = linkedin_agent.generate_linkedin_suggestions(
        resume_text=resume_text,
        target_role=target_role
    )

    return jsonify({
        "status": "success",
        "success": True,
        "suggestions": suggestions
    }), 200


@analysis_bp.route("/api/analysis/<analysis_id>/custom-roadmap", methods=["POST"])
def api_generate_custom_analysis_roadmap(analysis_id: str):
    """
    Generates a personalized week-by-week learning roadmap for missing skills (#536).
    Accepts student timeline choice (weeks) and weekly study hours commitment.
    Orders missing skills by prerequisites and importance.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or access denied."}), 404

    payload = request.get_json(silent=True) or request.form or {}
    try:
        weeks = int(payload.get("weeks", 6))
    except (ValueError, TypeError):
        weeks = 6
    try:
        hours_per_week = int(payload.get("hours_per_week", 10))
    except (ValueError, TypeError):
        hours_per_week = 10

    # Gather missing skills from analysis record
    results = analysis_record.get("results") or {}
    job_match = results.get("job_match") or results.get("job_compatibility") or {}
    missing_skills = []

    raw_missing = job_match.get("missing_skills") or []
    if isinstance(raw_missing, list):
        missing_skills.extend(raw_missing)
    elif isinstance(raw_missing, str) and raw_missing.strip():
        missing_skills.extend([s.strip() for s in raw_missing.split(",") if s.strip()])

    if results.get("skill_gap") and isinstance(results["skill_gap"], list):
        for sg in results["skill_gap"]:
            if isinstance(sg, dict):
                skill_name = sg.get("skill") or sg.get("missing_skill")
                if skill_name:
                    missing_skills.append({
                        "skill": skill_name,
                        "importance": sg.get("importance") or sg.get("priority", "Medium"),
                        "difficulty": sg.get("difficulty", "Medium")
                    })
            elif isinstance(sg, str) and sg.strip():
                missing_skills.append(sg.strip())

    target_role = analysis_record.get("target_role") or "Target Role"

    roadmap_data = generate_learning_roadmap(
        missing_skills=missing_skills,
        weeks=weeks,
        hours_per_week=hours_per_week,
        target_role=target_role
    )

    # Attach user progress if logged in (#885)
    current_uid = flask_session.get("user_id") or getattr(g, "user_id", None)
    if current_uid and roadmap_data.get("plan"):
        from utils.learning_resource_service import get_user_resource_progress
        u_prog = get_user_resource_progress(current_uid)
        for wk in roadmap_data["plan"]:
            for cr in wk.get("curated_resources", []):
                cr["status"] = u_prog.get(cr["id"], "not_started")

    return jsonify(roadmap_data), 200


# ---------------------------------------------------------------------------
# Learning Resources Progress API Endpoints (#885)
# ---------------------------------------------------------------------------

@analysis_bp.route("/api/learning-resources/progress", methods=["GET"])
def api_get_learning_resources_progress():
    """
    Returns progress mapping (resource_id -> status) for the logged-in student.
    """
    user_id = flask_session.get("user_id") or getattr(g, "user_id", None)
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required."}), 401

    from utils.learning_resource_service import get_user_resource_progress
    progress = get_user_resource_progress(user_id)
    return jsonify({"success": True, "progress": progress}), 200


@analysis_bp.route("/api/learning-resources/progress", methods=["POST"])
def api_update_learning_resource_progress():
    """
    Updates the student's progress for a learning resource ('not_started', 'started', 'done').
    """
    user_id = flask_session.get("user_id") or getattr(g, "user_id", None)
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required."}), 401

    payload = request.get_json(silent=True) or request.form or {}
    resource_id = payload.get("resource_id")
    status = payload.get("status")
    skill_name = payload.get("skill_name", "")

    if not resource_id or not status:
        return jsonify({"success": False, "error": "resource_id and status are required."}), 400

    from utils.learning_resource_service import set_user_resource_progress, VALID_STATUSES
    if status.lower().strip() not in VALID_STATUSES:
        return jsonify({
            "success": False,
            "error": f"Invalid status '{status}'. Must be one of {sorted(VALID_STATUSES)}."
        }), 400

    try:
        result = set_user_resource_progress(
            user_id=user_id,
            resource_id=resource_id,
            status=status,
            skill_name=skill_name
        )
        return jsonify(result), 200
    except Exception as e:
        logger.exception(f"Failed to update resource progress: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@analysis_bp.route("/api/analysis/<analysis_id>/roadmap/calendar.ics", methods=["GET", "POST"])
def api_export_roadmap_calendar(analysis_id: str):
    """
    Exports a personalised week-by-week study roadmap as an RFC 5545 iCalendar (.ics) file (#647).
    Allows choosing preparation start date and daily study time (default 19:00 / 1 hour).
    Generates deterministic UIDs so re-importing into Google Calendar or Apple Calendar updates
    existing events rather than creating duplicates. Contains zero personal data.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or access denied."}), 404

    payload = request.get_json(silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    start_date = payload.get("start_date") or request.args.get("start_date")
    daily_study_time = (
        payload.get("study_time")
        or payload.get("daily_study_time")
        or request.args.get("study_time")
        or request.args.get("daily_study_time")
        or "19:00"
    )
    duration_val = (
        payload.get("duration")
        or payload.get("duration_minutes")
        or request.args.get("duration")
        or request.args.get("duration_minutes")
        or 60
    )
    try:
        duration_minutes = int(duration_val)
    except (ValueError, TypeError):
        duration_minutes = 60

    all_day_raw = payload.get("all_day") if "all_day" in payload else request.args.get("all_day")
    all_day = str(all_day_raw).lower() in ("true", "1", "yes")

    roadmap_data = payload.get("custom_roadmap") or payload.get("roadmap") or payload.get("plan")
    if not roadmap_data:
        results = analysis_record.get("results") or {}
        job_match = results.get("job_match") or results.get("job_compatibility") or {}
        missing_skills = []

        raw_missing = job_match.get("missing_skills") or []
        if isinstance(raw_missing, list):
            missing_skills.extend(raw_missing)
        elif isinstance(raw_missing, str) and raw_missing.strip():
            missing_skills.extend([s.strip() for s in raw_missing.split(",") if s.strip()])

        if results.get("skill_gap") and isinstance(results["skill_gap"], list):
            for sg in results["skill_gap"]:
                if isinstance(sg, dict):
                    skill_name = sg.get("skill") or sg.get("missing_skill")
                    if skill_name:
                        missing_skills.append({
                            "skill": skill_name,
                            "importance": sg.get("importance") or sg.get("priority", "Medium"),
                            "difficulty": sg.get("difficulty", "Medium")
                        })
                elif isinstance(sg, str) and sg.strip():
                    missing_skills.append(sg.strip())

        try:
            weeks = int(payload.get("weeks") or request.args.get("weeks") or 6)
        except (ValueError, TypeError):
            weeks = 6
        try:
            hours_per_week = int(payload.get("hours_per_week") or request.args.get("hours_per_week") or 10)
        except (ValueError, TypeError):
            hours_per_week = 10

        target_role = analysis_record.get("target_role") or "Target Role"
        roadmap_data = generate_learning_roadmap(
            missing_skills=missing_skills,
            weeks=weeks,
            hours_per_week=hours_per_week,
            target_role=target_role
        )

    company_name = analysis_record.get("company_name", "Target Company")
    target_role = analysis_record.get("target_role")

    ics_content = generate_roadmap_ics(
        roadmap_data=roadmap_data,
        start_date=start_date,
        daily_study_time=daily_study_time,
        duration_minutes=duration_minutes,
        all_day=all_day,
        analysis_id=analysis_id,
        company_name=company_name,
        target_role=target_role,
        app_base_url=request.host_url.rstrip("/"),
    )

    clean_comp = re.sub(r"[^a-zA-Z0-9_-]", "_", company_name or "placement")
    filename = f"placement-roadmap-{clean_comp}.ics"

    response = make_response(ics_content)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@analysis_bp.route("/api/roadmap/calendar.ics", methods=["POST"])
def api_export_roadmap_calendar_standalone():
    """
    Direct endpoint to export a roadmap plan as an RFC 5545 iCalendar (.ics) file (#647).
    """
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    roadmap_data = payload.get("roadmap") or payload.get("plan") or payload
    start_date = payload.get("start_date")
    daily_study_time = payload.get("study_time") or payload.get("daily_study_time") or "19:00"
    duration_val = payload.get("duration") or payload.get("duration_minutes") or 60
    try:
        duration_minutes = int(duration_val)
    except (ValueError, TypeError):
        duration_minutes = 60

    all_day_raw = payload.get("all_day")
    all_day = str(all_day_raw).lower() in ("true", "1", "yes")
    analysis_id = payload.get("analysis_id", "custom")
    company_name = payload.get("company_name")
    target_role = payload.get("target_role")

    if not roadmap_data or (isinstance(roadmap_data, dict) and not roadmap_data.get("plan")):
        missing_skills = payload.get("missing_skills", [])
        try:
            weeks = int(payload.get("weeks", 6))
        except (ValueError, TypeError):
            weeks = 6
        try:
            hours_per_week = int(payload.get("hours_per_week", 10))
        except (ValueError, TypeError):
            hours_per_week = 10

        roadmap_data = generate_learning_roadmap(
            missing_skills=missing_skills,
            weeks=weeks,
            hours_per_week=hours_per_week,
            target_role=target_role or "Target Role"
        )

    ics_content = generate_roadmap_ics(
        roadmap_data=roadmap_data,
        start_date=start_date,
        daily_study_time=daily_study_time,
        duration_minutes=duration_minutes,
        all_day=all_day,
        analysis_id=analysis_id,
        company_name=company_name,
        target_role=target_role,
        app_base_url=request.host_url.rstrip("/"),
    )

    clean_comp = re.sub(r"[^a-zA-Z0-9_-]", "_", company_name or "study")
    filename = f"placement-roadmap-{clean_comp}.ics"

    response = make_response(ics_content)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@analysis_bp.route("/api/cover-letter/download", methods=["POST"])
def api_download_cover_letter():
    """
    Downloads an edited cover letter as a formatted .docx or .txt file (#537).
    """
    payload = request.get_json(silent=True) or {}
    content = (payload.get("content") or payload.get("cover_letter") or request.form.get("content") or request.form.get("cover_letter") or "").strip()
    file_format = (payload.get("format") or request.form.get("format") or "docx").strip().lower()
    company_name = (payload.get("company_name") or request.form.get("company_name") or "Company").strip()
    candidate_name = (payload.get("candidate_name") or request.form.get("candidate_name") or "Candidate").strip()
    role_title = (payload.get("role_title") or request.form.get("role_title") or "Software Engineer").strip()

    if not content:
        return jsonify({
            "success": False,
            "error": "Cannot download empty cover letter content."
        }), 400

    safe_comp = re.sub(r"[^\w\-]", "_", company_name)
    if file_format == "txt":
        txt_out = outreach_agent.export_cover_letter_txt(
            cover_letter_text=content,
            candidate_name=candidate_name,
            company_name=company_name,
            role_title=role_title
        )
        return send_file(
            io.BytesIO(txt_out.encode("utf-8")),
            mimetype="text/plain",
            as_attachment=True,
            download_name=f"Cover_Letter_{safe_comp}.txt"
        )
    else:  # default docx
        docx_bytes = outreach_agent.export_cover_letter_docx(
            cover_letter_text=content,
            candidate_name=candidate_name,
            company_name=company_name,
            role_title=role_title
        )
        return send_file(
            io.BytesIO(docx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=f"Cover_Letter_{safe_comp}.docx"
        )


@analysis_bp.route("/api/analysis/<analysis_id>/ats-breakdown", methods=["GET"])
def api_get_analysis_ats_breakdown(analysis_id: str):
    """
    Returns the deterministic ATS resume score and section-wise breakdown (#494).
    Scores contact info, summary, skills, experience, projects, and education
    out of 100, evaluates basics checks, and identifies the section hurting the score most.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    results = analysis_record.get("results") or {}
    ats_breakdown = results.get("ats_breakdown")
    if not ats_breakdown:
        from utils.ats_score_service import evaluate_resume_ats_score
        raw_resume = analysis_record.get("resume_text", "")
        ats_breakdown = evaluate_resume_ats_score(raw_resume)

    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "total_score": ats_breakdown.get("total_score", 0),
        "rating": ats_breakdown.get("rating", "Needs Rework"),
        "hurts_most": ats_breakdown.get("hurts_most"),
        "sections": ats_breakdown.get("sections", {}),
        "basics": ats_breakdown.get("basics", {}),
        "data": ats_breakdown,
    }), 200


@analysis_bp.route("/api/resume/ats-score", methods=["POST"])
def api_evaluate_resume_ats_score():
    """
    Evaluates resume text deterministically and returns the ATS section breakdown (#494).
    Accepts JSON body:
      - resume_text: raw text of the resume to score
      - analysis_id: (optional) ID of existing analysis to load text from
    """
    try:
        data = request.get_json(silent=True) or {}
        resume_text = data.get("resume_text")
        analysis_id = data.get("analysis_id")

        if not resume_text and analysis_id:
            record = get_analysis(analysis_id)
            if record and _is_student_authorized(record):
                resume_text = record.get("resume_text", "")

        from utils.ats_score_service import evaluate_resume_ats_score
        ats_breakdown = evaluate_resume_ats_score(resume_text or "")
        return jsonify({
            "status": "success",
            "success": True,
            "total_score": ats_breakdown["total_score"],
            "rating": ats_breakdown["rating"],
            "hurts_most": ats_breakdown["hurts_most"],
            "sections": ats_breakdown["sections"],
            "basics": ats_breakdown["basics"],
            "data": ats_breakdown,
        }), 200
    except Exception as exc:
        logger.error(f"Error in api_evaluate_resume_ats_score: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/analysis/<analysis_id>/timeline", methods=["GET"])
def api_get_analysis_timeline(analysis_id: str):
    """
    Returns the date consistency, timeline gaps, and overlaps review for a candidate resume (#840).
    Guarantees neutral phrasing without assumed reasons, constructive suggestions, and
    reflects any flags dismissed by the candidate.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    results = analysis_record.get("results") or {}
    raw_resume = (
        analysis_record.get("resume_text")
        or results.get("extracted_resume_text")
        or results.get("resume_text")
        or ""
    )
    from utils.resume_timeline_service import analyze_resume_timeline
    dismissed_ids = flask_session.get(f"dismissed_timeline_flags_{analysis_id}", [])
    gap_threshold = request.args.get("gap_threshold", default=6, type=int)

    timeline_data = analyze_resume_timeline(
        raw_resume,
        gap_threshold_months=gap_threshold,
        dismissed_flag_ids=dismissed_ids
    )

    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "timeline": timeline_data
    }), 200


@analysis_bp.route("/api/analysis/<analysis_id>/dismiss-timeline-flag", methods=["POST"])
def api_dismiss_timeline_flag(analysis_id: str):
    """
    Allows a student to dismiss or restore a timeline gap/overlap flag (#840).
    Saves state in session and updates persistent record if present.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    payload = request.get_json(silent=True) or request.form or {}
    flag_id = payload.get("flag_id")
    dismissed = payload.get("dismissed", True)
    if isinstance(dismissed, str):
        dismissed = dismissed.lower() in ("true", "1", "yes")

    if not flag_id:
        return jsonify({"status": "error", "success": False, "error": "flag_id is required."}), 400

    session_key = f"dismissed_timeline_flags_{analysis_id}"
    dismissed_set = set(flask_session.get(session_key, []))

    if dismissed:
        dismissed_set.add(flag_id)
    else:
        dismissed_set.discard(flag_id)

    flask_session[session_key] = list(dismissed_set)
    flask_session.modified = True

    # Re-evaluate timeline to return updated counts
    results = analysis_record.get("results") or {}
    raw_resume = (
        analysis_record.get("resume_text")
        or results.get("extracted_resume_text")
        or results.get("resume_text")
        or ""
    )
    from utils.resume_timeline_service import analyze_resume_timeline
    updated_timeline = analyze_resume_timeline(
        raw_resume,
        dismissed_flag_ids=list(dismissed_set)
    )

    # If results dictionary is in analysis_record, update timeline_analysis
    if isinstance(analysis_record.get("results"), dict):
        analysis_record["results"]["timeline_analysis"] = updated_timeline
        try:
            update_analysis_section(
                analysis_id=analysis_id,
                section_db_key="timeline_analysis",
                section_payload=updated_timeline,
                section_key="timeline_analysis",
            )
        except Exception as save_err:
            logger.debug(f"Could not persist updated timeline to db: {save_err}")

    return jsonify({
        "status": "success",
        "success": True,
        "flag_id": flag_id,
        "is_dismissed": bool(dismissed),
        "active_flags_count": updated_timeline["active_flags_count"],
        "dismissed_flags_count": updated_timeline["dismissed_flags_count"],
        "has_active_flags": updated_timeline["has_active_flags"],
    }), 200


@analysis_bp.route("/api/resume/timeline-check", methods=["POST"])
def api_check_resume_timeline():
    """
    Direct stateless endpoint to parse and evaluate resume timeline consistency (#840).
    Accepts JSON body:
      - resume_text: raw text of resume
      - gap_threshold_months: (optional, default 6)
    """
    try:
        data = request.get_json(silent=True) or {}
        resume_text = data.get("resume_text", "")
        gap_threshold = int(data.get("gap_threshold_months", 6))

        from utils.resume_timeline_service import analyze_resume_timeline
        timeline_result = analyze_resume_timeline(resume_text, gap_threshold_months=gap_threshold)
        return jsonify({
            "status": "success",
            "success": True,
            "timeline": timeline_result
        }), 200
    except Exception as exc:
        logger.error(f"Error in api_check_resume_timeline: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500





@analysis_bp.route("/api/analysis/<analysis_id>/resume-evidence", methods=["GET"])
def api_get_analysis_resume_evidence(analysis_id: str):
    """
    Returns structured Resume Evidence Cards connecting AI suggestions to original resume text (#599).
    Flags unmentioned content as missing information rather than confirmed defects.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    from utils.resume_evidence_service import build_resume_evidence_cards
    evidence_cards = build_resume_evidence_cards(analysis_record)
    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "evidence": evidence_cards
    }), 200


@analysis_bp.route("/api/analysis/<analysis_id>/format-compatibility", methods=["GET"])
def api_get_analysis_format_compatibility(analysis_id: str):
    """
    Returns the rule-based ATS format compatibility evaluation (#474).
    Evaluates page count, multi-column layout, tables/grids, embedded images,
    and text extraction/encoding legibility with pass/warn items and fixes.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    format_compat = get_or_compute_format_compatibility(analysis_record)
    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "format_compatibility": format_compat
    }), 200


@analysis_bp.route("/api/analysis/<analysis_id>/pdf-page-issues", methods=["GET"])
def api_get_analysis_pdf_page_issues(analysis_id: str):
    """
    Returns page-level PDF layout and extraction diagnostics for a resume (#597).
    Reports page numbers with multi-column, table, graphic, or extraction issues.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    from utils.pdf_page_issue_detector import analyze_pdf_page_issues
    file_id = analysis_record.get("id") or analysis_record.get("file_uuid")
    fname = analysis_record.get("resume_filename") or ""
    report = None
    try:
        import config
        upload_folder = getattr(config, "UPLOAD_FOLDER", "uploads")
        if file_id and fname:
            from werkzeug.utils import secure_filename
            target_path = os.path.join(upload_folder, secure_filename(f"{file_id}_{fname}"))
            if os.path.exists(target_path) and target_path.lower().endswith(".pdf"):
                report = analyze_pdf_page_issues(target_path)
    except Exception as e:
        logger.debug(f"Could not load PDF file for page issues: {e}")

    if not report:
        # Check if already present in format_compatibility
        fc = get_or_compute_format_compatibility(analysis_record)
        if fc and fc.get("page_level_issues"):
            report = fc["page_level_issues"]
        else:
            report = {
                "success": True,
                "total_pages": 1,
                "problem_page_count": 0,
                "problem_page_numbers": [],
                "overall_status": "clean",
                "pages": [{
                    "page_number": 1,
                    "has_issues": False,
                    "issue_count": 0,
                    "word_count": len((analysis_record.get("resume_text") or "").split()),
                    "char_count": len((analysis_record.get("resume_text") or "").strip()),
                    "snippet": (analysis_record.get("resume_text") or "")[:200],
                    "issues": []
                }],
                "summary_message": "Single-page text representation analyzed.",
                "general_recommendations": []
            }

    return jsonify({
        "status": "success",
        "success": True,
        "analysis_id": analysis_id,
        "page_issues": report
    }), 200


@analysis_bp.route("/api/resume/parse-sections", methods=["POST"])
def api_parse_resume_sections():
    """
    Parses uploaded resume or text into canonical sections for user review & approval (#601).
    Does not modify files on disk. Returns detected sections and missing warnings.
    """
    extracted_text, filename, file_uuid, validation_error = _extract_and_validate_resume(request)
    if validation_error:
        return jsonify({"success": False, "error": validation_error}), 400

    from utils.resume_section_approval_service import parse_resume_for_approval
    try:
        parsed = parse_resume_for_approval(extracted_text)
        return jsonify({
            "status": "success",
            "success": True,
            "filename": filename,
            "file_uuid": file_uuid,
            "data": parsed
        }), 200
    except Exception as exc:
        logger.error(f"Error parsing resume sections: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/resume/format-compatibility", methods=["POST"])
def api_evaluate_resume_format_compatibility():
    """
    Evaluates resume format compatibility on demand (#474).
    Accepts:
      - Uploaded resume file via multipart/form-data ('resume' file field)
      - JSON body: {"resume_text": "...", "analysis_id": "..."}
    """
    try:
        uploaded_file = request.files.get("resume")
        if uploaded_file and uploaded_file.filename != "":
            raw_bytes = uploaded_file.read()
            ext = os.path.splitext(uploaded_file.filename)[1].lower()
            if ext == ".pdf":
                format_compat = check_pdf_format_compatibility(raw_bytes)
            else:
                format_compat = check_resume_format_compatibility(raw_bytes, "")
            return jsonify({
                "status": "success",
                "success": True,
                "format_compatibility": format_compat
            }), 200

        data = request.get_json(silent=True) or {}
        analysis_id = data.get("analysis_id")
        if analysis_id:
            record = get_analysis(analysis_id)
            if record and _is_student_authorized(record):
                format_compat = get_or_compute_format_compatibility(record)
                return jsonify({
                    "status": "success",
                    "success": True,
                    "analysis_id": analysis_id,
                    "format_compatibility": format_compat
                }), 200

        resume_text = data.get("resume_text") or request.form.get("resume_text", "")
        format_compat = check_resume_format_compatibility(resume_text, resume_text)
        return jsonify({
            "status": "success",
            "success": True,
            "format_compatibility": format_compat
        }), 200
    except Exception as exc:
        logger.error(f"Error in api_evaluate_resume_format_compatibility: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/job-description/check-quality", methods=["POST"])
def api_check_job_description_quality():
    """
    Evaluates job description quality score, detected sections, and missing parts (#602).
    Never stores the description and never rejects valid short text automatically.
    """
    payload = request.get_json(silent=True) or request.form.to_dict()
    jd_text = (payload.get("job_description") or payload.get("text") or "").strip()

    from utils.jd_quality_checker_service import evaluate_job_description_quality
    try:
        report = evaluate_job_description_quality(jd_text)
        return jsonify({
            "status": "success",
            "success": True,
            "report": report
        }), 200
    except Exception as exc:
        logger.error(f"Error checking job description quality: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/resume/bullet-rewrites/<analysis_id>", methods=["GET"])
def get_bullet_rewrites_api(analysis_id: str):
    """Return detected weak bullets and suggested rewrites for an analysis (#429)."""
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"error": "Analysis not found"}), 404

    try:
        from utils.bullet_rewriter import get_bullet_rewrites_for_analysis
        data = get_bullet_rewrites_for_analysis(analysis_record)
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Error in bullet-rewrites API: {e}")
        return jsonify({"error": str(e), "has_bullets": False, "bullets": []}), 500


@analysis_bp.route("/api/timetable/generate", methods=["POST"])
def api_generate_timetable():
    """
    Generates personalized weekly preparation timetable based on daily hours (#605).
    """
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    availability = payload.get("availability")
    if availability is None:
        availability = (
            payload.get("daily_hours")
            or payload.get("daily_study_time")
            or payload.get("hours")
            or payload.get("available_hours")
            or {}
        )
    preferred_slot = payload.get("preferred_slot", "Evening (18:00 - 21:00)")
    custom_tasks = payload.get("custom_tasks")

    from utils.prep_timetable_service import generate_weekly_timetable
    try:
        result = generate_weekly_timetable(
            availability=availability,
            preferred_slot=preferred_slot,
            custom_tasks=custom_tasks
        )
        return jsonify(result), 200
    except ValueError as ve:
        return jsonify({"status": "error", "success": False, "error": str(ve)}), 400
    except Exception as exc:
        logger.error(f"Error generating timetable: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/study-time/allocate", methods=["POST"])
@analysis_bp.route("/api/study-schedule/daily", methods=["GET", "POST"])
def api_allocate_study_time():
    """
    Generates structured daily preparation schedule based on user's available study time.
    Accepts daily study hours (e.g. 2.0) or daily availability map.
    """
    if request.method == "GET":
        raw_hours = (
            request.args.get("hours")
            or request.args.get("daily_hours")
            or request.args.get("daily_study_time")
            or 2.0
        )
        preferred_slot = request.args.get("preferred_slot") or "Evening (18:00 - 21:00)"
        payload = {"daily_hours": raw_hours, "preferred_slot": preferred_slot}
    else:
        payload = request.get_json(silent=True) or request.form.to_dict() or {}

    available_time = payload.get("available_time")
    if available_time is None:
        available_time = (
            payload.get("daily_hours")
            or payload.get("daily_study_time")
            or payload.get("hours")
            or payload.get("study_time")
            or payload.get("availability")
            or 2.0
        )
    preferred_slot = payload.get("preferred_slot", "Evening (18:00 - 21:00)")
    custom_tasks = payload.get("custom_tasks")

    from utils.prep_timetable_service import allocate_daily_study_time
    try:
        result = allocate_daily_study_time(
            available_time=available_time,
            preferred_slot=preferred_slot,
            custom_tasks=custom_tasks
        )
        return jsonify(result), 200
    except ValueError as ve:
        return jsonify({"status": "error", "success": False, "error": str(ve)}), 400
    except Exception as exc:
        logger.error(f"Error allocating daily study time: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/snapshots", methods=["GET", "POST"])
def api_snapshots_collection():
    """
    Lists student preparation progress snapshots or creates a new manual/automatic snapshot (#607).
    """
    student_id = flask_session.get("user_id") or "default_student"
    from utils.prep_snapshot_service import (
        create_progress_snapshot,
        list_student_snapshots,
        compare_two_snapshots
    )

    if request.method == "GET":
        compare_a = request.args.get("compare_a")
        compare_b = request.args.get("compare_b")
        snapshots = list_student_snapshots(student_id)

        if compare_a and compare_b:
            snap_map = {s["id"]: s for s in snapshots}
            if compare_a in snap_map and compare_b in snap_map:
                comparison = compare_two_snapshots(snap_map[compare_a], snap_map[compare_b])
                return jsonify({"status": "success", "comparison": comparison}), 200
            return jsonify({"status": "error", "message": "One or both snapshots not found."}), 404

        return jsonify({"status": "success", "snapshots": snapshots}), 200

    payload = request.get_json(silent=True) or request.form.to_dict()
    label = payload.get("label", "Manual Snapshot")
    ats_score = payload.get("ats_score")
    coding_score = payload.get("coding_score")
    interview_score = payload.get("interview_score")
    skill_gaps_count = payload.get("skill_gaps_count")
    matched_skills_count = payload.get("matched_skills_count")

    new_snap = create_progress_snapshot(
        student_id=student_id,
        label=label,
        ats_score=int(ats_score) if ats_score is not None else None,
        coding_score=int(coding_score) if coding_score is not None else None,
        interview_score=int(interview_score) if interview_score is not None else None,
        skill_gaps_count=int(skill_gaps_count) if skill_gaps_count is not None else None,
        matched_skills_count=int(matched_skills_count) if matched_skills_count is not None else None
    )
    return jsonify({"status": "success", "snapshot": new_snap}), 201


@analysis_bp.route("/api/snapshots/<snapshot_id>", methods=["DELETE"])
def api_delete_snapshot(snapshot_id: str):
    """Deletes an owned preparation progress snapshot (#607)."""
    student_id = flask_session.get("user_id") or "default_student"
    from utils.prep_snapshot_service import delete_student_snapshot
    deleted = delete_student_snapshot(student_id, snapshot_id)
    if deleted:
        return jsonify({"status": "success", "message": "Snapshot deleted."}), 200
    return jsonify({"status": "error", "message": "Snapshot not found or unauthorized."}), 404



@analysis_bp.route("/dashboard/<analysis_id>/annotations", methods=["POST"])
def save_analysis_annotation_view(analysis_id: str):
    """Create or edit a private annotation attached to an owned analysis."""
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    # Ensure required nested dictionary structure in results
    results_raw = analysis_record.get("results")
    results = results_raw if isinstance(results_raw, dict) else {}
    if "resume_analysis" not in results or not isinstance(results.get("resume_analysis"), dict):
        results["resume_analysis"] = {
            "ats_score": 0,
            "strengths": [],
            "weaknesses": [],
            "formatting_suggestions": [],
            "grammar_suggestions": [],
            "project_feedback": "",
            "experience_feedback": "",
        }
    if "job_match" not in results or not isinstance(results.get("job_match"), dict):
        results["job_match"] = {
            "match_percentage": 0,
            "matched_skills": [],
            "missing_skills": [],
        }
    analysis_record["results"] = results

    student_id = analysis_record.get("student_id") or "default_student"
    annotation_id = (request.form.get("annotation_id") or "").strip()
    annotation_type = request.form.get("annotation_type", "note").strip()
    section = request.form.get("section", "General").strip()
    content = request.form.get("content", "").strip()

    if not content:
        flash("Please enter a note or highlight before saving.", "warning")
        return redirect(url_for("dashboard", analysis_id=analysis_id))
    if len(content) > 5000:
        flash("Annotations must be 5,000 characters or fewer.", "warning")
        return redirect(url_for("dashboard", analysis_id=analysis_id))

    try:
        if annotation_id:
            updated = update_analysis_annotation(
                student_id, analysis_id, annotation_id, annotation_type, section, content
            )
            flash(
                "Private analysis annotation updated." if updated else "Annotation not found or access denied.",
                "success" if updated else "warning",
            )
        else:
            create_analysis_annotation(student_id, analysis_id, annotation_type, section, content)
            flash("Private analysis annotation saved.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    return redirect(url_for("dashboard", analysis_id=analysis_id))


@analysis_bp.route("/dashboard/<analysis_id>/annotations/<annotation_id>/delete", methods=["POST"])
def delete_analysis_annotation_view(analysis_id: str, annotation_id: str):
    """Delete an owned private annotation without exposing another user's data."""
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    # Ensure required nested dictionary structure in results
    results_raw = analysis_record.get("results")
    results = results_raw if isinstance(results_raw, dict) else {}
    if "resume_analysis" not in results or not isinstance(results.get("resume_analysis"), dict):
        results["resume_analysis"] = {
            "ats_score": 0,
            "strengths": [],
            "weaknesses": [],
            "formatting_suggestions": [],
            "grammar_suggestions": [],
            "project_feedback": "",
            "experience_feedback": "",
        }
    if "job_match" not in results or not isinstance(results.get("job_match"), dict):
        results["job_match"] = {
            "match_percentage": 0,
            "matched_skills": [],
            "missing_skills": [],
        }
    analysis_record["results"] = results

    student_id = analysis_record.get("student_id") or "default_student"
    deleted = delete_analysis_annotation(student_id, analysis_id, annotation_id)
    flash(
        "Private annotation deleted." if deleted else "Annotation not found or access denied.",
        "success" if deleted else "warning",
    )
    return redirect(url_for("dashboard", analysis_id=analysis_id))


@analysis_bp.route("/api/analyses/<analysis_id>/annotations", methods=["GET", "POST"])
def api_analysis_annotations(analysis_id: str):
    """List or create private annotations for the authenticated owner's analysis."""
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"success": False, "error": "Analysis not found or access denied."}), 404

    student_id = analysis_record.get("student_id") or "default_student"
    if request.method == "GET":
        return jsonify({"success": True, "annotations": get_analysis_annotations(student_id, analysis_id)})

    payload = request.get_json(silent=True) or request.form
    content = (payload.get("content") or "").strip()
    if not content:
        return jsonify({"success": False, "error": "Annotation content is required."}), 400
    if len(content) > 5000:
        return jsonify({"success": False, "error": "Annotations must be 5,000 characters or fewer."}), 400

    annotation = create_analysis_annotation(
        student_id,
        analysis_id,
        (payload.get("annotation_type") or "note").strip(),
        (payload.get("section") or "General").strip(),
        content,
    )
    return jsonify({"success": True, "annotation": annotation}), 201


@analysis_bp.route("/api/analyses/<analysis_id>/annotations/<annotation_id>", methods=["PUT", "PATCH", "DELETE"])
def api_analysis_annotation_item(analysis_id: str, annotation_id: str):
    """Edit or delete one private annotation with owner and analysis checks."""
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"success": False, "error": "Analysis not found or access denied."}), 404

    student_id = analysis_record.get("student_id") or "default_student"
    if not get_analysis_annotation(student_id, analysis_id, annotation_id):
        return jsonify({"success": False, "error": "Annotation not found or access denied."}), 404

    if request.method == "DELETE":
        deleted = delete_analysis_annotation(student_id, analysis_id, annotation_id)
        return jsonify({"success": deleted}), 200 if deleted else 404

    payload = request.get_json(silent=True) or request.form
    content = (payload.get("content") or "").strip()
    if not content:
        return jsonify({"success": False, "error": "Annotation content is required."}), 400
    if len(content) > 5000:
        return jsonify({"success": False, "error": "Annotations must be 5,000 characters or fewer."}), 400

    updated = update_analysis_annotation(
        student_id,
        analysis_id,
        annotation_id,
        (payload.get("annotation_type") or "note").strip(),
        (payload.get("section") or "General").strip(),
        content,
    )
    refreshed = get_analysis_annotation(student_id, analysis_id, annotation_id) if updated else None
    return jsonify({"success": updated, "annotation": refreshed}), 200 if updated else 400


@analysis_bp.route("/api/analysis/<analysis_id>/skill-evidence", methods=["GET"])
def api_skill_evidence_mapping(analysis_id: str):
    """
    Returns the Skill Evidence Mapping for a specific placement analysis (#250).
    Shows where detected skills are supported in the candidate's resume and highlights
    skills lacking practical evidence.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"success": False, "error": "Analysis not found or unauthorized."}), 404

    results = analysis_record.get("results") or {}
    skill_evidence = results.get("skill_evidence_mapping")
    if not skill_evidence or not skill_evidence.get("skills"):
        from utils.skill_evidence_mapper import map_skill_evidence
        job_match = results.get("job_match") or {}
        matched = job_match.get("matched_skills") or job_match.get("matching_skills") or []
        if isinstance(matched, str):
            matched = [s.strip() for s in matched.split(",") if s.strip()]
        skill_evidence = map_skill_evidence(
            resume_text=analysis_record.get("resume_text", ""),
            analysis_record=analysis_record,
            target_skills=matched or None
        )

    return jsonify({
        "success": True,
        "analysis_id": analysis_id,
        "company_name": analysis_record.get("company_name", "General"),
        "skill_evidence": skill_evidence
    }), 200


@analysis_bp.route("/api/skill-gap/roles", methods=["GET"])
def api_get_skill_gap_roles():
    """
    Returns the list of all available job role benchmark profiles (#495).
    Dynamically discovers JSON files in data/roles/.
    """
    try:
        roles = get_available_roles()
        return jsonify({
            "status": "success",
            "success": True,
            "roles": roles,
            "count": len(roles),
        }), 200
    except Exception as exc:
        logger.error(f"Error fetching skill gap roles: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/skill-gap/roles/<role_id>", methods=["GET"])
def api_get_skill_gap_role_profile(role_id: str):
    """
    Returns profile details, required skills, nice-to-have skills, and suggested
    topics for a specific job role (#495).
    """
    try:
        profile = get_role_profile(role_id)
        if not profile:
            return jsonify({
                "status": "error",
                "success": False,
                "error": f"Role '{role_id}' not found."
            }), 404
        return jsonify({
            "status": "success",
            "success": True,
            "role": profile
        }), 200
    except Exception as exc:
        logger.error(f"Error fetching role profile '{role_id}': {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/skill-gap/analyze", methods=["POST"])
def api_analyze_skill_gap():
    """
    Analyzes candidate skills against a target job role profile (#495).
    Accepts JSON body:
      - role_id (or role): identifier of the role (e.g. 'software_developer')
      - skills (or candidate_skills): list or string of candidate skills
      - analysis_id: (optional) ID of existing analysis to load skills from
    """
    try:
        data = request.get_json(silent=True) or {}
        role_id = data.get("role_id") or data.get("role") or "software_developer"
        candidate_skills = data.get("skills") or data.get("candidate_skills")

        analysis_id = data.get("analysis_id")
        if not candidate_skills and analysis_id:
            analysis_record = get_analysis(analysis_id)
            if analysis_record:
                candidate_skills = analysis_record

        result = analyze_role_skill_gap(candidate_skills, role_id)
        return jsonify({
            "status": "success",
            "success": True,
            "data": result,
            **result,
        }), 200
    except Exception as exc:
        logger.error(f"Error in api_analyze_skill_gap: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/api/analysis/<analysis_id>/role-skill-gap", methods=["GET"])
def api_analysis_role_skill_gap(analysis_id: str):
    """
    Evaluates candidate skills from an existing analysis record against a selected target role (#495).
    Query parameter: ?role=<role_id> (defaults to first available role or 'software_developer').
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "success": False, "error": "Analysis not found or unauthorized."}), 404

    try:
        available_roles = get_available_roles()
        default_role = available_roles[0]["id"] if available_roles else "software_developer"
        role_id = request.args.get("role") or request.args.get("role_id") or default_role

        result = analyze_role_skill_gap(analysis_record, role_id)
        return jsonify({
            "status": "success",
            "success": True,
            "analysis_id": analysis_id,
            "selected_role_id": role_id,
            "available_roles": available_roles,
            "data": result,
            **result,
        }), 200
    except Exception as exc:
        logger.error(f"Error evaluating role skill gap for analysis {analysis_id}: {exc}")
        return jsonify({"status": "error", "success": False, "error": str(exc)}), 500


@analysis_bp.route("/dashboard/<analysis_id>/compare-previous", methods=["GET"])
@analysis_bp.route("/compare-previous/<analysis_id>", methods=["GET"])
def compare_with_previous_analysis_view(analysis_id: str):
    """
    Renders side-by-side comparison between the current resume analysis
    and the candidate's previous analysis for the same target role or company (#297).
    Enforces student authorization and computes diffs deterministically without AI calls.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    if not _is_student_authorized(analysis_record):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json":
            return jsonify({"status": "error", "message": "Unauthorized access to analysis."}), 403
        flash("You are not authorized to view this analysis comparison.", "danger")
        return redirect(url_for("index"))

    student_id = analysis_record.get("student_id") or "default_student"
    current_version = analysis_record.get("version_number", 1)
    target_company = analysis_record.get("company_name", "General")
    target_role = analysis_record.get("target_role")
    if not target_role:
        res = analysis_record.get("results") or {}
        target_role = (
            res.get("target_role") or
            res.get("job_match", {}).get("target_role") or
            res.get("role_preparation", {}).get("target_role") or
            "Software Engineer"
        )

    previous_record = get_previous_analysis_for_target(
        student_id=student_id,
        current_analysis_id=analysis_id,
        current_version=current_version,
        company_name=target_company,
        target_role=target_role
    )

    if previous_record and not _is_student_authorized(previous_record):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json":
            return jsonify({"status": "error", "message": "Unauthorized access to previous analysis."}), 403
        flash("You are not authorized to compare against this analysis.", "danger")
        return redirect(url_for("index"))

    versions_for_target = get_student_versions_for_target(
        student_id=student_id,
        company_name=target_company,
        target_role=target_role
    )

    comparison = compare_analysis_versions(
        current_record=analysis_record,
        previous_record=previous_record,
        versions_history=versions_for_target
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json":
        return jsonify({
            "status": "success",
            "has_previous": comparison.get("has_previous", False),
            "comparison": comparison
        })

    return render_template(
        "compare_previous.html",
        analysis=analysis_record,
        analysis_id=analysis_id,
        previous=previous_record,
        comparison=comparison,
        trend_chart=comparison.get("trend_chart", []),
        trend_chart_json=json.dumps(comparison.get("trend_chart", []))
    )


@analysis_bp.route("/api/analysis/<analysis_id>/compare-previous", methods=["GET"])
def api_compare_with_previous(analysis_id: str):
    """
    JSON API returning side-by-side ATS score changes, skills added/removed,
    weaknesses fixed/present, and version trajectory without AI calls (#297).
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        return jsonify({"status": "error", "message": "Analysis not found."}), 404

    if not _is_student_authorized(analysis_record):
        return jsonify({"status": "error", "message": "Unauthorized access."}), 403

    student_id = analysis_record.get("student_id") or "default_student"
    current_version = analysis_record.get("version_number", 1)
    target_company = analysis_record.get("company_name", "General")
    target_role = analysis_record.get("target_role")
    if not target_role:
        res = analysis_record.get("results") or {}
        target_role = (
            res.get("target_role") or
            res.get("job_match", {}).get("target_role") or
            res.get("role_preparation", {}).get("target_role") or
            "Software Engineer"
        )

    previous_record = get_previous_analysis_for_target(
        student_id=student_id,
        current_analysis_id=analysis_id,
        current_version=current_version,
        company_name=target_company,
        target_role=target_role
    )

    if previous_record and not _is_student_authorized(previous_record):
        return jsonify({"status": "error", "message": "Unauthorized access to previous record."}), 403

    versions_for_target = get_student_versions_for_target(
        student_id=student_id,
        company_name=target_company,
        target_role=target_role
    )

    comparison = compare_analysis_versions(
        current_record=analysis_record,
        previous_record=previous_record,
        versions_history=versions_for_target
    )

    return jsonify({
        "status": "success",
        "has_previous": comparison.get("has_previous", False),
        "comparison": comparison,
        "score_difference": comparison.get("score_difference", 0),
        "skills_added": comparison.get("skills_added", []),
        "skills_removed": comparison.get("skills_removed", []),
        "weaknesses_fixed": comparison.get("weaknesses_fixed", []),
        "weaknesses_still_present": comparison.get("weaknesses_still_present", []),
        "trend_chart": comparison.get("trend_chart", [])
    }), 200


@analysis_bp.route("/demo-analysis", methods=["GET"])
@analysis_bp.route("/try-sample", methods=["GET"])
@analysis_bp.route("/demo", methods=["GET", "POST"])
def demo_mode_view():
    """
    Opens full placement evaluation with a bundled, fictional resume and sample
    job description without requiring login or making AI calls (#435).
    """
    enter_demo_session(flask_session, time.time())
    demo_id = "demo-sample-analysis"
    target_url = url_for("dashboard", analysis_id=demo_id)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json":
        return jsonify({
            "status": "success",
            "message": "Demo session started.",
            "is_demo": True,
            "analysis_id": demo_id,
            "redirect_url": target_url
        }), 200

    return redirect(target_url)


@analysis_bp.route("/demo/exit", methods=["GET", "POST"])
@analysis_bp.route("/demo-exit", methods=["GET", "POST"])
def demo_mode_exit():
    """
    Exits demo mode and restores any pre-existing user session (#435).
    """
    was_authenticated, restored_uid = exit_demo_session(flask_session, time.time())
    flash("You have exited demo mode.", "info")
    return redirect(url_for("index"))


@analysis_bp.route("/api/demo/sample-resume", methods=["GET"])
def api_demo_sample_resume():
    """
    Returns the synthetic bundled sample resume text and candidate profile metadata (#435).
    """
    return jsonify({
        "status": "success",
        "candidate_name": "Alex Chen",
        "target_company": "Google",
        "target_role": "Software Development Engineer (SDE 1)",
        "resume_text": SYNTHETIC_SAMPLE_RESUME_TEXT.strip()
    }), 200


@analysis_bp.route("/api/demo/sample-job-description", methods=["GET"])
def api_demo_sample_job_description():
    """
    Returns the synthetic bundled sample job description (#435).
    """
    return jsonify({
        "status": "success",
        "company_name": "Google",
        "target_role": "Software Development Engineer (SDE 1)",
        "job_description": SYNTHETIC_SAMPLE_JOB_DESCRIPTION.strip()
    }), 200


@analysis_bp.route("/api/interview/<analysis_id>/projects", methods=["GET"])
def api_get_analysis_projects(analysis_id: str):
    """
    API endpoint returning extracted project entries from the analysed resume (#466).
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"success": False, "error": "Analysis not found or unauthorized."}), 404

    projects = project_deep_dive_agent.extract_projects(analysis_record)
    return jsonify({
        "status": "success",
        "success": True,
        "count": len(projects),
        "projects": projects,
    }), 200


@analysis_bp.route("/feedback", methods=["GET", "POST"])
def feedback_view():
    """
    Renders and processes candidate platform experience feedback (#197).
    Allows students to rate features, provide structured reviews, and view satisfaction statistics.
    """
    student_id = (
        flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    categories = [
        "Resume Analysis & ATS Scoring",
        "Mock Interview Simulator",
        "Job Application Tracker",
        "Role Preparation & Roadmaps",
        "Coding Practice & DSA",
        "UI & Performance",
        "General Experience"
    ]

    if request.method == "POST":
        rating_raw = request.form.get("rating", "5")
        category = request.form.get("category", "General Experience").strip()
        comment = request.form.get("comment", "").strip()

        try:
            rating = int(rating_raw)
        except (ValueError, TypeError):
            rating = 5

        if not comment:
            flash("Please enter feedback comments before submitting.", "danger")
        else:
            submit_user_feedback(
                student_id=student_id,
                rating=rating,
                category=category,
                comment=comment
            )
            flash("Thank you! Your feedback has been submitted successfully.", "success")
        return redirect("/feedback")

    feedback_list = get_user_feedback_list(limit=25)
    metrics = get_feedback_metrics_summary()

    return render_template(
        "feedback.html",
        categories=categories,
        feedback_list=feedback_list,
        metrics=metrics,
        student_id=student_id
    )


@analysis_bp.route("/api/feedback", methods=["GET", "POST"])
def api_feedback():
    """
    REST API endpoint for submitting and retrieving feedback (#197).
    """
    student_id = (
        request.args.get("student_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("user_id")
        or flask_session.get("student_id")
        or "default_student"
    )

    if request.method == "GET":
        cat = request.args.get("category")
        limit = request.args.get("limit", 50)
        try:
            limit_val = int(limit)
        except (ValueError, TypeError):
            limit_val = 50
        items = get_user_feedback_list(category_filter=cat, limit=limit_val)
        metrics = get_feedback_metrics_summary()
        return jsonify({
            "status": "success",
            "total": len(items),
            "metrics": metrics,
            "feedback": items
        }), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        rating_raw = payload.get("rating", 5)
        category = payload.get("category", "General Experience")
        comment = payload.get("comment", "").strip()

        try:
            rating = int(rating_raw)
        except (ValueError, TypeError):
            rating = 5

        if not comment:
            return jsonify({
                "status": "error",
                "message": "comment is required and cannot be empty."
            }), 400

        record = submit_user_feedback(
            student_id=student_id,
            rating=rating,
            category=category,
            comment=comment
        )
        return jsonify({
            "status": "success",
            "message": "Feedback recorded successfully",
            "feedback": record
        }), 201


@analysis_bp.route("/api/analysis/<aid>/feedback", methods=["GET", "POST"])
def api_analysis_section_feedback(aid: str):
    """
    API endpoint for getting and submitting thumbs-up / thumbs-down ratings
    and optional comments for specific dashboard analysis sections (#302).
    Allows users to rate and later update/change their rating per section.
    """
    user_id = (
        flask_session.get("user_id")
        or flask_session.get("user", {}).get("id")
        or flask_session.get("student_id")
    )
    if not user_id:
        user_id = f"guest_{request.remote_addr or 'anon'}"
        flask_session["user_id"] = user_id

    if request.method == "GET":
        section = request.args.get("section")
        feedback_data = get_analysis_section_feedback(analysis_id=aid, user_id=user_id, section=section)
        return jsonify({
            "success": True,
            "analysis_id": aid,
            "feedback": feedback_data
        }), 200

    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    section = payload.get("section")
    rating = payload.get("rating")
    comment = payload.get("comment")

    if not section or not str(section).strip():
        return jsonify({"success": False, "error": "Section is required."}), 400

    if not rating or str(rating).strip().lower() not in (
        "up", "down", "thumbs_up", "thumbs_down", "1", "-1", "positive", "negative", "true", "false"
    ):
        return jsonify({"success": False, "error": "Rating must be 'up' or 'down'."}), 400

    max_len = getattr(config, "MAX_FEEDBACK_COMMENT_LENGTH", 500)
    if comment and len(str(comment).strip()) > max_len:
        return jsonify({"success": False, "error": f"Comment exceeds maximum allowed length of {max_len} characters."}), 400

    try:
        record = save_analysis_section_feedback(
            analysis_id=aid,
            user_id=user_id,
            section=str(section).strip(),
            rating=str(rating).strip(),
            comment=str(comment).strip() if comment is not None else None,
            max_comment_length=max_len
        )
        return jsonify({
            "success": True,
            "message": "Feedback submitted successfully.",
            "feedback": record
        }), 200
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error saving section feedback for analysis {aid}: {e}")
        return jsonify({"success": False, "error": "Failed to save feedback."}), 500


@analysis_bp.route("/admin/analysis-feedback", methods=["GET"])
def admin_analysis_feedback_view():
    """
    Admin-only dashboard view summarizing section feedback ratings and comments (#302).
    Requires administrator session, X-Admin-Key header, or admin_key query param.
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    section_filter = request.args.get("section")
    rating_filter = request.args.get("rating")
    summary = get_analysis_section_feedback_summary(section_filter=section_filter, rating_filter=rating_filter)

    return render_template(
        "admin_analysis_feedback.html",
        summary=summary,
        selected_section=section_filter or "all",
        selected_rating=rating_filter or "all"
    )


@analysis_bp.route("/admin/analysis-feedback/export.csv", methods=["GET"])
def admin_analysis_feedback_export_csv():
    """
    Admin-only CSV export of all analysis section feedback records (#302).
    Requires administrator session, X-Admin-Key header, or admin_key query param.
    """
    admin_session = flask_session.get("is_admin") or (flask_session.get("user", {}).get("role") == "admin")
    admin_header = request.headers.get("X-Admin-Key")
    admin_param = request.args.get("admin_key")
    configured_key = getattr(config, "ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

    is_admin = bool(admin_session or (admin_header and admin_header == configured_key) or (admin_param and admin_param == configured_key))
    if not is_admin:
        return jsonify({"success": False, "error": "Unauthorized: Administrator access required."}), 403

    csv_data = export_analysis_section_feedback_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=analysis_section_feedback.csv"}
    )


@analysis_bp.route("/api/analyses/recent", methods=["GET"])
def api_recent_analyses():
    """
    Returns a list of recent placement analyses ordered by timestamp descending.
    """
    try:
        limit = request.args.get("limit", default=10, type=int)
        analyses = get_recent_analyses(
            limit=limit,
             student_id=flask_session["user_id"]
        )
        return jsonify({
            "status": "success",
            "count": len(analyses),
            "analyses": analyses
        }), 200
    except Exception as e:
        logger.error(f"Error fetching recent analyses: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "Failed to retrieve recent analyses."
        }), 500


@analysis_bp.route("/api/analysis/<analysis_id>", methods=["DELETE"])
def api_delete_analysis(analysis_id: str):
    """
    Deletes an analysis session and related mock interview records.
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if (
            not analysis_record
            or not _is_student_authorized(analysis_record)
        ):
            return jsonify({
                "status": "error",
                "message": "Analysis not found."
            }), 404

        deleted = delete_analysis(analysis_id)
        if not deleted:
            return jsonify({
                "status": "error",
                "message": f"Analysis with ID {analysis_id} not found."
            }), 404
        return jsonify({
            "status": "success",
            "message": f"Analysis {analysis_id} deleted successfully."
        }), 200
    except Exception as e:
        logger.error(f"Error deleting analysis {analysis_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "Failed to delete analysis record."
        }), 500


@analysis_bp.route("/api/progress/<student_id>", methods=["GET"])
def api_student_progress(student_id: str):
    """
    Returns a candidate's full longitudinal version history and trend metrics.
    """
    try:
        if not _is_student_authorized(student_id):
            return jsonify({
                "status": "error",
                "message": "Progress not found."
            }), 404
        versions = get_student_versions(student_id)
        trend_series = build_longitudinal_series(versions)
        return jsonify({
            "status": "success",
            "student_id": student_id,
            "total_versions": len(versions),
            "versions": versions,
            "trend_series": trend_series
        }), 200
    except Exception as e:
        logger.error(f"Error fetching progress for student {student_id}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@analysis_bp.route("/api/progress/diff/<id_a>/<id_b>", methods=["GET"])
def api_progress_diff(id_a: str, id_b: str):
    """
    Generates side-by-side comparison metrics between any two specific resume analyses.
    """
    try:
        rec_a = get_analysis(id_a)
        rec_b = get_analysis(id_b)
        if (
            not rec_a
            or not rec_b
            or not _is_student_authorized(rec_a)
            or not _is_student_authorized(rec_b)
        ):
            return jsonify({
                "status": "error",
                "message": "One or both analysis records not found."
            }), 404

        diff = generate_side_by_side_diff(rec_a, rec_b)
        return jsonify({
            "status": "success",
            "diff": diff
        }), 200
    except Exception as e:
        logger.error(f"Error computing diff between {id_a} and {id_b}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@analysis_bp.route("/compare-resumes", methods=["GET", "POST"])
def compare_resumes_view():
    """
    Dedicated view and handler for comparing two resume versions directly (#140).
    Accepts two uploaded files (.pdf or .docx) or raw text payloads, extracts content,
    and returns comprehensive side-by-side differentials and AI feedback.
    """
    if request.method == "GET":
        return render_template("resume_compare.html")

    try:
        file_v1 = request.files.get("resume_v1")
        file_v2 = request.files.get("resume_v2")
        text_v1 = request.form.get("text_v1", "").strip()
        text_v2 = request.form.get("text_v2", "").strip()
        name_v1 = "Resume Version 1"
        name_v2 = "Resume Version 2"

        if file_v1 and file_v1.filename:
            name_v1 = secure_filename(file_v1.filename)
            path_v1 = os.path.join(config.UPLOAD_FOLDER, f"temp_v1_{name_v1}")
            file_v1.save(path_v1)
            try:
                text_v1 = extract_text_from_file(path_v1)
            finally:
                if os.path.exists(path_v1):
                    os.remove(path_v1)

        if file_v2 and file_v2.filename:
            name_v2 = secure_filename(file_v2.filename)
            path_v2 = os.path.join(config.UPLOAD_FOLDER, f"temp_v2_{name_v2}")
            file_v2.save(path_v2)
            try:
                text_v2 = extract_text_from_file(path_v2)
            finally:
                if os.path.exists(path_v2):
                    os.remove(path_v2)

        if not text_v1 or not text_v2:
            err_msg = "Please provide content for both resume versions to compare."
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": err_msg}), 400
            flash(err_msg, "error")
            return redirect(url_for("compare_resumes_view"))

        comparison = compare_two_resume_texts(
            text_v1=text_v1,
            text_v2=text_v2,
            filename_v1=name_v1,
            filename_v2=name_v2,
        )

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "comparison": comparison}), 200

        return render_template("resume_compare.html", comparison=comparison)

    except (ValueError, ScannedPDFError) as ve:
        logger.warning(f"Validation error comparing resume versions: {ve}")
        err_msg = str(ve)
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": err_msg}), 400
        flash(err_msg, "error")
        return redirect(url_for("compare_resumes_view"))
    except Exception as exc:
        logger.error(f"Error comparing resume versions: {exc}")
        err_msg = f"Failed to compare resumes: {str(exc)}"
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": err_msg}), 500
        flash(err_msg, "error")
        return redirect(url_for("compare_resumes_view"))


@analysis_bp.route("/session-resume-compare", methods=["GET", "POST"], endpoint="session_resume_compare_view")
@analysis_bp.route("/resume/session-compare", methods=["GET", "POST"])
def session_resume_compare_view():
    """
    In-session side-by-side resume revision comparator (#535).
    Allows uploading a second version of the resume in the same session.
    Keeps both versions in memory for the session only with zero database writes.
    Highlights improvements, regressions, added/removed skills, and added/removed sections.
    """
    session_key = (
        flask_session.get("auth_session_id")
        or flask_session.get("user_id")
        or request.cookies.get("session")
        or "default_student_session"
    )

    if request.method == "GET":
        comparison = get_session_comparison(session_key) or flask_session.get("session_resume_comparison")
        active_analysis_id = flask_session.get("active_analysis_id", "")
        active_resume_name = flask_session.get("active_resume_filename", "")
        active_text_sample = flask_session.get("active_resume_text", "")
        target_role = flask_session.get("active_target_role", "Software Engineer")
        return render_template(
            "session_resume_compare.html",
            comparison=comparison,
            active_analysis_id=active_analysis_id,
            active_resume_name=active_resume_name,
            active_resume_text_sample=active_text_sample,
            target_role=target_role
        )

    # POST Handling
    try:
        file_v1 = request.files.get("resume_v1")
        file_v2 = request.files.get("resume_v2")
        text_v1 = request.form.get("text_v1", "").strip()
        text_v2 = request.form.get("text_v2", "").strip()
        target_role = (request.form.get("target_role") or flask_session.get("active_target_role") or "Software Engineer").strip()
        job_description = request.form.get("job_description", "").strip()
        name_v1 = "Version 1"
        name_v2 = "Version 2"

        # If file_v1 provided, extract text and clean up
        if file_v1 and file_v1.filename:
            name_v1 = secure_filename(file_v1.filename)
            temp_path_v1 = os.path.join(config.UPLOAD_FOLDER, f"temp_sess_v1_{name_v1}")
            file_v1.save(temp_path_v1)
            try:
                text_v1 = extract_text_from_file(temp_path_v1)
            finally:
                if os.path.exists(temp_path_v1):
                    os.remove(temp_path_v1)

        # If text_v1 not provided, fallback to active session resume
        if not text_v1 and flask_session.get("active_resume_text"):
            text_v1 = flask_session["active_resume_text"]
            name_v1 = flask_session.get("active_resume_filename", "Session Baseline Resume")

        # If file_v2 provided, extract text and clean up
        if file_v2 and file_v2.filename:
            name_v2 = secure_filename(file_v2.filename)
            temp_path_v2 = os.path.join(config.UPLOAD_FOLDER, f"temp_sess_v2_{name_v2}")
            file_v2.save(temp_path_v2)
            try:
                text_v2 = extract_text_from_file(temp_path_v2)
            finally:
                if os.path.exists(temp_path_v2):
                    os.remove(temp_path_v2)

        if not text_v1:
            err_msg = "Please provide Version 1 (Baseline) content or upload a baseline resume."
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": err_msg}), 400
            flash(err_msg, "error")
            return redirect(url_for("session_resume_compare_view"))

        if not text_v2:
            err_msg = "Please provide Version 2 (Revised) content to compare against Version 1."
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": err_msg}), 400
            flash(err_msg, "error")
            return redirect(url_for("session_resume_compare_view"))

        comparison = compare_resume_versions(
            text_v1=text_v1,
            text_v2=text_v2,
            filename_v1=name_v1,
            filename_v2=name_v2,
            target_role=target_role,
            job_description=job_description
        )

        # Keep strictly in memory for the session only
        store_session_comparison(session_key, comparison)
        flask_session["session_resume_comparison"] = comparison

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "comparison": comparison}), 200

        active_analysis_id = flask_session.get("active_analysis_id", "")
        return render_template(
            "session_resume_compare.html",
            comparison=comparison,
            active_analysis_id=active_analysis_id,
            active_resume_name=name_v1,
            target_role=target_role,
            job_description=job_description
        )

    except (ValueError, ScannedPDFError) as ve:
        err_msg = str(ve)
        logger.warning(f"Validation error in session resume comparison: {err_msg}")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": err_msg}), 400
        flash(err_msg, "error")
        return redirect(url_for("session_resume_compare_view"))
    except Exception as exc:
        logger.error(f"Error in session resume comparison: {exc}")
        err_msg = f"Failed to compare resume versions: {str(exc)}"
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": err_msg}), 500
        flash(err_msg, "error")
        return redirect(url_for("session_resume_compare_view"))


@analysis_bp.route("/api/resume/session-compare", methods=["GET", "POST"])
def api_session_resume_compare():
    """
    REST API endpoint for in-session resume comparison (#535).
    Keeps both versions in memory for the session only.
    Returns side-by-side score, detected skills, missing skills, and section changes.
    """
    session_key = (
        flask_session.get("auth_session_id")
        or flask_session.get("user_id")
        or request.headers.get("X-Session-ID")
        or "default_student_session"
    )

    if request.method == "GET":
        comp = get_session_comparison(session_key) or flask_session.get("session_resume_comparison")
        if not comp:
            return jsonify({"status": "success", "has_comparison": False, "comparison": None}), 200
        return jsonify({"status": "success", "has_comparison": True, "comparison": comp}), 200

    # POST
    payload = request.get_json(silent=True) or {}
    file_v1 = request.files.get("resume_v1")
    file_v2 = request.files.get("resume_v2")

    text_v1 = (payload.get("text_v1") or request.form.get("text_v1") or "").strip()
    text_v2 = (payload.get("text_v2") or request.form.get("text_v2") or "").strip()
    name_v1 = payload.get("filename_v1") or request.form.get("filename_v1") or "Version 1"
    name_v2 = payload.get("filename_v2") or request.form.get("filename_v2") or "Version 2"
    target_role = payload.get("target_role") or request.form.get("target_role") or flask_session.get("active_target_role", "Software Engineer")
    job_description = payload.get("job_description") or request.form.get("job_description")

    if file_v1 and file_v1.filename:
        name_v1 = secure_filename(file_v1.filename)
        temp_v1 = os.path.join(config.UPLOAD_FOLDER, f"temp_api_v1_{name_v1}")
        file_v1.save(temp_v1)
        try:
            text_v1 = extract_text_from_file(temp_v1)
        finally:
            if os.path.exists(temp_v1):
                os.remove(temp_v1)

    if not text_v1 and flask_session.get("active_resume_text"):
        text_v1 = flask_session["active_resume_text"]
        name_v1 = flask_session.get("active_resume_filename", "Session Baseline Resume")

    if file_v2 and file_v2.filename:
        name_v2 = secure_filename(file_v2.filename)
        temp_v2 = os.path.join(config.UPLOAD_FOLDER, f"temp_api_v2_{name_v2}")
        file_v2.save(temp_v2)
        try:
            text_v2 = extract_text_from_file(temp_v2)
        finally:
            if os.path.exists(temp_v2):
                os.remove(temp_v2)

    if not text_v1 or not text_v2:
        return jsonify({
            "status": "error",
            "success": False,
            "error": "Both Version 1 and Version 2 resume contents are required for comparison."
        }), 400

    comparison = compare_resume_versions(
        text_v1=text_v1,
        text_v2=text_v2,
        filename_v1=name_v1,
        filename_v2=name_v2,
        target_role=target_role,
        job_description=job_description
    )

    store_session_comparison(session_key, comparison)
    flask_session["session_resume_comparison"] = comparison

    return jsonify({
        "status": "success",
        "success": True,
        "comparison": comparison
    }), 200


@analysis_bp.route("/resume/session-compare/reset", methods=["GET", "POST"])
def session_resume_compare_reset():
    """Resets in-memory comparison state for the current session (#535)."""
    session_key = (
        flask_session.get("auth_session_id")
        or flask_session.get("user_id")
        or request.cookies.get("session")
        or "default_student_session"
    )
    clear_session_comparison(session_key)
    flask_session.pop("session_resume_comparison", None)
    flash("Session comparison reset successfully.", "info")
    return redirect(url_for("session_resume_compare_view"))


@analysis_bp.route("/api/resume/session-compare/reset", methods=["POST"])
def api_session_resume_compare_reset():
    """Resets the in-memory session comparison (#535)."""
    session_key = (
        flask_session.get("auth_session_id")
        or flask_session.get("user_id")
        or request.headers.get("X-Session-ID")
        or "default_student_session"
    )
    clear_session_comparison(session_key)
    flask_session.pop("session_resume_comparison", None)
    return jsonify({"status": "success", "cleared": True}), 200


@analysis_bp.route("/resume/templates", methods=["GET"], endpoint="resume_templates_gallery")
@analysis_bp.route("/resume/templates/<file_uuid>", methods=["GET"], endpoint="resume_templates_gallery_by_uuid")
def resume_templates_gallery(file_uuid: Optional[str] = None):
    """
    Renders the ATS-friendly Resume Template Gallery (#561).
    Displays 3 single-column templates (classic, compact, project-focused),
    section selection toggles, and live in-browser preview.
    """
    uuid_param = file_uuid or request.args.get("file_uuid")
    resume_data = None
    if uuid_param:
        resume_data = get_resume_review_cache(uuid_param)

    if not resume_data:
        # Fallback to realistic demo candidate resume data
        resume_data = dict(DEFAULT_SAMPLE_RESUME)

    selected_template = request.args.get("template", "classic").strip().lower()
    if selected_template not in TEMPLATES:
        selected_template = "classic"

    preview_html = generate_resume_preview_html(resume_data, template=selected_template)

    return render_template(
        "resume_templates.html",
        templates=get_available_templates(),
        selected_template=selected_template,
        preview_html=preview_html,
        file_uuid=uuid_param,
        resume_data=resume_data,
    )


@analysis_bp.route("/api/resume/preview", methods=["POST"], endpoint="api_resume_preview")
@csrf.exempt
def api_resume_preview():
    """
    Returns updated HTML preview of the ATS single-column resume (#561)
    based on selected template and included sections.
    """
    data = request.get_json(silent=True) or request.form
    file_uuid = data.get("file_uuid")
    template = (data.get("template") or "classic").strip().lower()
    sections = data.get("sections")

    resume_data = None
    if file_uuid:
        resume_data = get_resume_review_cache(file_uuid)
    if not resume_data:
        resume_data = dict(DEFAULT_SAMPLE_RESUME)

    preview_html = generate_resume_preview_html(resume_data, template=template, sections=sections)
    return jsonify({"success": True, "html": preview_html}), 200


@analysis_bp.route("/resume/download", methods=["POST", "GET"], endpoint="resume_template_download")
@analysis_bp.route("/resume/download/<file_uuid>", methods=["POST", "GET"], endpoint="resume_template_download_by_uuid")
def resume_template_download(file_uuid: Optional[str] = None):
    """
    Generates and downloads a clean single-column ATS resume in DOCX or PDF format (#561).
    Generated in-memory on demand without persisting student files on disk.
    """
    params = request.form if request.method == "POST" else request.args

    uuid_param = file_uuid or params.get("file_uuid")
    fmt = (params.get("format") or "docx").strip().lower()
    template = (params.get("template") or "classic").strip().lower()

    if request.method == "POST":
        sections = request.form.getlist("sections") or None
    else:
        sections_str = params.get("sections")
        sections = [s.strip() for s in sections_str.split(",") if s.strip()] if sections_str else None

    resume_data = None
    if uuid_param:
        resume_data = get_resume_review_cache(uuid_param)
    if not resume_data:
        resume_data = dict(DEFAULT_SAMPLE_RESUME)

    candidate_name = resume_data.get("candidate_name") or "Candidate"
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", candidate_name).strip("_") or "Resume"

    if fmt == "pdf":
        file_bytes = generate_resume_pdf(resume_data, template=template, sections=sections)
        mimetype = "application/pdf"
        download_name = f"{safe_name}_{template.capitalize()}_ATS_Resume.pdf"
    else:
        file_bytes = generate_resume_docx(resume_data, template=template, sections=sections)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        download_name = f"{safe_name}_{template.capitalize()}_ATS_Resume.docx"

    buffer = io.BytesIO(file_bytes)
    return send_file(
        buffer,
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name,
    )


@analysis_bp.route("/api/analysis/<analysis_id>/download-clean-resume", methods=["GET"])
def api_download_clean_resume(analysis_id: str):
    """
    Generates and downloads a clean, metadata-stripped copy of the uploaded PDF resume (#841).
    Ensures author, title, creator, producer, and identifying metadata are stripped.
    Strictly restricted to the uploading student.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record or not _is_student_authorized(analysis_record):
        return jsonify({"success": False, "error": "Analysis not found or unauthorized."}), 404

    # Locate uploaded PDF or generate clean PDF from extracted content
    fname = analysis_record.get("resume_filename") or "resume.pdf"
    file_id = analysis_record.get("id") or analysis_id
    results = analysis_record.get("results") or {}
    cand_info = results.get("candidate_info") or results.get("resume_analysis", {})
    candidate_name = None
    if isinstance(cand_info, dict):
        candidate_name = cand_info.get("name") or cand_info.get("candidate_name")
    target_role = analysis_record.get("target_role") or analysis_record.get("company_name")

    suggested_filename = generate_suggested_filename(candidate_name, target_role)

    # Check for stored PDF file in UPLOAD_FOLDER
    target_pdf_path = None
    if os.path.exists(config.UPLOAD_FOLDER):
        for candidate in (
            f"{file_id}_{fname}",
            f"{file_id}.pdf",
            f"{file_id}_resume.pdf",
            fname
        ):
            cand_path = os.path.join(config.UPLOAD_FOLDER, secure_filename(candidate))
            if os.path.exists(cand_path) and cand_path.lower().endswith(".pdf"):
                target_pdf_path = cand_path
                break

        if not target_pdf_path:
            for item in os.listdir(config.UPLOAD_FOLDER):
                if item.startswith(f"{file_id}") and item.lower().endswith(".pdf"):
                    target_pdf_path = os.path.join(config.UPLOAD_FOLDER, item)
                    break

    try:
        if target_pdf_path and os.path.exists(target_pdf_path):
            clean_stream = strip_pdf_metadata(target_pdf_path)
        else:
            # Fallback: synthesize a clean single-column PDF from resume data
            raw_resume = (
                analysis_record.get("resume_text")
                or results.get("extracted_resume_text")
                or results.get("resume_text")
                or "Resume"
            )
            synthesized_data = dict(DEFAULT_SAMPLE_RESUME)
            if candidate_name:
                synthesized_data["candidate_name"] = candidate_name
            pdf_bytes = generate_resume_pdf(synthesized_data, template="classic")
            clean_stream = strip_pdf_metadata(pdf_bytes)

        return send_file(
            clean_stream,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=suggested_filename
        )
    except Exception as e:
        logger.error(f"Failed to generate clean resume PDF for analysis {analysis_id}: {e}")
        return jsonify({"success": False, "error": f"Failed to generate clean resume: {str(e)}"}), 500


@analysis_bp.route("/history", methods=["GET"])
def history_view():
    """
    Renders the dedicated History page listing past placement analyses (#159).
    Displays date, target role, target company, and ATS score, ordered newest first.
    Allows reopening any analysis in the existing dashboard view or permanently deleting records.
    """
    try:
        analyses = get_analyses_history()
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": True,
                "analyses": analyses,
                "total": len(analyses)
            }), 200

        return render_template("history.html", analyses=analyses)
    except Exception as e:
        logger.error(f"Error loading analyses history: {str(e)}")
        flash("Failed to load analysis history.", "error")
        return render_template("history.html", analyses=[])


@analysis_bp.route("/history/delete/<analysis_id>", methods=["POST"])
@analysis_bp.route("/delete-analysis/<analysis_id>", methods=["POST"])
def delete_analysis_action(analysis_id: str):
    """
    Removes a past analysis record and its associated data (#159).
    Supports standard form submission redirects and JSON API responses.
    """
    try:
        deleted = delete_analysis(analysis_id)
        if not deleted:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": f"Analysis '{analysis_id}' not found."}), 404
            flash(f"Analysis with ID '{analysis_id}' was not found.", "error")
            return redirect(url_for("history_view"))

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": "Analysis deleted successfully."}), 200

        flash("Analysis and associated data deleted successfully.", "success")
        return redirect(url_for("history_view"))
    except Exception as e:
        logger.error(f"Error deleting analysis {analysis_id}: {str(e)}")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": "Internal server error deleting analysis."}), 500
        flash("An error occurred while deleting the analysis.", "error")
        return redirect(url_for("history_view"))


@analysis_bp.route("/api/role-prep/roles", methods=["GET"])
def api_role_prep_roles():
    """
    Returns the list of supported placement roles with descriptions and icons.
    """
    return jsonify({
        "success": True,
        "roles": SUPPORTED_ROLES
    }), 200


@analysis_bp.route("/api/analysis/<analysis_id>/role-prep", methods=["GET"])
@analysis_bp.route("/api/role-prep/<analysis_id>", methods=["GET"])
def api_role_prep_for_analysis(analysis_id: str):
    """
    Generates role-based preparation curriculum and recommendations for a student analysis.
    Supports switching target roles dynamically (Python Developer, Data Analyst, Data Scientist, Software Engineer).
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        return jsonify({"success": False, "error": "Analysis record not found"}), 404

    target_role = request.args.get("role")
    if not target_role and request.is_json:
        target_role = (request.get_json() or {}).get("role")
    if not target_role and request.form:
        target_role = request.form.get("role")
    target_role = (target_role or "Software Engineer").strip()

    results = analysis_record.get("results") or {}
    job_match = results.get("job_match") or {}
    skill_gap_items = results.get("skill_gap") or []

    existing_skills = job_match.get("matched_skills") or []
    gap_names = [g.get("missing_skill") for g in skill_gap_items if isinstance(g, dict) and g.get("missing_skill")] or job_match.get("missing_skills", [])

    prep_data = role_prep_agent.generate_role_preparation(
        target_role=target_role,
        existing_skills=existing_skills,
        skill_gaps=gap_names,
        company_name=analysis_record.get("company_name", "General"),
        resume_text=analysis_record.get("resume_text", "")
    )
    return jsonify(prep_data), 200


@analysis_bp.route("/api/role-prep/career-recommendations/<analysis_id>", methods=["GET"])
def api_all_career_recommendations(analysis_id: str):
    """
    Returns AI recommendation explanations across all supported career roles (#295).
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        return jsonify({"success": False, "error": "Analysis record not found"}), 404

    results = analysis_record.get("results") or {}
    job_match = results.get("job_match") or {}
    skill_gap_items = results.get("skill_gap") or []
    existing_skills = job_match.get("matched_skills") or []
    gap_names = [g.get("missing_skill") for g in skill_gap_items if isinstance(g, dict) and g.get("missing_skill")] or job_match.get("missing_skills", [])

    recommendations = role_prep_agent.get_all_role_recommendations(
        existing_skills=existing_skills,
        skill_gaps=gap_names,
        resume_text=analysis_record.get("resume_text", ""),
        analysis_record=analysis_record,
        company_name=analysis_record.get("company_name", "General")
    )
    return jsonify(recommendations), 200


@analysis_bp.route("/api/role-prep/generate", methods=["POST"])
def api_role_prep_generate():
    """
    Ad-hoc role preparation generation supporting custom skills, gaps, and roles.
    """
    body = request.get_json() or {}
    target_role = body.get("role") or request.form.get("role") or "Software Engineer"
    skills = body.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in re.split(r"[,;\n]+", skills) if s.strip()]
    skill_gaps = body.get("skill_gaps") or []
    if isinstance(skill_gaps, str):
        skill_gaps = [s.strip() for s in re.split(r"[,;\n]+", skill_gaps) if s.strip()]
    company = body.get("company") or request.form.get("company") or "General"

    prep_data = role_prep_agent.generate_role_preparation(
        target_role=target_role,
        existing_skills=skills,
        skill_gaps=skill_gaps,
        company_name=company
    )
    return jsonify(prep_data), 200


@analysis_bp.route("/role-preparation", methods=["GET", "POST"])
def role_preparation_view():
    """
    Role-Based Preparation Mode View (#154).
    Enables students to explore tailored learning tracks for Python Developer, Data Analyst,
    Data Scientist, and Software Engineer roles with personalized skill gap bridging.
    """
    analysis_id = request.args.get("analysis_id", "").strip()
    selected_role = request.args.get("role", "Software Engineer").strip()
    company = request.args.get("company", "").strip()
    raw_skills = request.args.get("skills", "").strip()
    raw_gaps = request.args.get("skill_gaps", "").strip()

    if request.method == "POST":
        selected_role = request.form.get("role", selected_role).strip()
        company = request.form.get("company", company).strip()
        raw_skills = request.form.get("skills", raw_skills).strip()
        raw_gaps = request.form.get("skill_gaps", raw_gaps).strip()

    analysis_record = None
    existing_skills = []
    skill_gaps = []

    if analysis_id:
        analysis_record = get_analysis(analysis_id)
        if analysis_record:
            results = analysis_record.get("results") or {}
            job_match = results.get("job_match") or {}
            skill_gap_items = results.get("skill_gap") or []
            existing_skills = job_match.get("matched_skills") or []
            skill_gaps = [g.get("missing_skill") for g in skill_gap_items if isinstance(g, dict) and g.get("missing_skill")] or job_match.get("missing_skills", [])
            if not company:
                company = analysis_record.get("company_name", "General")

    if not existing_skills and raw_skills:
        existing_skills = [s.strip() for s in re.split(r"[,;\n]+", raw_skills) if s.strip()]
    if not skill_gaps and raw_gaps:
        skill_gaps = [s.strip() for s in re.split(r"[,;\n]+", raw_gaps) if s.strip()]

    prep_data = role_prep_agent.generate_role_preparation(
        target_role=selected_role,
        existing_skills=existing_skills,
        skill_gaps=skill_gaps,
        company_name=company or "General Campus Recruitment",
        resume_text=analysis_record.get("resume_text", "") if analysis_record else None
    )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(prep_data), 200

    return render_template(
        "role_preparation.html",
        prep=prep_data,
        prep_json=json.dumps(prep_data),
        analysis=analysis_record,
        analysis_id=analysis_id,
        available_roles=SUPPORTED_ROLES,
        selected_role=prep_data["target_role"],
        company=company,
        skills_input=", ".join(existing_skills),
        gaps_input=", ".join(skill_gaps)
    )


@analysis_bp.route("/api/notifications/delivery-history", methods=["GET"])
def api_get_delivery_history():
    """
    Returns audit trail of dispatched notifications for the student.
    """
    from utils.delivery_history_service import delivery_history_service

    student_id = flask_session.get("user_id") or "default_student"
    channel = request.args.get("channel")
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))

    history = delivery_history_service.list_delivery_history(
        student_id=student_id,
        channel=channel,
        status=status,
        limit=limit
    )
    metrics = delivery_history_service.get_delivery_metrics(student_id)

    return jsonify({
        "success": True,
        "metrics": metrics,
        "count": len(history),
        "history": history
    }), 200


@analysis_bp.route("/api/applications/stats", methods=["GET"])
def api_applications_stats():
    """
    Returns pipeline breakdown counts across all application stages.
    """
    student_id = _application_import_student_id()
    counts = get_application_stage_counts(student_id)
    funnel_analytics = get_application_funnel_analytics(student_id)
    return jsonify({
        "success": True,
        "student_id": student_id,
        "stages": APPLICATION_STAGES,
        "stage_counts": counts,
        "counts": counts
        ,"funnel_analytics": funnel_analytics
    }), 200


@analysis_bp.route("/export", methods=["GET"])
def export_dashboard_page():
    """
    Dashboard page to download preparation data CSV files (#274).
    """
    student_id = flask_session.get("user_id") or "default_student"
    return render_template("csv_export.html", student_id=student_id)


@analysis_bp.route("/api/user/search", methods=["GET"])
def api_user_search():
    """
    Private search endpoint across all student saved records (#324).
    """
    from utils.saved_data_search_service import search_student_saved_data
    student_id = flask_session.get("user_id") or "default_student"
    query = request.args.get("q", "")
    category = request.args.get("category", "all")
    try:
        limit = min(50, max(1, int(request.args.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20

    data = search_student_saved_data(student_id=student_id, query=query, filter_category=category, limit=limit)
    return jsonify(data), 200


@analysis_bp.route("/search", methods=["GET"])
def search_page():
    """
    Search page UI for private student saved data (#324).
    """
    student_id = flask_session.get("user_id") or "default_student"
    initial_query = request.args.get("q", "")
    return render_template("search.html", student_id=student_id, initial_query=initial_query)


@analysis_bp.route("/api/resume/detect-language", methods=["POST"])
def api_detect_resume_language():
    """
    Analyzes resume text to determine primary language.
    Provides non-blocking warnings if non-English content is detected.
    """
    from utils.resume_language_detector import resume_language_detector

    payload = request.get_json(silent=True) or request.form
    resume_text = payload.get("resume_text", "")

    if not resume_text:
        return jsonify({
            "success": False,
            "error": "resume_text is required for language detection"
        }), 400

    result = resume_language_detector.detect_language(resume_text)
    return jsonify({
        "success": True,
        "detection": result
    }), 200


@analysis_bp.route("/api/resume/validate-sections", methods=["POST"])
def api_validate_resume_sections():
    """
    Analyzes resume text to ensure core sections (Education, Skills,
    Projects, Experience, Contact) are present, providing soft warnings.
    """
    from utils.resume_section_validator import resume_section_validator

    payload = request.get_json(silent=True) or request.form
    resume_text = payload.get("resume_text", "")

    if not resume_text:
        return jsonify({
            "success": False,
            "error": "resume_text is required for section completeness validation"
        }), 400

    validation_result = resume_section_validator.validate_sections(resume_text)
    return jsonify({
        "success": True,
        "completeness": validation_result
    }), 200


@analysis_bp.route("/api/resume/check-duplicate", methods=["POST"])
def api_check_duplicate_resume():
    """
    Checks if an uploaded resume file or text matches previous uploads.
    Returns status without blocking the user.
    """
    from utils.duplicate_resume_service import duplicate_resume_service

    student_id = flask_session.get("user_id") or "default_student"

    if "resume" in request.files:
        file = request.files["resume"]
        file_bytes = file.read()
        filename = file.filename or "resume.pdf"
        extracted_text = request.form.get("extracted_text", "")
    else:
        payload = request.get_json(silent=True) or request.form
        content = payload.get("content", "")
        file_bytes = content.encode("utf-8")
        filename = payload.get("filename", "resume.txt")
        extracted_text = payload.get("extracted_text") or content

    if not file_bytes:
        return jsonify({"success": False, "error": "No resume file or content provided"}), 400

    dup_status = duplicate_resume_service.check_for_duplicate(
        student_id=student_id,
        file_bytes=file_bytes,
        filename=filename,
        extracted_text=extracted_text
    )

    return jsonify({
        "success": True,
        "duplicate_check": dup_status
    }), 200


@analysis_bp.route("/api/resume/record-fingerprint", methods=["POST"])
def api_record_resume_fingerprint():
    """
    Records a resume fingerprint after successful processing or user confirmation.
    """
    from utils.duplicate_resume_service import duplicate_resume_service

    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form
    content = payload.get("content", "")
    filename = payload.get("filename", "resume.pdf")

    if not content:
        return jsonify({"success": False, "error": "content is required"}), 400

    file_bytes = content.encode("utf-8")
    record_id = duplicate_resume_service.record_upload_fingerprint(
        student_id=student_id,
        file_bytes=file_bytes,
        filename=filename,
        extracted_text=content
    )

    return jsonify({
        "success": True,
        "record_id": record_id,
        "message": "Resume fingerprint registered successfully."
    }), 201


@analysis_bp.route("/api/analysis/version-metadata", methods=["GET", "POST"])
def api_analysis_version_metadata():
    """
    Records or retrieves AI model name, version, and prompt version hash
    for a placement evaluation run.
    """
    from utils.ai_versioning_service import ai_versioning_service

    if request.method == "GET":
        analysis_id = request.args.get("analysis_id")
        if not analysis_id:
            return jsonify({"success": False, "error": "analysis_id is required"}), 400
        analysis_record = get_analysis(analysis_id)
        if analysis_record and not _is_student_authorized(analysis_record):
            return jsonify({"success": False, "error": "Version info not found"}), 404
        info = ai_versioning_service.get_analysis_version_info(analysis_id)
        if not info:
            return jsonify({"success": False, "error": "Version info not found"}), 404
        return jsonify({"success": True, "metadata": info}), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        analysis_id = payload.get("analysis_id")
        if not analysis_id:
            return jsonify({"success": False, "error": "analysis_id is required"}), 400

        student_id = flask_session.get("user_id") or payload.get("student_id") or "default_student"
        row_id = ai_versioning_service.record_analysis_version(
            analysis_id=analysis_id,
            student_id=student_id,
            model_name=payload.get("model_name"),
            model_version=payload.get("model_version"),
            prompt_version=payload.get("prompt_version"),
            raw_prompt_template=payload.get("raw_prompt_template"),
            execution_mode=payload.get("execution_mode", "production")
        )

        metadata = ai_versioning_service.get_analysis_version_info(analysis_id)
        return jsonify({
            "success": True,
            "record_id": row_id,
            "metadata": metadata
        }), 201


@analysis_bp.route("/api/analysis/reproducibility-summary", methods=["GET", "POST"])
def api_analysis_reproducibility_summary():
    """
    Generates or retrieves reproducibility summaries, verifying inputs,
    seeds, parameters, and model versions.
    """
    from utils.analysis_reproducibility_service import analysis_reproducibility_service

    if request.method == "GET":
        analysis_id = request.args.get("analysis_id")
        if not analysis_id:
            return jsonify({"success": False, "error": "analysis_id is required"}), 400
        analysis_record = get_analysis(analysis_id)
        if analysis_record and not _is_student_authorized(analysis_record):
            return jsonify({"success": False, "error": "Reproducibility summary not found"}), 404
        summary = analysis_reproducibility_service.get_reproducibility_summary(analysis_id)
        if not summary:
            return jsonify({"success": False, "error": "Reproducibility summary not found"}), 404
        return jsonify({"success": True, "reproducibility": summary}), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        analysis_id = payload.get("analysis_id")
        resume_text = payload.get("resume_text", "")

        if not analysis_id:
            return jsonify({"success": False, "error": "analysis_id is required"}), 400
        if not resume_text:
            return jsonify({"success": False, "error": "resume_text is required"}), 400

        student_id = flask_session.get("user_id") or payload.get("student_id") or "default_student"
        summary = analysis_reproducibility_service.record_reproducibility_summary(
            analysis_id=analysis_id,
            student_id=student_id,
            resume_text=resume_text,
            job_description=payload.get("job_description"),
            target_role=payload.get("target_role"),
            model_name=payload.get("model_name", "gemini-1.5-flash"),
            prompt_version=payload.get("prompt_version", "v2.1"),
            temperature=float(payload.get("temperature", 0.2)),
            seed=int(payload.get("seed", 42)),
            scores=payload.get("scores")
        )
        return jsonify({
            "success": True,
            "reproducibility": summary
        }), 201


@analysis_bp.route("/api/analysis/compare-reproducibility", methods=["POST"])
def api_compare_analysis_reproducibility():
    """
    Compares two evaluation runs to check whether identical inputs and configs were used.
    """
    from utils.analysis_reproducibility_service import analysis_reproducibility_service

    payload = request.get_json(silent=True) or request.form
    id_a = payload.get("analysis_id_a")
    id_b = payload.get("analysis_id_b")

    if not id_a or not id_b:
        return jsonify({"success": False, "error": "analysis_id_a and analysis_id_b are required"}), 400

    comparison = analysis_reproducibility_service.compare_runs_reproducibility(id_a, id_b)
    return jsonify({
        "success": True,
        "comparison": comparison
    }), 200


@analysis_bp.route("/api/resume/scan-pii", methods=["POST"])
def scan_resume_pii_endpoint():
    """
    Scans submitted resume text or uploaded resume file for Personally Identifiable
    Information (PII) including email addresses, phone numbers, addresses,
    and government identifiers (PAN, Aadhaar, SSN).
    """
    if "user_id" not in flask_session:
        return jsonify({"status": "error", "message": "Authentication required."}), 401

    text_to_scan = ""
    # Option 1: JSON payload
    if request.is_json:
        data = request.get_json(silent=True) or {}
        text_to_scan = data.get("text", "")
    elif "resume" in request.files:
        # Option 2: Uploaded file
        uploaded_file = request.files["resume"]
        if uploaded_file and uploaded_file.filename != "":
            try:
                text_to_scan, _, _, err = _extract_and_validate_resume(request)
                if err:
                    return jsonify({"status": "error", "message": err}), 400
            except Exception as e:
                return jsonify({"status": "error", "message": f"Failed to extract text: {str(e)}"}), 400
    else:
        text_to_scan = request.form.get("text", "")

    if not text_to_scan or not text_to_scan.strip():
        return jsonify({
            "status": "error",
            "message": "No resume text provided for PII scanning."
        }), 400

    scan_result = pii_service.detect_pii(text_to_scan)
    return jsonify({
        "status": "success",
        "scan": scan_result
    }), 200


@analysis_bp.route("/api/resume/redact-pii", methods=["POST"])
def redact_resume_pii_endpoint():
    """
    Applies configurable redaction rules to sensitive PII entities within resume text.
    Allows candidate granular control to redact emails, phone numbers, addresses,
    government IDs, or custom sensitive terms.
    """
    if "user_id" not in flask_session:
        return jsonify({"status": "error", "message": "Authentication required."}), 401

    data = request.get_json(silent=True) or request.form
    raw_text = data.get("text", "")

    if not raw_text or not raw_text.strip():
        return jsonify({
            "status": "error",
            "message": "No resume text provided for redaction."
        }), 400

    redact_emails = data.get("redact_emails", True)
    if isinstance(redact_emails, str):
        redact_emails = redact_emails.lower() not in ("false", "0", "no")

    redact_phones = data.get("redact_phones", True)
    if isinstance(redact_phones, str):
        redact_phones = redact_phones.lower() not in ("false", "0", "no")

    redact_addresses = data.get("redact_addresses", True)
    if isinstance(redact_addresses, str):
        redact_addresses = redact_addresses.lower() not in ("false", "0", "no")

    redact_gov_ids = data.get("redact_gov_ids", True)
    if isinstance(redact_gov_ids, str):
        redact_gov_ids = redact_gov_ids.lower() not in ("false", "0", "no")

    custom_tokens = data.get("custom_redact_values", [])
    if isinstance(custom_tokens, str):
        custom_tokens = [t.strip() for t in custom_tokens.split(",") if t.strip()]

    redacted_result = pii_service.redact_pii(
        raw_text,
        redact_emails=redact_emails,
        redact_phones=redact_phones,
        redact_addresses=redact_addresses,
        redact_gov_ids=redact_gov_ids,
        custom_redact_values=custom_tokens
    )

    return jsonify({
        "status": "success",
        "redaction": redacted_result
    }), 200


@analysis_bp.route("/api/interview/practice/history", methods=["GET"])
def api_practice_history():
    """Retrieves chronological practice session history for the logged-in student."""
    student_id = flask_session.get("user_id") or "default_student"
    history = get_student_practice_history(student_id)
    return jsonify({
        "status": "success",
        "student_id": student_id,
        "total_sessions": len(history),
        "history": history
    }), 200


@analysis_bp.route("/saved-job-descriptions", methods=["GET", "POST"])
def saved_job_descriptions_view():
    student_id, auth_response = _require_authenticated_student()
    if auth_response:
        return auth_response

    if request.method == "POST":
        fields, error = _saved_job_description_fields(request.form)
        if error:
            flash(error, "danger")
        else:
            create_saved_job_description(student_id=student_id, **fields)
            flash("Job description saved to your private library.", "success")
            return redirect(url_for("saved_job_descriptions_view"))

    return render_template(
        "saved_job_descriptions.html",
        saved_job_descriptions=get_saved_job_descriptions(student_id),
        editing=None
    )


@analysis_bp.route("/saved-job-descriptions/<saved_id>/edit", methods=["GET", "POST"])
def edit_saved_job_description_view(saved_id):
    student_id, auth_response = _require_authenticated_student()
    if auth_response:
        return auth_response
    saved = get_saved_job_description_by_id(saved_id, student_id)
    if not saved:
        flash("Saved job description not found or access denied.", "danger")
        return redirect(url_for("saved_job_descriptions_view"))

    if request.method == "POST":
        fields, error = _saved_job_description_fields(request.form)
        if error:
            flash(error, "danger")
        elif update_saved_job_description(saved_id, student_id, **fields):
            flash("Saved job description updated.", "success")
            return redirect(url_for("saved_job_descriptions_view"))
        else:
            flash("Saved job description could not be updated.", "danger")
        saved = dict(saved)
        saved.update(fields or {})

    return render_template(
        "saved_job_descriptions.html",
        saved_job_descriptions=get_saved_job_descriptions(student_id),
        editing=saved
    )


@analysis_bp.route("/saved-job-descriptions/<saved_id>/delete", methods=["POST"])
def delete_saved_job_description_view(saved_id):
    student_id, auth_response = _require_authenticated_student()
    if auth_response:
        return auth_response
    if delete_saved_job_description(saved_id, student_id):
        flash("Saved job description deleted.", "success")
    else:
        flash("Saved job description not found or access denied.", "danger")
    return redirect(url_for("saved_job_descriptions_view"))


@analysis_bp.route("/api/saved-job-descriptions", methods=["GET", "POST"])
def api_saved_job_descriptions():
    student_id, auth_response = _require_authenticated_student()
    if auth_response:
        return auth_response

    if request.method == "GET":
        descriptions = get_saved_job_descriptions(student_id)
        return jsonify({"success": True, "saved_job_descriptions": descriptions}), 200

    payload = request.get_json(silent=True) or request.form
    fields, error = _saved_job_description_fields(payload)
    if error:
        return jsonify({"success": False, "error": error}), 400
    saved = create_saved_job_description(student_id=student_id, **fields)
    return jsonify({"success": True, "saved_job_description": saved}), 201


@analysis_bp.route("/api/saved-job-descriptions/<saved_id>", methods=["GET", "PUT", "PATCH", "DELETE"])
def api_saved_job_description_item(saved_id):
    student_id, auth_response = _require_authenticated_student()
    if auth_response:
        return auth_response
    saved = get_saved_job_description_by_id(saved_id, student_id)
    if not saved:
        return jsonify({"success": False, "error": "Saved job description not found or access denied."}), 404

    if request.method == "GET":
        return jsonify({"success": True, "saved_job_description": saved}), 200
    if request.method == "DELETE":
        deleted = delete_saved_job_description(saved_id, student_id)
        return jsonify({"success": deleted, "message": "Saved job description deleted." if deleted else "Delete failed."}), 200 if deleted else 500

    payload = request.get_json(silent=True) or request.form
    fields, error = _saved_job_description_fields(payload)
    if error:
        return jsonify({"success": False, "error": error}), 400
    updated = update_saved_job_description(saved_id, student_id, **fields)
    if not updated:
        return jsonify({"success": False, "error": "Saved job description could not be updated."}), 500
    return jsonify({
        "success": True,
        "saved_job_description": get_saved_job_description_by_id(saved_id, student_id)
    }), 200


@analysis_bp.route("/api/job-description/extract-url", methods=["POST"])
@analysis_bp.route("/api/job-description/import-link", methods=["POST"])
@csrf.exempt
def api_extract_job_description_url():
    """
    Extracts and normalizes job posting description text from a student-provided URL (#689).
    Enforces SSRF prevention (blocking private, loopback, link-local IPs and dangerous ports),
    short timeouts, download limits, redirect IP validation, and readability extraction.
    """
    payload = request.get_json(silent=True) or request.form or {}
    url = (payload.get("url") or payload.get("link") or payload.get("job_url") or "").strip()

    if not url:
        return jsonify({
            "status": "error",
            "success": False,
            "error": "Please provide a valid job posting link."
        }), 400

    try:
        result = extract_job_description_from_url(url)
        return jsonify({
            "status": "success",
            "success": True,
            "url": url,
            "job_description": result["job_description"],
            "character_count": result["character_count"],
            "title": result.get("title", ""),
            "company": result.get("company", ""),
        }), 200
    except SSRFSecurityError as e:
        logger.warning(f"SSRF safety check blocked job URL request: {url} -> {e}")
        return jsonify({
            "status": "error",
            "success": False,
            "error_type": "security_restriction",
            "error": f"The provided URL cannot be fetched for security reasons: {str(e)} Please copy and paste the job description text manually."
        }), 400
    except JobURLExtractionError as e:
        logger.info(f"Job URL extraction failed for {url}: {e}")
        return jsonify({
            "status": "error",
            "success": False,
            "error_type": "extraction_failed",
            "error": str(e)
        }), 422
    except Exception as e:
        logger.exception(f"Unexpected error while extracting job URL {url}: {e}")
        return jsonify({
            "status": "error",
            "success": False,
            "error_type": "server_error",
            "error": "Failed to extract job description from the provided link. Please copy and paste the job description text manually."
        }), 500


@analysis_bp.route("/api/goals", methods=["GET", "POST"])
def api_preparation_goals():
    """
    Creates or lists student target-date preparation plans and milestone metrics.
    """
    from utils.goal_planner_service import goal_planner_service

    student_id = flask_session.get("user_id") or "default_student"

    if request.method == "GET":
        goals = goal_planner_service.list_student_goals(student_id)
        return jsonify({
            "success": True,
            "count": len(goals),
            "goals": goals
        }), 200

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        title = payload.get("goal_title", "My Placement Goal")
        role = payload.get("target_role", "Software Engineer")
        target_date = payload.get("target_date")
        target_coding = int(payload.get("target_coding_problems", 50))
        target_mock = int(payload.get("target_mock_interviews", 5))
        resume_ready = bool(payload.get("resume_completed", False))
        notes = payload.get("notes")

        if not target_date:
            return jsonify({"success": False, "error": "target_date (YYYY-MM-DD) is required"}), 400

        try:
            goal_id = goal_planner_service.create_goal(
                student_id=student_id,
                goal_title=title,
                target_role=role,
                target_date_str=target_date,
                target_coding_problems=target_coding,
                target_mock_interviews=target_mock,
                resume_completed=resume_ready,
                notes=notes
            )
        except ValueError as ve:
            return jsonify({"success": False, "error": f"Invalid date format: {str(ve)}"}), 400

        return jsonify({
            "success": True,
            "goal_id": goal_id,
            "message": "Preparation goal created successfully."
        }), 201


@analysis_bp.route("/api/goals/<int:gid>/progress", methods=["PUT", "POST"])
def api_update_goal_progress(gid):
    """
    Updates completion numbers for coding problems, mock interviews, or resume status.
    """
    from utils.goal_planner_service import goal_planner_service

    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form

    completed_coding = payload.get("completed_coding_problems")
    completed_interviews = payload.get("completed_mock_interviews")
    resume_completed = payload.get("resume_completed")
    status = payload.get("status")

    updated = goal_planner_service.update_goal_progress(
        student_id=student_id,
        goal_id=gid,
        completed_coding=int(completed_coding) if completed_coding is not None else None,
        completed_interviews=int(completed_interviews) if completed_interviews is not None else None,
        resume_completed=bool(resume_completed) if resume_completed is not None else None,
        status=status
    )

    if not updated:
        return jsonify({"success": False, "error": "Goal not found or no updates provided"}), 404

    return jsonify({"success": True, "message": "Goal progress updated successfully."}), 200


@analysis_bp.route("/api/trash/items", methods=["GET"])
def api_list_trash_items():
    """
    Lists soft-deleted items available for restore in the student's trash.
    """
    from utils.restore_service import restore_service

    student_id = flask_session.get("user_id") or "default_student"
    items = restore_service.list_trash_items(student_id)
    return jsonify({
        "success": True,
        "count": len(items),
        "items": items
    }), 200


@analysis_bp.route("/api/trash/soft-delete", methods=["POST"])
def api_soft_delete_item():
    """
    Soft-deletes an entity into the recycle bin with undo grace period.
    """
    from utils.restore_service import restore_service

    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form

    entity_type = payload.get("entity_type", "generic")
    entity_id = payload.get("entity_id")
    entity_title = payload.get("entity_title", "Untitled Item")
    original_data = payload.get("original_data") or {}

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id is required"}), 400

    result = restore_service.soft_delete_item(
        student_id=student_id,
        entity_type=entity_type,
        entity_id=str(entity_id),
        entity_title=entity_title,
        original_data=original_data
    )
    return jsonify(result), 200


@analysis_bp.route("/api/trash/undo/<undo_token>", methods=["POST"])
def api_undo_soft_deletion(undo_token):
    """
    Restores a soft-deleted item using its undo token.
    """
    from utils.restore_service import restore_service

    student_id = flask_session.get("user_id") or "default_student"
    restored = restore_service.restore_item(student_id, undo_token)

    if not restored.get("success"):
        return jsonify(restored), 400

    return jsonify(restored), 200


@analysis_bp.route("/api/recommendations/dismiss", methods=["POST"])
def api_dismiss_recommendation():
    """
    Marks an AI recommendation as not relevant with a student-chosen reason.
    """
    from utils.recommendation_dismissal_service import recommendation_dismissal_service

    student_id = flask_session.get("user_id") or "default_student"
    payload = request.get_json(silent=True) or request.form

    rec_id = payload.get("recommendation_id")
    rec_title = payload.get("recommendation_title", "Untitled Recommendation")
    rec_type = payload.get("recommendation_type", "skill_recommendation")
    reason = payload.get("reason", "Not interested in this technology / domain")
    custom_notes = payload.get("custom_notes")

    if not rec_id:
        return jsonify({"success": False, "error": "recommendation_id is required"}), 400

    result = recommendation_dismissal_service.dismiss_recommendation(
        student_id=student_id,
        recommendation_id=rec_id,
        recommendation_title=rec_title,
        recommendation_type=rec_type,
        reason=reason,
        custom_notes=custom_notes
    )

    return jsonify(result), 200


@analysis_bp.route("/api/recommendations/dismissed", methods=["GET"])
def api_get_dismissed_recommendations():
    """
    Retrieves all recommendations dismissed by the current student.
    """
    from utils.recommendation_dismissal_service import recommendation_dismissal_service

    student_id = flask_session.get("user_id") or "default_student"
    dismissed = recommendation_dismissal_service.list_dismissed_recommendations(student_id)
    return jsonify({
        "success": True,
        "count": len(dismissed),
        "dismissed": dismissed
    }), 200


@analysis_bp.route("/api/recommendations/undismiss/<rec_id>", methods=["POST", "DELETE"])
def api_undismiss_recommendation(rec_id):
    """
    Restores a previously dismissed recommendation.
    """
    from utils.recommendation_dismissal_service import recommendation_dismissal_service

    student_id = flask_session.get("user_id") or "default_student"
    restored = recommendation_dismissal_service.undismiss_recommendation(student_id, rec_id)

    if not restored:
        return jsonify({"success": False, "error": "Dismissed recommendation not found"}), 404

    return jsonify({
        "success": True,
        "message": f"Recommendation '{rec_id}' restored to active feed."
    }), 200


@analysis_bp.route("/api/analyses/feedback/categories", methods=["GET"])
def api_get_feedback_categories():
    """Retrieve list of valid AI analysis feedback and inaccuracy categories."""
    from utils.analysis_feedback_service import FEEDBACK_CATEGORIES
    return jsonify({
        "success": True,
        "categories": [
            {"id": k, "title": v["title"], "description": v["description"]}
            for k, v in FEEDBACK_CATEGORIES.items()
        ]
    }), 200


@analysis_bp.route("/api/analyses/<analysis_id>/feedback", methods=["POST"])
def api_submit_analysis_feedback(analysis_id: str):
    """Submit a correction or feedback report for an AI analysis."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    category = payload.get("category")
    claim_detail = payload.get("claim_detail")
    field_affected = payload.get("field_affected", "general")
    proposed_correction = payload.get("proposed_correction")

    try:
        from utils.analysis_feedback_service import submit_analysis_feedback
        report = submit_analysis_feedback(
            analysis_id=analysis_id,
            student_id=str(user_id),
            category=category,
            claim_detail=claim_detail,
            field_affected=field_affected,
            proposed_correction=proposed_correction,
        )
        return jsonify({
            "success": True,
            "message": "Feedback submitted successfully for review",
            "report": report
        }), 201
    except PermissionError as pe:
        return jsonify({"success": False, "error": str(pe)}), 403
    except KeyError as ke:
        return jsonify({"success": False, "error": str(ke)}), 404
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error submitting analysis feedback: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@analysis_bp.route("/api/analyses/<analysis_id>/feedback", methods=["GET"])
def api_get_analysis_feedback(analysis_id: str):
    """Retrieve all feedback reports for a given analysis."""
    user_id = flask_session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    try:
        from utils.analysis_feedback_service import get_analysis_feedback_reports
        reports = get_analysis_feedback_reports(analysis_id, student_id=str(user_id))
        return jsonify({
            "success": True,
            "analysis_id": analysis_id,
            "reports": reports
        }), 200
    except Exception as e:
        logger.error(f"Error retrieving analysis feedback: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==============================================================================
# ROLE SKILL TEMPLATES API (#646)
# ==============================================================================

@analysis_bp.route("/api/role-templates", methods=["GET"])
def api_get_role_templates():
    """
    Returns the list of predefined campus placement role templates (#646).
    """
    templates = load_role_templates()
    return jsonify({
        "success": True,
        "count": len(templates),
        "templates": templates
    }), 200


@analysis_bp.route("/api/role-templates/<role_id>", methods=["GET"])
def api_get_role_template_by_id(role_id: str):
    """
    Returns details for a specific role template by id (#646).
    """
    template = get_role_template_by_id(role_id)
    if not template:
        return jsonify({
            "success": False,
            "error": f"Role template '{role_id}' not found."
        }), 404
    return jsonify({
        "success": True,
        "template": template
    }), 200


@analysis_bp.route("/api/analysis/freshness-check", methods=["POST"])
def api_check_analysis_freshness():
    """Evaluates if an existing analysis report is current or outdated (#628)."""
    from utils.data_freshness_service import check_report_freshness
    data = request.get_json(silent=True) or {}
    report_meta = data.get("report_meta", {})
    current_profile = data.get("current_profile", {})

    freshness = check_report_freshness(report_meta, current_profile)
    return jsonify({"status": "success", "freshness": freshness}), 200


# ==============================================================================
# RESUME LIBRARY: MY RESUMES (Issue #842)
# ==============================================================================

@analysis_bp.route("/resumes", methods=["GET"])
def my_resumes_view():
    """
    Renders the 'My Resumes' library page where a student can store up to 5 named resumes
    (label, role focus, uploaded date) with their extracted text (#842).
    """
    student_id = flask_session.get("user_id") or flask_session.get("student_id")
    if not student_id:
        flash("Please log in to manage your resume library.", "info")
        return redirect(url_for("index"))

    from utils.resume_library_service import resume_library_service, MAX_STORED_RESUMES_PER_USER
    resumes = resume_library_service.list_resumes(student_id)
    return render_template(
        "my_resumes.html",
        resumes=resumes,
        max_resumes=MAX_STORED_RESUMES_PER_USER,
        current_count=len(resumes)
    )


@analysis_bp.route("/api/resumes", methods=["GET"])
def api_list_resumes():
    """Returns the authenticated student's stored resumes list (#842)."""
    student_id = flask_session.get("user_id") or flask_session.get("student_id")
    if not student_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    from utils.resume_library_service import resume_library_service, MAX_STORED_RESUMES_PER_USER
    resumes = resume_library_service.list_resumes(student_id)
    return jsonify({
        "success": True,
        "resumes": resumes,
        "count": len(resumes),
        "max_resumes": MAX_STORED_RESUMES_PER_USER
    }), 200


@analysis_bp.route("/api/resumes", methods=["POST"])
def api_create_resume():
    """
    Uploads/stores a new named resume into the student's library (#842).
    Enforces maximum limit of 5.
    """
    student_id = flask_session.get("user_id") or flask_session.get("student_id")
    if not student_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    from utils.resume_library_service import (
        resume_library_service,
        ResumeLimitExceededError
    )

    label = request.form.get("label", "").strip()
    role_focus = request.form.get("role_focus", "General").strip()
    pasted_text = request.form.get("resume_text", "").strip()
    uploaded_file = req = request.files.get("resume_file") or request.files.get("resume")

    extracted_text = ""
    resume_filename = f"{label}.pdf" if label else "resume.pdf"

    if uploaded_file and uploaded_file.filename:
        filename_lower = uploaded_file.filename.lower()
        ext = os.path.splitext(uploaded_file.filename)[1].lower()
        if ext not in (".pdf", ".docx"):
            return jsonify({"success": False, "error": "Only PDF (.pdf) and Word (.docx) files are supported."}), 400

        resume_filename = secure_filename(uploaded_file.filename)
        temp_uuid = str(uuid.uuid4())
        safe_path = os.path.join(config.UPLOAD_FOLDER, f"{temp_uuid}_{resume_filename}")
        os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
        uploaded_file.save(safe_path)

        try:
            from utils.pdf_reader import extract_text_from_file
            extracted_text = extract_text_from_file(safe_path)
        except Exception as e:
            return jsonify({"success": False, "error": f"Failed to extract text from resume: {str(e)}"}), 400
        finally:
            if os.path.exists(safe_path):
                try:
                    os.remove(safe_path)
                except Exception:
                    pass
    elif pasted_text:
        extracted_text = pasted_text
        resume_filename = f"{label or 'Pasted'}_Resume.txt"
    else:
        return jsonify({"success": False, "error": "Please provide a resume file or paste text."}), 400

    try:
        new_resume = resume_library_service.create_resume(
            student_id=student_id,
            label=label,
            role_focus=role_focus,
            extracted_text=extracted_text,
            resume_filename=resume_filename
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": True, "resume": new_resume, "message": f"Resume '{label}' saved successfully."}), 201

        flash(f"Resume '{label}' stored successfully in your library.", "success")
        return redirect(url_for("analysis.my_resumes_view"))
    except ResumeLimitExceededError as rle:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": str(rle)}), 400
        flash(str(rle), "warning")
        return redirect(url_for("analysis.my_resumes_view"))
    except ValueError as ve:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": str(ve)}), 400
        flash(str(ve), "error")
        return redirect(url_for("analysis.my_resumes_view"))
    except Exception as e:
        logger.error(f"Error creating stored resume: {e}")
        return jsonify({"success": False, "error": "Failed to store resume."}), 500


@analysis_bp.route("/api/resumes/<resume_id>", methods=["GET"])
def api_get_resume(resume_id: str):
    """Retrieves a single stored resume (#842)."""
    student_id = flask_session.get("user_id") or flask_session.get("student_id")
    if not student_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    from utils.resume_library_service import resume_library_service, ResumeAccessDeniedError
    try:
        resume = resume_library_service.get_resume(resume_id, student_id)
        if not resume:
            return jsonify({"success": False, "error": "Resume not found"}), 404
        return jsonify({"success": True, "resume": resume}), 200
    except ResumeAccessDeniedError as pe:
        return jsonify({"success": False, "error": str(pe)}), 403


@analysis_bp.route("/api/resumes/<resume_id>", methods=["PUT", "POST"])
def api_update_resume(resume_id: str):
    """
    Renames or replaces an existing stored resume (#842).
    """
    student_id = flask_session.get("user_id") or flask_session.get("student_id")
    if not student_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    from utils.resume_library_service import (
        resume_library_service,
        ResumeNotFoundError,
        ResumeAccessDeniedError
    )

    data = request.get_json(silent=True) or request.form
    label = data.get("label")
    role_focus = data.get("role_focus")
    extracted_text = data.get("resume_text")
    resume_filename = None

    uploaded_file = request.files.get("resume_file") or request.files.get("resume")
    if uploaded_file and uploaded_file.filename:
        ext = os.path.splitext(uploaded_file.filename)[1].lower()
        if ext not in (".pdf", ".docx"):
            return jsonify({"success": False, "error": "Only PDF and Word documents are supported."}), 400
        resume_filename = secure_filename(uploaded_file.filename)
        temp_uuid = str(uuid.uuid4())
        safe_path = os.path.join(config.UPLOAD_FOLDER, f"{temp_uuid}_{resume_filename}")
        os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
        uploaded_file.save(safe_path)
        try:
            from utils.pdf_reader import extract_text_from_file
            extracted_text = extract_text_from_file(safe_path)
        except Exception as e:
            return jsonify({"success": False, "error": f"Failed to extract text: {e}"}), 400
        finally:
            if os.path.exists(safe_path):
                try:
                    os.remove(safe_path)
                except Exception:
                    pass

    try:
        updated = resume_library_service.update_resume(
            resume_id=resume_id,
            student_id=student_id,
            label=label,
            role_focus=role_focus,
            extracted_text=extracted_text,
            resume_filename=resume_filename
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": True, "resume": updated, "message": "Resume updated successfully."}), 200

        flash("Resume updated successfully.", "success")
        return redirect(url_for("analysis.my_resumes_view"))
    except ResumeAccessDeniedError as pe:
        return jsonify({"success": False, "error": str(pe)}), 403
    except ResumeNotFoundError:
        return jsonify({"success": False, "error": "Resume not found"}), 404
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error updating resume {resume_id}: {e}")
        return jsonify({"success": False, "error": "Failed to update resume."}), 500


@analysis_bp.route("/api/resumes/<resume_id>/delete", methods=["POST", "DELETE"])
@analysis_bp.route("/resumes/delete/<resume_id>", methods=["POST"])
def api_delete_resume(resume_id: str):
    """
    Deletes a stored resume from the library (#842).
    Past analyses remain readable as they maintain independent snapshots.
    """
    student_id = flask_session.get("user_id") or flask_session.get("student_id")
    if not student_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    from utils.resume_library_service import (
        resume_library_service,
        ResumeNotFoundError,
        ResumeAccessDeniedError
    )

    try:
        resume_library_service.delete_resume(resume_id, student_id)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": True, "message": "Resume deleted successfully."}), 200

        flash("Resume deleted from your library.", "success")
        return redirect(url_for("analysis.my_resumes_view"))
    except ResumeAccessDeniedError as pe:
        return jsonify({"success": False, "error": str(pe)}), 403
    except ResumeNotFoundError:
        return jsonify({"success": False, "error": "Resume not found"}), 404
    except Exception as e:
        logger.error(f"Error deleting resume {resume_id}: {e}")
        return jsonify({"success": False, "error": "Failed to delete resume."}), 500


# -------------------------------------------------------------
# Skill Gap Certifications & Courses Recommendation Endpoints (#757)
# -------------------------------------------------------------

@analysis_bp.route("/api/skills/certifications", methods=["GET"])
def api_get_skill_certifications():
    """
    Returns curated course and certification recommendations for a given skill (#757).
    Shows up to 3 options with provider, level, free/paid type, duration, and last-verified date.
    Returns a generic fallback message if no curated entry exists.
    """
    skill = request.args.get("skill", "").strip()
    limit = request.args.get("limit", 3)
    try:
        limit = max(1, min(10, int(limit)))
    except (ValueError, TypeError):
        limit = 3

    if not skill:
        return jsonify({
            "success": False,
            "error": "Query parameter 'skill' is required."
        }), 400

    from utils.skill_certification_service import skill_certification_service
    rec = skill_certification_service.get_certifications_for_skill(skill, limit=limit)
    return jsonify({
        "success": True,
        "data": rec
    }), 200


@analysis_bp.route("/api/skills/certifications/batch", methods=["POST"])
def api_get_batch_skill_certifications():
    """
    Batch endpoint returning course & certification recommendations for multiple missing skills (#757).
    """
    body = request.get_json(silent=True) or {}
    skills = body.get("skills", [])
    if isinstance(skills, str):
        skills = [s.strip() for s in re.split(r"[,;\n]+", skills) if s.strip()]

    limit = body.get("limit", 3)
    try:
        limit = max(1, min(10, int(limit)))
    except (ValueError, TypeError):
        limit = 3

    from utils.skill_certification_service import skill_certification_service
    recommendations = skill_certification_service.get_recommendations_for_skills(skills, limit_per_skill=limit)
    return jsonify({
        "success": True,
        "count": len(recommendations),
        "results": recommendations
    }), 200


@analysis_bp.route("/api/analysis/<analysis_id>/skill-certifications", methods=["GET"])
def api_get_analysis_skill_certifications(analysis_id):
    """
    Retrieves course and certification recommendations for all missing skills in an analysis report (#757).
    """
    from utils.database import get_analysis_by_id
    analysis_record = get_analysis_by_id(analysis_id)
    if not analysis_record:
        return jsonify({"success": False, "error": "Analysis not found"}), 404

    results = analysis_record.get("results") or {}
    skill_gaps = results.get("skill_gap") or []
    missing_skills = []
    if isinstance(skill_gaps, list):
        for g in skill_gaps:
            if isinstance(g, dict) and (g.get("skill") or g.get("missing_skill")):
                missing_skills.append(g.get("skill") or g.get("missing_skill"))
            elif isinstance(g, str):
                missing_skills.append(g)

    # Fallback to job match missing skills if empty
    if not missing_skills:
        job_match = results.get("job_match") or {}
        missing_skills = job_match.get("missing_skills") or []

    from utils.skill_certification_service import skill_certification_service
    recommendations = skill_certification_service.get_recommendations_for_skills(missing_skills, limit_per_skill=3)
    return jsonify({
        "success": True,
        "analysis_id": analysis_id,
        "count": len(recommendations),
        "results": recommendations
    }), 200




