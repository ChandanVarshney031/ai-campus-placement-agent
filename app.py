import os
import io
import uuid
import json
import time
import logging
import re
from pathlib import Path
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    send_file,
    Response,
)
from werkzeug.utils import secure_filename


import config
from utils.database import (
    init_db,
    get_db_connection,
    save_analysis,
    get_analysis,
    get_recent_analyses,
    delete_analysis,
    create_interview_session,
    get_interview_session,
    update_interview_session,
    get_student_versions,
    get_previous_analysis,
    create_database_backup,
    verify_database_integrity,
    get_batch_comparison,
    get_student_interview_history,
    get_student_interview_performance_analytics,
    get_user_placement_history,
    save_batch_comparison,
    save_job_application,
    get_job_applications,
    delete_job_application,
    update_job_application_status,
)
from utils.batch_analyzer import run_batch_company_comparison
from utils.progress_tracker import (
    calculate_version_delta,
    build_longitudinal_series,
    generate_side_by_side_diff,
    compare_two_resume_texts
)
from utils.cohort_analytics import CohortAnalyticsService
from utils.pdf_reader import (
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_file,
    validate_resume_text,
    MAX_RESUME_TEXT_CHARS,
    ALLOWED_RESUME_EXTENSIONS,
)
from utils.gemini import (
    AIAnalysisError,
    AIRateLimitError,
    format_ai_error_for_user,
)
from utils.report_generator import generate_pdf_report, generate_certificate_pdf, generate_skills_gap_pdf
from utils.gemini import (
    AIAnalysisError,
    AIRateLimitError,
    format_ai_error_for_user,
)
from utils.report_generator import generate_pdf_report
from utils.rate_limiter import rate_limit
from utils.async_pipeline import (
    create_analysis_task,
    get_analysis_task,
    start_async_analysis,
)
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

# Configure structured application logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask application
app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH


@app.before_request
def assign_request_id():
    """Assign or extract an X-Request-ID for distributed tracing and structured correlation."""
    from flask import g
    req_id = request.headers.get("X-Request-ID")
    if not req_id:
        req_id = str(uuid.uuid4())
    g.request_id = req_id


@app.after_request
def inject_correlation_headers(response):
    """Echo the X-Request-ID correlation header in every response for client observability."""
    from flask import g
    req_id = getattr(g, "request_id", None)
    if req_id:
        response.headers["X-Request-ID"] = req_id
    return response


@app.errorhandler(413)
def request_entity_too_large(error):
    flash("Uploaded file exceeds the 16MB size limit. Please upload a smaller PDF.", "error")
    return redirect(url_for("index"))


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
    csp_directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://va.vercel-scripts.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com",
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:",
        "img-src 'self' data: https:",
        "connect-src 'self' https://va.vercel-scripts.com https://vitals.vercel-insights.com",
        "frame-ancestors 'self'",
    ]
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"
    return response

# Ensure directories exist
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(config.REPORT_FOLDER, exist_ok=True)

# Run database schema migrations/initializations
try:
    init_db()
except Exception as db_err:
    logger.critical(f"Critical: Database initialization failed: {str(db_err)}")

# Instantiate coordination and specialized interview agents
coordinator_agent = CoordinatorAgent()
live_interview_agent = InterviewAgent()
coding_assessment_agent = CodingAssessmentAgent()
star_evaluation_agent = StarEvaluationAgent()
github_analyzer_agent = GitHubAnalyzerAgent()
internship_agent = InternshipRecommendationAgent()
linkedin_agent = LinkedInAgent()
coding_practice_agent = CodingPracticeAgent()
outreach_agent = OutreachGeneratorAgent()

def get_available_companies() -> list:
    """
    Loads list of companies dynamically from data/companies.json with fallback defaults.
    """
    companies_file = config.BASE_DIR / "data" / "companies.json"
    default_companies = ["Google", "Microsoft", "Amazon", "TCS", "Infosys", "Accenture", "Capgemini", "Wipro"]
    try:
        if companies_file.exists():
            with open(companies_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return sorted(list(data.keys()))
    except Exception as e:
        logger.warning(f"Could not load dynamic companies from {companies_file}: {e}")
    return default_companies


@app.route("/healthz", methods=["GET"], endpoint="healthz")
@app.route("/health", methods=["GET"], endpoint="health")
@app.route("/api/health", methods=["GET"], endpoint="api_health")
def health_check():
    """
    Production-grade Kubernetes / Cloud liveness and readiness probe endpoint.
    Performs checks on:
    - SQLite database connectivity and query response time
    - Storage directories accessibility (uploads, reports)
    - Gemini API key configuration status
    """
    start_time = time.time()
    checks = {
        "database": {"status": "unknown"},
        "storage": {"status": "unknown"},
        "gemini_config": {"status": "unknown"},
    }
    all_healthy = True

    # 1. Database check
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        checks["database"] = {"status": "healthy", "engine": "sqlite"}
    except Exception as db_err:
        all_healthy = False
        checks["database"] = {"status": "unhealthy", "error": str(db_err)}

    # 2. Storage write permissions
    try:
        uploads_ok = os.path.exists(config.UPLOAD_FOLDER) and os.access(config.UPLOAD_FOLDER, os.W_OK)
        reports_ok = os.path.exists(config.REPORT_FOLDER) and os.access(config.REPORT_FOLDER, os.W_OK)
        if uploads_ok and reports_ok:
            checks["storage"] = {"status": "healthy", "writable": True}
        else:
            all_healthy = False
            checks["storage"] = {
                "status": "unhealthy",
                "uploads_writable": uploads_ok,
                "reports_writable": reports_ok,
            }
    except Exception as st_err:
        all_healthy = False
        checks["storage"] = {"status": "unhealthy", "error": str(st_err)}

    # 3. Gemini API key presence
    api_key = config.GEMINI_API_KEY
    if api_key and api_key != "YOUR_GEMINI_API_KEY_HERE":
        checks["gemini_config"] = {"status": "configured"}
    else:
        checks["gemini_config"] = {"status": "unconfigured_or_placeholder"}

    latency_ms = round((time.time() - start_time) * 1000, 2)
    http_status = 200 if all_healthy else 503

    return jsonify({
        "status": "healthy" if all_healthy else "degraded",
        "service": "ai-campus-placement-agent",
        "latency_ms": latency_ms,
        "checks": checks,
    }), http_status

@app.route("/")
def index() -> str:
    """
    Renders the placement landing page containing the configuration forms.
    """
    companies_list = get_available_companies()
    return render_template("index.html", companies=companies_list)


@app.route("/api/company/<company_name>")
def get_company_profile(company_name: str):
    """
    Returns company hiring details from data/companies.json for the preview explorer modal.
    """
    companies_file = config.BASE_DIR / "data" / "companies.json"
    try:
        if companies_file.exists():
            with open(companies_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if company_name in data:
                    return jsonify({"success": True, "company": company_name, "details": data[company_name]})
    except Exception as e:
        logger.error(f"Error fetching company details for {company_name}: {e}")
    return jsonify({"success": False, "error": f"Profile for '{company_name}' not found."}), 404

def _extract_and_validate_resume(req):
    """
    Validates uploaded resume file or pasted text from Flask request.
    Returns a tuple of (extracted_text, filename, file_uuid, error_message).
    """
    pasted_text = req.form.get("resume_text", "").strip()
    uploaded_file = req.files.get("resume")
    file_uuid = str(uuid.uuid4())

    if pasted_text:
        is_valid, validation_msg = validate_resume_text(pasted_text)
        if not is_valid:
            return None, None, None, validation_msg
        return pasted_text[:MAX_RESUME_TEXT_CHARS], "Pasted_Resume.txt", file_uuid, None
    elif uploaded_file and uploaded_file.filename != "":
        filename_lower = uploaded_file.filename.lower()
        if not any(filename_lower.endswith(ext) for ext in ALLOWED_RESUME_EXTENSIONS):
            return (
                None,
                None,
                None,
                "Invalid file format. Only PDF (.pdf) and Word (.docx) resumes are supported.",
            )
        resume_filename = uploaded_file.filename
        safe_name = secure_filename(f"{file_uuid}_{uploaded_file.filename}")
        saved_file_path = os.path.join(config.UPLOAD_FOLDER, safe_name)
        uploaded_file.save(saved_file_path)
        logger.info(f"Uploaded resume saved locally at: {saved_file_path}")
        extracted_resume_text = extract_text_from_file(saved_file_path)
        return extracted_resume_text, resume_filename, file_uuid, None
    else:
        return None, None, None, "Please upload a PDF resume or paste your resume text."


@app.route("/analyze", methods=["POST"])
@rate_limit(max_requests=15, window_seconds=60, scope="analyze_submission", is_api=False)
def analyze():
    """
    Handles PDF resume uploads, processes files, calls coordinator,
    persists result states, and redirects to candidate dashboards.
    Supports optional ?async=true query parameter or AJAX calls for background execution.
    """
    try:
        extracted_resume_text, resume_filename, file_uuid, validation_error = _extract_and_validate_resume(request)
        if validation_error:
            if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": validation_error}), 400
            flash(validation_error, "error")
            return redirect(url_for("index"))

        selected_company = request.form.get("company", "General").strip()
        job_description = request.form.get("job_description", "").strip()
        student_id = request.form.get("student_id", "default_student").strip()
        if len(job_description) > 15000:
            job_description = job_description[:15000]

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

        # Check for asynchronous execution request
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            task = create_analysis_task(
                company_name=selected_company,
                resume_filename=resume_filename
            )
            start_async_analysis(
                task=task,
                coordinator_agent=coordinator_agent,
                save_analysis_func=save_analysis,
                resume_text=extracted_resume_text,
                job_description=job_description,
                company_name=selected_company,
                resume_filename=resume_filename,
                analysis_id=file_uuid,
                student_id=student_id
            )
            return jsonify({
                "success": True,
                "status": "started",
                "task_id": task.task_id,
                "analysis_id": file_uuid,
                "stream_url": f"/api/analysis/stream/{task.task_id}",
                "status_url": f"/api/analysis/status/{task.task_id}"
            }), 202

        # Synchronous execution fallback (preserves 100% test compatibility)
        analysis_payload = coordinator_agent.analyze(
            resume_text=extracted_resume_text,
            job_description=job_description,
            company_name=selected_company
        )

        save_analysis(
            analysis_id=file_uuid,
            resume_filename=resume_filename,
            company_name=selected_company,
            job_description=job_description,
            results_dict=analysis_payload,
            student_id=student_id
        )

        return redirect(url_for("dashboard", analysis_id=file_uuid))

    except AIAnalysisError as aie:
        logger.warning(f"AIAnalysisError in analysis workflow: {str(aie)}")
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": aie.user_friendly_message}), 500
        flash(aie.user_friendly_message, "error")
        return redirect(url_for("index"))
    except ValueError as ve:
        logger.warning(f"Validation error in analysis workflow: {str(ve)}")
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": str(ve)}), 400
        flash(str(ve), "error")
        return redirect(url_for("index"))
    except Exception as e:
        logger.error(f"Analysis workflow crashed: {str(e)}")
        friendly_message = format_ai_error_for_user(e)
        if request.args.get("async") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": friendly_message}), 500
        flash(friendly_message, "error")
        return redirect(url_for("index"))


@app.route("/api/analysis/start", methods=["POST"])
def start_analysis_async():
    """
    Dedicated REST endpoint initiating asynchronous placement analysis.
    Validates resume text/file and schedules multi-agent processing in ThreadPoolExecutor.
    Returns task_id, stream_url (SSE), and status_url.
    """
    try:
        extracted_resume_text, resume_filename, file_uuid, validation_error = _extract_and_validate_resume(request)
        if validation_error:
            return jsonify({"success": False, "error": validation_error}), 400

        selected_company = request.form.get("company", "General").strip()
        job_description = request.form.get("job_description", "").strip()
        student_id = request.form.get("student_id", "default_student").strip()
        if len(job_description) > 15000:
            job_description = job_description[:15000]

        task = create_analysis_task(
            company_name=selected_company,
            resume_filename=resume_filename
        )

        start_async_analysis(
            task=task,
            coordinator_agent=coordinator_agent,
            save_analysis_func=save_analysis,
            resume_text=extracted_resume_text,
            job_description=job_description,
            company_name=selected_company,
            resume_filename=resume_filename,
            analysis_id=file_uuid,
            student_id=student_id
        )

        return jsonify({
            "success": True,
            "status": "started",
            "task_id": task.task_id,
            "analysis_id": file_uuid,
            "stream_url": f"/api/analysis/stream/{task.task_id}",
            "status_url": f"/api/analysis/status/{task.task_id}"
        }), 202

    except Exception as e:
        logger.error(f"Failed to start async analysis: {str(e)}")
        friendly_message = format_ai_error_for_user(e)
        return jsonify({"success": False, "error": friendly_message}), 500


@app.route("/api/analysis/stream/<task_id>")
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
                    if ev.get("status") in ("completed", "error"):
                        return
            else:
                if task.status in ("completed", "error"):
                    return
                # SSE heartbeat comment to keep HTTP connection alive
                yield ": keep-alive\n\n"

    response = Response(generate_events(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@app.route("/api/analysis/status/<task_id>")
def get_analysis_task_status(task_id: str):
    """
    Polling fallback endpoint returning current analysis task status and milestones.
    """
    task = get_analysis_task(task_id)
    if not task:
        return jsonify({"success": False, "error": f"Analysis task '{task_id}' not found"}), 404

    return jsonify({"success": True, **task.to_dict()}), 200


@app.route("/api/analysis/batch-compare", methods=["POST"])
@app.route("/batch-compare", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60, scope="batch_compare", is_api=True)
def api_batch_compare():
    """
    Dedicated endpoint for multi-company batch placement evaluation.
    Orchestrates concurrent evaluations, builds offer likelihood probability metrics,
    and returns comparison rankings and Chart.js radar multi-dataset data.
    """
    try:
        if request.is_json:
            json_data = request.get_json(silent=True) or {}
            resume_text = (json_data.get("resume_text") or "").strip()
            companies = json_data.get("companies") or json_data.get("batch_companies") or []
            if isinstance(companies, str):
                companies = [c.strip() for c in companies.split(",") if c.strip()]
            job_description = (json_data.get("job_description") or "").strip()
            student_id = (json_data.get("student_id") or "default_student").strip()
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
            student_id = request.form.get("student_id", "default_student").strip()

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


@app.route("/batch-compare/<batch_id>", methods=["GET"])
def view_batch_compare(batch_id: str):
    """
    Renders the Company Comparison Matrix view featuring Chart.js Multi-Dataset
    Radar charts and hiring probability rankings.
    """
    record = get_batch_comparison(batch_id)
    if not record:
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


@app.route("/api/analysis/batch-compare/<batch_id>", methods=["GET"])
def api_get_batch_compare(batch_id: str):
    """
    Retrieves stored batch comparison results JSON for a given batch_id.
    """
    record = get_batch_comparison(batch_id)
    if not record:
        return jsonify({"success": False, "error": f"Batch '{batch_id}' not found."}), 404
    return jsonify({
        "success": True,
        "batch_id": batch_id,
        "data": record.get("results", {})
    }), 200


@app.route("/dashboard/<analysis_id>")
def dashboard(analysis_id: str) -> str:
    """
    Loads and renders the candidate evaluation dashboard with longitudinal
    progress trends and multi-version delta analysis.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    student_id = analysis_record.get("student_id") or "default_student"
    all_versions = get_student_versions(student_id)
    if not all_versions:
        all_versions = [analysis_record]

    current_version = analysis_record.get("version_number", 1)
    previous_record = get_previous_analysis(student_id, current_version=current_version)
    delta_data = calculate_version_delta(analysis_record, previous_record)
    trend_series = build_longitudinal_series(all_versions)
    initial_outreach = outreach_agent.generate_outreach(analysis_record, tone="formal")

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

    return render_template(
        "dashboard.html",
        analysis=analysis_record,
        analysis_id=analysis_id,
        versions=all_versions,
        current_version=current_version,
        previous_record=previous_record,
        delta_data=delta_data,
        trend_series=trend_series,
        trend_series_json=json.dumps(trend_series),
        initial_outreach=initial_outreach,
        initial_outreach_json=json.dumps(initial_outreach)
    )
    return jsonify({
        "success": True,
        **view_data
    }), 200

@app.route("/interview/<analysis_id>")
def interview(analysis_id: str) -> str:
    """
    Bootstraps or loads an existing interactive mock interview session.
    Supports optional ?round=HR|Technical|Behavioral filter to practice focused rounds.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        flash("Cannot initialize mock interview. Parent analysis not found.", "error")
        return redirect(url_for("index"))

    raw_round = request.args.get("round", "").strip().lower()
    round_map = {"hr": "HR", "technical": "Technical", "behavioral": "Behavioral"}
    active_round = round_map.get(raw_round, "All")

    session_id = f"{analysis_id}_{raw_round}" if active_round in round_map.values() else analysis_id

    # Load or initialize the interview session from DB
    interview_session = get_interview_session(session_id)

    if not interview_session:
        # Extract questions generated by Coordinator Agent
        coordinator_questions = analysis_record["results"].get("interview_questions", {})
        
        flat_questions_list = []
        q_idx = 0

        categories_to_include = (
            [active_round] if active_round in round_map.values() else ["HR", "Technical", "Behavioral"]
        )
        
        for category in categories_to_include:
            key = category.lower()
            for question_text in coordinator_questions.get(key, []):
                flat_questions_list.append({"index": q_idx, "type": category, "question": question_text})
                q_idx += 1

        # Save new session into SQLite DB
        create_interview_session(
            interview_id=session_id,
            analysis_id=analysis_id,
            questions_list=flat_questions_list
        )
        interview_session = get_interview_session(session_id)

    return render_template(
        "interview.html",
        interview=interview_session,
        analysis_id=analysis_id,
        session_id=session_id,
        active_round=active_round,
        company_name=analysis_record.get("company_name", "General"),
    )

@app.route("/interview/<analysis_id>/submit", methods=["POST"])
@rate_limit(max_requests=30, window_seconds=60, scope="interview_eval", is_api=True)
def submit_interview_answer(analysis_id: str):
    """
    Endpoint that evaluates a candidate response dynamically and updates session progress.
    """
    try:
        post_data = request.get_json() or {}
        session_id = (
            post_data.get("session_id")
            or request.args.get("session_id")
            or (f"{analysis_id}_{request.args.get('round', '').lower()}" if request.args.get('round') else None)
            or analysis_id
        )

        session = get_interview_session(session_id)
        if not session and session_id != analysis_id:
            session_id = analysis_id
            session = get_interview_session(session_id)

        if not session:
            return jsonify({"error": "Mock interview session was not found."}), 404

        # Read JSON POST payloads
        candidate_response = post_data.get("answer", "").strip()
        question_index = int(post_data.get("question_index", 0))

        questions_list = session["questions"]
        answers_list = session["answers"]

        if question_index < 0 or question_index >= len(questions_list):
            return jsonify({"error": "The requested question index is out of bounds."}), 400

        target_question = questions_list[question_index]

        # Call InterviewAgent to dynamically evaluate response accuracy, metrics, and suggestions
        evaluation_results = live_interview_agent.evaluate_answer(
            question_type=target_question["type"],
            question=target_question["question"],
            answer=candidate_response
        )

        # Enhance with deep STAR evaluation and non-verbal speech telemetry
        speech_telemetry = post_data.get("speech_metrics") or {}
        star_eval = star_evaluation_agent.evaluate_star(
            question=target_question["question"],
            answer=candidate_response,
            question_type=target_question["type"],
            speech_metrics=speech_telemetry
        )
        evaluation_results["star_evaluation"] = star_eval
        if "star_score" in star_eval:
            evaluation_results["star_score"] = star_eval["star_score"]

        # Structure response details
        response_record = {
            "question_index": question_index,
            "type": target_question["type"],
            "question": target_question["question"],
            "answer": candidate_response,
            "evaluation": evaluation_results
        }

        # Protection against duplicate submission attempts
        is_replaced = False
        for idx, past_ans in enumerate(answers_list):
            if past_ans["question_index"] == question_index:
                answers_list[idx] = response_record
                is_replaced = True
                break
        if not is_replaced:
            answers_list.append(response_record)

        # Calculate progress parameters
        next_question_index = question_index + 1
        is_completed = next_question_index >= len(questions_list)

        # Update session logs in database
        update_interview_session(
            interview_id=session_id,
            current_question_index=next_question_index if not is_completed else question_index,
            answers_list=answers_list,
            completed=is_completed
        )

        result_payload = {
            "evaluation": evaluation_results,
            "next_index": next_question_index,
            "completed": is_completed
        }

        if is_completed:
            result_payload["summary"] = live_interview_agent.calculate_session_summary(answers_list)
        else:
            result_payload["next_question"] = questions_list[next_question_index]

        return jsonify(result_payload)

    except Exception as e:
        logger.error(f"Failed to process answer submission: {str(e)}")
        return jsonify({"error": f"Internal process error evaluating response: {str(e)}"}), 500


@app.route("/api/interview/evaluate-star", methods=["POST"])
@rate_limit(max_requests=30, window_seconds=60, scope="star_eval", is_api=True)
def api_evaluate_star():
    """
    Evaluates candidate interview responses against the STAR behavioral framework
    and analyzes verbal and non-verbal speech telemetry.
    """
    try:
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        question = data.get("question", "").strip()
        answer = data.get("answer", "").strip()
        question_type = data.get("question_type", "Behavioral").strip()
        analysis_id = data.get("analysis_id", "").strip()

        raw_metrics = data.get("speech_metrics")
        if isinstance(raw_metrics, str):
            try:
                speech_metrics = json.loads(raw_metrics)
            except Exception:
                speech_metrics = {}
        elif isinstance(raw_metrics, dict):
            speech_metrics = raw_metrics
        else:
            speech_metrics = {}

        if not answer:
            return jsonify({
                "status": "error",
                "message": "Answer text is required for STAR story evaluation."
            }), 400

        star_results = star_evaluation_agent.evaluate_star(
            question=question,
            answer=answer,
            question_type=question_type,
            speech_metrics=speech_metrics
        )

        return jsonify({
            "status": "success",
            "success": True,
            "star_evaluation": star_results
        }), 200

    except Exception as exc:
        logger.error(f"Error in STAR evaluation endpoint: {exc}")
        return jsonify({
            "status": "error",
            "message": str(exc)
        }), 500


@app.route("/interview/<analysis_id>/reset", methods=["POST"])
def reset_interview_session(analysis_id: str):
    """
    Resets an active mock interview session back to question index 0 with empty answers,
    enabling students to retake and practice rounds anew without losing the parent analysis.
    """
    try:
        raw_round = request.args.get("round", "").lower()
        session_id = f"{analysis_id}_{raw_round}" if raw_round in ["hr", "technical", "behavioral"] else analysis_id

        session = get_interview_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "Interview session not found"}), 404

        update_interview_session(
            interview_id=session_id,
            current_question_index=0,
            answers_list=[],
            completed=False,
        )

        return jsonify({
            "success": True,
            "message": "Interview session reset successfully. You can now retake this round.",
            "session_id": session_id,
        }), 200
    except Exception as exc:
        logger.error(f"Error resetting interview session: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500



@app.route("/api/interview/<analysis_id>/coding-problem", methods=["GET"])
@app.route("/api/interview/coding-problem", methods=["GET"])
def api_interview_coding_problem(analysis_id: str = None):
    """
    Returns a company-targeted DSA coding challenge with starter code and test cases.
    """
    try:
        company_name = request.args.get("company", "").strip()
        if not company_name and analysis_id:
            record = get_analysis(analysis_id)
            if record:
                company_name = record.get("company_name", "General")
        if not company_name:
            company_name = "General"

        difficulty = request.args.get("difficulty", "Medium").strip()
        category = request.args.get("category", None)
        if category:
            category = category.strip()

        problem = coding_assessment_agent.generate_problem(
            company_name=company_name,
            difficulty=difficulty,
            category=category,
        )

        return jsonify({
            "status": "success",
            "problem": problem
        }), 200

    except Exception as e:
        logger.error(f"Error generating coding challenge: {str(e)}")
        fallback = coding_assessment_agent.get_fallback_problem("General", "Medium")
        return jsonify({
            "status": "success",
            "problem": fallback,
            "fallback": True
        }), 200


@app.route("/api/interview/<analysis_id>/evaluate-code", methods=["POST"])
@app.route("/api/interview/evaluate-code", methods=["POST"])
def api_evaluate_code(analysis_id: str = None):
    """
    Evaluates candidate code submission against problem statement and test cases.
    Returns correctness score, test-case pass/fail breakdown, and Big-O complexity.
    """
    try:
        payload = request.get_json() or {}
        code = payload.get("code", "")
        language = payload.get("language", "python")
        problem = payload.get("problem")

        if not isinstance(problem, dict) or not problem:
            company = "General"
            if analysis_id:
                rec = get_analysis(analysis_id)
                if rec:
                    company = rec.get("company_name", "General")
            problem = coding_assessment_agent.get_fallback_problem(company)

        eval_result = coding_assessment_agent.evaluate_code(
            problem=problem,
            language=language,
            code=code
        )

        return jsonify({
            "status": "success",
            "evaluation": eval_result,
            **eval_result
        }), 200

    except Exception as e:
        logger.error(f"Error evaluating candidate code: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Code evaluation failed: {str(e)}"
        }), 500


@app.route("/interview/<analysis_id>/export")
def export_interview_json(analysis_id: str):
    """
    Exports the mock interview Q&A transcript and dynamic evaluations as a JSON download.
    """
    session_id = (
        request.args.get("session_id")
        or (f"{analysis_id}_{request.args.get('round', '').lower()}" if request.args.get('round') else None)
        or analysis_id
    )
    session = get_interview_session(session_id)
    if not session and session_id != analysis_id:
        session_id = analysis_id
        session = get_interview_session(session_id)

    analysis_record = get_analysis(analysis_id)
    if not session:
        flash("Interview session not found.", "error")
        return redirect(url_for("index"))

    company_name = analysis_record.get("company_name", "General") if analysis_record else "General"
    answers_recorded = session.get("answers", [])
    session_summary = live_interview_agent.calculate_session_summary(answers_recorded) if answers_recorded else {}

    export_data = {
        "analysis_id": analysis_id,
        "company_name": company_name,
        "completed": bool(session.get("completed", 0)),
        "total_questions": len(session.get("questions", [])),
        "answers_recorded": len(answers_recorded),
        "session_summary": session_summary,
        "transcript": answers_recorded,
    }

    response = jsonify(export_data)
    safe_company = "".join(c for c in company_name if c.isalnum() or c in ("-", "_"))
    response.headers["Content-Disposition"] = (
        f"attachment; filename=Mock_Interview_{safe_company}_{analysis_id[:8]}.json"
    )
    return response


@app.route("/certificate/<analysis_id>")
def certificate(analysis_id: str):
    """
    Renders printable placement readiness completion certificate.
    """
    analysis_record = get_analysis(analysis_id)
    if not analysis_record:
        flash("The requested placement analysis report could not be found.", "error")
        return redirect(url_for("index"))

    return render_template(
        "certificate.html",
        analysis=analysis_record,
        analysis_id=analysis_id,
    )


@app.route("/certificate/<analysis_id>/download")
def download_certificate_pdf(analysis_id: str):
    """
    Generates and serves a downloadable, formal placement readiness completion certificate in PDF format.
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if not analysis_record:
            flash("Cannot generate certificate. Placement record not found.", "error")
            return redirect(url_for("index"))

        cert_filename = f"Placement_Certificate_{analysis_id}.pdf"
        cert_path = os.path.join(config.REPORT_FOLDER, cert_filename)

        if not os.path.exists(cert_path):
            generate_certificate_pdf(analysis_record, cert_path)

        company_clean = secure_filename(analysis_record.get("company_name", "General"))
        return send_file(
            cert_path,
            as_attachment=True,
            download_name=f"Placement_Certificate_{company_clean}.pdf",
            mimetype="application/pdf",
        )
    except Exception as exc:
        logger.error(f"Error serving certificate PDF for {analysis_id}: {exc}")
        flash("Failed to generate certificate PDF. Please try printing directly.", "error")
        return redirect(url_for("certificate", analysis_id=analysis_id))

@app.route("/download/<analysis_id>")
def download_pdf_report(analysis_id: str):
    """
    Compiles placement metrics into a ReportLab PDF and downloads the file.
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if not analysis_record:
            flash("Cannot generate PDF. Placement record not found.", "error")
            return redirect(url_for("index"))

        # Check if file has already been generated
        pdf_output_name = f"Placement_Report_{analysis_id}.pdf"
        pdf_full_path = os.path.join(config.REPORT_FOLDER, pdf_output_name)

        if not os.path.exists(pdf_full_path):
            generate_pdf_report(
                analysis_data=analysis_record,
                output_path=pdf_full_path
            )

        return send_file(
            pdf_full_path,
            as_attachment=True,
            download_name=f"Placement_Readiness_Report_{analysis_record['company_name']}.pdf",
            mimetype="application/pdf"
        )

    except Exception as e:
        logger.error(f"Failed to generate and serve PDF report: {str(e)}")
        flash(f"Error compiling report file: {str(e)}", "error")
        return redirect(url_for("dashboard", analysis_id=analysis_id))


@app.route("/download/skills-gap/<analysis_id>")
def download_skills_gap_report(analysis_id: str):
    """
    Compiles identified candidate skill gaps and upskilling roadmaps into a standalone PDF report (#136).
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if not analysis_record:
            flash("Cannot generate Skills Gap report. Placement record not found.", "error")
            return redirect(url_for("index"))

        pdf_output_name = f"Skills_Gap_Report_{analysis_id}.pdf"
        pdf_full_path = os.path.join(config.REPORT_FOLDER, pdf_output_name)

        if not os.path.exists(pdf_full_path):
            generate_skills_gap_pdf(
                analysis_data=analysis_record,
                output_path=pdf_full_path
            )

        company = analysis_record.get("company_name", "Target_Company").replace(" ", "_")
        return send_file(
            pdf_full_path,
            as_attachment=True,
            download_name=f"Skills_Gap_Report_{company}.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        logger.error(f"Failed to generate and serve Skills Gap report: {str(e)}")
        flash(f"Error compiling skills gap report: {str(e)}", "error")
        return redirect(url_for("dashboard", analysis_id=analysis_id))


@app.route("/api/generate-outreach/<analysis_id>", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60, scope="generate_outreach", is_api=True)
def api_generate_outreach(analysis_id: str):
    """
    Generates tailored application outreach assets (formal cover letter, LinkedIn InMail,
    cold recruiter email, subject lines, key highlights) for a given analysis ID.
    Supports tone selection ('formal', 'enthusiastic', 'concise') and custom role title.
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if not analysis_record:
            return jsonify({
                "success": False,
                "error": f"Analysis '{analysis_id}' not found."
            }), 404

        payload = request.get_json(silent=True) or {}
        tone = payload.get("tone") or request.form.get("tone", "formal")
        role_title = payload.get("role_title") or request.form.get("role_title", "")

        outreach_data = outreach_agent.generate_outreach(
            analysis_record=analysis_record,
            tone=tone,
            role_title=role_title
        )

        return jsonify({
            "success": True,
            "analysis_id": analysis_id,
            "tone": outreach_data.get("tone", tone),
            "role_title": outreach_data.get("role_title", role_title),
            "outreach": outreach_data
        }), 200

    except Exception as e:
        logger.error(f"Error generating outreach for {analysis_id}: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Failed to generate outreach materials: {str(e)}"
        }), 500


@app.route("/api/outreach/download", methods=["POST"])
@app.route("/api/outreach/<analysis_id>/download", methods=["GET", "POST"])
def api_download_outreach(analysis_id: str = None):
    """
    Downloads generated application outreach assets as formatted .docx or plain .txt files.
    Can accept pre-generated outreach data in POST body or dynamically generate for an analysis_id.
    """
    try:
        payload = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
        aid = analysis_id or payload.get("analysis_id") or request.args.get("analysis_id")
        file_format = (payload.get("format") or request.args.get("format") or "docx").strip().lower()
        tone = (payload.get("tone") or request.args.get("tone") or "formal").strip().lower()
        role_title = payload.get("role_title") or request.args.get("role_title", "")
        candidate_name = payload.get("candidate_name") or request.args.get("candidate_name", "Candidate")
        content_type = (payload.get("content_type") or request.args.get("type") or "all").strip().lower()
        custom_content = payload.get("content")

        analysis_record = get_analysis(aid) if aid else None
        company_name = (
            payload.get("company_name")
            or (analysis_record.get("company_name") if analysis_record else None)
            or request.args.get("company_name")
            or "Target_Company"
        )

        # If client passed complete outreach_data, use it; otherwise generate
        outreach_data = payload.get("outreach") or payload.get("outreach_data")
        if not outreach_data and analysis_record:
            outreach_data = outreach_agent.generate_outreach(
                analysis_record=analysis_record,
                tone=tone,
                role_title=role_title
            )
        elif not outreach_data and not analysis_record:
            if not custom_content:
                return jsonify({"success": False, "error": "Analysis record or outreach content required."}), 404
            outreach_data = {
                "tone": tone,
                "role_title": role_title or "Software Engineer",
                "company_name": company_name,
                "cover_letter": custom_content,
                "linkedin_inmail": custom_content,
                "cold_email": custom_content,
                "subject_lines": [],
                "key_highlights": []
            }

        safe_company = "".join(c for c in company_name if c.isalnum() or c in ("-", "_")) or "Company"
        safe_name = "".join(c for c in candidate_name if c.isalnum() or c in ("-", "_")) or "Candidate"

        if file_format == "txt":
            if custom_content and content_type != "all":
                txt_data = custom_content
                filename = f"{safe_name}_{safe_company}_{content_type}.txt"
            else:
                txt_data = outreach_agent.export_to_txt(
                    outreach_data=outreach_data,
                    candidate_name=safe_name,
                    company_name=safe_company
                )
                filename = f"{safe_name}_{safe_company}_Outreach_Portfolio.txt"

            return Response(
                txt_data,
                mimetype="text/plain; charset=utf-8",
                headers={
                    "Content-Disposition": f"attachment; filename=\"{filename}\""
                }
            )

        # Default to DOCX
        docx_bytes = outreach_agent.export_to_docx(
            outreach_data=outreach_data,
            candidate_name=safe_name,
            company_name=safe_company
        )
        filename = f"{safe_name}_{safe_company}_Outreach_Portfolio.docx"
        return send_file(
            io.BytesIO(docx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Error serving outreach download: {str(e)}")
        return jsonify({"success": False, "error": f"Failed to download outreach: {str(e)}"}), 500

@app.route("/api/analyses/recent", methods=["GET"])
def api_recent_analyses():
    """
    Returns a list of recent placement analyses ordered by timestamp descending.
    """
    try:
        limit = request.args.get("limit", default=10, type=int)
        analyses = get_recent_analyses(limit=limit)
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

@app.route("/api/analysis/<analysis_id>", methods=["DELETE"])
def api_delete_analysis(analysis_id: str):
    """
    Deletes an analysis session and related mock interview records.
    """
    try:
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

@app.route("/api/progress/<student_id>", methods=["GET"])
def api_student_progress(student_id: str):
    """
    Returns a candidate's full longitudinal version history and trend metrics.
    """
    try:
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


@app.route("/api/progress/diff/<id_a>/<id_b>", methods=["GET"])
def api_progress_diff(id_a: str, id_b: str):
    """
    Generates side-by-side comparison metrics between any two specific resume analyses.
    """
    try:
        rec_a = get_analysis(id_a)
        rec_b = get_analysis(id_b)
        if not rec_a or not rec_b:
            return jsonify({"status": "error", "message": "One or both analysis records not found."}), 404

        diff = generate_side_by_side_diff(rec_a, rec_b)
        return jsonify({
            "status": "success",
            "diff": diff
        }), 200
    except Exception as e:
        logger.error(f"Error computing diff between {id_a} and {id_b}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/compare-resumes", methods=["GET", "POST"])
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

    except Exception as exc:
        logger.error(f"Error comparing resume versions: {exc}")
        err_msg = f"Failed to compare resumes: {str(exc)}"
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": err_msg}), 500
        flash(err_msg, "error")
        return redirect(url_for("compare_resumes_view"))


@app.route("/api/jobs/recommendations/<analysis_id>", methods=["GET"])
def api_job_recommendations(analysis_id: str):
    """
    Retrieves and returns tailored campus recruitment drive recommendations for a candidate,
    scored against verified technical skill overlap, ATS readiness, and hiring requirements.
    Supports query parameters: location, role_type, min_match, search, and force_refresh.
    """
    try:
        analysis_record = get_analysis(analysis_id)
        if not analysis_record:
            return jsonify({
                "success": False,
                "error": f"Candidate analysis record '{analysis_id}' not found."
            }), 404

        location = request.args.get("location", "all").strip()
        role_type = request.args.get("role_type", "all").strip()
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

        feed_service = JobFeedService()
        active_jobs = feed_service.get_all_jobs(force_refresh=force_refresh)

        matcher_agent = JobRecommendationAgent()
        recommendations = matcher_agent.recommend_jobs(
            analysis_record=analysis_record,
            jobs=active_jobs,
            location=location,
            role_type=role_type,
            min_match=min_match,
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
                "min_match": min_match,
                "search": search_query
            },
            "jobs": recommendations
        }), 200

    except Exception as e:
        logger.error(f"Error computing job recommendations for {analysis_id}: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Failed to compute job recommendations: {str(e)}"
        }), 500


@app.route("/api/admin/database/integrity", methods=["GET"])
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


@app.route("/api/admin/database/backup", methods=["POST"])
def api_database_backup():
    """
    Triggers an atomic online hot backup of the SQLite database and returns metadata.
    """
    try:
        backup_filename = f"placement_backup_{int(time.time())}.db"
        backup_path = os.path.join(config.BASE_DIR, backup_filename)
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


@app.route("/api/admin/retention/run", methods=["POST"])
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


@app.route("/github-analyzer", methods=["GET", "POST"])
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
@app.route("/internships", methods=["GET", "POST"])
def internships_view():
    """
    Interactive student internship recommendation engine (#142).
    Matches candidate skills with real internship listings and explains match rationales.
    """
    skills_raw = request.args.get("skills") or request.form.get("skills", "")
    role_filter = request.args.get("role") or request.form.get("role", "All")
    location_filter = request.args.get("location") or request.form.get("location", "All")
    search_query = request.args.get("search") or request.form.get("search", "")

    candidate_skills = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else [
        "Python", "SQL", "Git", "Data Structures", "Java"
    ]

    recommendations_data = internship_agent.get_recommendations(
        candidate_skills=candidate_skills,
        preferred_role=None if role_filter == "All" else role_filter,
        location=None if location_filter == "All" else location_filter,
        search_query=search_query
    )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(recommendations_data), 200

    return render_template(
        "internships.html",
        data=recommendations_data,
        skills_str=",".join(candidate_skills),
        current_role=role_filter,
        current_location=location_filter,
        search_query=search_query
    )
@app.route("/applications", methods=["GET", "POST"])
def job_applications_tracker():
    """
    Candidate Job Application Tracker view and management handler (#143).
    Enforces per-student data segregation and allows creating, updating, and filtering applications.
    """
    student_id = request.args.get("student_id") or request.form.get("student_id", "default_student")
    status_filter = request.args.get("status", "All")

    if request.method == "POST":
        company = request.form.get("company_name", "").strip()
        role = request.form.get("role_title", "").strip()
        status = request.form.get("status", "Applied").strip()
        app_date = request.form.get("application_date", "").strip()
        job_url = request.form.get("job_url", "").strip()
        notes = request.form.get("notes", "").strip()

        if not company or not role:
            flash("Company name and job role are required.", "error")
            return redirect(url_for("job_applications_tracker", student_id=student_id))

        app_id = f"app-{uuid.uuid4().hex[:12]}"
        save_job_application(
            app_id=app_id,
            student_id=student_id,
            company_name=company,
            role_title=role,
            status=status,
            application_date=app_date or None,
            job_url=job_url,
            notes=notes
        )
        flash("Application record saved successfully!", "success")
        return redirect(url_for("job_applications_tracker", student_id=student_id))

    applications = get_job_applications(student_id=student_id, status_filter=status_filter)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "student_id": student_id,
            "count": len(applications),
            "applications": applications
        }), 200

    return render_template(
        "applications.html",
        applications=applications,
        student_id=student_id,
        current_status=status_filter
    )


@app.route("/api/applications/<app_id>/status", methods=["POST"])
def api_update_application_status(app_id: str):
    """
    Updates the stage of an application (Applied, Assessment, Interview, Selected, Rejected).
    """
    payload = request.get_json(silent=True) or {}
    new_status = payload.get("status") or request.form.get("status", "")
    student_id = payload.get("student_id") or request.form.get("student_id", "default_student")

    if not new_status:
        return jsonify({"success": False, "error": "New status is required."}), 400

    updated = update_job_application_status(app_id=app_id, student_id=student_id, new_status=new_status)
    return jsonify({"success": updated}), (200 if updated else 404)


@app.route("/api/applications/<app_id>", methods=["DELETE"])
def api_delete_application(app_id: str):
    """
    Deletes an application record owned by student_id.
    """
    student_id = request.args.get("student_id") or "default_student"
    deleted = delete_job_application(app_id=app_id, student_id=student_id)
    return jsonify({"success": deleted}), (200 if deleted else 404)
@app.route("/interview-history")
def interview_history_view():
    """
    Mock Interview Performance History view (#144).
    Displays historical mock interview attempts, scores by question type, and score progression trends.
    """
    student_id = request.args.get("student_id", "default_student").strip()
    analytics = get_student_interview_performance_analytics(student_id=student_id)
    return render_template("interview_history.html", analytics=analytics, student_id=student_id)


@app.route("/api/interview-history")
def api_interview_history():
    """
    API endpoint returning historical mock interview attempts and analytics (#144).
    """
    student_id = request.args.get("student_id", "default_student").strip()
    analytics = get_student_interview_performance_analytics(student_id=student_id)
    return jsonify({
        "success": True,
        "student_id": student_id,
        "analytics": analytics
    })

@app.route("/linkedin-analyzer", methods=["GET", "POST"])
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
@app.route("/coding-practice", methods=["GET", "POST"])
def coding_practice_view():
    """
    Personalized Coding Practice Recommendation View and Endpoint (#147).
    Recommends targeted LeetCode/DSA problems based on detected skill gaps, target companies, and difficulty.
    """
    target_company = request.args.get("company", "").strip()
    difficulty = request.args.get("difficulty", "All").strip()
    raw_skills = request.args.get("skills", "").strip()

    if request.method == "POST":
        target_company = request.form.get("company", "").strip()
        difficulty = request.form.get("difficulty", "All").strip()
        raw_skills = request.form.get("skills", "").strip()

    skill_gaps = [s.strip() for s in re.split(r"[,;\n]+", raw_skills) if s.strip()]

    recommendations_data = coding_practice_agent.recommend_problems(
        skill_gaps=skill_gaps,
        target_company=target_company,
        difficulty_preference=difficulty,
        limit=8
    )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "data": recommendations_data
        }), 200

    return render_template(
        "coding_practice.html",
        data=recommendations_data,
        target_company=target_company,
        difficulty=difficulty,
        skills_input=raw_skills
    )


@app.route("/api/coding-practice/recommendations")
def api_coding_practice_recommendations():
    """
    API endpoint returning tailored DSA practice problems for candidate placement prep (#147).
    """
    target_company = request.args.get("company", "").strip()
    difficulty = request.args.get("difficulty", "All").strip()
    raw_skills = request.args.get("skills", "").strip()
    skill_gaps = [s.strip() for s in re.split(r"[,;\n]+", raw_skills) if s.strip()]

    recommendations_data = coding_practice_agent.recommend_problems(
        skill_gaps=skill_gaps,
        target_company=target_company,
        difficulty_preference=difficulty,
        limit=10
    )
    return jsonify({
        "success": True,
        "data": recommendations_data
    }), 200


@app.route("/my-reports")
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


@app.route("/api/user/analyses")
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


if __name__ == "__main__":
    # Boot Flask server locally on default port 5000
    app.run(debug=True, port=5000)
