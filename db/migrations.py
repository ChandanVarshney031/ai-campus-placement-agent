"""
Schema Migrations Runner (db/migrations.py)
===========================================
Defines the versioned schema migration registry and runner. Ensures all table
definitions, alterations, and indexes are documented in one authoritative place.
"""

import logging
import sqlite3
from typing import Callable, List, NamedTuple, Optional

from db.connection import get_connection, transaction

logger = logging.getLogger(__name__)


class Migration(NamedTuple):
    version: str
    description: str
    action: Callable[[sqlite3.Cursor], None]


def _migration_001_core_schema(cursor: sqlite3.Cursor) -> None:
    """Core analysis, interviews, users, applications, cohort tables."""
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interviews (
            id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            current_question_index INTEGER NOT NULL DEFAULT 0,
            questions_json TEXT NOT NULL,
            answers_json TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
        )
    """)
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
            resume_analysis_id TEXT,
            resume_filename TEXT,
            resume_version INTEGER,
            job_description_snapshot TEXT,
            tags TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            leaderboard_opt_in INTEGER NOT NULL DEFAULT 1,
            is_verified INTEGER DEFAULT 0,
            verification_token TEXT,
            token_created_at TIMESTAMP,
            two_factor_enabled INTEGER NOT NULL DEFAULT 0,
            two_factor_secret TEXT,
            two_factor_backup_codes TEXT
        )
    """)
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
    # Core Indexes
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)")


def _migration_002_user_auth_and_sessions(cursor: sqlite3.Cursor) -> None:
    """User sessions, notification preferences, feedback, and assessments."""
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_active ON auth_sessions (user_id, revoked_at, last_seen_at DESC)")

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_feedback_student ON user_feedback (student_id, created_at DESC)")

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tech_assessments_student ON technical_self_assessments (student_id, category)")


def _migration_003_tracker_and_events(cursor: sqlite3.Cursor) -> None:
    """Preparation calendar, checklists, contacts, and column alterations (#477)."""
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_calendar_student_date ON preparation_calendar_events (student_id, event_date ASC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_calendar_student_completed ON preparation_calendar_events (student_id, completed, event_date ASC)")

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
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_checklist_student_comp ON company_preparation_checklists (student_id, company_name)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_application_contacts (
            id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT,
            email TEXT,
            phone TEXT,
            linkedin_url TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (application_id) REFERENCES job_applications(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_app_contacts_app ON job_application_contacts (application_id, student_id)")

    # Ensure deadline_date (#477) and metadata columns are verified in job_applications
    cursor.execute("PRAGMA table_info(job_applications)")
    app_cols = {col[1] for col in cursor.fetchall()}
    for col_name, col_def in [
        ("location", "TEXT DEFAULT 'Remote'"),
        ("job_type", "TEXT DEFAULT 'Full-Time'"),
        ("experience_level", "TEXT DEFAULT 'Entry-Level'"),
        ("deadline_date", "TEXT"),
        ("resume_analysis_id", "TEXT"),
        ("resume_filename", "TEXT"),
        ("resume_version", "INTEGER"),
        ("job_description_snapshot", "TEXT"),
        ("tags", "TEXT DEFAULT ''"),
    ]:
        if col_name not in app_cols:
            cursor.execute(f"ALTER TABLE job_applications ADD COLUMN {col_name} {col_def}")


def _migration_004_coding_practice_and_interviews(cursor: sqlite3.Cursor) -> None:
    """Coding submissions, student profiles, bookmarks, experiences, prep notes."""
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_coding_subs_student ON coding_submissions (student_id, submitted_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_coding_subs_analysis ON coding_submissions (analysis_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_profiles (
            student_id TEXT PRIMARY KEY,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            department TEXT,
            grad_year INTEGER,
            cgpa REAL,
            backlogs INTEGER DEFAULT 0,
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interview_bookmarks_student ON bookmarked_interview_questions (student_id, category, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interview_bookmarks_practiced ON bookmarked_interview_questions (student_id, is_practiced, created_at DESC)")

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_company ON interview_experiences (company_name, interview_date DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_reported_date ON interview_experiences (reported_flag, interview_date DESC, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_student ON interview_experiences (student_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_experiences_helpful ON interview_experiences (helpful_count DESC, interview_date DESC)")

    cursor.execute("PRAGMA table_info(interview_experiences)")
    exp_col_names = {row["name"] for row in cursor.fetchall()}
    if "helpful_count" not in exp_col_names:
        cursor.execute("ALTER TABLE interview_experiences ADD COLUMN helpful_count INTEGER NOT NULL DEFAULT 0")

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


def _migration_005_annotations_and_saved_jds(cursor: sqlite3.Cursor) -> None:
    """Annotations, saved job descriptions, recently viewed jobs."""
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jd_student_company ON saved_job_descriptions (student_id, company_name)")

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


def _migration_006_tokens_and_streaks(cursor: sqlite3.Cursor) -> None:
    """Auth tokens, activity log, student streaks, section feedback."""
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


def _migration_007_monitoring_and_practice(cursor: sqlite3.Cursor) -> None:
    """Analysis cache, aptitude, gd attempts, gemini logs, telemetry."""
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


def _migration_008_reports_and_sharing(cursor: sqlite3.Cursor) -> None:
    """Mentor sharing links, report view counts, and audit logs."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shared_reports (
            id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            student_id TEXT NOT NULL DEFAULT 'default_student',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            access_code TEXT,
            FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_reports_analysis ON shared_reports (analysis_id, is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_reports_student ON shared_reports (student_id, created_at DESC)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shared_report_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_id TEXT NOT NULL,
            viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            viewer_ip TEXT,
            user_agent TEXT,
            FOREIGN KEY (share_id) REFERENCES shared_reports(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_report_views_share ON shared_report_views (share_id, viewed_at DESC)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS secure_shared_reports (
            id TEXT PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            analysis_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            view_count INTEGER DEFAULT 0,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP,
            last_viewed_at TIMESTAMP,
            FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_secure_shared_reports_token ON secure_shared_reports (token)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_secure_shared_reports_student ON secure_shared_reports (student_id, created_at DESC)")


def _migration_009_student_profile_college(cursor: sqlite3.Cursor) -> None:
    """Add college column to student_profiles table (#654)."""
    cursor.execute("PRAGMA table_info(student_profiles)")
    cols = [col[1] for col in cursor.fetchall()]
    if "college" not in cols:
        cursor.execute("ALTER TABLE student_profiles ADD COLUMN college TEXT")


def _migration_010_faculty_interventions(cursor: sqlite3.Cursor) -> None:
    """Create faculty_interventions table and indexes for coordinator intervention queue (#786)."""
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


def _migration_010_api_retry_records(cursor: sqlite3.Cursor) -> None:
    """Create api_retry_records table for failed operation request replay (#445, #553)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_retry_records (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default_student',
            operation_type TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            http_method TEXT NOT NULL DEFAULT 'POST',
            payload_json TEXT NOT NULL,
            idempotency_key TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'failed',
            error_message TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            last_response_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_retry_records_user ON api_retry_records (user_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_retry_records_expires ON api_retry_records (expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_retry_records_idempotency ON api_retry_records (idempotency_key)")


def _migration_011_deprecated_api_metrics(cursor: sqlite3.Cursor) -> None:
    """Create deprecated_api_metrics table to track deprecated field and route invocations (#445, #553)."""
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


def _migration_012_student_profile_backlogs(cursor: sqlite3.Cursor) -> None:
    """Add the backlogs field to existing student profiles (#844)."""
    cursor.execute("PRAGMA table_info(student_profiles)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "backlogs" not in existing_cols:
        cursor.execute(
            "ALTER TABLE student_profiles ADD COLUMN backlogs INTEGER DEFAULT 0"
        )


def _migration_014_saved_questions_score_tracking(cursor: sqlite3.Cursor) -> None:
    """Adds last_score and good_score_count columns to bookmarked_interview_questions (#846)."""
    cursor.execute("PRAGMA table_info(bookmarked_interview_questions)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "last_score" not in existing_cols:
        cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN last_score REAL")
    if "good_score_count" not in existing_cols:
        cursor.execute("ALTER TABLE bookmarked_interview_questions ADD COLUMN good_score_count INTEGER NOT NULL DEFAULT 0")


def _migration_015_account_lockouts(cursor: sqlite3.Cursor) -> None:
    """Create failed_login_attempts and account_lockouts tables (#851)."""
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_lockouts (
            identifier TEXT PRIMARY KEY,
            locked_until REAL NOT NULL,
            created_at REAL NOT NULL,
            notified_at REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_lockouts_until ON account_lockouts (locked_until)")


# Documented, sequential migration registry
def _migration_013_feature_flags(cursor: sqlite3.Cursor) -> None:
    """Create feature_flags and feature_flag_audit_logs tables for gradual rollouts (#854)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            key TEXT PRIMARY KEY,
            description TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 0,
            rollout_percentage INTEGER NOT NULL DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
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
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ff_audit_flag ON feature_flag_audit_logs(flag_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ff_audit_created ON feature_flag_audit_logs(created_at DESC)")


def _migration_017_learning_resource_progress(cursor: sqlite3.Cursor) -> None:
    """Create student_learning_resource_progress table for skill & roadmap resources (#885)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_learning_resource_progress (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'not_started',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, resource_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_slrp_user_id ON student_learning_resource_progress(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_slrp_resource_id ON student_learning_resource_progress(resource_id)")


def _migration_018_placement_activity_timeline(cursor: sqlite3.Cursor) -> None:
    """Create placement activity timeline table for tracking readiness milestones and custom events."""
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


MIGRATIONS: List[Migration] = [
    Migration("001_core_schema", "Create analyses, interviews, users, job applications, cohort benchmarks", _migration_001_core_schema),
    Migration("002_user_auth_and_sessions", "Create auth_sessions, notification preferences, feedback, assessments", _migration_002_user_auth_and_sessions),
    Migration("003_tracker_and_events", "Create prep calendar, checklists, contacts; ensure deadline_date column", _migration_003_tracker_and_events),
    Migration("004_coding_practice_and_interviews", "Create coding submissions, profiles, bookmarks, experiences, prep notes", _migration_004_coding_practice_and_interviews),
    Migration("005_annotations_and_saved_jds", "Create analysis annotations, saved job descriptions, viewed jobs", _migration_005_annotations_and_saved_jds),
    Migration("006_tokens_and_streaks", "Create email/password reset tokens, activity log, student streaks, feedback", _migration_006_tokens_and_streaks),
    Migration("007_monitoring_and_practice", "Create analysis cache, aptitude, gd attempts, gemini logs, telemetry", _migration_007_monitoring_and_practice),
    Migration("008_reports_and_sharing", "Create shared reports and view logs for mentor reviews", _migration_008_reports_and_sharing),
    Migration("009_student_profile_college", "Add college column to student_profiles for campus tracking", _migration_009_student_profile_college),
    Migration("010_api_retry_records", "Create api_retry_records table for failed operation request replay", _migration_010_api_retry_records),
    Migration("011_deprecated_api_metrics", "Create deprecated_api_metrics table for API field and route deprecation tracking", _migration_011_deprecated_api_metrics),
    Migration("012_student_profile_backlogs", "Add backlogs column to student_profiles for eligibility checks", _migration_012_student_profile_backlogs),
    Migration("013_feature_flags", "Create feature_flags and audit logs for gradual rollout", _migration_013_feature_flags),
    Migration("014_saved_questions_score_tracking", "Add score tracking to bookmarked interview questions", _migration_014_saved_questions_score_tracking),
    Migration("015_account_lockouts", "Create account lockout tracking tables", _migration_015_account_lockouts),
    Migration("016_faculty_interventions", "Create faculty intervention tables", _migration_010_faculty_interventions),
    Migration("017_learning_resource_progress", "Create learning resource progress table", _migration_017_learning_resource_progress),
    Migration("018_placement_activity_timeline", "Create placement_activity_timeline table for readiness activity tracking", _migration_018_placement_activity_timeline),
]


def ensure_migration_table(cursor: sqlite3.Cursor) -> None:
    """Ensures schema_migrations table exists to track applied migrations."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        )
    """)


def get_applied_migrations(cursor: sqlite3.Cursor) -> List[str]:
    """Returns list of version strings of migrations already applied."""
    ensure_migration_table(cursor)
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC")
    return [row[0] for row in cursor.fetchall()]


def run_migrations(db_path: Optional[str] = None) -> int:
    """
    Executes any unapplied migrations in sequential order.
    Returns the count of newly applied migrations.
    """
    applied_count = 0
    with transaction(db_path) as cursor:
        ensure_migration_table(cursor)
        cursor.execute("SELECT version FROM schema_migrations")
        applied_set = {row[0] for row in cursor.fetchall()}

        for mig in MIGRATIONS:
            if mig.version not in applied_set:
                logger.info(f"Applying database migration {mig.version}: {mig.description}")
                mig.action(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    (mig.version, mig.description)
                )
                applied_count += 1

    return applied_count


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initializes database tables and indexes via the versioned migration list.
    Seeds default cohort peer benchmarks if empty.
    """
    run_migrations(db_path)

    # Seed initial cohort benchmarks if table is empty
    with transaction(db_path) as cursor:
        cursor.execute("SELECT COUNT(*) FROM cohort_benchmarks")
        if cursor.fetchone()[0] == 0:
            from utils.database import _seed_cohort_benchmarks
            _seed_cohort_benchmarks(cursor)
