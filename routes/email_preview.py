"""
Development-Only Email Preview & Inspector Route (#889).

Enables developers and designers to preview and verify responsive HTML rendering
and plain-text alternatives for all placement platform transactional emails.

Disabled in production environments per acceptance criteria.
"""

import os
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    Response,
)

import config
from utils.email_service import EmailNotificationService

email_preview_bp = Blueprint("email_preview", __name__)


def is_email_preview_disabled() -> bool:
    """Checks whether the development email preview route should be disabled (#889)."""
    # 1. Explicit production environment checks take precedence
    if (
        os.getenv("FLASK_ENV", "").strip().lower() == "production"
        or os.getenv("ENVIRONMENT", "").strip().lower() == "production"
        or getattr(config, "IS_VERCEL", False)
    ):
        return True

    if current_app:
        if (
            str(current_app.config.get("ENV", "")).strip().lower() == "production"
            or str(current_app.config.get("FLASK_ENV", "")).strip().lower() == "production"
        ):
            return True
        if current_app.config.get("EMAIL_PREVIEW_ENABLED") is False:
            return True

    return False


@email_preview_bp.route("/dev/email-preview", methods=["GET"])
@email_preview_bp.route("/email-preview", methods=["GET"])
def dev_email_preview_view():
    """
    Renders development preview of transactional emails or raw parts (#889).
    Disabled in production environments.
    """
    if is_email_preview_disabled():
        abort(404, description="Email preview is disabled in production.")

    email_type = request.args.get("type", "welcome").strip().lower()
    part = request.args.get("part", "preview").strip().lower()
    locale = request.args.get("locale", request.args.get("lang", "en")).strip()

    service = EmailNotificationService()

    # Load realistic sample data
    sample_data = service.get_sample_email_data(email_type)
    # Allow URL query param overrides for interactive testing (e.g. testing HTML escape)
    for k, v in request.args.items():
        if k not in ("type", "part", "locale", "lang"):
            sample_data[k] = v

    email_data = service.build_templated_email(
        email_type=email_type,
        locale=locale,
        **sample_data,
    )

    if part == "html":
        return Response(email_data["html"], mimetype="text/html; charset=utf-8")
    elif part in ("text", "plain"):
        return Response(email_data["plain_text"], mimetype="text/plain; charset=utf-8")
    elif part == "json":
        return jsonify({
            "success": True,
            "supported_types": service.SUPPORTED_EMAIL_TYPES,
            **email_data,
        })

    # Render developer dashboard
    return render_template(
        "email/preview_dashboard.html",
        current_type=email_data["email_type"],
        supported_types=service.SUPPORTED_EMAIL_TYPES,
        email_data=email_data,
    )
