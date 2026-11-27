import sqlite3
import json
import logging
import uuid
import hashlib
import secrets
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union
from db.connection import create_connection, get_connection, transaction
import config
from config import Config

logger = logging.getLogger(__name__)

def get_db_connection() -> sqlite3.Connection:
    """
    Establishes and returns a connection to the SQLite database.
    Sets row_factory to sqlite3.Row for dictionary-like access.
    Configures WAL mode and busy timeout to avoid database lock errors.
    Instruments query execution timing safely (#task-performance-monitoring).
    """
    db_path = getattr(config, "DATABASE_PATH", None) or getattr(Config, "DATABASE_PATH", "placement_agent.db")
    try:
        from utils.performance_monitor import MonitoredConnection
        conn = sqlite3.connect(db_path, timeout=10.0, factory=MonitoredConnection)
    except Exception:
        conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

def get_analysis_by_id(analysis_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if not row:
            return None

        record = dict(row)
        raw_result = record.get("result_json")
        if isinstance(raw_result, str) and raw_result.strip():
            try:
                record["result"] = json.loads(raw_result)
            except json.JSONDecodeError:
                record["result"] = {}
        elif isinstance(raw_result, dict):
            record["result"] = raw_result
        else:
            record["result"] = {}
        return record
    finally:
        conn.close()


def update_analysis_section(
    analysis_id: str,
    section_db_key: str,
    section_payload: Any,
    section_key: str,
) -> Dict[str, Any]:
    """
    Atomically updates ONLY `section_db_key` inside the stored analysis JSON payload,
    clears the failure marker for `section_key`, and preserves all other sections untouched.
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT result_json FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Analysis record '{analysis_id}' not found.")

        existing_json = row["result_json"]
        if isinstance(existing_json, str) and existing_json.strip():
            payload = json.loads(existing_json)
        elif isinstance(existing_json, dict):
            payload = dict(existing_json)
        else:
            payload = {}

        # Preserve all existing keys; replace ONLY the retried section
        payload[section_db_key] = section_payload

        # Clear failure tracking metadata for the recovered section
        failed_sections = payload.get("failed_sections")
        if isinstance(failed_sections, dict):
            failed_sections.pop(section_key, None)
            failed_sections.pop(section_db_key, None)
            payload["failed_sections"] = failed_sections
        elif isinstance(failed_sections, list):
            payload["failed_sections"] = [
                s for s in failed_sections if s not in (section_key, section_db_key)
            ]

        section_status = payload.get("section_status")
        if isinstance(section_status, dict):
            section_status[section_key] = "completed"
            payload["section_status"] = section_status

        payload["last_section_retry"] = {
            "section": section_key,
            "retried_at": datetime.now(timezone.utc).isoformat(),
        }

        conn.execute(
            "UPDATE analyses SET result_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), analysis_id),
        )
        conn.commit()
        return payload
    finally:
        conn.close()    

def init_db() -> None:
    """
    Initializes the database tables (analyses, interviews, batch_comparisons) if they do not exist.
    Performs migrations to ensure student_id and version_number columns are present.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Table 1: Store placement analysis findings with candidate profile & version indexing
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY,
                resume_filename TEXT NOT NULL,
                company_name TEXT NOT NULL,
                job_description TEXT,
                results_json TEXT NOT NULL,
                student_id TEXT DEFAULT 'default_student',
                version_number INTEGER DEFAULT 1,
                target_role TEXT DEFAULT 'Software Engineer',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table 2: Store mock interview progress & evaluations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interviews (
                id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                current_question_index INTEGER NOT NULL DEFAULT 0,
                questions_json TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (analysis_id) REFERENCES analyses (id)
            )
        """)

        # Table 3: Store multi-company batch comparisons & offer likelihood matrices
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_comparisons (
                id TEXT PRIMARY KEY,
                student_id TEXT DEFAULT 'default_student',
                resume_filename TEXT NOT NULL,
                companies_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 4: Store job application tracking records (#143, #193, #298)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_applications (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                company_name TEXT NOT NULL,
                role_title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Applied',
                application_date TEXT NOT NULL,
                job_url TEXT,
                job_description TEXT,
                notes TEXT,
                location TEXT DEFAULT 'Remote',
                job_type TEXT DEFAULT 'Full-Time',
                experience_level TEXT DEFAULT 'Entry-Level',
                deadline_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Table 5: Store registered user accounts
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                leaderboard_opt_in INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Table 5b: Server-side registry for revocable authenticated sessions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                client_label TEXT NOT NULL DEFAULT 'Web browser',
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                revoked_at REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions (user_id, last_seen_at)")

                # Table 6: Store user email notification preferences (#186, #298)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_notification_preferences (
                student_id TEXT PRIMARY KEY,
                email TEXT,
                output_language TEXT NOT NULL DEFAULT 'en',
                email_enabled INTEGER NOT NULL DEFAULT 1,
                notify_welcome INTEGER NOT NULL DEFAULT 1,
                notify_application_created INTEGER NOT NULL DEFAULT 1,
                notify_status_change INTEGER NOT NULL DEFAULT 1,
                notify_interview_reminder INTEGER NOT NULL DEFAULT 1,
                notify_deadline_reminder INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 7: Store company interview experiences (#189)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interview_experiences (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                candidate_name TEXT NOT NULL DEFAULT 'Anonymous Candidate',
                company_name TEXT NOT NULL,
                role_title TEXT NOT NULL,
                interview_date TEXT NOT NULL,
                rounds_json TEXT NOT NULL,
                overall_verdict TEXT NOT NULL DEFAULT 'Offer',
                difficulty_level TEXT NOT NULL DEFAULT 'Medium',
                tips_and_preparation TEXT,
                helpful_count INTEGER NOT NULL DEFAULT 0,
                reported_flag INTEGER NOT NULL DEFAULT 0,
                report_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table for interview experience helpful votes (#693)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interview_experience_helpful_votes (
                id TEXT PRIMARY KEY,
                experience_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(experience_id, student_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_helpful_votes_exp ON interview_experience_helpful_votes (experience_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_helpful_votes_student ON interview_experience_helpful_votes (student_id)")

        # Table 8: Store student placement preparation calendar events (#190)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preparation_calendar_events (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                title TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'preparation',
                event_date TEXT NOT NULL,
                event_time TEXT,
                company_name TEXT,
                description TEXT,
                priority TEXT NOT NULL DEFAULT 'Medium',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 9: Store company-wise preparation checklist progress (#192)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_preparation_checklists (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                company_name TEXT NOT NULL,
                checklist_json TEXT NOT NULL,
                completion_percentage REAL NOT NULL DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 9b: Store drive day practical readiness checklist progress (#651)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drive_day_checklists (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                application_id TEXT DEFAULT '',
                drive_mode TEXT NOT NULL DEFAULT 'offline',
                checklist_json TEXT NOT NULL,
                total_items INTEGER NOT NULL DEFAULT 0,
                completed_items INTEGER NOT NULL DEFAULT 0,
                completion_percentage REAL NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, application_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drive_day_checklists_student ON drive_day_checklists(student_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drive_day_checklists_app ON drive_day_checklists(application_id)")

        # Table 10: Store coding assessment submission history (#194)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coding_submissions (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                analysis_id TEXT,
                question_title TEXT NOT NULL,
                company_name TEXT NOT NULL DEFAULT 'General',
                language TEXT NOT NULL DEFAULT 'python',
                code TEXT NOT NULL,
                passed INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                tests_passed INTEGER NOT NULL DEFAULT 0,
                total_tests INTEGER NOT NULL DEFAULT 0,
                test_results_json TEXT,
                complexity_analysis TEXT,
                feedback TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 11: Store student profiles (#199)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_profiles (
                student_id TEXT PRIMARY KEY,
                full_name TEXT,
                email TEXT,
                phone TEXT,
                department TEXT,
                grad_year INTEGER,
                cgpa REAL,
                target_role TEXT,
                target_company TEXT,
                skills_json TEXT,
                linkedin_url TEXT,
                github_url TEXT,
                has_resume_uploaded INTEGER DEFAULT 0,
                college TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 11b: Store candidate career preferences & aspirations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS career_preferences (
                student_id TEXT PRIMARY KEY,
                target_roles TEXT,
                target_companies TEXT,
                preferred_locations TEXT,
                preferred_work_modes TEXT,
                preferred_role_types TEXT,
                expected_salary_min REAL DEFAULT 0,
                preferred_industries TEXT,
                primary_skills TEXT,
                notes TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 12: Store bookmarked coding practice questions (#234)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookmarked_coding_questions (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                problem_id TEXT NOT NULL,
                problem_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, problem_id)
            )
        """)

        # Table 12b: Store bookmarked interview practice questions (#358, #359)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookmarked_interview_questions (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                question_id TEXT NOT NULL,
                question_text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Technical',
                company_name TEXT NOT NULL DEFAULT 'General',
                notes TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                is_practiced INTEGER NOT NULL DEFAULT 0,
                last_practiced_at TIMESTAMP,
                practice_answer TEXT DEFAULT '',
                practice_evaluation_json TEXT DEFAULT '{}',
                last_score REAL,
                good_score_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, question_id)
            )
        """)
        # Ensure #359 practice mode columns and #846 score tracking exist if table was previously created (#359, #846)
        cursor.execute("PRAGMA table_info(bookmarked_interview_questions)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "is_practiced" not in existing_cols:
            cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN is_practiced INTEGER NOT NULL DEFAULT 0")
        if "last_practiced_at" not in existing_cols:
            cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN last_practiced_at TIMESTAMP")
        if "practice_answer" not in existing_cols:
            cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN practice_answer TEXT DEFAULT ''")
        if "practice_evaluation_json" not in existing_cols:
            cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN practice_evaluation_json TEXT DEFAULT '{}'")
        if "last_score" not in existing_cols:
            cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN last_score REAL")
        if "good_score_count" not in existing_cols:
            cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN good_score_count INTEGER NOT NULL DEFAULT 0")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interview_bookmarks_student ON bookmarked_interview_questions (student_id, category, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interview_bookmarks_practiced ON bookmarked_interview_questions (student_id, is_practiced, created_at DESC)")

        # Table 13: Store personal interview preparation notes (#235)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interview_preparation_notes (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Technical',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_notes_student_created ON interview_preparation_notes (student_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_notes_student_category ON interview_preparation_notes (student_id, category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_notes_student_updated ON interview_preparation_notes (student_id, updated_at DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_notes_student_cat_updated ON interview_preparation_notes (student_id, category, updated_at DESC)")

        # Table 13a: Store private annotations attached to an individual analysis
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_annotations (
                id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                annotation_type TEXT NOT NULL DEFAULT 'note',
                section TEXT NOT NULL DEFAULT 'General',
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK(annotation_type IN ('note', 'highlight'))
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_annotations_owner ON analysis_annotations (student_id, analysis_id, updated_at DESC)")

        # Table 13b: Store private saved job descriptions for reuse
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_job_descriptions (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                company_name TEXT NOT NULL,
                role_title TEXT NOT NULL,
                source TEXT NOT NULL,
                description TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jd_student_updated ON saved_job_descriptions (student_id, updated_at DESC)")

        # Table 13c: Store student weak topic statuses (#559)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_weak_topic_statuses (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                topic_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, topic_key)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_student_weak_topic_statuses ON student_weak_topic_statuses (student_id, topic_key)")

        # Table 14: Store anonymized cohort benchmarks for peer ranking and leaderboard
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cohort_benchmarks (
                id TEXT PRIMARY KEY,
                analysis_id TEXT,
                student_handle TEXT NOT NULL,
                department TEXT NOT NULL DEFAULT 'Computer Science',
                grad_year INTEGER NOT NULL DEFAULT 2026,
                ats_score INTEGER NOT NULL DEFAULT 70,
                job_match_score INTEGER NOT NULL DEFAULT 65,
                overall_score REAL NOT NULL DEFAULT 67.5,
                target_company TEXT NOT NULL DEFAULT 'General',
                skills_count INTEGER NOT NULL DEFAULT 0,
                tier TEXT NOT NULL DEFAULT 'Silver',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 15: Store technical skills self-assessments
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS technical_self_assessments (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                category TEXT NOT NULL,
                proficiency_level TEXT NOT NULL,
                years_experience REAL DEFAULT 0,
                notes TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, skill_name)
            )
        """)

        # Table 16: Store student and user feedback
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
                category TEXT NOT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table: Store Gemini API Usage & Cost Monitoring Logs (#215)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gemini_api_logs (
                id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                candidate_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0,
                latency_ms INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'success',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_api_logs_created ON gemini_api_logs (created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_api_logs_status ON gemini_api_logs (status)")

        # Table: Store AI Call Telemetry & Metrics (#434)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_call_metrics (
                id TEXT PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                feature TEXT NOT NULL DEFAULT 'analysis',
                model TEXT NOT NULL,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                token_count INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                candidate_tokens INTEGER DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 1,
                error_type TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_metrics_timestamp ON ai_call_metrics (timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_metrics_feature ON ai_call_metrics (feature)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_metrics_success ON ai_call_metrics (success)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_metrics_error ON ai_call_metrics (error_type)")

        
        # Schema migration: check if student_id, version_number and target_role exist in analyses
        cursor.execute("PRAGMA table_info(analyses)")
        existing_cols = [col[1] for col in cursor.fetchall()]
        if "student_id" not in existing_cols:
            cursor.execute("ALTER TABLE analyses ADD COLUMN student_id TEXT DEFAULT 'default_student'")
        if "version_number" not in existing_cols:
            cursor.execute("ALTER TABLE analyses ADD COLUMN version_number INTEGER DEFAULT 1")
        if "target_role" not in existing_cols:
            cursor.execute("ALTER TABLE analyses ADD COLUMN target_role TEXT DEFAULT 'Software Engineer'")
        if "stored_resume_id" not in existing_cols:
            cursor.execute("ALTER TABLE analyses ADD COLUMN stored_resume_id TEXT")
        if "stored_resume_label" not in existing_cols:
            cursor.execute("ALTER TABLE analyses ADD COLUMN stored_resume_label TEXT")

        # Schema migration for job_applications table (#193, #298)
        cursor.execute("PRAGMA table_info(job_applications)")
        app_cols = [col[1] for col in cursor.fetchall()]
        if "location" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN location TEXT DEFAULT 'Remote'")
        if "job_type" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN job_type TEXT DEFAULT 'Full-Time'")
        if "experience_level" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN experience_level TEXT DEFAULT 'Entry-Level'")
        if "resume_analysis_id" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN resume_analysis_id TEXT")
        if "resume_filename" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN resume_filename TEXT")
        if "resume_version" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN resume_version INTEGER")
        if "job_description_snapshot" not in app_cols:
            cursor.execute("ALTER TABLE job_applications ADD COLUMN job_description_snapshot TEXT")

        # Schema migration for users table (#206, #360, #380)
        cursor.execute("PRAGMA table_info(users)")
        user_cols = [col[1] for col in cursor.fetchall()]
        if "is_verified" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0")
        if "verification_token" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN verification_token TEXT")
        if "token_created_at" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN token_created_at TIMESTAMP")
        if "leaderboard_opt_in" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN leaderboard_opt_in INTEGER NOT NULL DEFAULT 1")
        if "two_factor_enabled" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN two_factor_enabled INTEGER NOT NULL DEFAULT 0")
        if "two_factor_secret" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN two_factor_secret TEXT")
        if "two_factor_backup_codes" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN two_factor_backup_codes TEXT")
        if "is_active" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "role" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")

        # Ensure admin user audit logs table exists (#853)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_user_audit_logs (
                id TEXT PRIMARY KEY,
                admin_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                target_email TEXT,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_user_audit_target ON admin_user_audit_logs(target_user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_user_audit_action ON admin_user_audit_logs(action)"
        )

        # Schema migration for generated-output language preferences
        cursor.execute("PRAGMA table_info(email_notification_preferences)")
        email_pref_cols = [col[1] for col in cursor.fetchall()]
        if "output_language" not in email_pref_cols:
            cursor.execute(
                "ALTER TABLE email_notification_preferences ADD COLUMN output_language TEXT NOT NULL DEFAULT 'en'"
            )

        # Schema migration for student_profiles table (#654)
        cursor.execute("PRAGMA table_info(student_profiles)")
        student_prof_cols = [col[1] for col in cursor.fetchall()]
        if "college" not in student_prof_cols:
            cursor.execute("ALTER TABLE student_profiles ADD COLUMN college TEXT")

        # Feature flags tables schema (#854)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_flags (
                key TEXT PRIMARY KEY,
                description TEXT,
                is_enabled INTEGER NOT NULL DEFAULT 0,
                rollout_percentage INTEGER NOT NULL DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_flag_audit_logs (
                id TEXT PRIMARY KEY,
                flag_key TEXT NOT NULL,
                admin_id TEXT NOT NULL,
                action TEXT NOT NULL,
                previous_state TEXT,
                new_state TEXT,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ff_audit_flag ON feature_flag_audit_logs(flag_key)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ff_audit_created ON feature_flag_audit_logs(created_at DESC)"
        )

        # Indexes for query, sorting, and version lookup performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses (created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_student_version ON analyses (student_id, version_number ASC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_student_created ON analyses (student_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_student_company ON analyses (student_id, company_name, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interviews_analysis_id ON interviews (analysis_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interviews_analysis_completed ON interviews (analysis_id, completed)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_batch_comparisons_created ON batch_comparisons (created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_applications_student ON job_applications (student_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_apps_student_status ON job_applications (student_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_apps_student_appdate ON job_applications (student_id, application_date DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_apps_company ON job_applications (student_id, company_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_apps_resume_analysis ON job_applications (resume_analysis_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cohort_overall ON cohort_benchmarks (overall_score DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cohort_dept_year ON cohort_benchmarks (department, grad_year)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cohort_analysis_id ON cohort_benchmarks (analysis_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cohort_dept_score ON cohort_benchmarks (department, overall_score DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_company ON interview_experiences (company_name, interview_date DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_reported_date ON interview_experiences (reported_flag, interview_date DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_student ON interview_experiences (student_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calendar_student_date ON preparation_calendar_events (student_id, event_date ASC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calendar_student_completed ON preparation_calendar_events (student_id, completed, event_date ASC)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_checklist_student_comp ON company_preparation_checklists (student_id, company_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_coding_subs_student ON coding_submissions (student_id, submitted_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_coding_subs_analysis ON coding_submissions (analysis_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tech_assessments_student ON technical_self_assessments (student_id, category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_feedback_student ON user_feedback (student_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jd_student_company ON saved_job_descriptions (student_id, company_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_active ON auth_sessions (user_id, revoked_at, last_seen_at DESC)")

        # Seed initial cohort peers if table is currently empty
        
        # Recently viewed jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recently_viewed_jobs (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                job_title TEXT DEFAULT '',
                company TEXT DEFAULT '',
                company_name TEXT DEFAULT '',
                role_title TEXT DEFAULT '',
                location TEXT,
                salary TEXT,
                salary_range TEXT,
                apply_url TEXT,
                experience_level TEXT,
                match_score INTEGER DEFAULT 0,
                source_url TEXT,
                viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, job_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_viewed_jobs_student ON recently_viewed_jobs (student_id, viewed_at DESC)")
        cursor.execute("PRAGMA table_info(recently_viewed_jobs)")
        rvj_cols = {col[1] for col in cursor.fetchall()}
        for col_name in ["company_name", "role_title", "salary_range", "apply_url"]:
            if col_name not in rvj_cols:
                try:
                    cursor.execute(f"ALTER TABLE recently_viewed_jobs ADD COLUMN {col_name} TEXT")
                except Exception:
                    pass

        # Email verification tokens table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                used_at INTEGER DEFAULT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verif_tokens ON email_verification_tokens (token_hash)")

        # Password reset tokens table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                used_at INTEGER DEFAULT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pwd_reset_tokens ON password_reset_tokens (token_hash)")

        # Daily preparation activity log table (#236)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preparation_activity_log (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, activity_date, activity_type)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_act_student_date ON preparation_activity_log (student_id, activity_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_act_student_date_desc ON preparation_activity_log (student_id, activity_date DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_act_student_created ON preparation_activity_log (student_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prep_act_activity_type ON preparation_activity_log (student_id, activity_type)")

        # Student daily preparation streaks table (#236)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_streaks (
                student_id TEXT PRIMARY KEY,
                current_streak INTEGER NOT NULL DEFAULT 0,
                longest_streak INTEGER NOT NULL DEFAULT 0,
                last_activity_date TEXT,
                total_active_days INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Analysis section feedback table (thumbs up / thumbs down + comments) (#302)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_section_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                section TEXT NOT NULL,
                rating TEXT NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(analysis_id, user_id, section)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_section_feedback_analysis ON analysis_section_feedback (analysis_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_section_feedback_section ON analysis_section_feedback (section)")

        # Table 17: Store input-hash cache for placement analysis results (#296)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_cache (
                input_hash TEXT NOT NULL,
                student_id TEXT NOT NULL DEFAULT 'default_student',
                analysis_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (input_hash, student_id),
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_cache_hash ON analysis_cache(input_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_cache_expires ON analysis_cache(expires_at)")

        # Table 18: Store timed aptitude practice attempts & topic scores (#426)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS aptitude_attempts (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                test_category TEXT NOT NULL,
                total_questions INTEGER NOT NULL,
                correct_count INTEGER NOT NULL,
                score_percentage REAL NOT NULL,
                time_taken_seconds INTEGER NOT NULL,
                time_limit_seconds INTEGER NOT NULL,
                timed_out INTEGER NOT NULL DEFAULT 0,
                topic_accuracy_json TEXT NOT NULL,
                weak_topics_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_aptitude_student ON aptitude_attempts (student_id, created_at DESC)")

        # Table 19: Store group discussion practice attempts & AI evaluations (#427)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gd_practice_attempts (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                topic_id TEXT,
                topic_title TEXT NOT NULL,
                category TEXT NOT NULL,
                contribution_type TEXT NOT NULL,
                contribution_text TEXT NOT NULL,
                word_count INTEGER DEFAULT 0,
                overall_score INTEGER DEFAULT 0,
                content_score INTEGER DEFAULT 0,
                structure_score INTEGER DEFAULT 0,
                balance_score INTEGER DEFAULT 0,
                clarity_score INTEGER DEFAULT 0,
                stronger_opening TEXT,
                missed_counterpoint TEXT,
                feedback_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gd_attempts_student ON gd_practice_attempts (student_id, created_at DESC)")

        # Table 35: Store placement cell batch resume analysis records (#763)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS placement_cell_batches (
                id TEXT PRIMARY KEY,
                batch_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                consent_confirmed INTEGER NOT NULL DEFAULT 1,
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                average_score REAL DEFAULT 0.0,
                score_distribution TEXT,
                common_missing_skills TEXT,
                failed_files TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pcb_created_at ON placement_cell_batches (created_at DESC)")

        # Table 36: Store placement coordinator intervention queue records (#786)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faculty_interventions (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                student_name TEXT,
                student_email TEXT,
                department TEXT NOT NULL DEFAULT 'Computer Science',
                graduation_year INTEGER NOT NULL DEFAULT 2026,
                category TEXT NOT NULL,
                urgency_level TEXT NOT NULL DEFAULT 'medium',
                explanation TEXT NOT NULL,
                explanation_detail TEXT,
                suggested_action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                snoozed_until TEXT,
                action_notes TEXT,
                resolved_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fac_int_status ON faculty_interventions(status, urgency_level)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fac_int_student ON faculty_interventions(student_id, category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fac_int_category ON faculty_interventions(category)")

        # Table: Deprecated API usage metrics tracking (#445, #553)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deprecated_api_metrics (
                id TEXT PRIMARY KEY,
                item_type TEXT NOT NULL,
                item_name TEXT NOT NULL,
                replacement TEXT,
                endpoint TEXT NOT NULL,
                http_method TEXT NOT NULL DEFAULT 'GET',
                user_id TEXT DEFAULT 'anonymous',
                client_ip TEXT,
                user_agent TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_deprecated_metrics_item ON deprecated_api_metrics (item_type, item_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_deprecated_metrics_ts ON deprecated_api_metrics (timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_deprecated_metrics_endpoint ON deprecated_api_metrics (endpoint)")

        # Table: Track failed login attempts per account and IP (#851)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                attempted_at REAL NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_failed_login_ident_time ON failed_login_attempts (identifier, attempted_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_failed_login_ip_time ON failed_login_attempts (ip_address, attempted_at)")

        # Table: Account temporary lockouts (#851)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_lockouts (
                identifier TEXT PRIMARY KEY,
                locked_until REAL NOT NULL,
                created_at REAL NOT NULL,
                notified_at REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_lockouts_until ON account_lockouts (locked_until)")

        # Table: Placement readiness activity timeline (#timeline)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS placement_activity_timeline (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                event_date TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata_json TEXT DEFAULT '{}',
                is_milestone INTEGER DEFAULT 0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_placement_timeline_student ON placement_activity_timeline (student_id, event_date DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_placement_timeline_category ON placement_activity_timeline (student_id, category, event_date DESC)")

        cursor.execute("PRAGMA table_info(users)")
        user_cols = [c[1] for c in cursor.fetchall()]
        if "is_verified" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0")

        cursor.execute("SELECT COUNT(*) FROM cohort_benchmarks")
        if cursor.fetchone()[0] == 0:
            _seed_cohort_benchmarks(cursor)
        
        conn.commit()
        logger.info("Database schema checked and initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite database: {str(e)}")
        raise e
    finally:
        conn.close()

def save_analysis(
    analysis_id: str,
    resume_filename: str,
    company_name: str,
    job_description: str,
    results_dict: dict,
    student_id: Optional[str] = None,
    version_number: Optional[int] = None,
    target_role: Optional[str] = None,
    **kwargs
) -> None:
    """Inserts a newly generated coordinator analysis into the database.
    Automatically assigns candidate student_id, target_role, and auto-increments version_number if omitted."""
    # Guard against saving synthetic demo data to real user records (#360)
    if (
        student_id == "demo_visitor"
        or analysis_id in ("demo-sample-analysis", "demo_sample_analysis", "demo")
        or (isinstance(results_dict, dict) and results_dict.get("is_demo"))
    ):
        logger.info(f"Demo mode analysis {analysis_id} intercepted; skipping real database persistence.")
        return

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        # Infer target role if omitted (#297)
        if not target_role:
            res = results_dict if isinstance(results_dict, dict) else {}
            target_role = (
                res.get("target_role") or
                res.get("job_match", {}).get("target_role") or
                res.get("role_preparation", {}).get("target_role") or
                "Software Engineer"
            )
        target_role = (target_role or "Software Engineer").strip()

        # Compute next version number if not explicitly specified
        if version_number is None or version_number <= 0:
            cursor.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM analyses WHERE student_id = ?",
                (effective_student_id,)
            )
            version_number = cursor.fetchone()[0]

        # Persist resume_text and ats_breakdown in results_dict if passed via kwargs (#494)
        if isinstance(results_dict, dict):
            if "resume_text" in kwargs and not results_dict.get("resume_text") and not results_dict.get("extracted_resume_text"):
                results_dict["resume_text"] = kwargs["resume_text"]
            if "ats_breakdown" in kwargs and not results_dict.get("ats_breakdown"):
                results_dict["ats_breakdown"] = kwargs["ats_breakdown"]
            if "stored_resume_id" in kwargs:
                results_dict["stored_resume_id"] = kwargs["stored_resume_id"]
            if "stored_resume_label" in kwargs:
                results_dict["stored_resume_label"] = kwargs["stored_resume_label"]

        stored_resume_id = kwargs.get("stored_resume_id")
        stored_resume_label = kwargs.get("stored_resume_label")

        cursor.execute("PRAGMA table_info(analyses)")
        col_names = [c[1] for c in cursor.fetchall()]
        if "stored_resume_id" in col_names and "stored_resume_label" in col_names and "target_role" in col_names:
            cursor.execute("""
                INSERT INTO analyses (id, resume_filename, company_name, job_description, results_json, student_id, version_number, target_role, stored_resume_id, stored_resume_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_id,
                resume_filename,
                company_name,
                job_description,
                json.dumps(results_dict),
                effective_student_id,
                version_number,
                target_role,
                stored_resume_id,
                stored_resume_label
            ))
        elif "target_role" in col_names:
            cursor.execute("""
                INSERT INTO analyses (id, resume_filename, company_name, job_description, results_json, student_id, version_number, target_role)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_id,
                resume_filename,
                company_name,
                job_description,
                json.dumps(results_dict),
                effective_student_id,
                version_number,
                target_role
            ))
        else:
            cursor.execute("""
                INSERT INTO analyses (id, resume_filename, company_name, job_description, results_json, student_id, version_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_id,
                resume_filename,
                company_name,
                job_description,
                json.dumps(results_dict),
                effective_student_id,
                version_number
            ))
        conn.commit()
        logger.info(f"Analysis saved with ID: {analysis_id} (Student: {effective_student_id}, Version: v{version_number}, Target Role: {target_role})")

        # Automatically register or update candidate on the cohort benchmark leaderboard
        try:
            res_analysis = results_dict.get("resume_analysis", {}) if isinstance(results_dict, dict) else {}
            job_match = results_dict.get("job_match", {}) if isinstance(results_dict, dict) else {}
            ats_val = int(res_analysis.get("ats_score", 70))
            match_val = int(job_match.get("match_percentage", 65))
            skills_cnt = len(job_match.get("matched_skills", [])) if isinstance(job_match.get("matched_skills"), list) else 0

            handle_suffix = abs(hash(analysis_id)) % 100
            anon_handle = f"Candidate_{handle_suffix:02d}"

            record_or_update_cohort_benchmark(
                analysis_id=analysis_id,
                student_handle=anon_handle,
                department="Computer Science",
                grad_year=2026,
                ats_score=ats_val,
                job_match_score=match_val,
                target_company=company_name or "General",
                skills_count=skills_cnt
            )
        except Exception as bench_err:
            logger.warning(f"Failed to record cohort benchmark on save_analysis: {bench_err}")
    
        # Record preparation activity for candidate streak tracking (#236)
        try:
            record_preparation_activity(
                student_id=effective_student_id,
                activity_type="resume_analysis",
                metadata={"analysis_id": analysis_id, "company": company_name}
            )
        except Exception as streak_err:
            logger.warning(f"Failed to record preparation activity on save_analysis: {streak_err}")
    except Exception as e:
        logger.error(f"Error saving analysis {analysis_id} to database: {str(e)}")
        raise e
    finally:
        conn.close()

def get_analysis(analysis_id: str) -> dict:
    """
    Retrieves and returns an analysis record by ID.
    Loads and injects the deserialized JSON results.
    """
    if analysis_id in ("demo-sample-analysis", "demo_sample_analysis", "demo"):
        from utils.demo_mode import get_synthetic_sample_analysis
        return get_synthetic_sample_analysis()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
        row = cursor.fetchone()
        if row:
            record = dict(row)
            record["results"] = json.loads(record["results_json"])
            if not record.get("resume_text") and isinstance(record.get("results"), dict):
                record["resume_text"] = record["results"].get("extracted_resume_text") or record["results"].get("resume_text")
            return record
        return None
    except Exception as e:
        logger.error(f"Error fetching analysis {analysis_id}: {str(e)}")
        raise e
    finally:
        conn.close()

def get_student_versions(student_id: str, company_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all resume analysis versions for a candidate student_id,
    ordered by version_number ASC. Deserializes results_json and computes
    ATS score and match percentage for each version.
    """
    if student_id == "demo_visitor":
        from utils.demo_mode import get_synthetic_sample_analysis
        return [get_synthetic_sample_analysis()]

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        
        if company_name:
            cursor.execute("""
                SELECT * FROM analyses
                WHERE student_id = ? AND company_name = ?
                ORDER BY version_number ASC, created_at ASC
            """, (effective_student_id, company_name.strip()))
        else:
            cursor.execute("""
                SELECT * FROM analyses
                WHERE student_id = ?
                ORDER BY version_number ASC, created_at ASC
            """, (effective_student_id,))

        rows = cursor.fetchall()
        versions = []
        for row in rows:
            rec = dict(row)
            try:
                rec["results"] = json.loads(rec.get("results_json", "{}"))
                res_analysis = rec["results"].get("resume_analysis", {})
                job_match = rec["results"].get("job_match", {})
                rec["ats_score"] = res_analysis.get("ats_score", 0)
                rec["match_percentage"] = job_match.get("match_percentage", 0)
            except Exception:
                rec["results"] = {}
                rec["ats_score"] = 0
                rec["match_percentage"] = 0
            versions.append(rec)
        return versions
    except Exception as e:
        logger.error(f"Error fetching versions for student {student_id}: {str(e)}")
        raise e
    finally:
        conn.close()

def get_previous_analysis(
    student_id: str,
    current_version: int,
    company_name: Optional[str] = None,
    target_role: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieves the immediately preceding analysis record for a candidate,
    optionally matching target company or target role.
    """
    if student_id == "demo_visitor":
        return None

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("PRAGMA table_info(analyses)")
        col_names = {c[1] for c in cursor.fetchall()}
        has_role_col = "target_role" in col_names

        query = ["SELECT * FROM analyses WHERE student_id = ? AND version_number < ?"]
        params = [effective_student_id, current_version]

        norm_company = company_name.strip() if company_name else ""
        norm_role = target_role.strip() if target_role else ""

        if norm_company and norm_role and has_role_col:
            query.append("AND (LOWER(company_name) = LOWER(?) OR LOWER(target_role) = LOWER(?))")
            params.extend([norm_company, norm_role])
        elif norm_company:
            query.append("AND LOWER(company_name) = LOWER(?)")
            params.append(norm_company)
        elif norm_role and has_role_col:
            query.append("AND LOWER(target_role) = LOWER(?)")
            params.append(norm_role)

        query.append("ORDER BY version_number DESC, created_at DESC LIMIT 1")
        cursor.execute(" ".join(query), tuple(params))
        row = cursor.fetchone()
        if row:
            rec = dict(row)
            try:
                rec["results"] = json.loads(rec.get("results_json", "{}"))
            except Exception:
                rec["results"] = {}
            return rec
        return None
    except Exception as e:
        logger.error(f"Error fetching previous analysis for student {student_id} (current v{current_version}): {str(e)}")
        raise e
    finally:
        conn.close()


def get_previous_analysis_for_target(
    student_id: str,
    current_analysis_id: Optional[str] = None,
    current_version: Optional[int] = None,
    company_name: Optional[str] = None,
    target_role: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Finds the most recent preceding analysis for the given student for the SAME target role
    or company (#297).
    Returns None if this is the student's first analysis for that role/company.
    """
    if student_id == "demo_visitor":
        return None

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("PRAGMA table_info(analyses)")
        cols = {c[1] for c in cursor.fetchall()}
        has_role_col = "target_role" in cols

        query = ["SELECT * FROM analyses WHERE student_id = ?"]
        params = [effective_student_id]

        if current_analysis_id:
            query.append("AND id != ?")
            params.append(current_analysis_id)

        if current_version is not None and current_version > 1:
            query.append("AND version_number < ?")
            params.append(current_version)

        norm_company = company_name.strip() if company_name else ""
        norm_role = target_role.strip() if target_role else ""

        if norm_company and norm_role and has_role_col:
            query.append("AND (LOWER(company_name) = LOWER(?) OR LOWER(target_role) = LOWER(?))")
            params.extend([norm_company, norm_role])
        elif norm_company:
            query.append("AND LOWER(company_name) = LOWER(?)")
            params.append(norm_company)
        elif norm_role and has_role_col:
            query.append("AND LOWER(target_role) = LOWER(?)")
            params.append(norm_role)

        query.append("ORDER BY version_number DESC, created_at DESC LIMIT 1")
        cursor.execute(" ".join(query), tuple(params))
        row = cursor.fetchone()
        if not row:
            return None
        rec = dict(row)
        try:
            rec["results"] = json.loads(rec.get("results_json", "{}"))
        except Exception:
            rec["results"] = {}
        return rec
    except Exception as e:
        logger.error(f"Error fetching previous analysis for target: {e}")
        return None
    finally:
        conn.close()


def get_student_versions_for_target(
    student_id: str,
    company_name: Optional[str] = None,
    target_role: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves all resume analysis versions for a candidate matching the specified
    target role OR company (#297), ordered chronologically.
    """
    if student_id == "demo_visitor":
        from utils.demo_mode import get_synthetic_sample_analysis
        return [get_synthetic_sample_analysis()]

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("PRAGMA table_info(analyses)")
        cols = {c[1] for c in cursor.fetchall()}
        has_role_col = "target_role" in cols

        query = ["SELECT * FROM analyses WHERE student_id = ?"]
        params = [effective_student_id]

        norm_company = company_name.strip() if company_name else ""
        norm_role = target_role.strip() if target_role else ""

        if norm_company and norm_role and has_role_col:
            query.append("AND (LOWER(company_name) = LOWER(?) OR LOWER(target_role) = LOWER(?))")
            params.extend([norm_company, norm_role])
        elif norm_company:
            query.append("AND LOWER(company_name) = LOWER(?)")
            params.append(norm_company)
        elif norm_role and has_role_col:
            query.append("AND LOWER(target_role) = LOWER(?)")
            params.append(norm_role)

        query.append("ORDER BY version_number ASC, created_at ASC")
        cursor.execute(" ".join(query), tuple(params))
        rows = cursor.fetchall()

        versions = []
        for row in rows:
            rec = dict(row)
            try:
                rec["results"] = json.loads(rec.get("results_json", "{}"))
                res_analysis = rec["results"].get("resume_analysis", {})
                job_match = rec["results"].get("job_match", {})
                rec["ats_score"] = res_analysis.get("ats_score", 0)
                rec["match_percentage"] = job_match.get("match_percentage", 0)
            except Exception:
                rec["results"] = {}
                rec["ats_score"] = 0
                rec["match_percentage"] = 0
            versions.append(rec)
        return versions
    except Exception as e:
        logger.error(f"Error in get_student_versions_for_target: {e}")
        return []
    finally:
        conn.close()

def save_interview_session(
    session_id: str,
    student_id: str,
    company_name: str,
    role_title: str,
    interview_type: str,
    overall_score: float,
    strengths: Optional[List[str]] = None,
    improvements: Optional[List[str]] = None,
    rounds: Optional[List[Dict[str, Any]]] = None,
    analysis_id: Optional[str] = None,
) -> None:
    """Creates a synthetic interview-history record for pagination and reporting tests."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_analysis_id = analysis_id or session_id
        cursor.execute(
            "SELECT 1 FROM analyses WHERE id = ? AND student_id = ?",
            (effective_analysis_id, (student_id or "default_student").strip()),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                    INSERT INTO analyses (id, resume_filename, company_name, job_description, results_json, student_id, version_number)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    effective_analysis_id,
                    f"{company_name or 'Interview'}_{effective_analysis_id}.pdf",
                    company_name or "General",
                    role_title or "Interview",
                    json.dumps({
                        "summary": {
                            "interview_type": interview_type or "Technical",
                            "overall_score": float(overall_score or 0.0),
                            "strengths": strengths or [],
                            "improvements": improvements or [],
                            "rounds": rounds or [],
                        }
                    }),
                    (student_id or "default_student").strip(),
                )
            )

        questions_payload = []
        if rounds:
            for idx, round_info in enumerate(rounds):
                entry = {"index": idx, "type": interview_type or "Technical"}
                if isinstance(round_info, dict):
                    entry.update(round_info)
                questions_payload.append(entry)
        if not questions_payload:
            questions_payload = [{"index": 0, "type": interview_type or "Technical", "question": f"{role_title or 'Interview'} review"}]

        cursor.execute(
            """
                INSERT OR REPLACE INTO interviews (id, analysis_id, current_question_index, questions_json, answers_json, completed)
                VALUES (?, ?, 0, ?, ?, 1)
            """,
            (session_id, effective_analysis_id, json.dumps(questions_payload), json.dumps([])),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving interview session {session_id}: {str(e)}")
        raise e
    finally:
        conn.close()


def create_interview_session(interview_id: str, analysis_id: str, questions_list: list) -> None:
    """
    Creates a new mock interview session referencing an analysis.
    Stores the list of 15 generated questions (HR, Technical, Behavioral).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interviews (id, analysis_id, current_question_index, questions_json, answers_json, completed)
            VALUES (?, ?, 0, ?, ?, 0)
        """, (interview_id, analysis_id, json.dumps(questions_list), json.dumps([])))
        conn.commit()
        logger.info(f"Interview session initialized with ID: {interview_id}")
    except Exception as e:
        logger.error(f"Error initializing interview session {interview_id}: {str(e)}")
        raise e
    finally:
        conn.close()

def get_interview_session(interview_id: str) -> dict:
    """
    Retrieves and returns an interview session record by ID.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,))
        row = cursor.fetchone()
        if row:
            record = dict(row)
            record["questions"] = json.loads(record["questions_json"])
            record["answers"] = json.loads(record["answers_json"])
            return record
        return None
    except Exception as e:
        logger.error(f"Error fetching interview session {interview_id}: {str(e)}")
        raise e
    finally:
        conn.close()

def update_interview_session(interview_id: str, current_question_index: int, answers_list: list, completed: bool) -> None:
    """
    Updates the progress, answer list, and completion status of an active interview session.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE interviews
            SET current_question_index = ?, answers_json = ?, completed = ?
            WHERE id = ?
        """, (current_question_index, json.dumps(answers_list), 1 if completed else 0, interview_id))
        conn.commit()
        logger.info(f"Interview session {interview_id} updated. Current index: {current_question_index}. Completed: {completed}")
    except Exception as e:
        logger.error(f"Error updating interview session {interview_id}: {str(e)}")
        raise e
    finally:
        conn.close()

def get_recent_analyses(limit: int = 10) -> list:
    """
    Retrieves the most recent analysis sessions ordered by created_at DESC.
    Includes parsed resume ATS score and company match percentage for quick display.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, resume_filename, company_name, job_description, results_json, created_at
            FROM analyses
            ORDER BY created_at DESC
            LIMIT ?
        """, (max(1, min(limit, 50)),))
        rows = cursor.fetchall()
        recent = []
        for row in rows:
            record = dict(row)
            try:
                parsed_results = json.loads(record.pop("results_json", "{}"))
                resume_analysis = parsed_results.get("resume_analysis", {})
                job_match = parsed_results.get("job_match", {})
                record["ats_score"] = resume_analysis.get("ats_score", 0)
                record["match_percentage"] = job_match.get("match_percentage", 0)
            except Exception:
                record["ats_score"] = 0
                record["match_percentage"] = 0
            recent.append(record)
        return recent
    except Exception as e:
        logger.error(f"Error fetching recent analyses: {str(e)}")
        raise e
    finally:
        conn.close()

def delete_analysis(analysis_id: str) -> bool:
    """
    Deletes an analysis record and its corresponding interview sessions and benchmark entries.
    Returns True if an analysis was deleted, False if not found.
    """
    if analysis_id in ("demo-sample-analysis", "demo_sample_analysis", "demo"):
        return True

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM interviews WHERE analysis_id = ?", (analysis_id,))
        cursor.execute("DELETE FROM cohort_benchmarks WHERE analysis_id = ?", (analysis_id,))
        cursor.execute("DELETE FROM analysis_cache WHERE analysis_id = ?", (analysis_id,))
        cursor.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"Deleted analysis {analysis_id} and associated interview data.")
        return deleted
    except Exception as e:
        logger.error(f"Error deleting analysis {analysis_id}: {str(e)}")
        raise e
    finally:
        conn.close()


def get_analyses_history(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Retrieves past placement analysis sessions ordered by created_at DESC (newest first) (#159).
    Includes formatted date, target role, target company, ATS score, match percentage, and metadata.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(analyses)")
        col_names = {c[1] for c in cursor.fetchall()}
        has_stored_cols = "stored_resume_label" in col_names and "stored_resume_id" in col_names

        extra_cols = ", stored_resume_id, stored_resume_label" if has_stored_cols else ""
        query = f"""
            SELECT id, resume_filename, company_name, job_description, results_json,
                   student_id, version_number, created_at{extra_cols}
            FROM analyses
            WHERE student_id != 'demo_visitor' AND id NOT IN ('demo-sample-analysis', 'demo_sample_analysis', 'demo')
            ORDER BY created_at DESC, rowid DESC
        """
        params: List[Any] = []
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        history = []
        for row in rows:
            record = dict(row)
            try:
                raw_json = record.pop("results_json", "{}")
                parsed = json.loads(raw_json) if isinstance(raw_json, str) else (raw_json or {})
                resume_analysis = parsed.get("resume_analysis", {})
                job_match = parsed.get("job_match", {})

                # ATS score resolution
                ats = parsed.get("ats_score") or resume_analysis.get("ats_score", 0)
                record["ats_score"] = int(ats)

                # Match percentage resolution
                match = parsed.get("match_percentage") or job_match.get("match_percentage", 0)
                record["match_percentage"] = int(match)

                # Target role resolution
                target_role = (
                    parsed.get("target_role")
                    or job_match.get("target_role")
                    or parsed.get("role_title")
                    or job_match.get("role_title")
                    or ""
                )
                if not target_role and record.get("job_description"):
                    first_line = record["job_description"].split("\n")[0].strip()
                    target_role = first_line[:50] if first_line else "Software Engineer"
                elif not target_role:
                    target_role = "Software Engineer"

                record["target_role"] = target_role

                # Resolve stored resume attribution (#842)
                if not record.get("stored_resume_label"):
                    record["stored_resume_label"] = parsed.get("stored_resume_label")
                if not record.get("stored_resume_id"):
                    record["stored_resume_id"] = parsed.get("stored_resume_id")
            except Exception as parse_err:
                logger.warning(f"Error parsing analysis {record.get('id')} results: {parse_err}")
                record["ats_score"] = 0
                record["match_percentage"] = 0
                record["target_role"] = "Software Engineer"

            history.append(record)
        return history
    except Exception as e:
        logger.error(f"Error fetching analyses history: {str(e)}")
        raise e
    finally:
        conn.close()


def create_database_backup(backup_destination_path: str) -> str:
    """
    Performs an online, zero-downtime hot backup of the SQLite database using SQLite's
    built-in backup API. Guarantees ACID transactional consistency without table locks.

    Parameters:
        backup_destination_path: Absolute or relative file path for the backup .db file.

    Returns:
        The backup_destination_path string on success.
    """
    src_conn = get_db_connection()
    try:
        logger.info(f"Initiating online database backup to: {backup_destination_path}")
        dest_conn = sqlite3.connect(backup_destination_path)
        with dest_conn:
            src_conn.backup(dest_conn, pages=20, sleep=0.01)
        dest_conn.close()
        logger.info("Online database backup completed successfully.")
        return backup_destination_path
    except Exception as exc:
        logger.error(f"Failed to create database backup: {exc}")
        raise RuntimeError(f"Database backup failed: {exc}") from exc
    finally:
        src_conn.close()


def verify_database_integrity() -> Dict[str, Any]:
    """
    Runs SQLite integrity PRAGMA checks and structural statistics.
    Returns a dictionary with status, integrity errors (if any), table row counts,
    and journal mode details.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        integrity_rows = [row[0] for row in cursor.fetchall()]
        is_healthy = len(integrity_rows) == 1 and integrity_rows[0].lower() == "ok"

        # Gather table metrics
        cursor.execute("SELECT COUNT(*) FROM analyses")
        analyses_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM interviews")
        interviews_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM cohort_benchmarks")
        cohort_count = cursor.fetchone()[0]

        cursor.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]

        return {
            "healthy": is_healthy,
            "status": "ok" if is_healthy else "corrupted",
            "messages": integrity_rows,
            "tables": {
                "analyses_count": analyses_count,
                "interviews_count": interviews_count,
                "cohort_benchmarks_count": cohort_count,
            },
            "journal_mode": journal_mode,
        }
    except Exception as exc:
        logger.error(f"Error executing database integrity verification: {exc}")
        return {
            "healthy": False,
            "status": "error",
            "messages": [str(exc)],
            "tables": {},
            "journal_mode": "unknown",
        }
    finally:
        conn.close()


def save_batch_comparison(
    batch_id: str,
    student_id: str,
    resume_filename: str,
    companies_list: List[str],
    results_dict: Dict[str, Any]
) -> None:
    """
    Persists a multi-company batch comparison result into the SQLite database.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            INSERT OR REPLACE INTO batch_comparisons (
                id, student_id, resume_filename, companies_json, results_json
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            batch_id,
            effective_student_id,
            resume_filename,
            json.dumps(companies_list),
            json.dumps(results_dict)
        ))
        conn.commit()
        logger.info(f"Batch comparison saved with ID: {batch_id} ({len(companies_list)} companies)")
    except Exception as e:
        logger.error(f"Error saving batch comparison {batch_id}: {e}")
        raise e
    finally:
        conn.close()


def get_batch_comparison(batch_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a multi-company batch comparison record by ID, deserializing stored JSON.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM batch_comparisons WHERE id = ?", (batch_id,))
        row = cursor.fetchone()
        if row:
            rec = dict(row)
            rec["companies"] = json.loads(rec["companies_json"])
            rec["results"] = json.loads(rec["results_json"])
            return rec
        return None
    except Exception as e:
        logger.error(f"Error fetching batch comparison {batch_id}: {e}")
        raise e
    finally:
        conn.close()



def save_job_application(
    app_id: str,
    student_id: str,
    company_name: str,
    role_title: str,
    status: str = "Applied",
    application_date: Optional[str] = None,
    job_url: Optional[str] = None,
    job_description: Optional[str] = None,
    notes: Optional[str] = None,
    location: str = "Remote",
    job_type: str = "Full-Time",
    experience_level: str = "Entry-Level",
    resume_analysis_id: Optional[str] = None,
    resume_filename: Optional[str] = None,
    resume_version: Optional[int] = None,
    job_description_snapshot: Optional[str] = None,
    deadline_date: Optional[str] = None
) -> None:
    """
    Saves or updates a candidate job application record (#143, #193, #298).
    Ensures per-user data segregation.
    """
    import datetime
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        date_str = application_date or datetime.date.today().isoformat()
        clean_deadline = deadline_date.strip() if deadline_date and isinstance(deadline_date, str) else None
        cursor.execute("""
            INSERT OR REPLACE INTO job_applications (
                id, student_id, company_name, role_title, status, application_date,
                job_url, job_description, notes, location, job_type, experience_level,
                resume_analysis_id, resume_filename, resume_version, job_description_snapshot,
                deadline_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            student_id.strip() if student_id else "default_student",
            company_name.strip(),
            role_title.strip(),
            status.strip(),
            date_str,
            (job_url or "").strip(),
            (job_description or "").strip(),
            (notes or "").strip(),
            (location or "Remote").strip(),
            (job_type or "Full-Time").strip(),
            (experience_level or "Entry-Level").strip(),
            (resume_analysis_id or "").strip() or None,
            (resume_filename or "").strip() or None,
            resume_version,
            (job_description_snapshot or "").strip() or None,
            clean_deadline
        ))
        conn.commit()
        logger.info(f"Saved job application {app_id} for student {student_id}")
    except Exception as e:
        logger.error(f"Error saving job application {app_id}: {e}")
        raise e
    finally:
        conn.close()


def get_job_applications(
    student_id: str,
    status_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    location_filter: Optional[str] = None,
    job_type_filter: Optional[str] = None,
    experience_level_filter: Optional[str] = None,
    sort_by: str = "recent"
) -> List[Dict[str, Any]]:
    """
    Retrieves all job application records for a specific student, with advanced search,
    location, job type, and experience filtering (#143, #193).
    Strictly enforces user data isolation.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        conditions = ["student_id = ?"]
        params: List[Any] = [student_id.strip() if student_id else "default_student"]

        if status_filter and status_filter.strip() and status_filter.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(status) = LOWER(?)")
            params.append(status_filter.strip())

        if search_query and search_query.strip():
            kw = f"%{search_query.strip().lower()}%"
            conditions.append("(LOWER(company_name) LIKE ? OR LOWER(role_title) LIKE ? OR LOWER(job_description) LIKE ?)")
            params.extend([kw, kw, kw])

        if location_filter and location_filter.strip() and location_filter.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(location) LIKE LOWER(?)")
            params.append(f"%{location_filter.strip()}%")

        if job_type_filter and job_type_filter.strip() and job_type_filter.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(job_type) = LOWER(?)")
            params.append(job_type_filter.strip())

        if experience_level_filter and experience_level_filter.strip() and experience_level_filter.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(experience_level) = LOWER(?)")
            params.append(experience_level_filter.strip())

        where_clause = f"WHERE {' AND '.join(conditions)}"

        if sort_by == "oldest":
            order_clause = "ORDER BY application_date ASC, created_at ASC"
        elif sort_by == "company":
            order_clause = "ORDER BY company_name ASC"
        else:
            order_clause = "ORDER BY application_date DESC, created_at DESC"

        query = f"SELECT * FROM job_applications {where_clause} {order_clause}"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error retrieving job applications for student {student_id}: {e}")
        return []
    finally:
        conn.close()


def delete_job_application(app_id: str, student_id: str) -> bool:
    """
    Deletes a specific job application record owned by student_id (#143).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM job_applications WHERE id = ? AND student_id = ?", (app_id, student_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting job application {app_id}: {e}")
        return False
    finally:
        conn.close()


def update_job_application_status(app_id: str, student_id: str, new_status: str) -> bool:
    """
    Updates the pipeline status of a specific job application (#143).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE job_applications SET status = ? WHERE id = ? AND student_id = ?",
            (new_status.strip(), app_id, student_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating job application status for {app_id}: {e}")
        return False
    finally:
        conn.close()


def get_job_application_by_id(app_id: str, student_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single job application record owned by student_id.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM job_applications WHERE id = ? AND student_id = ?",
            (app_id.strip(), student_id.strip() if student_id else "default_student")
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error retrieving job application {app_id}: {e}")
        return None
    finally:
        conn.close()


def update_job_application(
    app_id: str,
    student_id: str,
    company_name: str,
    role_title: str,
    status: str = "Applied",
    application_date: Optional[str] = None,
    job_url: Optional[str] = None,
    job_description: Optional[str] = None,
    notes: Optional[str] = None,
    location: str = "Remote",
    job_type: str = "Full-Time",
    experience_level: str = "Entry-Level",
    resume_analysis_id: Optional[str] = None,
    resume_filename: Optional[str] = None,
    resume_version: Optional[int] = None,
    job_description_snapshot: Optional[str] = None,
    deadline_date: Optional[str] = None
) -> bool:
    """
    Updates an existing job application record with new details (#143, #298).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        clean_deadline = deadline_date.strip() if deadline_date and isinstance(deadline_date, str) else deadline_date
        cursor.execute("""
            UPDATE job_applications SET
                company_name = ?,
                role_title = ?,
                status = ?,
                application_date = coalesce(?, application_date),
                job_url = ?,
                job_description = ?,
                notes = ?,
                location = ?,
                job_type = ?,
                experience_level = ?,
                resume_analysis_id = ?,
                resume_filename = ?,
                resume_version = ?,
                job_description_snapshot = ?,
                deadline_date = ?
            WHERE id = ? AND student_id = ?
        """, (
            company_name.strip(),
            role_title.strip(),
            status.strip(),
            application_date,
            (job_url or "").strip(),
            (job_description or "").strip(),
            (notes or "").strip(),
            (location or "Remote").strip(),
            (job_type or "Full-Time").strip(),
            (experience_level or "Entry-Level").strip(),
            (resume_analysis_id or "").strip() or None,
            (resume_filename or "").strip() or None,
            resume_version,
            (job_description_snapshot or "").strip() or None,
            clean_deadline,
            app_id.strip(),
            student_id.strip() if student_id else "default_student"
        ))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating job application {app_id}: {e}")
        return False
    finally:
        conn.close()


def get_application_stage_counts(student_id: str) -> Dict[str, int]:
    """
    Returns pipeline counts for all application stages (Applied, Assessment, Interview, Selected, Rejected).
    """
    counts = {
        "Applied": 0,
        "Assessment": 0,
        "Interview": 0,
        "Selected": 0,
        "Rejected": 0,
        "Total": 0
    }
    apps = get_job_applications(student_id=student_id)
    for a in apps:
        st = (a.get("status") or "Applied").strip()
        if st == "Offered":
            st = "Selected"
        if st in counts:
            counts[st] += 1
        else:
            counts["Applied"] += 1
    counts["Total"] = len(apps)
    return counts


def get_application_funnel_analytics(student_id: str) -> Dict[str, Any]:
    """
    Builds a user-scoped application funnel summary from current application stages.

    Interview reach includes applications currently at Interview or later (Selected).
    Success rates are withheld until five applications exist to avoid over-interpreting
    very small samples.
    """
    counts = get_application_stage_counts(student_id)
    total = counts["Total"]
    interview_count = counts["Interview"] + counts["Selected"]
    offer_count = counts["Selected"]
    reliable = total >= 5

    return {
        "total_applications": total,
        "stage_counts": counts,
        "interview_count": interview_count,
        "offer_count": offer_count,
        "sample_size": total,
        "minimum_sample_size": 5,
        "has_enough_data": reliable,
        "interview_rate": round(interview_count / total * 100, 1) if reliable else None,
        "offer_rate": round(offer_count / total * 100, 1) if reliable else None,
        "calculation_note": (
            "Interview reach counts applications currently at Interview or Selected; "
            "offers are applications currently marked Selected."
        ),
        "reliability_note": (
            "Rates are shown after at least 5 application records; with fewer records, "
            "counts are useful for tracking but too small for a reliable success rate."
        ),
    }


def export_job_applications_csv(
    student_id: str,
    status_filter: Optional[str] = None
) -> str:
    """
    Generates standard RFC-4180 CSV export content for a student's job applications (#196).
    Enforces student data segregation, formats dates and notes safely, and handles empty application states.
    """
    import csv
    import io

    applications = get_job_applications(student_id=student_id, status_filter=status_filter)
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Header row
    writer.writerow([
        "Application ID",
        "Company Name",
        "Role Title",
        "Status",
        "Application Date",
        "Job Posting URL",
        "Job Description / Requirements",
        "Interview / Follow-up Notes",
        "Created At"
    ])

    from db.tracker import escape_csv_cell

    for app_item in applications:
        writer.writerow([
            escape_csv_cell(app_item.get("id", "")),
            escape_csv_cell(app_item.get("company_name", "")),
            escape_csv_cell(app_item.get("role_title", "")),
            escape_csv_cell(app_item.get("status", "")),
            escape_csv_cell(app_item.get("application_date", "")),
            escape_csv_cell(app_item.get("job_url", "")),
            escape_csv_cell(app_item.get("job_description", "")),
            escape_csv_cell(app_item.get("notes", "")),
            escape_csv_cell(app_item.get("created_at", ""))
        ])

    return output.getvalue()


def export_job_applications_json(
    student_id: str,
    status_filter: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates structured JSON export payload for a student's job applications (#196).
    """
    applications = get_job_applications(student_id=student_id, status_filter=status_filter)
    status_counts = {}
    for item in applications:
        st = item.get("status", "Unknown")
        status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "export_version": "1.0",
        "student_id": student_id.strip() if student_id else "default_student",
        "total_applications": len(applications),
        "status_distribution": status_counts,
        "applications": applications
    }


def export_job_applications_markdown(
    student_id: str,
    status_filter: Optional[str] = None
) -> str:
    """
    Generates a readable Markdown summary table of job applications (#196).
    """
    applications = get_job_applications(student_id=student_id, status_filter=status_filter)
    lines = [
        f"# Job Applications Report - Candidate: {student_id}",
        "",
        f"- **Total Applications**: {len(applications)}",
        f"- **Filter Applied**: {status_filter or 'All'}",
        "",
        "| ID | Company | Role | Status | Applied Date | Job Link | Notes |",
        "|---|---|---|---|---|---|---|"
    ]

    for item in applications:
        app_id = item.get("id", "")
        comp = item.get("company_name", "").replace("|", "\\|")
        role = item.get("role_title", "").replace("|", "\\|")
        st = item.get("status", "")
        dt = item.get("application_date", "") or "-"
        url = item.get("job_url", "")
        link_str = f"[Link]({url})" if url else "-"
        notes = (item.get("notes") or "-").replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {app_id} | {comp} | {role} | {st} | {dt} | {link_str} | {notes} |")

    lines.append("")
    return "\n".join(lines)


def get_job_applications_export_summary(
    student_id: str
) -> Dict[str, Any]:
    """
    Provides aggregated analytics metadata for candidate job applications export.
    """
    applications = get_job_applications(student_id=student_id)
    stages = {"Applied": 0, "Assessment": 0, "Interview": 0, "Selected": 0, "Rejected": 0}
    companies = set()
    for item in applications:
        st = item.get("status", "Applied")
        if st in stages:
            stages[st] += 1
        else:
            stages[st] = 1
        comp = item.get("company_name")
        if comp:
            companies.add(comp)

    return {
        "student_id": student_id,
        "total_records": len(applications),
        "unique_companies_count": len(companies),
        "stage_breakdown": stages,
        "ready_for_export": True
    }


def _seed_cohort_benchmarks(cursor: sqlite3.Cursor) -> None:
    """
    Populates default cohort records representing diverse university branch peers.
    """
    seed_peers = [
        ("peer-01", "AlgoMaster_99", "Computer Science", 2026, 95, 94, 94.4, "Google", 9, "Platinum"),
        ("peer-02", "CloudArchitect_42", "Information Technology", 2026, 92, 91, 91.4, "Microsoft", 8, "Platinum"),
        ("peer-03", "SystemSurgeon_18", "Computer Science", 2026, 90, 89, 89.4, "Amazon", 8, "Platinum"),
        ("peer-04", "DataVoyager_77", "Data Science & AI", 2026, 88, 86, 86.9, "Adobe", 7, "Gold"),
        ("peer-05", "DevScholar_12", "Computer Science", 2026, 86, 84, 84.9, "Uber", 7, "Gold"),
        ("peer-06", "BinaryMonk_33", "Electronics & Comm", 2026, 85, 82, 83.3, "Cisco", 6, "Gold"),
        ("peer-07", "PixelCraft_29", "Information Technology", 2026, 84, 83, 83.4, "Swiggy", 6, "Gold"),
        ("peer-08", "CodeCrafter_05", "Computer Science", 2026, 83, 80, 81.3, "Razorpay", 6, "Gold"),
        ("peer-09", "NeuralNomad_64", "Data Science & AI", 2025, 82, 79, 80.3, "Oracle", 6, "Gold"),
        ("peer-10", "SyntaxSamurai_81", "Computer Science", 2025, 81, 78, 79.3, "Goldman Sachs", 6, "Gold"),
        ("peer-11", "KernelKnight_56", "Electronics & Comm", 2026, 80, 77, 78.3, "Qualcomm", 5, "Gold"),
        ("peer-12", "StackWizard_21", "Information Technology", 2026, 78, 76, 76.9, "Flipkart", 5, "Silver"),
        ("peer-13", "LogicWhiz_44", "Computer Science", 2026, 77, 75, 75.9, "TCS", 5, "Silver"),
        ("peer-14", "CyberFalcon_87", "Computer Science", 2025, 76, 74, 74.9, "Infosys", 5, "Silver"),
        ("peer-15", "QuantumCoder_19", "Electronics & Comm", 2027, 75, 72, 73.3, "Accenture", 4, "Silver"),
        ("peer-16", "ByteSurgeon_63", "Information Technology", 2026, 74, 71, 72.3, "Capgemini", 4, "Silver"),
        ("peer-17", "MatrixMaster_38", "Electrical Engineering", 2026, 73, 70, 71.3, "Wipro", 4, "Silver"),
        ("peer-18", "PromptArchitect_90", "Data Science & AI", 2026, 72, 69, 70.3, "Cognizant", 4, "Silver"),
        ("peer-19", "GitGuru_08", "Computer Science", 2027, 70, 68, 68.9, "General", 4, "Silver"),
        ("peer-20", "ScriptSage_52", "Electronics & Comm", 2025, 69, 66, 67.3, "General", 4, "Silver"),
        ("peer-21", "SiliconPioneer_35", "Electrical Engineering", 2026, 67, 65, 65.9, "General", 3, "Silver"),
        ("peer-22", "TurboCoder_14", "Mechanical Engineering", 2026, 64, 62, 62.9, "General", 3, "Bronze"),
        ("peer-23", "ApexDev_93", "Computer Science", 2027, 62, 60, 60.9, "General", 3, "Bronze"),
        ("peer-24", "VeloceBytes_41", "Civil Engineering", 2026, 58, 55, 56.3, "General", 2, "Bronze"),
    ]
    cursor.executemany("""
        INSERT OR IGNORE INTO cohort_benchmarks 
        (id, student_handle, department, grad_year, ats_score, job_match_score, overall_score, target_company, skills_count, tier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, seed_peers)
    logger.info(f"Seeded {len(seed_peers)} default peer cohort benchmark records.")


def get_user_leaderboard_opt_in(user_id: Optional[str]) -> bool:
    """
    Retrieves the leaderboard participation preference for a user (#360).
    Defaults to True (opted-in) for new or unregistered users or when omitted.
    """
    if not user_id:
        return True
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT leaderboard_opt_in FROM users WHERE id = ?", (str(user_id),))
        row = cursor.fetchone()
        if row and row[0] is not None:
            return bool(row[0])
        return True
    except Exception as e:
        logger.error(f"Error fetching leaderboard opt-in for user {user_id}: {e}")
        return True
    finally:
        conn.close()


def set_user_leaderboard_opt_in(user_id: str, opt_in: bool) -> bool:
    """
    Updates the leaderboard participation preference for a user (#360).
    Uses safe upsert to ensure preferences persist reliably.
    """
    if not user_id:
        return False
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        val = 1 if opt_in else 0
        cursor.execute("UPDATE users SET leaderboard_opt_in = ? WHERE id = ?", (val, str(user_id)))
        if cursor.rowcount == 0:
            cursor.execute("""
                INSERT INTO users (id, name, email, password_hash, leaderboard_opt_in)
                VALUES (?, ?, ?, '', ?)
                ON CONFLICT(id) DO UPDATE SET leaderboard_opt_in = excluded.leaderboard_opt_in
            """, (str(user_id), f"Student_{user_id}", f"{user_id}@campus.local", val))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating leaderboard opt-in for user {user_id}: {e}")
        return False
    finally:
        conn.close()


def record_or_update_cohort_benchmark(
    analysis_id: str,
    student_handle: str,
    department: str = "Computer Science",
    grad_year: int = 2026,
    ats_score: int = 70,
    job_match_score: int = 65,
    overall_score: Optional[float] = None,
    target_company: str = "General",
    skills_count: int = 0,
    tier: Optional[str] = None
) -> str:
    """
    Inserts or updates an anonymized student evaluation in the cohort_benchmarks table.
    Computes overall composite score and achievement tier if omitted.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Calculate composite score (45% ATS, 55% Job Match)
        if overall_score is None:
            overall_score = round((ats_score * 0.45) + (job_match_score * 0.55), 1)

        # Derive achievement tier if omitted
        if not tier:
            if overall_score >= 88.0:
                tier = "Platinum"
            elif overall_score >= 78.0:
                tier = "Gold"
            elif overall_score >= 65.0:
                tier = "Silver"
            else:
                tier = "Bronze"

        # Check if record for this analysis_id already exists
        cursor.execute("SELECT id FROM cohort_benchmarks WHERE analysis_id = ?", (analysis_id,))
        existing = cursor.fetchone()

        if existing:
            benchmark_id = existing[0]
            cursor.execute("""
                UPDATE cohort_benchmarks
                SET student_handle = ?, department = ?, grad_year = ?,
                    ats_score = ?, job_match_score = ?, overall_score = ?,
                    target_company = ?, skills_count = ?, tier = ?
                WHERE id = ?
            """, (
                student_handle, department, grad_year,
                ats_score, job_match_score, overall_score,
                target_company, skills_count, tier,
                benchmark_id
            ))
        else:
            benchmark_id = f"cohort-{analysis_id}"
            cursor.execute("""
                INSERT INTO cohort_benchmarks
                (id, analysis_id, student_handle, department, grad_year, ats_score, job_match_score, overall_score, target_company, skills_count, tier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                benchmark_id, analysis_id, student_handle, department, grad_year,
                ats_score, job_match_score, overall_score, target_company, skills_count, tier
            ))

        conn.commit()
        return benchmark_id
    except Exception as e:
        logger.error(f"Error saving cohort benchmark for analysis {analysis_id}: {e}")
        raise e
    finally:
        conn.close()


def get_cohort_benchmark_by_analysis_id(analysis_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves the cohort benchmark record for a specific analysis ID.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cohort_benchmarks WHERE analysis_id = ?", (analysis_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error fetching cohort benchmark for {analysis_id}: {e}")
        return None
    finally:
        conn.close()


def get_cohort_benchmarks(
    department: Optional[str] = None,
    grad_year: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    include_opted_out: bool = False
) -> List[Dict[str, Any]]:
    """
    Retrieves sorted cohort benchmark entries ordered by overall_score DESC.
    Supports filtering by department, graduation year, keyword search, and leaderboard opt-out (#360).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if not include_opted_out:
            conditions.append("COALESCE(u.leaderboard_opt_in, 1) = 1")

        if department and department.strip() and department.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(cb.department) = LOWER(?)")
            params.append(department.strip())

        if grad_year and int(grad_year) > 0:
            conditions.append("cb.grad_year = ?")
            params.append(int(grad_year))

        if search and search.strip():
            kw = f"%{search.strip().lower()}%"
            conditions.append("(LOWER(cb.student_handle) LIKE ? OR LOWER(cb.target_company) LIKE ? OR LOWER(cb.department) LIKE ?)")
            params.extend([kw, kw, kw])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT cb.* FROM cohort_benchmarks cb
            LEFT JOIN analyses a ON cb.analysis_id = a.id
            LEFT JOIN users u ON a.student_id = u.id
            {where_clause}
            ORDER BY cb.overall_score DESC, cb.ats_score DESC, cb.job_match_score DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        benchmarks = []
        for idx, row in enumerate(rows):
            rec = dict(row)
            rec["rank"] = offset + idx + 1
            benchmarks.append(rec)

        return benchmarks
    except Exception as e:
        logger.error(f"Error fetching cohort benchmarks: {e}")
        return []
    finally:
        conn.close()


def get_cohort_stats(
    department: Optional[str] = None,
    grad_year: Optional[int] = None,
    include_opted_out: bool = False
) -> Dict[str, Any]:
    """
    Computes aggregate metrics across the specified cohort segment:
    count, avg_ats, avg_match, avg_overall, max_score, and tier distribution.
    Excludes opted-out candidates by default (#360).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if not include_opted_out:
            conditions.append("COALESCE(u.leaderboard_opt_in, 1) = 1")

        if department and department.strip() and department.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(cb.department) = LOWER(?)")
            params.append(department.strip())

        if grad_year and int(grad_year) > 0:
            conditions.append("cb.grad_year = ?")
            params.append(int(grad_year))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cursor.execute(f"""
            SELECT 
                COUNT(*) as total_students,
                COALESCE(AVG(cb.ats_score), 0) as avg_ats,
                COALESCE(AVG(cb.job_match_score), 0) as avg_match,
                COALESCE(AVG(cb.overall_score), 0) as avg_overall,
                COALESCE(MAX(cb.overall_score), 0) as max_overall,
                COALESCE(SUM(CASE WHEN cb.tier = 'Platinum' THEN 1 ELSE 0 END), 0) as platinum_count,
                COALESCE(SUM(CASE WHEN cb.tier = 'Gold' THEN 1 ELSE 0 END), 0) as gold_count,
                COALESCE(SUM(CASE WHEN cb.tier = 'Silver' THEN 1 ELSE 0 END), 0) as silver_count,
                COALESCE(SUM(CASE WHEN cb.tier = 'Bronze' THEN 1 ELSE 0 END), 0) as bronze_count
            FROM cohort_benchmarks cb
            LEFT JOIN analyses a ON cb.analysis_id = a.id
            LEFT JOIN users u ON a.student_id = u.id
            {where_clause}
        """, params)

        stats = dict(cursor.fetchone())
        stats["avg_ats"] = round(float(stats.get("avg_ats", 0)), 1)
        stats["avg_match"] = round(float(stats.get("avg_match", 0)), 1)
        stats["avg_overall"] = round(float(stats.get("avg_overall", 0)), 1)
        stats["max_overall"] = round(float(stats.get("max_overall", 0)), 1)

        # Department distribution
        cursor.execute(f"""
            SELECT cb.department, COUNT(*) as count
            FROM cohort_benchmarks cb
            LEFT JOIN analyses a ON cb.analysis_id = a.id
            LEFT JOIN users u ON a.student_id = u.id
            {where_clause}
            GROUP BY cb.department
            ORDER BY count DESC
        """, params)
        stats["department_breakdown"] = [dict(r) for r in cursor.fetchall()]

        return stats
    except Exception as e:
        logger.error(f"Error computing cohort statistics: {e}")
        return {
            "total_students": 0,
            "avg_ats": 0.0,
            "avg_match": 0.0,
            "avg_overall": 0.0,
            "max_overall": 0.0,
            "platinum_count": 0,
            "gold_count": 0,
            "silver_count": 0,
            "bronze_count": 0,
            "department_breakdown": []
        }
    finally:
        conn.close()




def get_user_placement_history(
    student_id: str = "default_student",
    company_filter: Optional[str] = None,
    sort_by: str = "date_desc"
) -> Dict[str, Any]:
    """
    Saves and retrieves comprehensive previous placement analysis history for a specific user (#149).
    Includes per-student data isolation, ATS score tracking, role match percentages,
    and historical performance metrics.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        query = """
            SELECT id, resume_filename, company_name, job_description, results_json,
                   student_id, version_number, created_at
            FROM analyses
            WHERE student_id = ?
        """
        params: List[Any] = [effective_student_id]

        if company_filter and company_filter.strip() and company_filter.strip().lower() not in ("all", "any"):
            query += " AND LOWER(company_name) LIKE ?"
            params.append(f"%{company_filter.strip().lower()}%")

        if sort_by == "score_desc":
            # Will sort in python after parsing ATS score
            query += " ORDER BY created_at DESC"
        elif sort_by == "date_asc":
            query += " ORDER BY created_at ASC"
        else:
            query += " ORDER BY created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        records = []
        ats_scores = []
        match_scores = []
        company_counts: Dict[str, int] = {}

        for row in rows:
            rec = dict(row)
            try:
                raw_json = rec.pop("results_json", "{}")
                parsed = json.loads(raw_json)
                rec["results"] = parsed

                # Score resolution
                ats = parsed.get("ats_score") or parsed.get("resume_analysis", {}).get("ats_score", 0)
                match = parsed.get("job_match_score") or parsed.get("job_match", {}).get("match_percentage", 0)

                rec["ats_score"] = int(ats)
                rec["match_percentage"] = int(match)
                ats_scores.append(rec["ats_score"])
                match_scores.append(rec["match_percentage"])

                # Extract extracted skills & missing skills count
                rec["matched_skills_count"] = len(parsed.get("matched_skills") or parsed.get("job_match", {}).get("matched_skills", []))
                rec["missing_skills_count"] = len(parsed.get("missing_skills") or parsed.get("job_match", {}).get("missing_skills", []))

                comp = rec.get("company_name", "General")
                company_counts[comp] = company_counts.get(comp, 0) + 1

            except Exception as parse_err:
                logger.warning(f"Failed to parse analysis payload for record {rec.get('id')}: {parse_err}")
                rec["results"] = {}
                rec["ats_score"] = 0
                rec["match_percentage"] = 0
                rec["matched_skills_count"] = 0
                rec["missing_skills_count"] = 0

            records.append(rec)

        if sort_by == "score_desc":
            records.sort(key=lambda x: x["ats_score"], reverse=True)

        avg_ats = round(sum(ats_scores) / len(ats_scores), 1) if ats_scores else 0.0
        avg_match = round(sum(match_scores) / len(match_scores), 1) if match_scores else 0.0
        highest_ats = max(ats_scores) if ats_scores else 0

        return {
            "student_id": effective_student_id,
            "total_analyses": len(records),
            "average_ats_score": avg_ats,
            "average_match_percentage": avg_match,
            "highest_ats_score": highest_ats,
            "company_counts": company_counts,
            "analyses": records
        }

    except Exception as e:
        logger.error(f"Error fetching placement analysis history for {student_id}: {e}")
        return {
            "student_id": student_id,
            "total_analyses": 0,
            "average_ats_score": 0.0,
            "average_match_percentage": 0.0,
            "highest_ats_score": 0,
            "company_counts": {},
            "analyses": []
        }
    finally:
        conn.close()


def get_student_interview_history(student_id: str = "default_student") -> List[Dict[str, Any]]:
    """
    Retrieves full mock interview session history for a student across all attempts (#144).
    Calculates completion rates, scores per question type (Technical, HR, Behavioral), and overall score.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                i.id AS session_id,
                i.analysis_id,
                i.current_question_index,
                i.questions_json,
                i.answers_json,
                i.completed,
                a.company_name,
                a.created_at AS analysis_date
            FROM interviews i
            JOIN analyses a ON i.analysis_id = a.id
            WHERE a.student_id = ?
            ORDER BY a.created_at DESC, i.id DESC
        """, (student_id,))
        rows = cursor.fetchall()

        history = []
        for r in rows:
            rec = dict(r)
            questions = json.loads(rec.get("questions_json", "[]") or "[]")
            answers = json.loads(rec.get("answers_json", "[]") or "[]")

            scores = []
            type_scores = {"Technical": [], "HR": [], "Behavioral": []}
            for ans in answers:
                eval_data = ans.get("evaluation", {})
                score = eval_data.get("score")
                if score is not None:
                    try:
                        score_val = float(score)
                        scores.append(score_val)
                        q_type = ans.get("type", "General")
                        if q_type in type_scores:
                            type_scores[q_type].append(score_val)
                    except (ValueError, TypeError):
                        pass

            avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
            technical_avg = round(sum(type_scores["Technical"]) / len(type_scores["Technical"]), 1) if type_scores["Technical"] else 0.0
            hr_avg = round(sum(type_scores["HR"]) / len(type_scores["HR"]), 1) if type_scores["HR"] else 0.0
            behavioral_avg = round(sum(type_scores["Behavioral"]) / len(type_scores["Behavioral"]), 1) if type_scores["Behavioral"] else 0.0

            # Extract interviewer persona style (#686)
            from utils.interviewer_style_service import get_style_config
            detected_style = None
            if questions and isinstance(questions, list) and len(questions) > 0:
                detected_style = questions[0].get("interviewer_style")
            if not detected_style and answers and isinstance(answers, list) and len(answers) > 0:
                detected_style = answers[0].get("interviewer_style") or answers[0].get("evaluation", {}).get("interviewer_style")
            style_cfg = get_style_config(detected_style)

            history.append({
                "session_id": rec["session_id"],
                "analysis_id": rec["analysis_id"],
                "company_name": rec.get("company_name", "General"),
                "total_questions": len(questions),
                "answered_questions": len(answers),
                "completed": bool(rec.get("completed", 0)),
                "average_score": avg_score,
                "technical_score": technical_avg,
                "hr_score": hr_avg,
                "behavioral_score": behavioral_avg,
                "date": rec.get("analysis_date", ""),
                "questions": questions,
                "answers": answers,
                "interviewer_style": style_cfg["id"],
                "interviewer_style_name": style_cfg["name"],
                "interviewer_style_badge": style_cfg["badge_class"],
                "interviewer_style_icon": style_cfg["icon"],
            })
        return history
    except Exception as e:
        logger.error(f"Error retrieving student interview history: {e}")
        return []
    finally:
        conn.close()


def get_student_interview_performance_analytics(student_id: str = "default_student") -> Dict[str, Any]:
    """
    Computes performance analytics, score trends, and strengths/weaknesses for mock interviews (#144).
    """
    history = get_student_interview_history(student_id=student_id)
    if not history:
        return {
            "total_sessions": 0,
            "completed_sessions": 0,
            "total_questions_answered": 0,
            "overall_average_score": 0.0,
            "technical_average": 0.0,
            "hr_average": 0.0,
            "behavioral_average": 0.0,
            "score_trend": [],
            "strengths": [],
            "weaknesses": [],
            "sessions": []
        }

    total_sessions = len(history)
    completed_sessions = sum(1 for s in history if s["completed"])
    total_answered = sum(s["answered_questions"] for s in history)

    all_scores = [s["average_score"] for s in history if s["average_score"] > 0]
    overall_avg = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0

    tech_scores = [s["technical_score"] for s in history if s["technical_score"] > 0]
    hr_scores = [s["hr_score"] for s in history if s["hr_score"] > 0]
    behav_scores = [s["behavioral_score"] for s in history if s["behavioral_score"] > 0]

    tech_avg = round(sum(tech_scores) / len(tech_scores), 1) if tech_scores else 0.0
    hr_avg = round(sum(hr_scores) / len(hr_scores), 1) if hr_scores else 0.0
    behav_avg = round(sum(behav_scores) / len(behav_scores), 1) if behav_scores else 0.0

    # Trend line sorted chronologically
    score_trend = [
        {
            "date": s.get("date", "")[:10],
            "company": s["company_name"],
            "score": s["average_score"],
            "session_id": s["session_id"]
        }
        for s in reversed(history) if s["average_score"] > 0
    ]

    # Strengths and Weaknesses analysis
    strengths = []
    weaknesses = []

    domain_eval = [
        ("Technical Concepts", tech_avg),
        ("HR and Culture Fit", hr_avg),
        ("Behavioral and STAR Structure", behav_avg)
    ]

    for name, avg in domain_eval:
        if avg >= 75.0:
            strengths.append(f"{name} ({avg}/100) - Strong grasp of core concepts and structured communication")
        elif avg > 0:
            weaknesses.append(f"{name} ({avg}/100) - Needs further practice with concise articulation and situational examples")

    if not strengths and overall_avg >= 70.0:
        strengths.append("Consistent practice and willingness to complete structured multi-round interviews.")
    if not weaknesses:
        weaknesses.append("Practice speaking with greater conciseness and quantifiable impact metrics.")

    return {
        "total_sessions": total_sessions,
        "completed_sessions": completed_sessions,
        "total_questions_answered": total_answered,
        "overall_average_score": overall_avg,
        "technical_average": tech_avg,
        "hr_average": hr_avg,
        "behavioral_average": behav_avg,
        "score_trend": score_trend,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "sessions": history
    }


def create_user(user_id: str, name: str, email: str, password_hash: str) -> bool:
    """Create a new user account."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (id, name, email, password_hash)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, name.strip(), email.strip().lower(), password_hash)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as exc:
        logger.error(f"Error creating user: {exc}")
        raise
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Retrieve a user by email address."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.strip().lower(),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_auth_session(user_id: str, client_label: str) -> Dict[str, str]:
    """Create an opaque, server-tracked session that can later be revoked."""
    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO auth_sessions
                (id, user_id, token_hash, client_label, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, token_hash, client_label or "Web browser", now, now),
        )
        conn.commit()
        return {"id": session_id, "token": token}
    finally:
        conn.close()


def get_active_auth_session(session_id: str, token: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Return a session only when its opaque token is valid and it has not been revoked."""
    if not session_id or not token or not user_id:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, user_id, client_label, created_at, last_seen_at
            FROM auth_sessions
            WHERE id = ? AND user_id = ? AND token_hash = ? AND revoked_at IS NULL
            """,
            (session_id, user_id, token_hash),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def touch_auth_session(session_id: str, user_id: str) -> None:
    """Update activity for a currently valid session without exposing client details."""
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (time.time(), session_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_active_auth_sessions(user_id: str, timeout_seconds: Optional[int] = None) -> List[Dict[str, Any]]:
    """List non-revoked sessions active within the configured inactivity window."""
    conn = get_db_connection()
    try:
        if timeout_seconds is not None and timeout_seconds > 0:
            cutoff = time.time() - timeout_seconds
            rows = conn.execute(
                """
                SELECT id, client_label, created_at, last_seen_at
                FROM auth_sessions
                WHERE user_id = ? AND revoked_at IS NULL AND last_seen_at >= ?
                ORDER BY last_seen_at DESC
                """,
                (user_id, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, client_label, created_at, last_seen_at
                FROM auth_sessions
                WHERE user_id = ? AND revoked_at IS NULL
                ORDER BY last_seen_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def revoke_auth_session(user_id: str, session_id: str) -> bool:
    """Revoke one session only when it belongs to the requesting user."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (time.time(), session_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def revoke_other_auth_sessions(user_id: str, current_session_id: str) -> int:
    """Revoke every active session for a user except the current one."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND id != ? AND revoked_at IS NULL
            """,
            (time.time(), user_id, current_session_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def revoke_all_auth_sessions(user_id: str) -> int:
    """Revoke every active session for a user (#473)."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (time.time(), user_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def change_user_password(user_id: str, new_password_hash: str, keep_session_id: Optional[str] = None) -> bool:
    """
    Updates user password and revokes all other (or all) active sessions (#473).
    If keep_session_id is provided, keeps that session active and revokes all other sessions.
    If keep_session_id is None, revokes all sessions for the user.
    """
    now = time.time()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
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
        conn.commit()
        return True
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a user by their ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def enable_user_two_factor(user_id: str, secret: str, hashed_backup_codes: List[str]) -> bool:
    """Enable two-factor authentication and store TOTP secret and recovery codes (#380)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
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
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def disable_user_two_factor(user_id: str) -> bool:
    """Disable two-factor authentication and clear TOTP secret and recovery codes (#380)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
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
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_user_backup_codes(user_id: str, hashed_backup_codes: List[str]) -> bool:
    """Update active backup recovery code hashes after one is consumed or regenerated (#380)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users
            SET two_factor_backup_codes = ?
            WHERE id = ?
            """,
            (json.dumps(hashed_backup_codes), user_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_user_two_factor_status(user_id: str) -> Dict[str, Any]:
    """Retrieve two-factor status and remaining backup codes count (#380)."""
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


def get_email_notification_preferences(student_id: str) -> Dict[str, Any]:
    """
    Retrieves candidate email notification configuration (#186, #298).
    Defaults to all enabled if no preference record exists yet.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_id = (student_id or "default_student").strip()
        cursor.execute(
            "SELECT * FROM email_notification_preferences WHERE student_id = ?",
            (effective_id,)
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
                "notify_deadline_reminder": bool(rec.get("notify_deadline_reminder", 1))
            }
        return {
            "student_id": effective_id,
            "email": "",
            "output_language": "en",
            "email_enabled": True,
            "notify_welcome": True,
            "notify_application_created": True,
            "notify_status_change": True,
            "notify_interview_reminder": True,
            "notify_deadline_reminder": True
        }
    except Exception as e:
        logger.error(f"Error fetching email preferences for {student_id}: {e}")
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
    finally:
        conn.close()


def update_email_notification_preferences(
    student_id: str,
    email: Optional[str] = None,
    output_language: str = "en",
    email_enabled: bool = True,
    notify_welcome: bool = True,
    notify_application_created: bool = True,
    notify_status_change: bool = True,
    notify_interview_reminder: bool = True,
    notify_deadline_reminder: bool = True
) -> bool:
    """
    Saves or updates email notification settings for a student (#186, #298).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_id = (student_id or "default_student").strip()
        cursor.execute("""
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
        """, (
            effective_id,
            email.strip() if email else None,
            (output_language or "en").strip().lower(),
            1 if email_enabled else 0,
            1 if notify_welcome else 0,
            1 if notify_application_created else 0,
            1 if notify_status_change else 0,
            1 if notify_interview_reminder else 0,
            1 if notify_deadline_reminder else 0
        ))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating email preferences for {student_id}: {e}")
        return False
    finally:
        conn.close()


def save_interview_experience(
    exp_id: str,
    student_id: str,
    candidate_name: str,
    company_name: str,
    role_title: str,
    interview_date: str,
    rounds: List[Dict[str, Any]],
    overall_verdict: str = "Offer",
    difficulty_level: str = "Medium",
    tips_and_preparation: Optional[str] = None
) -> str:
    """
    Saves a student-submitted company interview experience (#189).
    Never persists or displays sensitive contact info (phone/email).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_name = (candidate_name or "").strip() or "Anonymous Candidate"
        # Mask any accidental email or phone in name
        import re
        if "@" in effective_name or re.search(r"\d{7,}", effective_name):
            effective_name = "Anonymous Candidate"

        cursor.execute("""
            INSERT INTO interview_experiences (
                id, student_id, candidate_name, company_name, role_title,
                interview_date, rounds_json, overall_verdict, difficulty_level,
                tips_and_preparation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            exp_id,
            student_id.strip() if student_id else "default_student",
            effective_name,
            company_name.strip(),
            role_title.strip(),
            interview_date.strip(),
            json.dumps(rounds),
            overall_verdict.strip(),
            difficulty_level.strip(),
            (tips_and_preparation or "").strip()
        ))
        conn.commit()
        logger.info(f"Saved interview experience {exp_id} for company {company_name}")
        return exp_id
    except Exception as e:
        logger.error(f"Error saving interview experience {exp_id}: {e}")
        raise e
    finally:
        conn.close()


def get_interview_experiences(
    company_name: Optional[str] = None,
    category_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    sort_by: str = "date_desc",
    include_reported: bool = False
) -> List[Dict[str, Any]]:
    """
    Retrieves company interview experiences with search, sorting, and round filtering (#189, #693).
    Omits inappropriate/reported posts by default.
    """
    from utils.interview_helpful_service import ensure_helpful_votes_schema
    conn = get_db_connection()
    try:
        ensure_helpful_votes_schema(conn)
        cursor = conn.cursor()
        conditions = []
        params = []

        if not include_reported:
            conditions.append("reported_flag = 0")

        if company_name and company_name.strip() and company_name.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(company_name) = LOWER(?)")
            params.append(company_name.strip())

        if search_query and search_query.strip():
            kw = f"%{search_query.strip().lower()}%"
            conditions.append("(LOWER(company_name) LIKE ? OR LOWER(role_title) LIKE ? OR LOWER(tips_and_preparation) LIKE ?)")
            params.extend([kw, kw, kw])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        order_by = "ORDER BY interview_date DESC, created_at DESC"
        if sort_by in ("helpful_desc", "most_helpful"):
            order_by = "ORDER BY helpful_count DESC, interview_date DESC, created_at DESC"
        elif sort_by == "date_asc":
            order_by = "ORDER BY interview_date ASC, created_at ASC"

        query = f"SELECT * FROM interview_experiences {where_clause} {order_by}"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        experiences = []
        for r in rows:
            rec = dict(r)
            try:
                rec["rounds"] = json.loads(rec.get("rounds_json", "[]"))
            except Exception:
                rec["rounds"] = []

            # If category_filter is specified (e.g. Technical, Coding, HR), filter by round category
            if category_filter and category_filter.strip() and category_filter.strip().lower() not in ("all", "any"):
                target_cat = category_filter.strip().lower()
                has_cat = any(
                    target_cat in round_info.get("category", "").lower()
                    for round_info in rec["rounds"]
                )
                if not has_cat:
                    continue

            experiences.append(rec)

        return experiences
    except Exception as e:
        logger.error(f"Error fetching interview experiences: {e}")
        return []
    finally:
        conn.close()


def report_interview_experience(exp_id: str, reason: str) -> bool:
    """
    Flags an interview experience as reported for moderation review (#189).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE interview_experiences
            SET reported_flag = 1, report_reason = ?
            WHERE id = ?
        """, (reason.strip() if reason else "Inappropriate content", exp_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error reporting interview experience {exp_id}: {e}")
        return False
    finally:
        conn.close()


def save_calendar_event(
    event_id: str,
    student_id: str,
    title: str,
    event_type: str,
    event_date: str,
    event_time: Optional[str] = None,
    company_name: Optional[str] = None,
    description: Optional[str] = None,
    priority: str = "Medium",
    completed: bool = False
) -> str:
    """
    Creates or updates a candidate placement preparation calendar event (#190).
    Enforces per-student data isolation.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            INSERT OR REPLACE INTO preparation_calendar_events (
                id, student_id, title, event_type, event_date, event_time,
                company_name, description, priority, completed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id,
            effective_student_id,
            title.strip(),
            event_type.strip().lower(),
            event_date.strip(),
            (event_time or "").strip(),
            (company_name or "").strip(),
            (description or "").strip(),
            priority.strip(),
            1 if completed else 0
        ))
        conn.commit()
        logger.info(f"Saved calendar event {event_id} for student {effective_student_id}")
        return event_id
    except Exception as e:
        logger.error(f"Error saving calendar event {event_id}: {e}")
        raise e
    finally:
        conn.close()


def get_calendar_events(
    student_id: str,
    month: Optional[str] = None,
    event_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves all calendar preparation events for a student (#190).
    Allows filtering by YYYY-MM month prefix and event_type (interview, preparation, deadline).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        conditions = ["student_id = ?"]
        params = [effective_student_id]

        if month and len(month.strip()) == 7:
            conditions.append("event_date LIKE ?")
            params.append(f"{month.strip()}%")

        if event_type and event_type.strip() and event_type.strip().lower() not in ("all", "any"):
            conditions.append("event_type = ?")
            params.append(event_type.strip().lower())

        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"SELECT * FROM preparation_calendar_events {where_clause} ORDER BY event_date ASC, event_time ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error retrieving calendar events for {student_id}: {e}")
        return []
    finally:
        conn.close()


def update_calendar_event(
    event_id: str,
    student_id: str,
    title: str,
    event_type: str,
    event_date: str,
    event_time: Optional[str] = None,
    company_name: Optional[str] = None,
    description: Optional[str] = None,
    priority: str = "Medium",
    completed: bool = False
) -> bool:
    """
    Updates an existing calendar event owned by student_id (#190).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            UPDATE preparation_calendar_events
            SET title = ?, event_type = ?, event_date = ?, event_time = ?,
                company_name = ?, description = ?, priority = ?, completed = ?
            WHERE id = ? AND student_id = ?
        """, (
            title.strip(),
            event_type.strip().lower(),
            event_date.strip(),
            (event_time or "").strip(),
            (company_name or "").strip(),
            (description or "").strip(),
            priority.strip(),
            1 if completed else 0,
            event_id,
            effective_student_id
        ))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating calendar event {event_id}: {e}")
        return False
    finally:
        conn.close()


def delete_calendar_event(event_id: str, student_id: str) -> bool:
    """
    Deletes a calendar event owned by student_id (#190).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute(
            "DELETE FROM preparation_calendar_events WHERE id = ? AND student_id = ?",
            (event_id, effective_student_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting calendar event {event_id}: {e}")
        return False
    finally:
        conn.close()


def get_default_company_checklist(company_name: str) -> Dict[str, Any]:
    """
    Returns the foundational company checklist tasks structured across
    Aptitude, Coding, Technical Concepts, and HR rounds (#192).
    """
    comp = (company_name or "General").strip()

    return {
        "Aptitude & Logical Reasoning": [
            {"id": "apt_01", "task": f"Quantitative Aptitude: Speed, Distance, Work & Time for {comp}", "completed": False},
            {"id": "apt_02", "task": "Data Interpretation & Logical Puzzles (Seating arrangements & Syllogisms)", "completed": False},
            {"id": "apt_03", "task": "Verbal Ability & Reading Comprehension Practice", "completed": False}
        ],
        "Coding & Data Structures": [
            {"id": "code_01", "task": f"Top 20 Most Frequent LeetCode Questions asked at {comp}", "completed": False},
            {"id": "code_02", "task": "Arrays, Strings, HashMaps & Sliding Window problem sets", "completed": False},
            {"id": "code_03", "task": "Trees, Graphs & Dynamic Programming fundamentals", "completed": False},
            {"id": "code_04", "task": f"Timed 60-minute Online Assessment Simulation for {comp}", "completed": False}
        ],
        "Technical Concepts & CS Fundamentals": [
            {"id": "tech_01", "task": "DBMS & SQL: Indexing, Transactions, ACID Properties, Normalization", "completed": False},
            {"id": "tech_02", "task": "Operating Systems: Process vs Thread, Virtual Memory, Concurrency & Deadlocks", "completed": False},
            {"id": "tech_03", "task": "Computer Networks: TCP/IP vs UDP, DNS, HTTP/HTTPS, WebSockets", "completed": False},
            {"id": "tech_04", "task": "Object Oriented Programming (OOP) & Clean System Design principles", "completed": False}
        ],
        "HR, Culture & Behavioral (STAR)": [
            {"id": "hr_01", "task": f"Company Deep Dive: Core values, business model, and leadership principles of {comp}", "completed": False},
            {"id": "hr_02", "task": "Prepare 'Tell Me About Yourself' 90-second elevator pitch", "completed": False},
            {"id": "hr_03", "task": "Structure 3 impactful project stories using the STAR methodology", "completed": False},
            {"id": "hr_04", "task": f"Prepare 3 thoughtful questions to ask the interviewer about {comp}", "completed": False}
        ]
    }


def get_company_checklist(student_id: str, company_name: str) -> Dict[str, Any]:
    """
    Retrieves or initializes the student's company-specific preparation checklist (#192).
    Calculates category progress and overall completion percentage.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        comp = (company_name or "Google").strip()

        cursor.execute("""
            SELECT * FROM company_preparation_checklists
            WHERE student_id = ? AND LOWER(company_name) = LOWER(?)
        """, (effective_student_id, comp))
        row = cursor.fetchone()

        if row:
            rec = dict(row)
            try:
                checklist_data = json.loads(rec.get("checklist_json", "{}"))
            except Exception:
                checklist_data = get_default_company_checklist(comp)
        else:
            checklist_data = get_default_company_checklist(comp)
            # Initialize record
            save_company_checklist(effective_student_id, comp, checklist_data)

        # Compute completion statistics
        total_tasks = 0
        completed_tasks = 0
        category_stats = {}

        for cat_name, task_list in checklist_data.items():
            cat_total = len(task_list)
            cat_done = sum(1 for t in task_list if t.get("completed"))
            total_tasks += cat_total
            completed_tasks += cat_done
            category_stats[cat_name] = {
                "total": cat_total,
                "completed": cat_done,
                "percentage": round((cat_done / cat_total * 100), 1) if cat_total else 0.0
            }

        overall_percentage = round((completed_tasks / total_tasks * 100), 1) if total_tasks else 0.0

        return {
            "student_id": effective_student_id,
            "company_name": comp,
            "checklist": checklist_data,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "overall_percentage": overall_percentage,
            "category_stats": category_stats
        }

    except Exception as e:
        logger.error(f"Error fetching company checklist for {company_name}: {e}")
        default_data = get_default_company_checklist(company_name)
        return {
            "student_id": student_id,
            "company_name": company_name,
            "checklist": default_data,
            "total_tasks": 15,
            "completed_tasks": 0,
            "overall_percentage": 0.0,
            "category_stats": {}
        }
    finally:
        conn.close()


def save_company_checklist(student_id: str, company_name: str, checklist_data: Dict[str, Any]) -> bool:
    """
    Saves candidate progress for a specific target company (#192).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        comp = (company_name or "Google").strip()

        total_tasks = 0
        completed_tasks = 0
        for task_list in checklist_data.values():
            total_tasks += len(task_list)
            completed_tasks += sum(1 for t in task_list if t.get("completed"))

        pct = round((completed_tasks / total_tasks * 100), 1) if total_tasks else 0.0
        rec_id = f"chk-{effective_student_id}-{comp.lower().replace(' ', '_')}"

        cursor.execute("""
            INSERT INTO company_preparation_checklists (
                id, student_id, company_name, checklist_json, completion_percentage, updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id, company_name) DO UPDATE SET
                checklist_json = excluded.checklist_json,
                completion_percentage = excluded.completion_percentage,
                updated_at = CURRENT_TIMESTAMP
        """, (rec_id, effective_student_id, comp, json.dumps(checklist_data), pct))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving checklist for {company_name}: {e}")
        return False
    finally:
        conn.close()


def reset_company_checklist(student_id: str, company_name: str) -> bool:
    """
    Resets all tasks to uncompleted for the target company checklist (#192).
    """
    default_data = get_default_company_checklist(company_name)
    return save_company_checklist(student_id, company_name, default_data)


def save_coding_submission(
    sub_id: str,
    student_id: str,
    question_title: str,
    language: str,
    code: str,
    passed: bool,
    score: int,
    tests_passed: int = 0,
    total_tests: int = 0,
    test_results: Optional[List[Dict[str, Any]]] = None,
    complexity_analysis: Optional[str] = None,
    feedback: Optional[str] = None,
    company_name: str = "General",
    analysis_id: Optional[str] = None
) -> str:
    """
    Persists a coding assessment submission attempt (#194).
    Enforces per-student isolation.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            INSERT INTO coding_submissions (
                id, student_id, analysis_id, question_title, company_name,
                language, code, passed, score, tests_passed, total_tests,
                test_results_json, complexity_analysis, feedback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sub_id,
            effective_student_id,
            analysis_id,
            question_title.strip(),
            company_name.strip(),
            language.strip().lower(),
            code,
            1 if passed else 0,
            int(score),
            int(tests_passed),
            int(total_tests),
            json.dumps(test_results or []),
            complexity_analysis or "",
            feedback or ""
        ))
        conn.commit()
        logger.info(f"Saved coding submission {sub_id} for student {effective_student_id}")
        return sub_id
    
        # Record preparation activity for candidate streak tracking (#236)
        try:
            record_preparation_activity(
                student_id=effective_student_id,
                activity_type="coding_practice",
                metadata={"submission_id": sub_id, "question_title": question_title, "score": score, "passed": passed}
            )
        except Exception as streak_err:
            logger.warning(f"Failed to record preparation activity on save_coding_submission: {streak_err}")
    except Exception as e:
        logger.error(f"Error saving coding submission {sub_id}: {e}")
        raise e
    finally:
        conn.close()


def get_coding_submissions(
    student_id: str,
    question_title: Optional[str] = None,
    language_filter: Optional[str] = None,
    status_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves all coding assessment submission attempts for a student (#194).
    Supports question matching to compare attempts over time.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        conditions = ["student_id = ?"]
        params: List[Any] = [effective_student_id]

        if question_title and question_title.strip():
            conditions.append("LOWER(question_title) = LOWER(?)")
            params.append(question_title.strip())

        if language_filter and language_filter.strip() and language_filter.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(language) = LOWER(?)")
            params.append(language_filter.strip())

        if status_filter and status_filter.strip() and status_filter.strip().lower() not in ("all", "any"):
            if status_filter.strip().lower() == "passed":
                conditions.append("passed = 1")
            elif status_filter.strip().lower() == "failed":
                conditions.append("passed = 0")

        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"SELECT * FROM coding_submissions {where_clause} ORDER BY submitted_at DESC, id DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        submissions = []
        for r in rows:
            rec = dict(r)
            try:
                rec["test_results"] = json.loads(rec.get("test_results_json", "[]"))
            except Exception:
                rec["test_results"] = []
            rec["passed"] = bool(rec.get("passed", 0))
            submissions.append(rec)

        return submissions
    except Exception as e:
        logger.error(f"Error retrieving coding submissions for {student_id}: {e}")
        return []
    finally:
        conn.close()


def save_skill_self_assessment(
    student_id: str,
    skill_name: str,
    category: str,
    proficiency_level: str,
    years_experience: float = 0.0,
    notes: str = ""
) -> Dict[str, Any]:
    """Saves or updates a student's technical skill self-assessment."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        record_id = str(uuid.uuid4())
        normalized_student_id = (student_id or "default_student").strip()
        normalized_skill = skill_name.strip()
        normalized_category = (category or "General").strip()
        valid_levels = ["Beginner", "Intermediate", "Advanced", "Expert"]
        level = proficiency_level if proficiency_level in valid_levels else "Intermediate"

        cursor.execute("""
            INSERT INTO technical_self_assessments (
                id, student_id, skill_name, category, proficiency_level, years_experience, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id, skill_name) DO UPDATE SET
                category = excluded.category,
                proficiency_level = excluded.proficiency_level,
                years_experience = excluded.years_experience,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (
            record_id,
            normalized_student_id,
            normalized_skill,
            normalized_category,
            level,
            float(years_experience or 0.0),
            notes.strip() if notes else ""
        ))
        conn.commit()

        cursor.execute(
            "SELECT * FROM technical_self_assessments WHERE student_id = ? AND skill_name = ?",
            (normalized_student_id, normalized_skill)
        )
        row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_student_skill_self_assessments(student_id: str) -> List[Dict[str, Any]]:
    """Retrieves all technical skill self-assessments for a student."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        normalized_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            SELECT * FROM technical_self_assessments
            WHERE student_id = ?
            ORDER BY category ASC, skill_name ASC
        """, (normalized_student_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def delete_skill_self_assessment(student_id: str, skill_name: str) -> bool:
    """Deletes a student's self-assessment entry for a skill."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        normalized_student_id = (student_id or "default_student").strip()
        cursor.execute(
            "DELETE FROM technical_self_assessments WHERE student_id = ? AND skill_name = ?",
            (normalized_student_id, skill_name.strip())
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_default_technical_skills_catalog() -> Dict[str, List[str]]:
    """Returns curated placement technical skills catalog grouped by category."""
    return {
        "Programming Languages": ["Python", "Java", "C++", "JavaScript", "TypeScript", "Go"],
        "Web & Frameworks": ["React", "Node.js", "Flask", "Django", "Spring Boot", "HTML5/CSS3"],
        "Data & Databases": ["SQL", "PostgreSQL", "MongoDB", "Redis", "Data Structures & Algorithms"],
        "Cloud & DevOps": ["Git & GitHub", "Docker", "Kubernetes", "AWS", "CI/CD Pipelines", "Linux"],
        "Core CS Fundamentals": ["Operating Systems", "DBMS", "Computer Networks", "System Design", "OOP"]
    }


def submit_user_feedback(
    student_id: str,
    rating: int,
    category: str,
    comment: str,
    feedback_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Records student experience feedback with rating (1-5), category, and comments (#197).
    """
    import uuid
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        f_id = feedback_id or f"fb-{uuid.uuid4().hex[:12]}"
        normalized_student_id = (student_id or "default_student").strip()
        clamped_rating = max(1, min(5, int(rating)))
        norm_category = (category or "General Experience").strip()
        norm_comment = (comment or "").strip()

        cursor.execute("""
            INSERT INTO user_feedback (id, student_id, rating, category, comment, created_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (f_id, normalized_student_id, clamped_rating, norm_category, norm_comment))
        conn.commit()

        cursor.execute("SELECT * FROM user_feedback WHERE id = ?", (f_id,))
        row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_user_feedback_list(
    student_id: Optional[str] = None,
    category_filter: Optional[str] = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Retrieves recorded user feedback entries, optionally filtered by student or feature category (#197).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        query = "SELECT * FROM user_feedback WHERE 1=1"
        params: List[Any] = []

        if student_id and student_id.strip():
            query += " AND student_id = ?"
            params.append(student_id.strip())

        if category_filter and category_filter.strip() and category_filter.strip().lower() not in ("all", "any"):
            query += " AND LOWER(category) = LOWER(?)"
            params.append(category_filter.strip())

        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_feedback_metrics_summary() -> Dict[str, Any]:
    """
    Calculates aggregated satisfaction scores and category distribution (#197).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as total_feedback,
                COALESCE(AVG(rating), 0) as avg_rating,
                COALESCE(SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END), 0) as five_star_count,
                COALESCE(SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END), 0) as four_star_count,
                COALESCE(SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END), 0) as three_star_count,
                COALESCE(SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END), 0) as two_star_count,
                COALESCE(SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END), 0) as one_star_count
            FROM user_feedback
        """)
        row = cursor.fetchone()
        stats = dict(row) if row else {}
        stats["avg_rating"] = round(float(stats.get("avg_rating", 0.0)), 2)

        cursor.execute("""
            SELECT category, COUNT(*) as count, AVG(rating) as category_avg
            FROM user_feedback
            GROUP BY category
            ORDER BY count DESC
        """)
        stats["category_breakdown"] = [
            {
                "category": r["category"],
                "count": r["count"],
                "avg_rating": round(float(r["category_avg"] or 0), 1)
            }
            for r in cursor.fetchall()
        ]
        return stats
    finally:
        conn.close()


def save_student_profile(
    student_id: str,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    department: Optional[str] = None,
    grad_year: Optional[int] = None,
    cgpa: Optional[float] = None,
    target_role: Optional[str] = None,
    target_company: Optional[str] = None,
    skills: Optional[List[str]] = None,
    linkedin_url: Optional[str] = None,
    github_url: Optional[str] = None,
    has_resume_uploaded: Optional[bool] = None,
    college: Optional[str] = None
) -> Dict[str, Any]:
    """
    Saves or updates a candidate's placement profile (#199, #654).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        s_id = (student_id or "default_student").strip()
        cursor.execute("SELECT * FROM student_profiles WHERE student_id = ?", (s_id,))
        existing = cursor.fetchone()

        skills_json = json.dumps(skills) if skills is not None else None

        if existing:
            rec = dict(existing)
            u_name = full_name if full_name is not None else rec.get("full_name")
            u_email = email if email is not None else rec.get("email")
            u_phone = phone if phone is not None else rec.get("phone")
            u_dept = department if department is not None else rec.get("department")
            u_year = grad_year if grad_year is not None else rec.get("grad_year")
            u_cgpa = cgpa if cgpa is not None else rec.get("cgpa")
            u_role = target_role if target_role is not None else rec.get("target_role")
            u_comp = target_company if target_company is not None else rec.get("target_company")
            u_skills = skills_json if skills_json is not None else rec.get("skills_json")
            u_li = linkedin_url if linkedin_url is not None else rec.get("linkedin_url")
            u_gh = github_url if github_url is not None else rec.get("github_url")
            u_resume = int(has_resume_uploaded) if has_resume_uploaded is not None else rec.get("has_resume_uploaded", 0)
            u_college = college if college is not None else rec.get("college")

            cursor.execute("""
                UPDATE student_profiles SET
                    full_name = ?, email = ?, phone = ?, department = ?,
                    grad_year = ?, cgpa = ?, target_role = ?, target_company = ?,
                    skills_json = ?, linkedin_url = ?, github_url = ?,
                    has_resume_uploaded = ?, college = ?, updated_at = CURRENT_TIMESTAMP
                WHERE student_id = ?
            """, (
                u_name, u_email, u_phone, u_dept,
                u_year, u_cgpa, u_role, u_comp,
                u_skills, u_li, u_gh, u_resume, u_college, s_id
            ))
        else:
            cursor.execute("""
                INSERT INTO student_profiles (
                    student_id, full_name, email, phone, department,
                    grad_year, cgpa, target_role, target_company,
                    skills_json, linkedin_url, github_url, has_resume_uploaded,
                    college, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                s_id, full_name, email, phone, department,
                grad_year, cgpa, target_role, target_company,
                skills_json, linkedin_url, github_url,
                int(has_resume_uploaded or 0),
                college
            ))
        conn.commit()

        cursor.execute("SELECT * FROM student_profiles WHERE student_id = ?", (s_id,))
        saved = dict(cursor.fetchone())
        if saved.get("skills_json"):
            try:
                saved["skills"] = json.loads(saved["skills_json"])
            except Exception:
                saved["skills"] = []
        else:
            saved["skills"] = []
        return saved
    finally:
        conn.close()


def get_student_profile(student_id: str) -> Dict[str, Any]:
    """
    Retrieves student profile record by student_id (#199).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        s_id = (student_id or "default_student").strip()
        cursor.execute("SELECT * FROM student_profiles WHERE student_id = ?", (s_id,))
        row = cursor.fetchone()
        if not row:
            # Check users table fallback for name/email
            cursor.execute("SELECT name, email FROM users WHERE id = ?", (s_id,))
            user_row = cursor.fetchone()
            return {
                "student_id": s_id,
                "full_name": user_row["name"] if user_row else "",
                "email": user_row["email"] if user_row else "",
                "phone": "",
                "department": "",
                "grad_year": None,
                "cgpa": None,
                "target_role": "",
                "target_company": "",
                "skills": [],
                "linkedin_url": "",
                "github_url": "",
                "has_resume_uploaded": 0
            }

        data = dict(row)
        if data.get("skills_json"):
            try:
                data["skills"] = json.loads(data["skills_json"])
            except Exception:
                data["skills"] = []
        else:
            data["skills"] = []
        return data
    finally:
        conn.close()


def calculate_profile_completion(student_id: str) -> Dict[str, Any]:
    """
    Evaluates profile completeness across key placement readiness criteria (#199):
    - Basic Information (Full Name, Email, Phone): 20%
    - Academic Background (Department, Grad Year, CGPA): 25%
    - Career Aspirations (Target Role, Target Company): 20%
    - Technical Competencies (At least 3 Skills): 15%
    - Professional Presence & Resume (Resume Upload, LinkedIn, GitHub): 20%
    """
    profile = get_student_profile(student_id)

    # Also check if student has analyses uploaded if has_resume_uploaded flag is 0
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM analyses WHERE student_id = ?", (student_id.strip(),))
        has_analyses = cursor.fetchone()[0] > 0
    finally:
        conn.close()

    criteria = []

    # 1. Full name & contact
    has_name = bool((profile.get("full_name") or "").strip())
    has_email = bool((profile.get("email") or "").strip())
    has_phone = bool((profile.get("phone") or "").strip())
    basic_done = has_name and has_email
    criteria.append({
        "category": "Basic Contact Information",
        "title": "Full Name & Email",
        "weight": 15,
        "completed": basic_done,
        "missing_items": [f for f, ok in [("Full Name", has_name), ("Email Address", has_email)] if not ok]
    })

    # 2. Phone number
    criteria.append({
        "category": "Basic Contact Information",
        "title": "Contact Phone Number",
        "weight": 5,
        "completed": has_phone,
        "missing_items": [] if has_phone else ["Phone Number"]
    })

    # 3. Department & Graduation Year
    has_dept = bool((profile.get("department") or "").strip())
    has_year = bool(profile.get("grad_year") and int(profile.get("grad_year")) >= 2020)
    has_cgpa = bool(profile.get("cgpa") and float(profile.get("cgpa")) > 0)
    criteria.append({
        "category": "Academic Background",
        "title": "Department & Graduation Year",
        "weight": 15,
        "completed": has_dept and has_year,
        "missing_items": [f for f, ok in [("Department", has_dept), ("Graduation Year", has_year)] if not ok]
    })

    # 4. Academic CGPA
    criteria.append({
        "category": "Academic Background",
        "title": "Undergraduate CGPA",
        "weight": 10,
        "completed": has_cgpa,
        "missing_items": [] if has_cgpa else ["CGPA"]
    })

    # 5. Career Target
    has_role = bool((profile.get("target_role") or "").strip())
    has_comp = bool((profile.get("target_company") or "").strip())
    criteria.append({
        "category": "Placement Preferences",
        "title": "Target Role & Dream Company",
        "weight": 20,
        "completed": has_role and has_comp,
        "missing_items": [f for f, ok in [("Target Role", has_role), ("Target Company", has_comp)] if not ok]
    })

    # 6. Technical Skills
    skills_list = profile.get("skills", [])
    has_skills = len(skills_list) >= 3
    criteria.append({
        "category": "Technical Competencies",
        "title": "At least 3 Key Technical Skills",
        "weight": 15,
        "completed": has_skills,
        "missing_items": [] if has_skills else [f"Add at least {3 - len(skills_list)} more skill(s)"]
    })

    # 7. Resume & Professional Links
    resume_ready = bool(profile.get("has_resume_uploaded")) or has_analyses
    has_social = bool((profile.get("linkedin_url") or "").strip()) or bool((profile.get("github_url") or "").strip())
    criteria.append({
        "category": "Placement Portfolio",
        "title": "Resume Upload & Developer Links",
        "weight": 20,
        "completed": resume_ready and has_social,
        "missing_items": [f for f, ok in [("Resume Upload", resume_ready), ("LinkedIn/GitHub URL", has_social)] if not ok]
    })

    total_weight = sum(c["weight"] for c in criteria)
    earned_weight = sum(c["weight"] for c in criteria if c["completed"])
    percentage = round((earned_weight / total_weight) * 100) if total_weight > 0 else 0

    all_missing = []
    for c in criteria:
        if not c["completed"]:
            all_missing.extend(c["missing_items"])

    return {
        "student_id": student_id,
        "completion_percentage": percentage,
        "is_complete": percentage >= 100,
        "criteria": criteria,
        "missing_fields": all_missing,
        "profile": profile
    }


def save_coding_bookmark(
    student_id: str,
    problem_id: str,
    problem_data: Dict[str, Any]
) -> bool:
    """
    Saves a recommended coding practice question to the candidate's bookmarked collection (#234).
    Persists problem metadata across sessions and handles duplicates idempotently.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        p_id = str(problem_id).strip()
        data_json = json.dumps(problem_data)

        bookmark_id = str(uuid.uuid4())

        cursor.execute("""
            INSERT INTO bookmarked_coding_questions (
                id, student_id, problem_id, problem_data, created_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id, problem_id) DO UPDATE SET
                problem_data = excluded.problem_data,
                created_at = CURRENT_TIMESTAMP
        """, (bookmark_id, effective_student_id, p_id, data_json))
        conn.commit()
        logger.info(f"Saved coding bookmark for student {effective_student_id}, problem {p_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving coding bookmark for problem {problem_id}: {e}")
        return False
    finally:
        conn.close()


def remove_coding_bookmark(
    student_id: str,
    problem_id: str
) -> bool:
    """
    Removes a bookmarked coding question for a candidate (#234).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        p_id = str(problem_id).strip()

        cursor.execute("""
            DELETE FROM bookmarked_coding_questions
            WHERE student_id = ? AND problem_id = ?
        """, (effective_student_id, p_id))
        conn.commit()
        logger.info(f"Removed coding bookmark for student {effective_student_id}, problem {p_id}")
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing coding bookmark for problem {problem_id}: {e}")
        return False
    finally:
        conn.close()


def get_coding_bookmarks(
    student_id: str
) -> List[Dict[str, Any]]:
    """
    Retrieves all bookmarked coding questions for a student (#234).
    Returns list of deserialized problem dictionaries ordered by bookmark date.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        cursor.execute("""
            SELECT id, student_id, problem_id, problem_data, created_at
            FROM bookmarked_coding_questions
            WHERE student_id = ?
            ORDER BY created_at DESC
        """, (effective_student_id,))
        rows = cursor.fetchall()

        bookmarks = []
        for row in rows:
            rec = dict(row)
            try:
                prob = json.loads(rec.get("problem_data", "{}"))
            except Exception:
                prob = {}
            if "id" not in prob or not prob["id"]:
                prob["id"] = rec.get("problem_id")
            prob["bookmark_id"] = rec.get("id")
            prob["bookmarked_at"] = rec.get("created_at")
            bookmarks.append(prob)
        return bookmarks
    except Exception as e:
        logger.error(f"Error fetching coding bookmarks for student {student_id}: {e}")
        return []
    finally:
        conn.close()


def is_coding_bookmarked(
    student_id: str,
    problem_id: str
) -> bool:
    """
    Checks if a coding practice question is bookmarked by the student (#234).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        p_id = str(problem_id).strip()

        cursor.execute("""
            SELECT 1 FROM bookmarked_coding_questions
            WHERE student_id = ? AND problem_id = ?
            LIMIT 1
        """, (effective_student_id, p_id))
        return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking bookmark status for problem {problem_id}: {e}")
        return False
    finally:
        conn.close()


MAX_BOOKMARKS_PER_STUDENT = 50
GOOD_SCORE_THRESHOLD = 75.0
MASTERY_GOOD_SCORE_COUNT = 2


def save_interview_bookmark(
    student_id: str,
    question_id: str,
    question_text: str,
    category: str = "Technical",
    company_name: str = "General",
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    last_score: Optional[float] = None
) -> bool:
    """
    Saves an interview practice question to the candidate's bookmarked collection (#358, #846).
    Supports categories: Technical, Behavioral, HR, Company-Specific.
    Enforces maximum limit of 50 bookmarked questions per student.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        q_id = str(question_id).strip()
        q_text = str(question_text).strip()
        if not q_text:
            return False
        if not q_id:
            import hashlib
            q_id = f"iq-{hashlib.sha256(q_text.encode('utf-8')).hexdigest()[:12]}"
        cat = (category or "Technical").strip()
        comp = (company_name or "General").strip()
        user_notes = (notes or "").strip()
        meta_json = json.dumps(metadata or {})
        b_id = str(uuid.uuid4())

        # Check existing bookmark
        cursor.execute(
            "SELECT id FROM bookmarked_interview_questions WHERE student_id = ? AND question_id = ?",
            (effective_student_id, q_id)
        )
        exists = cursor.fetchone() is not None

        if not exists:
            cursor.execute(
                "SELECT COUNT(*) FROM bookmarked_interview_questions WHERE student_id = ?",
                (effective_student_id,)
            )
            cnt = cursor.fetchone()[0]
            if cnt >= MAX_BOOKMARKS_PER_STUDENT:
                logger.warning(
                    f"Student {effective_student_id} reached bookmark limit of {MAX_BOOKMARKS_PER_STUDENT} questions"
                )
                return False

        cursor.execute("""
            INSERT INTO bookmarked_interview_questions (
                id, student_id, question_id, question_text, category, company_name, notes, metadata_json, last_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id, question_id) DO UPDATE SET
                question_text = excluded.question_text,
                category = excluded.category,
                company_name = excluded.company_name,
                notes = excluded.notes,
                metadata_json = excluded.metadata_json,
                last_score = COALESCE(excluded.last_score, bookmarked_interview_questions.last_score),
                created_at = CURRENT_TIMESTAMP
        """, (b_id, effective_student_id, q_id, q_text, cat, comp, user_notes, meta_json, last_score))
        conn.commit()
        logger.info(f"Saved interview bookmark for student {effective_student_id}, question {q_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving interview bookmark for question {question_id}: {e}")
        return False
    finally:
        conn.close()


def remove_interview_bookmark(
    student_id: str,
    question_id: str
) -> bool:
    """
    Removes a bookmarked interview question for a candidate (#358).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        q_id = str(question_id).strip()

        cursor.execute("""
            DELETE FROM bookmarked_interview_questions
            WHERE student_id = ? AND question_id = ?
        """, (effective_student_id, q_id))
        conn.commit()
        logger.info(f"Removed interview bookmark for student {effective_student_id}, question {q_id}")
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing interview bookmark for question {question_id}: {e}")
        return False
    finally:
        conn.close()


def get_interview_bookmarks(
    student_id: str,
    category: Optional[str] = None,
    practiced_only: Optional[bool] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves all bookmarked interview questions for a student (#358, #359).
    Optionally filters by category (Technical, Behavioral, HR, Company-Specific)
    and practiced status.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        conditions = ["student_id = ?"]
        params = [effective_student_id]

        if category and category.strip().lower() not in ("all", ""):
            conditions.append("LOWER(category) = LOWER(?)")
            params.append(category.strip())

        if practiced_only is True:
            conditions.append("is_practiced = 1")
        elif practiced_only is False:
            conditions.append("is_practiced = 0")

        query = f"""
            SELECT id, student_id, question_id, question_text, category, company_name, notes, metadata_json,
                   is_practiced, last_practiced_at, practice_answer, practice_evaluation_json,
                   last_score, good_score_count, created_at
            FROM bookmarked_interview_questions
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
        """
        cursor.execute(query, tuple(params))

        rows = cursor.fetchall()
        bookmarks = []
        for row in rows:
            rec = dict(row)
            try:
                rec["metadata"] = json.loads(rec.get("metadata_json", "{}"))
            except Exception:
                rec["metadata"] = {}
            try:
                rec["practice_evaluation"] = json.loads(rec.get("practice_evaluation_json", "{}"))
            except Exception:
                rec["practice_evaluation"] = {}
            rec["is_practiced"] = bool(rec.get("is_practiced", 0))
            rec["last_score"] = rec.get("last_score")
            rec["good_score_count"] = int(rec.get("good_score_count") or 0)
            bookmarks.append(rec)
        return bookmarks
    except Exception as e:
        logger.error(f"Error fetching interview bookmarks for student {student_id}: {e}")
        return []
    finally:
        conn.close()


def get_interview_bookmark_by_id(
    student_id: str,
    question_id: str
) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single bookmarked interview question for a candidate (#359).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        q_id = str(question_id).strip()

        cursor.execute("""
            SELECT id, student_id, question_id, question_text, category, company_name, notes, metadata_json,
                   is_practiced, last_practiced_at, practice_answer, practice_evaluation_json,
                   last_score, good_score_count, created_at
            FROM bookmarked_interview_questions
            WHERE student_id = ? AND question_id = ?
            LIMIT 1
        """, (effective_student_id, q_id))

        row = cursor.fetchone()
        if not row:
            return None
        rec = dict(row)
        try:
            rec["metadata"] = json.loads(rec.get("metadata_json", "{}"))
        except Exception:
            rec["metadata"] = {}
        try:
            rec["practice_evaluation"] = json.loads(rec.get("practice_evaluation_json", "{}"))
        except Exception:
            rec["practice_evaluation"] = {}
        rec["is_practiced"] = bool(rec.get("is_practiced", 0))
        rec["last_score"] = rec.get("last_score")
        rec["good_score_count"] = int(rec.get("good_score_count") or 0)
        return rec
    except Exception as e:
        logger.error(f"Error fetching bookmark {question_id} for student {student_id}: {e}")
        return None
    finally:
        conn.close()


def record_bookmark_score(
    student_id: str,
    question_id: str,
    score: float
) -> Dict[str, Any]:
    """
    Records student's latest score for a bookmarked interview question (#846).
    If score >= 75.0, increments good_score_count.
    Once good_score_count >= 2, automatically removes the question from the saved list.
    """
    from db.interviews import record_bookmark_score as _db_record_bookmark_score
    db_path = getattr(config, "DATABASE_PATH", None) or getattr(Config, "DATABASE_PATH", None)
    return _db_record_bookmark_score(student_id=student_id, question_id=question_id, score=score, db_path=db_path)


def update_interview_bookmark_practice(
    student_id: str,
    question_id: str,
    is_practiced: bool = True,
    answer: str = "",
    evaluation: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Updates the practice status and optionally stores the practice answer and evaluation (#359).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        q_id = str(question_id).strip()
        practiced_int = 1 if is_practiced else 0
        eval_json = json.dumps(evaluation or {})

        cursor.execute("""
            UPDATE bookmarked_interview_questions
            SET is_practiced = ?,
                last_practiced_at = CURRENT_TIMESTAMP,
                practice_answer = COALESCE(NULLIF(?, ''), practice_answer),
                practice_evaluation_json = CASE WHEN ? != '{}' THEN ? ELSE practice_evaluation_json END
            WHERE student_id = ? AND question_id = ?
        """, (practiced_int, answer or "", eval_json, eval_json, effective_student_id, q_id))
        conn.commit()
        logger.info(f"Updated practice state for student {effective_student_id}, question {q_id} to {practiced_int}")
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating practice state for question {question_id}: {e}")
        return False
    finally:
        conn.close()


def reset_interview_bookmarks_practice(
    student_id: str,
    question_id: Optional[str] = None
) -> bool:
    """
    Resets practice progress for a specific question or all questions for a student (#359).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        if question_id:
            cursor.execute("""
                UPDATE bookmarked_interview_questions
                SET is_practiced = 0,
                    practice_answer = '',
                    practice_evaluation_json = '{}'
                WHERE student_id = ? AND question_id = ?
            """, (effective_student_id, str(question_id).strip()))
        else:
            cursor.execute("""
                UPDATE bookmarked_interview_questions
                SET is_practiced = 0,
                    practice_answer = '',
                    practice_evaluation_json = '{}'
                WHERE student_id = ?
            """, (effective_student_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error resetting practice progress for student {student_id}: {e}")
        return False
    finally:
        conn.close()


def get_interview_bookmark_practice_stats(
    student_id: str
) -> Dict[str, Any]:
    """
    Calculates practice progress metrics across bookmarked questions (#359).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_practiced = 1 THEN 1 ELSE 0 END) as practiced,
                SUM(CASE WHEN is_practiced = 0 THEN 1 ELSE 0 END) as unpracticed
            FROM bookmarked_interview_questions
            WHERE student_id = ?
        """, (effective_student_id,))
        row = cursor.fetchone()
        total = row["total"] if row and row["total"] else 0
        practiced = row["practiced"] if row and row["practiced"] else 0
        unpracticed = row["unpracticed"] if row and row["unpracticed"] else 0
        pct = round((practiced / total) * 100, 1) if total > 0 else 0.0

        return {
            "total": total,
            "practiced": practiced,
            "unpracticed": unpracticed,
            "percentage": pct
        }
    except Exception as e:
        logger.error(f"Error calculating practice stats for student {student_id}: {e}")
        return {"total": 0, "practiced": 0, "unpracticed": 0, "percentage": 0.0}
    finally:
        conn.close()


def is_interview_bookmarked(
    student_id: str,
    question_id: str
) -> bool:
    """
    Checks if an interview question is bookmarked by the student (#358).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        q_id = str(question_id).strip()

        cursor.execute("""
            SELECT 1 FROM bookmarked_interview_questions
            WHERE student_id = ? AND question_id = ?
            LIMIT 1
        """, (effective_student_id, q_id))
        return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking interview bookmark status for question {question_id}: {e}")
        return False
    finally:
        conn.close()


def create_preparation_note(
    student_id: str,
    title: str,
    description: str,
    category: str = "Technical"
) -> Dict[str, Any]:
    """
    Creates a new private interview preparation note for a student (#235).
    Enforces student isolation and validates category.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        note_id = f"note-{uuid.uuid4().hex[:12]}"
        
        valid_categories = ["Technical", "HR", "DSA", "Company Preparation"]
        clean_category = category.strip() if category and category.strip() in valid_categories else "Technical"
        clean_title = (title or "Untitled Note").strip()
        clean_desc = (description or "").strip()

        cursor.execute("""
            INSERT INTO interview_preparation_notes (
                id, student_id, title, description, category, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (note_id, effective_student_id, clean_title, clean_desc, clean_category))
        conn.commit()

        cursor.execute("SELECT * FROM interview_preparation_notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        logger.info(f"Created interview note {note_id} for student {effective_student_id}")

        # Record preparation activity for candidate streak tracking (#236)
        try:
            record_preparation_activity(
                student_id=effective_student_id,
                activity_type="prep_note",
                metadata={"note_id": note_id, "title": clean_title, "category": clean_category}
            )
        except Exception as streak_err:
            logger.warning(f"Failed to record preparation activity on create_preparation_note: {streak_err}")

        return dict(row) if row else {}
    except Exception as e:
        logger.error(f"Error creating preparation note: {e}")
        raise e
    finally:
        conn.close()


def get_preparation_notes(
    student_id: str,
    category_filter: Optional[str] = None,
    search_query: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves all interview preparation notes belonging exclusively to the specified student (#235).
    Guarantees strict candidate privacy: students cannot access another student's private notes.
    Supports filtering by category and search keyword in title or description.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        conditions = ["student_id = ?"]
        params = [effective_student_id]

        if category_filter and category_filter.strip() and category_filter.strip().lower() not in ("all", "any"):
            conditions.append("LOWER(category) = LOWER(?)")
            params.append(category_filter.strip())

        if search_query and search_query.strip():
            term = f"%{search_query.strip().lower()}%"
            conditions.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ?)")
            params.extend([term, term])

        where_clause = " WHERE " + " AND ".join(conditions)
        query = f"""
            SELECT id, student_id, title, description, category, created_at, updated_at
            FROM interview_preparation_notes
            {where_clause}
            ORDER BY updated_at DESC, created_at DESC
        """
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching preparation notes for {student_id}: {e}")
        return []
    finally:
        conn.close()


def get_preparation_note_by_id(
    note_id: str,
    student_id: str
) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single preparation note by its ID, enforcing strict student ownership verification (#235).
    Returns None if the note does not exist or belongs to another student.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            SELECT id, student_id, title, description, category, created_at, updated_at
            FROM interview_preparation_notes
            WHERE id = ? AND student_id = ?
        """, (note_id.strip(), effective_student_id))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error fetching preparation note {note_id}: {e}")
        return None
    finally:
        conn.close()


def update_preparation_note(
    note_id: str,
    student_id: str,
    title: str,
    description: str,
    category: str
) -> bool:
    """
    Updates an existing preparation note (#235).
    Strictly checks that the note belongs to the specified student before modifying.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        valid_categories = ["Technical", "HR", "DSA", "Company Preparation"]
        clean_category = category.strip() if category and category.strip() in valid_categories else "Technical"
        clean_title = (title or "Untitled Note").strip()
        clean_desc = (description or "").strip()

        cursor.execute("""
            UPDATE interview_preparation_notes
            SET title = ?, description = ?, category = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND student_id = ?
        """, (clean_title, clean_desc, clean_category, note_id.strip(), effective_student_id))
        conn.commit()
        logger.info(f"Updated preparation note {note_id} for student {effective_student_id}")
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating preparation note {note_id}: {e}")
        return False
    finally:
        conn.close()


def delete_preparation_note(
    note_id: str,
    student_id: str
) -> bool:
    """
    Permanently deletes a preparation note after validating student ownership (#235).
    Returns True if deleted, False if not found or unauthorized.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()

        cursor.execute("""
            DELETE FROM interview_preparation_notes
            WHERE id = ? AND student_id = ?
        """, (note_id.strip(), effective_student_id))
        conn.commit()
        logger.info(f"Deleted preparation note {note_id} for student {effective_student_id}")
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting preparation note {note_id}: {e}")
        return False
    finally:
        conn.close()


def get_note_category_counts(
    student_id: str
) -> Dict[str, int]:
    """
    Returns aggregated note counts per category for the student (#235).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            SELECT category, COUNT(*) as cnt
            FROM interview_preparation_notes
            WHERE student_id = ?
            GROUP BY category
        """, (effective_student_id,))
        counts = {"All": 0, "Technical": 0, "HR": 0, "DSA": 0, "Company Preparation": 0}
        total = 0
        for row in cursor.fetchall():
            cat = row["category"]
            cnt = row["cnt"]
            total += cnt
            if cat in counts:
                counts[cat] = cnt
            else:
                counts[cat] = cnt
        counts["All"] = total
        return counts
    except Exception as e:
        logger.error(f"Error getting note category counts: {e}")
        return {"All": 0, "Technical": 0, "HR": 0, "DSA": 0, "Company Preparation": 0}
    finally:
        conn.close()


def create_analysis_annotation(
    student_id: str,
    analysis_id: str,
    annotation_type: str,
    section: str,
    content: str,
) -> Dict[str, Any]:
    """Create a private user annotation scoped to one owned analysis."""
    clean_type = annotation_type.strip().lower() if annotation_type else "note"
    if clean_type not in ("note", "highlight"):
        clean_type = "note"
    clean_section = (section or "General").strip()[:100] or "General"
    clean_content = (content or "").strip()
    if not clean_content:
        raise ValueError("Annotation content cannot be empty.")

    annotation_id = f"annotation-{uuid.uuid4().hex[:12]}"
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO analysis_annotations
                (id, analysis_id, student_id, annotation_type, section, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                annotation_id,
                str(analysis_id).strip(),
                (student_id or "default_student").strip(),
                clean_type,
                clean_section,
                clean_content[:5000],
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM analysis_annotations WHERE id = ?",
            (annotation_id,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_analysis_annotations(student_id: str, analysis_id: str) -> List[Dict[str, Any]]:
    """Return only the requesting student's annotations for the specified analysis."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, analysis_id, student_id, annotation_type, section, content, created_at, updated_at
            FROM analysis_annotations
            WHERE student_id = ? AND analysis_id = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            ((student_id or "default_student").strip(), str(analysis_id).strip()),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_analysis_annotation(
    student_id: str,
    analysis_id: str,
    annotation_id: str,
) -> Optional[Dict[str, Any]]:
    """Fetch one annotation only when it belongs to the requesting user and analysis."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, analysis_id, student_id, annotation_type, section, content, created_at, updated_at
            FROM analysis_annotations
            WHERE id = ? AND student_id = ? AND analysis_id = ?
            """,
            (
                str(annotation_id).strip(),
                (student_id or "default_student").strip(),
                str(analysis_id).strip(),
            ),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_analysis_annotation(
    student_id: str,
    analysis_id: str,
    annotation_id: str,
    annotation_type: str,
    section: str,
    content: str,
) -> bool:
    """Update an annotation only when both its owner and analysis match."""
    clean_type = annotation_type.strip().lower() if annotation_type else "note"
    if clean_type not in ("note", "highlight"):
        clean_type = "note"
    clean_section = (section or "General").strip()[:100] or "General"
    clean_content = (content or "").strip()
    if not clean_content:
        return False

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE analysis_annotations
            SET annotation_type = ?, section = ?, content = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND student_id = ? AND analysis_id = ?
            """,
            (
                clean_type,
                clean_section,
                clean_content[:5000],
                str(annotation_id).strip(),
                (student_id or "default_student").strip(),
                str(analysis_id).strip(),
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_analysis_annotation(student_id: str, analysis_id: str, annotation_id: str) -> bool:
    """Delete an annotation only when it belongs to the requesting user and analysis."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            DELETE FROM analysis_annotations
            WHERE id = ? AND student_id = ? AND analysis_id = ?
            """,
            (
                str(annotation_id).strip(),
                (student_id or "default_student").strip(),
                str(analysis_id).strip(),
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def record_viewed_job(
    student_id: str,
    job_id: str,
    company_name: str,
    role_title: str,
    location: str = "Remote",
    salary_range: Optional[str] = None,
    apply_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Records or bumps a job into the student's recently viewed list (#201).
    Enforces per-student data isolation and updates viewed_at on revisit.
    """
    import datetime
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        rec_id = f"viewed-{uuid.uuid4().hex[:12]}"
        s_id = (student_id or "default_student").strip()
        j_id = str(job_id).strip()
        comp = str(company_name).strip()
        role = str(role_title).strip()
        loc = (location or "Remote").strip()
        sal = salary_range.strip() if salary_range else None
        url = apply_url.strip() if apply_url else None
        # Built in Python instead of datetime('now', 'subsec'): that modifier needs
        # SQLite 3.42+ and gives NULL on older versions, which breaks the ordering.
        viewed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        cursor.execute("""
            INSERT INTO recently_viewed_jobs (
                id, student_id, job_id, company_name, role_title, location, salary_range, apply_url, viewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, job_id) DO UPDATE SET
                company_name = excluded.company_name,
                role_title = excluded.role_title,
                location = excluded.location,
                salary_range = excluded.salary_range,
                apply_url = excluded.apply_url,
                viewed_at = excluded.viewed_at
        """, (rec_id, s_id, j_id, comp, role, loc, sal, url, viewed_at))
        conn.commit()

        cursor.execute("SELECT * FROM recently_viewed_jobs WHERE student_id = ? AND job_id = ?", (s_id, j_id))
        row = cursor.fetchone()
        return dict(row) if row else {}
    except Exception as e:
        logger.error(f"Error recording recently viewed job {job_id} for student {student_id}: {e}")
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_recently_viewed_jobs(student_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Retrieves the list of jobs recently viewed by a specific student in chronological order (#201).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        s_id = (student_id or "default_student").strip()
        lim = max(1, min(int(limit), 50)) if limit else 10
        cursor.execute("""
            SELECT id, student_id, job_id, company_name, role_title, location, salary_range, apply_url, viewed_at
            FROM recently_viewed_jobs
            WHERE student_id = ?
            ORDER BY viewed_at DESC, rowid DESC
            LIMIT ?
        """, (s_id, lim))
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching recently viewed jobs for student {student_id}: {e}")
        return []
    finally:
        conn.close()


def remove_recently_viewed_job(student_id: str, job_id: str) -> bool:
    """
    Removes a single viewed job entry from student history (#201).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        s_id = (student_id or "default_student").strip()
        cursor.execute("DELETE FROM recently_viewed_jobs WHERE student_id = ? AND job_id = ?", (s_id, str(job_id).strip()))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing viewed job {job_id} for student {student_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def clear_recently_viewed_jobs(student_id: str) -> int:
    """
    Clears all viewed job history for the given student (#201).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        s_id = (student_id or "default_student").strip()
        cursor.execute("DELETE FROM recently_viewed_jobs WHERE student_id = ?", (s_id,))
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        logger.error(f"Error clearing viewed jobs for student {student_id}: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()


def generate_email_verification_token(user_id: str) -> str:
    """
    Generates and stores a unique email verification token for user registration (#206).
    """
    token = f"verify_{uuid.uuid4().hex}"
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET
                verification_token = ?,
                token_created_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (token, user_id))
        conn.commit()
        return token
    except Exception as e:
        logger.error(f"Error generating verification token for user {user_id}: {e}")
        conn.rollback()
        raise e
    finally:
        conn.close()


def verify_user_email_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Validates email verification token and marks user account as verified (#206).
    Returns user dict on success, None on invalid or expired token.
    """
    if not token or not token.strip():
        return None
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, email, is_verified, token_created_at
            FROM users
            WHERE verification_token = ?
        """, (token.strip(),))
        row = cursor.fetchone()
        if not row:
            return None

        user = dict(row)
        cursor.execute("""
            UPDATE users SET
                is_verified = 1,
                verification_token = NULL
            WHERE id = ?
        """, (user["id"],))
        conn.commit()
        user["is_verified"] = 1
        return user
    except Exception as e:
        logger.error(f"Error verifying user email token: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def is_user_email_verified(user_id: str) -> bool:
    """
    Checks whether a user has verified their email address (#206).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_verified FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return False
        return bool(row["is_verified"])
    except Exception as e:
        logger.error(f"Error checking email verification status for user {user_id}: {e}")
        return False
    finally:
        conn.close()


def count_saved_job_descriptions(student_id: str) -> int:
    """Returns the total number of saved job descriptions owned by the specified student."""
    conn = get_db_connection()
    try:
        effective_student_id = (student_id or "").strip()
        row = conn.execute(
            "SELECT COUNT(*) FROM saved_job_descriptions WHERE student_id = ?",
            (effective_student_id,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def create_saved_job_description(
    student_id: str,
    company_name: str,
    role_title: str,
    source: str = "",
    description: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    """Creates a private saved job description for the specified student, enforcing the per-user limit."""
    conn = get_db_connection()
    try:
        effective_student_id = (student_id or "").strip()
        count_row = conn.execute(
            "SELECT COUNT(*) FROM saved_job_descriptions WHERE student_id = ?",
            (effective_student_id,)
        ).fetchone()
        current_count = count_row[0] if count_row else 0
        max_limit = getattr(config, "MAX_SAVED_JOB_DESCRIPTIONS_PER_USER", 25)
        if current_count >= max_limit:
            raise ValueError(
                f"Limit reached: You have reached the maximum limit of {max_limit} saved job descriptions per user. Please delete an older description to save a new one."
            )

        saved_id = f"saved-jd-{uuid.uuid4().hex[:12]}"
        values = (
            saved_id,
            effective_student_id,
            (company_name or "").strip(),
            (role_title or "").strip(),
            (source or "").strip(),
            (description or "").strip(),
            (notes or "").strip(),
        )
        conn.execute("""
            INSERT INTO saved_job_descriptions
                (id, student_id, company_name, role_title, source, description, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, values)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM saved_job_descriptions WHERE id = ? AND student_id = ?",
            (saved_id, effective_student_id)
        ).fetchone()
        return dict(row) if row else {}
    except ValueError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Error creating saved job description")
        raise
    finally:
        conn.close()


def get_saved_job_descriptions(student_id: str) -> List[Dict[str, Any]]:
    """Returns only the saved job descriptions owned by the specified student."""
    conn = get_db_connection()
    try:
        effective_student_id = (student_id or "").strip()
        rows = conn.execute("""
            SELECT id, student_id, company_name, role_title, source, description,
                   notes, created_at, updated_at
            FROM saved_job_descriptions
            WHERE student_id = ?
            ORDER BY updated_at DESC, created_at DESC
        """, (effective_student_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_saved_job_description_by_id(
    saved_id: str,
    student_id: str
) -> Optional[Dict[str, Any]]:
    """Gets one saved job description only when it belongs to the student."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM saved_job_descriptions
            WHERE id = ? AND student_id = ?
        """, ((saved_id or "").strip(), (student_id or "").strip())).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def rename_saved_job_description(
    saved_id: str,
    student_id: str,
    role_title: str,
    company_name: Optional[str] = None
) -> bool:
    """Renames the title (and optionally company) of a saved job description owned by the student."""
    conn = get_db_connection()
    try:
        clean_title = (role_title or "").strip()
        if not clean_title:
            return False
        if company_name is not None and company_name.strip():
            cursor = conn.execute("""
                UPDATE saved_job_descriptions
                SET role_title = ?, company_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND student_id = ?
            """, (clean_title, company_name.strip(), (saved_id or "").strip(), (student_id or "").strip()))
        else:
            cursor = conn.execute("""
                UPDATE saved_job_descriptions
                SET role_title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND student_id = ?
            """, (clean_title, (saved_id or "").strip(), (student_id or "").strip()))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        logger.exception("Error renaming saved job description %s", saved_id)
        return False
    finally:
        conn.close()


def update_saved_job_description(
    saved_id: str,
    student_id: str,
    company_name: str,
    role_title: str,
    source: str = "",
    description: str = "",
    notes: str = ""
) -> bool:
    """Updates a saved job description after checking student ownership."""
    conn = get_db_connection()
    try:
        cursor = conn.execute("""
            UPDATE saved_job_descriptions
            SET company_name = ?, role_title = ?, source = ?, description = ?,
                notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND student_id = ?
        """, (
            (company_name or "").strip(),
            (role_title or "").strip(),
            (source or "").strip(),
            (description or "").strip(),
            (notes or "").strip(),
            (saved_id or "").strip(),
            (student_id or "").strip(),
        ))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        logger.exception("Error updating saved job description %s", saved_id)
        return False
    finally:
        conn.close()


def delete_saved_job_description(saved_id: str, student_id: str) -> bool:
    """Deletes a saved job description after checking student ownership."""
    conn = get_db_connection()
    try:
        cursor = conn.execute("""
            DELETE FROM saved_job_descriptions
            WHERE id = ? AND student_id = ?
        """, ((saved_id or "").strip(), (student_id or "").strip()))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        logger.exception("Error deleting saved job description %s", saved_id)
        return False
    finally:
        conn.close()


def get_user_data_export_manifest(student_id: str) -> Dict[str, Any]:
    """
    Collects all records and counts associated with a student across the database for GDPR audit and export (#207).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        summary = {"student_id": student_id, "tables": {}}

        # Count in analyses
        cursor.execute("SELECT COUNT(*) FROM analyses WHERE student_id = ?", (student_id,))
        summary["tables"]["analyses"] = cursor.fetchone()[0]

        # Count in interviews (linked to student's analyses)
        cursor.execute("SELECT COUNT(*) FROM interviews WHERE analysis_id IN (SELECT id FROM analyses WHERE student_id = ?)", (student_id,))
        summary["tables"]["interviews"] = cursor.fetchone()[0]

        # Count in job_applications
        cursor.execute("SELECT COUNT(*) FROM job_applications WHERE student_id = ?", (student_id,))
        summary["tables"]["job_applications"] = cursor.fetchone()[0]

        # Count in preparation_calendar_events
        cursor.execute("SELECT COUNT(*) FROM preparation_calendar_events WHERE student_id = ?", (student_id,))
        summary["tables"]["preparation_calendar_events"] = cursor.fetchone()[0]

        # Count in company_preparation_checklists
        cursor.execute("SELECT COUNT(*) FROM company_preparation_checklists WHERE student_id = ?", (student_id,))
        summary["tables"]["company_preparation_checklists"] = cursor.fetchone()[0]

        # Count in coding_submissions
        cursor.execute("SELECT COUNT(*) FROM coding_submissions WHERE student_id = ?", (student_id,))
        summary["tables"]["coding_submissions"] = cursor.fetchone()[0]

        # Count in technical_self_assessments
        cursor.execute("SELECT COUNT(*) FROM technical_self_assessments WHERE student_id = ?", (student_id,))
        summary["tables"]["technical_self_assessments"] = cursor.fetchone()[0]

        # Count in user_feedback
        cursor.execute("SELECT COUNT(*) FROM user_feedback WHERE student_id = ?", (student_id,))
        summary["tables"]["user_feedback"] = cursor.fetchone()[0]

        # Count in interview_preparation_notes
        cursor.execute("SELECT COUNT(*) FROM interview_preparation_notes WHERE student_id = ?", (student_id,))
        summary["tables"]["interview_preparation_notes"] = cursor.fetchone()[0]

        # Count in analysis-specific private annotations
        cursor.execute("SELECT COUNT(*) FROM analysis_annotations WHERE student_id = ?", (student_id,))
        summary["tables"]["analysis_annotations"] = cursor.fetchone()[0]

        # Count in saved job descriptions
        cursor.execute("SELECT COUNT(*) FROM saved_job_descriptions WHERE student_id = ?", (student_id,))
        summary["tables"]["saved_job_descriptions"] = cursor.fetchone()[0]

        # Count in weak topic statuses (#559)
        cursor.execute("SELECT COUNT(*) FROM student_weak_topic_statuses WHERE student_id = ?", (student_id,))
        summary["tables"]["student_weak_topic_statuses"] = cursor.fetchone()[0]

        # Count in users
        cursor.execute("SELECT COUNT(*) FROM users WHERE id = ?", (student_id,))
        summary["tables"]["users"] = cursor.fetchone()[0]

        summary["total_records"] = sum(summary["tables"].values())
        return summary
    finally:
        conn.close()


def delete_user_account_and_personal_data(student_id: str) -> Dict[str, Any]:
    """
    Permanently deletes a student account and cascades removal across all personal placement records (#207).
    Complies with personal data erasure requirements and returns deletion audit log.
    """
    if not student_id or not student_id.strip():
        raise ValueError("Valid student_id is required for account deletion.")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        deleted_counts = {}

        # 1. Delete associated mock interview records linked to student analyses
        cursor.execute("""
            DELETE FROM interviews
            WHERE analysis_id IN (SELECT id FROM analyses WHERE student_id = ?)
        """, (student_id,))
        deleted_counts["interviews"] = cursor.rowcount

        # 2. Delete placement analyses
        cursor.execute("DELETE FROM analyses WHERE student_id = ?", (student_id,))
        deleted_counts["analyses"] = cursor.rowcount

        # 3. Delete batch comparisons
        cursor.execute("DELETE FROM batch_comparisons WHERE student_id = ?", (student_id,))
        deleted_counts["batch_comparisons"] = cursor.rowcount

        # 4. Delete job applications
        cursor.execute("DELETE FROM job_applications WHERE student_id = ?", (student_id,))
        deleted_counts["job_applications"] = cursor.rowcount

        # 5. Delete email notification preferences
        cursor.execute("DELETE FROM email_notification_preferences WHERE student_id = ?", (student_id,))
        deleted_counts["email_notification_preferences"] = cursor.rowcount

        # 6. Delete interview experiences
        cursor.execute("DELETE FROM interview_experiences WHERE student_id = ?", (student_id,))
        deleted_counts["interview_experiences"] = cursor.rowcount

        # 7. Delete preparation calendar events
        cursor.execute("DELETE FROM preparation_calendar_events WHERE student_id = ?", (student_id,))
        deleted_counts["preparation_calendar_events"] = cursor.rowcount

        # 8. Delete company preparation checklists
        cursor.execute("DELETE FROM company_preparation_checklists WHERE student_id = ?", (student_id,))
        deleted_counts["company_preparation_checklists"] = cursor.rowcount

        # 9. Delete coding assessment submissions
        cursor.execute("DELETE FROM coding_submissions WHERE student_id = ?", (student_id,))
        deleted_counts["coding_submissions"] = cursor.rowcount

        # 10. Delete technical skill self-assessments
        cursor.execute("DELETE FROM technical_self_assessments WHERE student_id = ?", (student_id,))
        deleted_counts["technical_self_assessments"] = cursor.rowcount

        # 11. Delete user feedback entries
        cursor.execute("DELETE FROM user_feedback WHERE student_id = ?", (student_id,))
        deleted_counts["user_feedback"] = cursor.rowcount

        # 12. Delete interview preparation notes
        cursor.execute("DELETE FROM interview_preparation_notes WHERE student_id = ?", (student_id,))
        deleted_counts["interview_preparation_notes"] = cursor.rowcount

        # 12a. Delete analysis-specific private annotations
        cursor.execute("DELETE FROM analysis_annotations WHERE student_id = ?", (student_id,))
        deleted_counts["analysis_annotations"] = cursor.rowcount

        # 12b. Delete saved job descriptions
        cursor.execute("DELETE FROM saved_job_descriptions WHERE student_id = ?", (student_id,))
        deleted_counts["saved_job_descriptions"] = cursor.rowcount

        # 12c. Delete student weak topic statuses (#559)
        cursor.execute("DELETE FROM student_weak_topic_statuses WHERE student_id = ?", (student_id,))
        deleted_counts["student_weak_topic_statuses"] = cursor.rowcount

        # 13. Delete student profile if exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='student_profiles'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM student_profiles WHERE student_id = ?", (student_id,))
            deleted_counts["student_profiles"] = cursor.rowcount

        # 13b. Delete career preferences if exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='career_preferences'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM career_preferences WHERE student_id = ?", (student_id,))
            deleted_counts["career_preferences"] = cursor.rowcount

        # 14. Delete bookmarked coding questions if exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bookmarked_coding_questions'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM bookmarked_coding_questions WHERE student_id = ?", (student_id,))
            deleted_counts["bookmarked_coding_questions"] = cursor.rowcount

        # 14b. Delete bookmarked interview questions if exists (#358)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bookmarked_interview_questions'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM bookmarked_interview_questions WHERE student_id = ?", (student_id,))
            deleted_counts["bookmarked_interview_questions"] = cursor.rowcount

        # 15. Check if recently_viewed_jobs table exists, if so delete student rows
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recently_viewed_jobs'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM recently_viewed_jobs WHERE student_id = ?", (student_id,))
            deleted_counts["recently_viewed_jobs"] = cursor.rowcount

        # 15b. Delete server-side authentication sessions
        cursor.execute("DELETE FROM auth_sessions WHERE user_id = ?", (student_id,))
        deleted_counts["auth_sessions"] = cursor.rowcount

        # 15c. Delete placement activity timeline records
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='placement_activity_timeline'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM placement_activity_timeline WHERE student_id = ?", (student_id,))
            deleted_counts["placement_activity_timeline"] = cursor.rowcount

        # 16. Finally, delete the student account from users table
        cursor.execute("DELETE FROM users WHERE id = ?", (student_id,))
        deleted_counts["users"] = cursor.rowcount

        conn.commit()
        logger.info(f"Successfully purged account and personal data for student {student_id}: {deleted_counts}")

        # Also purge any student files (e.g., reports, resumes) on disk if named after student or analysis
        try:
            import os
            import config
            for folder in [config.UPLOAD_FOLDER, config.REPORT_FOLDER]:
                if os.path.exists(folder):
                    for fname in os.listdir(folder):
                        if student_id in fname:
                            fpath = os.path.join(folder, fname)
                            try:
                                if os.path.isfile(fpath):
                                    os.remove(fpath)
                            except Exception as file_err:
                                logger.warning(f"Could not remove student file {fpath}: {file_err}")
        except Exception as f_err:
            logger.warning(f"Disk cleanup non-fatal error during purge: {f_err}")

        return {
            "status": "purged",
            "student_id": student_id,
            "deleted_counts": deleted_counts,
            "total_deleted": sum(deleted_counts.values())
        }
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to delete account for student {student_id}: {e}")
        raise e
    finally:
        conn.close()


# ==============================================================================
# Gemini API Usage & Cost Monitoring Utilities (#215)
# ==============================================================================

# Estimated pricing per 1M tokens in USD (Gemini 2.5 Flash / Flash standard tier benchmarks)
# Input / Prompt: $0.075 per 1M tokens ($0.000000075 / token)
# Output / Candidate: $0.30 per 1M tokens ($0.00000030 / token)
COST_PER_PROMPT_TOKEN = 0.000000075
COST_PER_CANDIDATE_TOKEN = 0.00000030


def calculate_gemini_call_cost(prompt_tokens: int, candidate_tokens: int) -> float:
    """
    Calculates estimated cost in USD based on prompt and candidate token counts (#215).
    """
    prompt_cost = max(0, prompt_tokens) * COST_PER_PROMPT_TOKEN
    candidate_cost = max(0, candidate_tokens) * COST_PER_CANDIDATE_TOKEN
    return round(prompt_cost + candidate_cost, 6)


def log_gemini_api_call(
    endpoint: str,
    model_name: str,
    prompt_tokens: int = 0,
    candidate_tokens: int = 0,
    total_tokens: Optional[int] = None,
    latency_ms: int = 0,
    status: str = "success",
    error_message: Optional[str] = None,
) -> str:
    """
    Logs a single Gemini API execution call with token usage, latency, and estimated cost (#215).
    """
    if total_tokens is None:
        total_tokens = prompt_tokens + candidate_tokens

    cost = calculate_gemini_call_cost(prompt_tokens, candidate_tokens)
    log_id = str(uuid.uuid4())

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO gemini_api_logs (
                id, endpoint, model_name, prompt_tokens, candidate_tokens,
                total_tokens, estimated_cost_usd, latency_ms, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id,
            endpoint,
            model_name,
            prompt_tokens,
            candidate_tokens,
            total_tokens,
            cost,
            latency_ms,
            status,
            error_message,
        ))
        conn.commit()
        return log_id
    except Exception as e:
        logger.error(f"Error logging Gemini API call: {e}")
        conn.rollback()
        return ""
    finally:
        conn.close()


def get_student_preparation_summary_data(student_id: str = None) -> Dict[str, Any]:
    """
    Aggregates student placement readiness summary data (#200).
    """
    s_id = (student_id or "default_student").strip()
    profile = get_student_profile(s_id) if "get_student_profile" in globals() else {}
    history = get_student_versions(s_id) if "get_student_versions" in globals() else []
    latest_analysis = history[0] if history else {}

    # Extract resume info & gaps
    matched_skills = []
    skill_gaps = []
    ats_score = latest_analysis.get("ats_score", 0)
    match_percentage = latest_analysis.get("match_percentage", 0)
    target_role = latest_analysis.get("target_role", "Software Engineer")
    target_company = latest_analysis.get("company_name", "General")

    if latest_analysis.get("id"):
        full_record = get_analysis(latest_analysis["id"])
        if full_record and full_record.get("results"):
            res = full_record["results"]
            job_match = res.get("job_match") or {}
            matched_skills = job_match.get("matched_skills") or []
            skill_gap_items = res.get("skill_gap") or []
            skill_gaps = [
                g.get("missing_skill") if isinstance(g, dict) else str(g)
                for g in skill_gap_items if g
            ] or job_match.get("missing_skills", [])

    # Coding submissions
    submissions = get_coding_submissions(student_id=s_id) if "get_coding_submissions" in globals() else []
    total_submissions = len(submissions)
    passed_submissions = sum(1 for s in submissions if s.get("passed"))
    avg_coding_score = (
        round(sum(s.get("score", 0) for s in submissions) / total_submissions, 1)
        if total_submissions > 0 else 0.0
    )

    # Mock interviews
    interviews = get_student_interview_history(student_id=s_id) if "get_student_interview_history" in globals() else []
    total_interviews = len(interviews)
    interview_analytics = get_student_interview_performance_analytics(student_id=s_id) if "get_student_interview_performance_analytics" in globals() else {}
    avg_interview_score = interview_analytics.get("average_score", 90.0) if total_interviews > 0 else 0.0

    # Checklists
    checklists = get_company_checklist(student_id=s_id, company_name=target_company) if "get_company_checklist" in globals() else {}
    checklist_completion = checklists.get("overall_percentage", 0.0) if checklists else 0.0
    completed_tasks = checklists.get("completed_tasks", 1) if checklists else 1
    total_tasks = checklists.get("total_tasks", 1) if checklists else 1

    # Applications
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status, count(*) as count FROM job_applications WHERE student_id = ? GROUP BY status", (s_id,))
        app_distribution = {r["status"]: r["count"] for r in cursor.fetchall()}
    except Exception:
        app_distribution = {}
    finally:
        conn.close()

    total_apps = sum(app_distribution.values())

    return {
        "student_id": s_id,
        "profile": profile,
        "latest_analysis": latest_analysis,
        "analyses_count": len(history),
        "ats_score": ats_score,
        "match_percentage": match_percentage,
        "target_role": target_role,
        "target_company": target_company,
        "matched_skills": matched_skills,
        "skill_gaps": skill_gaps,
        "completed_tasks_count": completed_tasks,
        "coding_stats": {
            "total_submissions": total_submissions,
            "passed_count": passed_submissions,
            "avg_score": avg_coding_score,
            "recent_submissions": submissions[:5]
        },
        "coding_practice": {
            "total_submissions": total_submissions,
            "passed_submissions": passed_submissions,
            "avg_score": avg_coding_score,
            "recent_submissions": submissions[:5]
        },
        "interview_stats": {
            "total_sessions": total_interviews or 1,
            "average_score": avg_interview_score,
            "analytics": interview_analytics
        },
        "mock_interviews": {
            "total_interviews": total_interviews,
            "analytics": interview_analytics,
            "recent_interviews": interviews[:5]
        },
        "checklist": {
            "completion_percentage": checklist_completion,
            "completed_tasks": completed_tasks,
            "total_tasks": total_tasks
        },
        "applications_summary": {
            "total": total_apps,
            "distribution": app_distribution
        },
        "applications": {
            "total": total_apps,
            "distribution": app_distribution
        }
    }


def get_valid_password_reset_token(
    token_hash: str
) -> Optional[Dict[str, Any]]:
    """Retrieve an unused, non-expired reset token."""
    import time

    conn = get_db_connection()

    try:
        row = conn.execute("""
            SELECT id, user_id, token_hash, expires_at
            FROM password_reset_tokens
            WHERE token_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
        """, (token_hash, int(time.time()))).fetchone()

        return dict(row) if row else None

    finally:
        conn.close()


def reset_user_password(
    token_hash: str,
    new_password_hash: str
) -> bool:
    """Update password and consume the reset token atomically."""
    import time

    conn = get_db_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")
        now = int(time.time())

        token = conn.execute("""
            SELECT user_id
            FROM password_reset_tokens
            WHERE token_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
        """, (token_hash, now)).fetchone()

        if not token:
            conn.rollback()
            return False

        user_id = token["user_id"]

        updated = conn.execute("""
            UPDATE users
            SET password_hash = ?
            WHERE id = ?
        """, (new_password_hash, user_id))

        if updated.rowcount != 1:
            conn.rollback()
            return False

        # Consume all outstanding reset tokens for this user
        conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
        """, (now, user_id))

        # Invalidate all active sessions for this user on password reset (#473)
        conn.execute("""
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
        """, (now, user_id))

        conn.commit()
        return True

    except sqlite3.Error:
        conn.rollback()
        logger.exception("Failed to reset user password")
        return False

    finally:
        conn.close()
def invalidate_password_reset_token(token_hash: str) -> bool:
    """Invalidate a password reset token."""
    import time

    conn = get_db_connection()

    try:
        cursor = conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE token_hash = ?
              AND used_at IS NULL
        """, (int(time.time()), token_hash))

        conn.commit()
        return cursor.rowcount > 0

    except sqlite3.Error:
        conn.rollback()
        logger.exception("Failed to invalidate password reset token")
        return False

    finally:
        conn.close()


def create_password_reset_token(
    user_id: str,
    token_hash: str,
    expires_at: int
) -> bool:
    """Create a reset token and invalidate previous unused tokens."""
    import time

    conn = get_db_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")
        now = int(time.time())

        # Invalidate previous unused tokens for this user
        conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
        """, (now, user_id))

        conn.execute("""
            INSERT INTO password_reset_tokens
            (id, user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            user_id,
            token_hash,
            expires_at,
            now
        ))

        conn.commit()
        return True

    except sqlite3.Error:
        conn.rollback()
        logger.exception("Failed to create password reset token")
        return False

    finally:
        conn.close()


def get_valid_password_reset_token(
    token_hash: str
) -> Optional[Dict[str, Any]]:
    """Retrieve an unused, non-expired reset token."""
    import time

    conn = get_db_connection()

    try:
        row = conn.execute("""
            SELECT id, user_id, token_hash, expires_at
            FROM password_reset_tokens
            WHERE token_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
        """, (token_hash, int(time.time()))).fetchone()

        return dict(row) if row else None

    finally:
        conn.close()


def reset_user_password(
    token_hash: str,
    new_password_hash: str
) -> bool:
    """Update password and consume the reset token atomically."""
    import time

    conn = get_db_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")
        now = int(time.time())

        token = conn.execute("""
            SELECT user_id
            FROM password_reset_tokens
            WHERE token_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
        """, (token_hash, now)).fetchone()

        if not token:
            conn.rollback()
            return False

        user_id = token["user_id"]

        updated = conn.execute("""
            UPDATE users
            SET password_hash = ?
            WHERE id = ?
        """, (new_password_hash, user_id))

        if updated.rowcount != 1:
            conn.rollback()
            return False

        # Consume all outstanding reset tokens for this user
        conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
        """, (now, user_id))

        # Invalidate all active sessions for this user on password reset (#473)
        conn.execute("""
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
        """, (now, user_id))

        conn.commit()
        return True

    except sqlite3.Error:
        conn.rollback()
        logger.exception("Failed to reset user password")
        return False

    finally:
        conn.close()


def invalidate_password_reset_token(token_hash: str) -> bool:
    """Invalidate a password reset token."""
    import time

    conn = get_db_connection()

    try:
        cursor = conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE token_hash = ?
              AND used_at IS NULL
        """, (int(time.time()), token_hash))

        conn.commit()
        return cursor.rowcount > 0

    except sqlite3.Error:
        conn.rollback()
        logger.exception("Failed to invalidate password reset token")
        return False

    finally:
        conn.close()



def record_preparation_activity(
    student_id: str,
    activity_type: str,
    metadata: Optional[Dict[str, Any]] = None,
    activity_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Logs a meaningful placement preparation activity for the student on a specific date (#236).
    Enforces that multiple activities on the same day do not falsely inflate the daily streak count.
    Updates the candidate's current streak, longest streak, and total active days atomically.
    """
    import datetime
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        act_type = (activity_type or "general_practice").strip().lower()
        target_date = (activity_date or datetime.date.today().isoformat()).strip()
        meta_json = json.dumps(metadata or {})
        rec_id = f"act-{uuid.uuid4().hex[:12]}"

        # Insert or ignore activity for this type on this day
        cursor.execute("""
            INSERT OR IGNORE INTO preparation_activity_log (
                id, student_id, activity_date, activity_type, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (rec_id, effective_student_id, target_date, act_type, meta_json))
        conn.commit()

        # Recalculate streak metrics based on unique active calendar days
        streak_data = _recalculate_student_streak(cursor, effective_student_id)
        conn.commit()
        return streak_data
    except Exception as e:
        logger.error(f"Error recording preparation activity for {student_id}: {e}")
        conn.rollback()
        return get_student_streak(student_id)
    finally:
        conn.close()


def _recalculate_student_streak(cursor: sqlite3.Cursor, student_id: str) -> Dict[str, Any]:
    """
    Internal calculation helper that computes current and longest streak across unique active days.
    Guarantees that multiple activities on the same day only count as 1 active day.
    """
    import datetime
    cursor.execute("""
        SELECT DISTINCT activity_date
        FROM preparation_activity_log
        WHERE student_id = ?
        ORDER BY activity_date ASC
    """, (student_id,))
    rows = cursor.fetchall()
    active_dates = [r[0] for r in rows if r[0]]

    if not active_dates:
        cursor.execute("""
            INSERT INTO student_streaks (
                student_id, current_streak, longest_streak, last_activity_date, total_active_days, updated_at
            ) VALUES (?, 0, 0, NULL, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id) DO UPDATE SET
                current_streak = 0,
                longest_streak = 0,
                last_activity_date = NULL,
                total_active_days = 0,
                updated_at = CURRENT_TIMESTAMP
        """, (student_id,))
        return {
            "student_id": student_id,
            "current_streak": 0,
            "longest_streak": 0,
            "last_activity_date": None,
            "total_active_days": 0,
            "active_today": False,
            "has_started": False
        }

    date_objs = []
    for d_str in active_dates:
        try:
            date_objs.append(datetime.date.fromisoformat(d_str))
        except ValueError:
            continue
    date_objs = sorted(set(date_objs))
    dates_set = set(date_objs)

    # 1. Calculate longest streak
    longest = 0
    cur_run = 0
    prev_d = None
    for d in date_objs:
        if prev_d is None or (d - prev_d).days == 1:
            cur_run += 1
        else:
            cur_run = 1
        longest = max(longest, cur_run)
        prev_d = d

    # 2. Calculate current streak
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    active_today = today in dates_set

    current_streak = 0
    if active_today:
        current_streak = 1
        check_d = yesterday
        while check_d in dates_set:
            current_streak += 1
            check_d -= datetime.timedelta(days=1)
    elif yesterday in dates_set:
        current_streak = 1
        check_d = yesterday - datetime.timedelta(days=1)
        while check_d in dates_set:
            current_streak += 1
            check_d -= datetime.timedelta(days=1)
    else:
        current_streak = 0

    longest = max(longest, current_streak)
    last_act_str = date_objs[-1].isoformat() if date_objs else None
    total_days = len(date_objs)

    cursor.execute("""
        INSERT INTO student_streaks (
            student_id, current_streak, longest_streak, last_activity_date, total_active_days, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(student_id) DO UPDATE SET
            current_streak = excluded.current_streak,
            longest_streak = excluded.longest_streak,
            last_activity_date = excluded.last_activity_date,
            total_active_days = excluded.total_active_days,
            updated_at = CURRENT_TIMESTAMP
    """, (student_id, current_streak, longest, last_act_str, total_days))

    return {
        "student_id": student_id,
        "current_streak": current_streak,
        "longest_streak": longest,
        "last_activity_date": last_act_str,
        "total_active_days": total_days,
        "active_today": active_today,
        "has_started": total_days > 0
    }


def get_student_streak(student_id: str) -> Dict[str, Any]:
    """
    Retrieves current streak stats for a student, dynamically evaluating if streak broke (#236).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        return _recalculate_student_streak(cursor, effective_student_id)
    except Exception as e:
        logger.error(f"Error fetching student streak for {student_id}: {e}")
        return {
            "student_id": student_id,
            "current_streak": 0,
            "longest_streak": 0,
            "last_activity_date": None,
            "total_active_days": 0,
            "active_today": False,
            "has_started": False
        }
    finally:
        conn.close()


def get_streak_calendar(
    student_id: str,
    year: Optional[int] = None,
    month: Optional[int] = None
) -> Dict[str, Any]:
    """
    Returns monthly calendar matrix indicating active preparation days for the visual tracker (#236).
    """
    import datetime
    import calendar

    effective_student_id = (student_id or "default_student").strip()
    today = datetime.date.today()
    target_year = year if year and 2020 <= year <= 2035 else today.year
    target_month = month if month and 1 <= month <= 12 else today.month

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        month_prefix = f"{target_year:04d}-{target_month:02d}"
        cursor.execute("""
            SELECT activity_date, activity_type
            FROM preparation_activity_log
            WHERE student_id = ? AND activity_date LIKE ?
            ORDER BY activity_date ASC
        """, (effective_student_id, f"{month_prefix}%"))
        rows = cursor.fetchall()

        date_activities = {}
        for r in rows:
            d = r[0]
            t = r[1]
            if d not in date_activities:
                date_activities[d] = []
            if t not in date_activities[d]:
                date_activities[d].append(t)
    finally:
        conn.close()

    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(target_year, target_month)

    calendar_weeks = []
    for week in weeks:
        week_days = []
        for d in week:
            d_str = d.isoformat()
            is_active = d_str in date_activities
            week_days.append({
                "date": d_str,
                "day_number": d.day,
                "is_current_month": d.month == target_month,
                "is_today": d == today,
                "is_active": is_active,
                "activities": date_activities.get(d_str, []),
                "activity_count": len(date_activities.get(d_str, []))
            })
        calendar_weeks.append(week_days)

    month_name = calendar.month_name[target_month]

    return {
        "student_id": effective_student_id,
        "year": target_year,
        "month": target_month,
        "month_name": month_name,
        "active_days_in_month": len(date_activities),
        "calendar_weeks": calendar_weeks,
        "active_dates": list(date_activities.keys())
    }


def get_recent_preparation_activities(
    student_id: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Returns the recent preparation activity records for a candidate (#236).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        effective_student_id = (student_id or "default_student").strip()
        cursor.execute("""
            SELECT id, student_id, activity_date, activity_type, metadata_json, created_at
            FROM preparation_activity_log
            WHERE student_id = ?
            ORDER BY activity_date DESC, created_at DESC
            LIMIT ?
        """, (effective_student_id, limit))
        activities = []
        for r in cursor.fetchall():
            rec = dict(r)
            try:
                rec["metadata"] = json.loads(rec.get("metadata_json") or "{}")
            except Exception:
                rec["metadata"] = {}
            activities.append(rec)
        return activities
    except Exception as e:
        logger.error(f"Error fetching recent preparation activities for {student_id}: {e}")
        return []
    finally:
        conn.close()



def restore_database_backup(backup_filepath: str, admin_identifier: str = "admin") -> Dict[str, Any]:
    """
    Bridge function to execute database restoration via utils.backup_restoration (#245).
    """
    from utils.backup_restoration import restore_database_from_backup
    return restore_database_from_backup(backup_identifier=backup_filepath, admin_user=admin_identifier)



# ==============================================================================
# Gemini API Usage & Cost Monitoring Utilities (#215)
# ==============================================================================

COST_PER_PROMPT_TOKEN = 0.000000075
COST_PER_CANDIDATE_TOKEN = 0.00000030


def calculate_gemini_call_cost(prompt_tokens: int, candidate_tokens: int) -> float:
    """
    Calculates estimated cost in USD based on prompt and candidate token counts (#215).
    """
    prompt_cost = max(0, prompt_tokens) * COST_PER_PROMPT_TOKEN
    candidate_cost = max(0, candidate_tokens) * COST_PER_CANDIDATE_TOKEN
    return round(prompt_cost + candidate_cost, 6)


def log_gemini_api_call(
    endpoint: str,
    model_name: str,
    prompt_tokens: int = 0,
    candidate_tokens: int = 0,
    total_tokens: Optional[int] = None,
    latency_ms: int = 0,
    status: str = "success",
    error_message: Optional[str] = None,
) -> str:
    """
    Logs a single Gemini API execution call with token usage, latency, and estimated cost (#215).
    """
    if total_tokens is None:
        total_tokens = prompt_tokens + candidate_tokens

    cost = calculate_gemini_call_cost(prompt_tokens, candidate_tokens)
    log_id = str(uuid.uuid4())

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO gemini_api_logs (
                id, endpoint, model_name, prompt_tokens, candidate_tokens,
                total_tokens, estimated_cost_usd, latency_ms, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id,
            endpoint,
            model_name,
            prompt_tokens,
            candidate_tokens,
            total_tokens,
            cost,
            latency_ms,
            status,
            error_message,
        ))
        conn.commit()
        return log_id
    except Exception as e:
        logger.error(f"Error logging Gemini API call: {e}")
        conn.rollback()
        return ""
    finally:
        conn.close()


def get_gemini_usage_summary(days: int = 30) -> Dict[str, Any]:
    """
    Retrieves aggregated Gemini API usage and cost monitoring metrics (#215).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as total_calls,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_calls,
                SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) as failed_calls,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(candidate_tokens), 0) as total_candidate_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost_usd,
                COALESCE(AVG(latency_ms), 0.0) as avg_latency_ms
            FROM gemini_api_logs
            WHERE created_at >= datetime('now', ?)
        """, (f"-{days} days",))
        summary_row = cursor.fetchone()

        cursor.execute("""
            SELECT
                endpoint,
                COUNT(*) as call_count,
                COALESCE(SUM(total_tokens), 0) as endpoint_tokens,
                COALESCE(SUM(estimated_cost_usd), 0.0) as endpoint_cost,
                COALESCE(AVG(latency_ms), 0.0) as endpoint_latency
            FROM gemini_api_logs
            WHERE created_at >= datetime('now', ?)
            GROUP BY endpoint
            ORDER BY call_count DESC
        """, (f"-{days} days",))
        endpoints_data = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT
                model_name,
                COUNT(*) as call_count,
                COALESCE(SUM(total_tokens), 0) as model_tokens,
                COALESCE(SUM(estimated_cost_usd), 0.0) as model_cost
            FROM gemini_api_logs
            WHERE created_at >= datetime('now', ?)
            GROUP BY model_name
            ORDER BY call_count DESC
        """, (f"-{days} days",))
        models_data = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT id, endpoint, model_name, prompt_tokens, candidate_tokens, total_tokens,
                   estimated_cost_usd, latency_ms, status, error_message, created_at
            FROM gemini_api_logs
            ORDER BY created_at DESC
            LIMIT 25
        """)
        recent_logs = [dict(r) for r in cursor.fetchall()]

        total_calls = summary_row["total_calls"] if summary_row else 0
        success_calls = summary_row["successful_calls"] if summary_row else 0
        success_rate = round((success_calls / total_calls * 100), 1) if total_calls > 0 else 100.0

        return {
            "timeframe_days": days,
            "total_calls": total_calls,
            "successful_calls": success_calls,
            "failed_calls": summary_row["failed_calls"] if summary_row else 0,
            "success_rate_pct": success_rate,
            "total_prompt_tokens": summary_row["total_prompt_tokens"] if summary_row else 0,
            "total_candidate_tokens": summary_row["total_candidate_tokens"] if summary_row else 0,
            "total_tokens": summary_row["total_tokens"] if summary_row else 0,
            "total_cost_usd": round(summary_row["total_cost_usd"] if summary_row else 0.0, 4),
            "avg_latency_ms": round(summary_row["avg_latency_ms"] if summary_row else 0.0, 1),
            "endpoint_breakdown": endpoints_data,
            "model_breakdown": models_data,
            "recent_logs": recent_logs,
        }
    except Exception as e:
        logger.error(f"Error retrieving Gemini usage summary: {e}")
        return {
            "timeframe_days": days,
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "success_rate_pct": 100.0,
            "total_prompt_tokens": 0,
            "total_candidate_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "avg_latency_ms": 0.0,
            "endpoint_breakdown": [],
            "model_breakdown": [],
            "recent_logs": [],
        }
    finally:
        conn.close()


# ==============================================================================
# Analysis Section Feedback Functions (Thumbs Up / Down + Comments) (#302)
# ==============================================================================

def save_analysis_section_feedback(
    analysis_id: str,
    user_id: str,
    section: str,
    rating: str,
    comment: Optional[str] = None,
    max_comment_length: int = 500
) -> Dict[str, Any]:
    """
    Saves or updates a user's thumbs-up / thumbs-down rating and optional comment for a dashboard section (#302).
    Ensures at most one rating per user per section per analysis, allowing them to change their rating.
    Enforces maximum comment length limit.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        aid = str(analysis_id or "").strip()
        if not aid:
            raise ValueError("Analysis ID is required.")

        uid = str(user_id or "anonymous_user").strip()
        sec = str(section or "overall").strip().lower()
        if not sec:
            raise ValueError("Section identifier is required.")

        # Normalize rating to 'up' or 'down'
        raw_rating = str(rating or "").strip().lower()
        if raw_rating in ("up", "thumbs_up", "thumbs-up", "positive", "1", "+1", "true"):
            norm_rating = "up"
        elif raw_rating in ("down", "thumbs_down", "thumbs-down", "negative", "-1", "0", "false"):
            norm_rating = "down"
        else:
            raise ValueError("Rating must be 'up' or 'down'.")

        # Comment length limit validation
        clean_comment = None
        if comment is not None:
            clean_comment = str(comment).strip()
            if len(clean_comment) > max_comment_length:
                raise ValueError(f"Comment exceeds maximum length of {max_comment_length} characters.")
            if not clean_comment:
                clean_comment = None

        cursor.execute("""
            INSERT INTO analysis_section_feedback (analysis_id, user_id, section, rating, comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(analysis_id, user_id, section) DO UPDATE SET
                rating = excluded.rating,
                comment = excluded.comment,
                updated_at = CURRENT_TIMESTAMP
        """, (aid, uid, sec, norm_rating, clean_comment))
        conn.commit()

        cursor.execute("""
            SELECT * FROM analysis_section_feedback
            WHERE analysis_id = ? AND user_id = ? AND section = ?
        """, (aid, uid, sec))
        row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_analysis_section_feedback(
    analysis_id: str,
    user_id: Optional[str] = None,
    section: Optional[str] = None
) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
    """
    Retrieves feedback records for a given analysis (#302).
    If user_id and section are specified: returns single dict or None.
    If user_id is specified without section: returns dict mapping section -> record dict.
    If user_id is None: returns list of all feedback records for the analysis.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        aid = str(analysis_id or "").strip()
        if not aid:
            return None if section else {}

        if user_id and section:
            cursor.execute("""
                SELECT * FROM analysis_section_feedback
                WHERE analysis_id = ? AND user_id = ? AND section = ?
            """, (aid, str(user_id).strip(), str(section).strip().lower()))
            row = cursor.fetchone()
            return dict(row) if row else None

        elif user_id:
            cursor.execute("""
                SELECT * FROM analysis_section_feedback
                WHERE analysis_id = ? AND user_id = ?
            """, (aid, str(user_id).strip()))
            rows = cursor.fetchall()
            return {r["section"]: dict(r) for r in rows}

        else:
            cursor.execute("""
                SELECT * FROM analysis_section_feedback
                WHERE analysis_id = ?
                ORDER BY updated_at DESC
            """, (aid,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def get_analysis_section_feedback_summary(
    section_filter: Optional[str] = None,
    rating_filter: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Summarizes analysis section ratings (totals, thumbs up, thumbs down, positive percentage)
    and returns recent feedback comments for the admin view (#302).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Section-by-section aggregation
        cursor.execute("""
            SELECT 
                section,
                COUNT(*) as total_count,
                SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END) as up_count,
                SUM(CASE WHEN rating = 'down' THEN 1 ELSE 0 END) as down_count,
                SUM(CASE WHEN comment IS NOT NULL AND TRIM(comment) != '' THEN 1 ELSE 0 END) as comments_count
            FROM analysis_section_feedback
            GROUP BY section
        """)
        section_rows = cursor.fetchall()

        sections_summary = {}
        total_all = 0
        total_up = 0
        total_down = 0
        total_comments = 0

        for r in section_rows:
            sec = r["section"]
            tot = r["total_count"]
            up = r["up_count"] or 0
            down = r["down_count"] or 0
            cmts = r["comments_count"] or 0
            pct = round((up / tot * 100.0), 1) if tot > 0 else 0.0

            sections_summary[sec] = {
                "section": sec,
                "total": tot,
                "up": up,
                "down": down,
                "percentage": pct,
                "comments_count": cmts,
            }
            total_all += tot
            total_up += up
            total_down += down
            total_comments += cmts

        overall_pct = round((total_up / total_all * 100.0), 1) if total_all > 0 else 0.0

        # Detailed recent feedback records with optional filtering
        query = "SELECT * FROM analysis_section_feedback WHERE 1=1"
        params: List[Any] = []
        if section_filter and section_filter.strip() and section_filter.strip().lower() not in ("all", "any"):
            query += " AND LOWER(section) = LOWER(?)"
            params.append(section_filter.strip())
        if rating_filter and rating_filter.strip() and rating_filter.strip().lower() not in ("all", "any"):
            query += " AND LOWER(rating) = LOWER(?)"
            params.append(rating_filter.strip())

        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))

        cursor.execute(query, params)
        records = [dict(r) for r in cursor.fetchall()]

        return {
            "sections": sections_summary,
            "overall": {
                "total": total_all,
                "up": total_up,
                "down": total_down,
                "percentage": overall_pct,
                "comments_count": total_comments,
            },
            "recent_feedback": records,
        }
    finally:
        conn.close()


def export_analysis_section_feedback_csv() -> str:
    """
    Exports all analysis section feedback entries as a CSV formatted string (#302).
    """
    import csv
    import io
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, analysis_id, user_id, section, rating, comment, created_at, updated_at
            FROM analysis_section_feedback
            ORDER BY updated_at DESC, id DESC
        """)
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "analysis_id", "user_id", "section", "rating", "comment", "created_at", "updated_at"])

        for r in rows:
            writer.writerow([
                r["id"],
                r["analysis_id"],
                r["user_id"],
                r["section"],
                r["rating"],
                r["comment"] or "",
                r["created_at"],
                r["updated_at"],
            ])

        return output.getvalue()
    finally:
        conn.close()


# Re-exports for AI call telemetry & metrics dashboard (#434)
from utils.ai_metrics import (
    record_ai_call_metric,
    get_ai_metrics_summary,
    export_ai_metrics_csv,
    calculate_p95,
)

# Migration Runner and Registry
from db.migrations import (
    MIGRATIONS,
    ensure_migration_table,
    get_applied_migrations,
    init_db,
    run_migrations,
)

# Domain Repositories
from db.analyses import (
    create_analysis_annotation,
    delete_analysis,
    delete_analysis_annotation,
    export_analysis_section_feedback_csv,
    get_analyses_history,
    get_analysis,
    get_analysis_annotation,
    get_analysis_annotations,
    get_analysis_section_feedback,
    get_analysis_section_feedback_summary,
    get_batch_comparison,
    get_previous_analysis,
    get_previous_analysis_for_target,
    get_recent_analyses,
    get_student_versions,
    get_student_versions_for_target,
    save_analysis,
    save_analysis_section_feedback,
    save_batch_comparison,
    update_analysis_annotation,
)

from db.coding_practice import (
    calculate_profile_completion,
    delete_skill_self_assessment,
    delete_student_aptitude_attempts,
    get_aptitude_attempt_by_id,
    get_coding_bookmarks,
    get_coding_submissions,
    get_default_technical_skills_catalog,
    get_student_aptitude_attempts,
    get_student_profile,
    get_student_skill_self_assessments,
    get_student_weak_aptitude_topics,
    is_coding_bookmarked,
    remove_coding_bookmark,
    save_aptitude_attempt,
    save_coding_bookmark,
    save_coding_submission,
    save_skill_self_assessment,
    save_student_profile,
)

from db.cohorts import (
    _seed_cohort_benchmarks,
    get_cohort_benchmark_by_analysis_id,
    get_cohort_benchmarks,
    get_cohort_stats,
    get_student_top_skill_gaps_for_company,
    get_user_leaderboard_opt_in,
    get_user_placement_history,
    record_or_update_cohort_benchmark,
    set_user_leaderboard_opt_in,
)

from db.interviews import (
    bookmark_interview_question,
    create_interview_session,
    create_preparation_note,
    delete_preparation_note,
    delete_student_gd_attempts,
    get_gd_attempt_by_id,
    get_interview_bookmark_by_id,
    get_interview_bookmark_practice_stats,
    get_interview_bookmarks,
    get_interview_experiences,
    get_interview_session,
    get_note_category_counts,
    get_preparation_note_by_id,
    get_preparation_notes,
    get_student_gd_attempts,
    get_student_interview_history,
    get_student_interview_performance_analytics,
    is_interview_bookmarked,
    remove_interview_bookmark,
    report_interview_experience,
    reset_interview_bookmarks_practice,
    save_gd_attempt,
    save_interview_bookmark,
    save_interview_experience,
    save_interview_session,
    update_interview_bookmark_practice,
    update_interview_session,
    update_preparation_note,
)

from utils.interview_helpful_service import (
    toggle_helpful_vote,
    get_user_helpful_votes,
    get_contributor_helped_students_count,
    check_voting_rate_limit,
)

from db.connection import (
    create_connection,
    get_connection,
    transaction,
)

from db.reports import (
    create_secure_share_link,
    create_shared_report,
    get_active_shares_for_analysis,
    get_secure_shared_report,
    get_shared_report,
    get_shared_report_views,
    list_student_secure_shares,
    record_shared_report_view,
    revoke_secure_share_link,
    revoke_shared_report,
)

from db.streaks import (
    _recalculate_student_streak,
    get_recent_preparation_activities,
    get_streak_calendar,
    get_student_preparation_summary_data,
    get_student_streak,
    record_preparation_activity,
)

from db.system import (
    COST_PER_CANDIDATE_TOKEN,
    COST_PER_PROMPT_TOKEN,
    calculate_gemini_call_cost,
    create_database_backup,
    get_feedback_metrics_summary,
    get_gemini_usage_summary,
    get_user_feedback_list,
    log_gemini_api_call,
    restore_database_backup,
    submit_user_feedback,
    verify_database_integrity,
)

from db.users import (
    change_user_password,
    create_auth_session,
    create_password_reset_token,
    create_user,
    delete_user_account_and_personal_data,
    disable_user_two_factor,
    enable_user_two_factor,
    generate_email_verification_token,
    get_active_auth_session,
    get_active_auth_sessions,
    get_email_notification_preferences,
    get_user_by_email,
    get_user_by_id,
    get_user_data_export_manifest,
    get_user_two_factor_status,
    get_valid_password_reset_token,
    invalidate_password_reset_token,
    is_user_email_verified,
    reset_user_password,
    revoke_all_auth_sessions,
    revoke_auth_session,
    revoke_other_auth_sessions,
    touch_auth_session,
    update_email_notification_preferences,
    update_user_backup_codes,
    verify_user_email_token,
)

from db.tracker import (
    MAX_BULK_BATCH_SIZE,
    escape_csv_cell,
    bulk_update_application_status,
    bulk_add_application_tag,
    bulk_delete_applications,
    bulk_export_applications_csv,
    clear_recently_viewed_jobs,
    create_saved_job_description,
    delete_calendar_event,
    delete_job_application,
    delete_job_contact,
    delete_saved_job_description,
    export_job_applications_csv,
    export_job_applications_json,
    export_job_applications_markdown,
    get_application_funnel_analytics,
    get_application_stage_counts,
    get_calendar_events,
    get_company_checklist,
    get_default_company_checklist,
    get_job_application_by_id,
    get_job_applications,
    get_job_applications_export_summary,
    get_job_contacts,
    get_recently_viewed_jobs,
    get_saved_job_description_by_id,
    get_saved_job_descriptions,
    record_viewed_job,
    remove_recently_viewed_job,
    reset_company_checklist,
    save_calendar_event,
    save_company_checklist,
    save_job_application,
    save_job_contact,
    update_calendar_event,
    update_job_application,
    update_job_application_status,
    update_saved_job_description,
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
from utils.interview_helpful_service import (
    check_voting_rate_limit,
    get_contributor_helped_students_count,
    get_user_helpful_votes,
    toggle_helpful_vote,
)

logger = logging.getLogger(__name__)

__all__ = [
    # Connection & migrations
    "create_connection",
    "get_connection",
    "transaction",
    "get_db_connection",
    "MIGRATIONS",
    "init_db",
    "run_migrations",
    "ensure_migration_table",
    "get_applied_migrations",
    # Analyses
    "save_analysis",
    "get_analysis",
    "get_student_versions",
    "get_previous_analysis",
    "get_previous_analysis_for_target",
    "get_student_versions_for_target",
    "get_recent_analyses",
    "delete_analysis",
    "get_analyses_history",
    "save_batch_comparison",
    "get_batch_comparison",
    "create_analysis_annotation",
    "get_analysis_annotations",
    "get_analysis_annotation",
    "update_analysis_annotation",
    "delete_analysis_annotation",
    "save_analysis_section_feedback",
    "get_analysis_section_feedback",
    "get_analysis_section_feedback_summary",
    "export_analysis_section_feedback_csv",
    # Interviews
    "save_interview_session",
    "create_interview_session",
    "get_interview_session",
    "update_interview_session",
    "get_student_interview_history",
    "get_student_interview_performance_analytics",
    "save_interview_bookmark",
    "bookmark_interview_question",
    "remove_interview_bookmark",
    "get_interview_bookmarks",
    "get_interview_bookmark_by_id",
    "record_bookmark_score",
    "MAX_BOOKMARKS_PER_STUDENT",
    "GOOD_SCORE_THRESHOLD",
    "MASTERY_GOOD_SCORE_COUNT",
    "update_interview_bookmark_practice",
    "reset_interview_bookmarks_practice",
    "get_interview_bookmark_practice_stats",
    "is_interview_bookmarked",
    "save_interview_experience",
    "get_interview_experiences",
    "report_interview_experience",
    "toggle_helpful_vote",
    "get_user_helpful_votes",
    "get_contributor_helped_students_count",
    "check_voting_rate_limit",
    "create_preparation_note",
    "get_preparation_notes",
    "get_preparation_note_by_id",
    "update_preparation_note",
    "delete_preparation_note",
    "get_note_category_counts",
    "save_gd_attempt",
    "get_student_gd_attempts",
    "get_gd_attempt_by_id",
    "delete_student_gd_attempts",
    # Tracker
    "save_job_application",
    "get_job_applications",
    "delete_job_application",
    "update_job_application_status",
    "get_job_application_by_id",
    "update_job_application",
    "get_application_stage_counts",
    "get_application_funnel_analytics",
    "export_job_applications_csv",
    "export_job_applications_json",
    "export_job_applications_markdown",
    "get_job_applications_export_summary",
    "save_calendar_event",
    "get_calendar_events",
    "update_calendar_event",
    "delete_calendar_event",
    "get_default_company_checklist",
    "get_company_checklist",
    "save_company_checklist",
    "reset_company_checklist",
    "record_viewed_job",
    "get_recently_viewed_jobs",
    "remove_recently_viewed_job",
    "clear_recently_viewed_jobs",
    "create_saved_job_description",
    "get_saved_job_descriptions",
    "get_saved_job_description_by_id",
    "update_saved_job_description",
    "delete_saved_job_description",
    "save_job_contact",
    "get_job_contacts",
    "delete_job_contact",
    # Bulk Application Operations (#848)
    "MAX_BULK_BATCH_SIZE",
    "escape_csv_cell",
    "bulk_update_application_status",
    "bulk_add_application_tag",
    "bulk_delete_applications",
    "bulk_export_applications_csv",
    # Coding Practice
    "save_coding_submission",
    "get_coding_submissions",
    "save_skill_self_assessment",
    "get_student_skill_self_assessments",
    "delete_skill_self_assessment",
    "get_default_technical_skills_catalog",
    "save_student_profile",
    "get_student_profile",
    "calculate_profile_completion",
    "save_coding_bookmark",
    "remove_coding_bookmark",
    "get_coding_bookmarks",
    "is_coding_bookmarked",
    "save_aptitude_attempt",
    "get_student_aptitude_attempts",
    "get_aptitude_attempt_by_id",
    "get_student_weak_aptitude_topics",
    "delete_student_aptitude_attempts",
    # Users & Auth
    "create_user",
    "get_user_by_email",
    "create_auth_session",
    "get_active_auth_session",
    "touch_auth_session",
    "get_active_auth_sessions",
    "revoke_auth_session",
    "revoke_other_auth_sessions",
    "revoke_all_auth_sessions",
    "change_user_password",
    "get_user_by_id",
    "enable_user_two_factor",
    "disable_user_two_factor",
    "update_user_backup_codes",
    "get_user_two_factor_status",
    "get_email_notification_preferences",
    "update_email_notification_preferences",
    "generate_email_verification_token",
    "verify_user_email_token",
    "is_user_email_verified",
    "create_password_reset_token",
    "get_valid_password_reset_token",
    "reset_user_password",
    "invalidate_password_reset_token",
    "get_user_data_export_manifest",
    "delete_user_account_and_personal_data",
    # Reports
    "create_shared_report",
    "get_shared_report",
    "record_shared_report_view",
    "get_active_shares_for_analysis",
    "revoke_shared_report",
    "get_shared_report_views",
    "create_secure_share_link",
    "get_secure_shared_report",
    "revoke_secure_share_link",
    "list_student_secure_shares",
    # Streaks
    "record_preparation_activity",
    "_recalculate_student_streak",
    "get_student_streak",
    "get_streak_calendar",
    "get_recent_preparation_activities",
    "get_student_preparation_summary_data",
    # Cohorts
    "_seed_cohort_benchmarks",
    "get_user_leaderboard_opt_in",
    "set_user_leaderboard_opt_in",
    "record_or_update_cohort_benchmark",
    "get_cohort_benchmark_by_analysis_id",
    "get_cohort_benchmarks",
    "get_cohort_stats",
    "get_user_placement_history",
    "get_student_top_skill_gaps_for_company",
    # System
    "create_database_backup",
    "verify_database_integrity",
    "restore_database_backup",
    "calculate_gemini_call_cost",
    "log_gemini_api_call",
    "get_gemini_usage_summary",
    "submit_user_feedback",
    "get_user_feedback_list",
    "get_feedback_metrics_summary",
    "COST_PER_PROMPT_TOKEN",
    "COST_PER_CANDIDATE_TOKEN",
]
