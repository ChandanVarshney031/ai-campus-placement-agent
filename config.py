import os
from pathlib import Path
from dotenv import load_dotenv

# Project version
__version__ = "1.0.0"

# Load environment variables from .env
load_dotenv()

# Base project directory
BASE_DIR = Path(__file__).resolve().parent

# Detect Vercel environment
IS_VERCEL = os.getenv("VERCEL") == "1"

# Directory configurations
if IS_VERCEL:
    import tempfile
    # In Vercel serverless (Linux), /tmp is the writable storage directory.
    # On Windows or local test environments where /tmp is not directly accessible, fall back to tempfile.gettempdir().
    if os.name == "nt":
        STORAGE_DIR = Path(tempfile.gettempdir()) / "placement_agent_vercel"
    else:
        STORAGE_DIR = Path("/tmp")
else:
    STORAGE_DIR = BASE_DIR

UPLOAD_FOLDER = STORAGE_DIR / "uploads"
REPORT_FOLDER = STORAGE_DIR / "reports"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(STORAGE_DIR / "placement_agent.db")))
DEV_DATABASE_PATH = STORAGE_DIR / "placement_agent_dev.db"
BACKUP_DIR = STORAGE_DIR / "backups"

# Ensure folders exist safely with graceful fallback
try:
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    REPORT_FOLDER.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError):
    import tempfile
    STORAGE_DIR = Path(tempfile.gettempdir()) / "placement_agent_fallback"
    UPLOAD_FOLDER = STORAGE_DIR / "uploads"
    REPORT_FOLDER = STORAGE_DIR / "reports"
    DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(STORAGE_DIR / "placement_agent.db")))
    DEV_DATABASE_PATH = STORAGE_DIR / "placement_agent_dev.db"
    BACKUP_DIR = STORAGE_DIR / "backups"
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    REPORT_FOLDER.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# API & Security Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
if IS_VERCEL or os.getenv("FLASK_ENV", "").strip().lower() == "production":
    # A serverless deployment must never sign sessions with the shared local default.
    secret_value = (FLASK_SECRET_KEY or "").strip()
    if (
        len(secret_value.encode("utf-8")) < 32
        or len(set(secret_value)) < 2
        or secret_value.lower().startswith(("replace_", "change-", "changeme", "your_", "your-"))
    ):
        raise RuntimeError(
            "Production requires FLASK_SECRET_KEY (or SECRET_KEY) with at least "
            "32 bytes of random secret material, not a default or placeholder."
        )
elif not FLASK_SECRET_KEY:
    FLASK_SECRET_KEY = os.getenv("DEFAULT_SECRET_KEY", "dev-secret-key-12345")

ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "admin-default-secret-key-2026")

# Centralized model setting
GEMINI_MODEL = "gemini-2.5-flash"

# Upload file size limit (default: 5MB, configurable via MAX_UPLOAD_SIZE_MB or MAX_RESUME_SIZE_MB) (Issue #153)
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", os.getenv("MAX_RESUME_SIZE_MB", "5")))
MAX_RESUME_FILE_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_FILE_SIZE_BYTES = MAX_RESUME_FILE_SIZE_BYTES

# API Request Size Limits & Diagnostics (#784)
MAX_JSON_PAYLOAD_MB = int(os.getenv("MAX_JSON_PAYLOAD_MB", "2"))
MAX_JSON_PAYLOAD_BYTES = int(os.getenv("MAX_JSON_PAYLOAD_BYTES", str(MAX_JSON_PAYLOAD_MB * 1024 * 1024)))
MAX_CSV_FILE_SIZE_MB = int(os.getenv("MAX_CSV_FILE_SIZE_MB", "5"))
MAX_CSV_FILE_SIZE_BYTES = MAX_CSV_FILE_SIZE_MB * 1024 * 1024

# Total HTTP request content length (includes multipart form boundaries and metadata)
DEFAULT_MAX_CONTENT_LENGTH = max(
    MAX_RESUME_FILE_SIZE_BYTES + 2 * 1024 * 1024,
    10 * 1024 * 1024
)
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(DEFAULT_MAX_CONTENT_LENGTH)))
MAX_FEEDBACK_COMMENT_LENGTH = int(os.getenv("MAX_FEEDBACK_COMMENT_LENGTH", "500"))

# Placement Cell Batch Resume Processing Limits (#763)
MAX_PLACEMENT_BATCH_SIZE = int(os.getenv("MAX_PLACEMENT_BATCH_SIZE", "50"))

# Structured Logging & Request Tracing Configurations (#301)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "key_value").lower()

# Rate Limiting & Abuse Prevention Configurations (Issue #160)
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")

# Configurable rate limit definitions (format: "<count>/<unit>" e.g. "5/hour", "30/hour", "100/minute")
# Stricter quotas apply to computationally expensive AI-backed endpoints to prevent API exhaustion:
RATE_LIMIT_ANALYZE = os.getenv("RATE_LIMIT_ANALYZE", "5/hour")
RATE_LIMIT_INTERVIEW = os.getenv("RATE_LIMIT_INTERVIEW", "30/hour")
RATE_LIMIT_BATCH = os.getenv("RATE_LIMIT_BATCH", "10/hour")
RATE_LIMIT_OUTREACH = os.getenv("RATE_LIMIT_OUTREACH", "20/hour")
RATE_LIMIT_STAR = os.getenv("RATE_LIMIT_STAR", "30/hour")
RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "100/minute")

RATE_LIMITS = {
    "analyze": RATE_LIMIT_ANALYZE,
    "analyze_submission": RATE_LIMIT_ANALYZE,
    "interview_eval": RATE_LIMIT_INTERVIEW,
    "interview": RATE_LIMIT_INTERVIEW,
    "batch_compare": RATE_LIMIT_BATCH,
    "generate_outreach": RATE_LIMIT_OUTREACH,
    "outreach": RATE_LIMIT_OUTREACH,
    "star_eval": RATE_LIMIT_STAR,
    "default": RATE_LIMIT_DEFAULT,
}

# Scanned / Image-Only PDF Detection & OCR Fallback (Issue #154)
# Detects image-only / scanned PDFs where PyPDF2 extracts fewer than MIN_PDF_TEXT_CHARS.
# Prevents meaningless analyses and wasted AI quota.
MIN_PDF_TEXT_CHARS = int(os.getenv("MIN_PDF_TEXT_CHARS", "100"))
MIN_PDF_EXTRACTED_CHARS = MIN_PDF_TEXT_CHARS
ENABLE_OCR_FALLBACK = os.getenv("ENABLE_OCR_FALLBACK", "false").lower() in ("true", "1", "yes")

# Gemini API Transient Error Retry & Exponential Backoff Configurations (Issue #155, #497)
# Transient errors (429 rate limit, 500/503 server errors, network timeouts) are retried automatically.
# Permanent errors (400 bad request, 401/403 invalid credentials) fail fast without retry.
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", os.getenv("GEMINI_MAX_RETRIES", "3")))
GEMINI_MAX_RETRIES = GEMINI_RETRY_ATTEMPTS
GEMINI_RETRY_INITIAL_DELAY = float(os.getenv("GEMINI_RETRY_INITIAL_DELAY", "1.0"))  # in seconds (e.g. 1s, 2s, 4s)
GEMINI_RETRY_BACKOFF_FACTOR = float(os.getenv("GEMINI_RETRY_BACKOFF_FACTOR", "2.0"))
GEMINI_RETRY_MAX_DELAY = float(os.getenv("GEMINI_RETRY_MAX_DELAY", "10.0"))  # in seconds
GEMINI_RETRY_JITTER = os.getenv("GEMINI_RETRY_JITTER", "true").lower() in ("true", "1", "yes")

# Gemini API Request Timeout (in seconds) (#497)
# Sets a request timeout on every AI call to prevent hanging when the AI provider is slow or down.
GEMINI_REQUEST_TIMEOUT = float(os.getenv("GEMINI_REQUEST_TIMEOUT", "30.0"))
AI_REQUEST_TIMEOUT = GEMINI_REQUEST_TIMEOUT

# Session Expiration & Inactivity Tracking Configurations
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", str(SESSION_TIMEOUT_MINUTES * 60)))
SESSION_WARNING_SECONDS = int(os.getenv("SESSION_WARNING_SECONDS", "120"))

# Optional CAPTCHA Bot Protection (Cloudflare Turnstile or hCaptcha) (#653)
# Supported providers: 'turnstile', 'hcaptcha'
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "").strip().lower()
CAPTCHA_SITE_KEY = os.getenv("CAPTCHA_SITE_KEY", "").strip()
CAPTCHA_SECRET_KEY = os.getenv("CAPTCHA_SECRET_KEY", "").strip()
CAPTCHA_FAIL_OPEN = os.getenv("CAPTCHA_FAIL_OPEN", "false").lower() in ("true", "1", "yes")
CAPTCHA_VERIFY_TIMEOUT = float(os.getenv("CAPTCHA_VERIFY_TIMEOUT", "5.0"))
CAPTCHA_ALWAYS_ENFORCE = os.getenv("CAPTCHA_ALWAYS_ENFORCE", "true").lower() in ("true", "1", "yes")
CAPTCHA_REQUIRE_AFTER_FAILURES = int(os.getenv("CAPTCHA_REQUIRE_AFTER_FAILURES", "0"))

# Performance Monitoring & Latency Tracking Configurations (#task-performance-monitoring)
PERFORMANCE_MONITORING_ENABLED = os.getenv("PERFORMANCE_MONITORING_ENABLED", "true").lower() in ("true", "1", "yes")
SLOW_REQUEST_THRESHOLD_MS = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500.0"))
PERFORMANCE_RETENTION_DAYS = int(os.getenv("PERFORMANCE_RETENTION_DAYS", "30"))
EXCLUDE_STATIC_PERFORMANCE = os.getenv("EXCLUDE_STATIC_PERFORMANCE", "true").lower() in ("true", "1", "yes")

# Inactive Account Lifecycle & Retention Settings (#888)
# Default is 0 (off / disabled). When set to a positive integer (e.g. 365),
# accounts with no login or activity for that period are warned and then deleted.
INACTIVE_ACCOUNT_DAYS = int(os.getenv("INACTIVE_ACCOUNT_DAYS", "0"))

# SMTP & Transactional Email Configurations (#186, #298)
SMTP_SERVER = os.getenv("SMTP_SERVER", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFICATION_SENDER_EMAIL = os.getenv("NOTIFICATION_SENDER_EMAIL", "placements@aicampus.edu")
EMAIL_DELIVERY_DISABLED = os.getenv("EMAIL_DELIVERY_DISABLED", "false").lower() in ("true", "1", "yes")

# Resume Personal Contact Details & PII Redaction Before AI Submission (#295)
ENABLE_PII_REDACTION = os.getenv("ENABLE_PII_REDACTION", "true").lower() in ("true", "1", "yes")
PII_REDACTION_ENABLED = ENABLE_PII_REDACTION

# Analysis Result Input Hash Caching Configurations (#296)
# Caches identical (resume, JD, company) submissions for 24 hours to conserve AI quota
ANALYSIS_CACHE_ENABLED = os.getenv("ANALYSIS_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
ANALYSIS_CACHE_TTL_HOURS = int(os.getenv("ANALYSIS_CACHE_TTL_HOURS", "24"))
ANALYSIS_CACHE_EXPIRY_SECONDS = int(os.getenv("ANALYSIS_CACHE_EXPIRY_SECONDS", str(ANALYSIS_CACHE_TTL_HOURS * 3600)))

# Optional Sentry Error Monitoring & Privacy-Preserving Observability (#562)
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", os.getenv("FLASK_ENV", "production")).strip()
SENTRY_RELEASE = os.getenv("SENTRY_RELEASE", f"ai-campus-placement-agent@{__version__}").strip()
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))

# Timezone & Regional Localization Settings (#650)
# Default display timezone for all dates and times shown across pages and alerts.
# Database timestamps remain strictly in UTC.
DISPLAY_TIMEZONE = os.getenv("DISPLAY_TIMEZONE", os.getenv("TIMEZONE", "Asia/Kolkata")).strip() or "Asia/Kolkata"
TIMEZONE = DISPLAY_TIMEZONE

# IP Address Anonymization for User Privacy (#852)
# Truncates IPv4 to /24 and IPv6 to /48 in logs and security activity history
ANONYMIZE_IPS = os.getenv("ANONYMIZE_IPS", "true").lower() in ("true", "1", "yes")


# HTTP Response Compression and Static Asset Caching (#857)
RESPONSE_COMPRESSION_ENABLED = os.getenv("RESPONSE_COMPRESSION_ENABLED", "true").lower() in ("true", "1", "yes")
RESPONSE_COMPRESSION_MIN_SIZE = int(os.getenv("RESPONSE_COMPRESSION_MIN_SIZE", "500"))
STATIC_CACHE_MAX_AGE_VERSIONED = int(os.getenv("STATIC_CACHE_MAX_AGE_VERSIONED", "31536000"))
STATIC_CACHE_MAX_AGE_DEFAULT = int(os.getenv("STATIC_CACHE_MAX_AGE_DEFAULT", "86400"))


# Compatibility alias for modules or tests importing `Config` class / namespace
class Config:
    DATABASE_PATH = str(DATABASE_PATH)
    UPLOAD_FOLDER = str(UPLOAD_FOLDER)
    REPORT_FOLDER = str(REPORT_FOLDER)
    GEMINI_API_KEY = GEMINI_API_KEY
    GEMINI_MODEL = GEMINI_MODEL
    GEMINI_REQUEST_TIMEOUT = GEMINI_REQUEST_TIMEOUT
    RATE_LIMIT_ENABLED = RATE_LIMIT_ENABLED
    RATE_LIMIT_DEFAULT = RATE_LIMIT_DEFAULT
    MAX_PLACEMENT_BATCH_SIZE = MAX_PLACEMENT_BATCH_SIZE
    MAX_JSON_PAYLOAD_BYTES = MAX_JSON_PAYLOAD_BYTES
    MAX_CSV_FILE_SIZE_BYTES = MAX_CSV_FILE_SIZE_BYTES
    MAX_CONTENT_LENGTH = MAX_CONTENT_LENGTH
    INACTIVE_ACCOUNT_DAYS = INACTIVE_ACCOUNT_DAYS
    INACTIVE_ACCOUNT_DAYS = INACTIVE_ACCOUNT_DAYS






