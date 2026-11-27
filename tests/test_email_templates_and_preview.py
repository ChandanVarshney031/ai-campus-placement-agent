"""
Unit and Integration Tests for Responsive Email Templates & Preview Route (#889).

Acceptance Criteria:
- All emails use the shared template and include a plain-text part.
- Dynamic values are HTML-escaped.
- The preview route is disabled in production.
- Tests check the structure for each email type.
"""

import html
from unittest.mock import patch, MagicMock
import pytest

from app import create_app
from utils.email_service import EmailNotificationService


@pytest.fixture
def email_service():
    """Provides an instance of EmailNotificationService."""
    return EmailNotificationService()


@pytest.fixture
def app_instance():
    """Provides a testing Flask app instance."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["ENV"] = "development"
    app.config["EMAIL_PREVIEW_ENABLED"] = True
    return app


@pytest.fixture
def client(app_instance):
    """Provides a Flask test client."""
    with app_instance.test_client() as client:
        yield client


# ==============================================================================
# 1. Structure Tests for Each Supported Email Type
# ==============================================================================


@pytest.mark.parametrize("email_type", EmailNotificationService.SUPPORTED_EMAIL_TYPES)
def test_all_email_types_produce_both_html_and_plaintext(email_service, email_type):
    """
    AC 1 & 4:
    - All emails use the shared template and include a plain-text part.
    - Tests check the structure for each email type.
    """
    sample_data = email_service.get_sample_email_data(email_type)
    email_data = email_service.build_templated_email(email_type=email_type, **sample_data)

    assert email_data is not None
    assert "subject" in email_data and len(email_data["subject"]) > 0
    assert "html" in email_data and len(email_data["html"]) > 0
    assert "plain_text" in email_data and len(email_data["plain_text"]) > 0

    html_content = email_data["html"]
    plain_text = email_data["plain_text"]

    # --- HTML Structure Validations ---
    # 1. Doctype and Container
    assert "<!DOCTYPE html>" in html_content
    assert "<meta name=\"viewport\"" in html_content
    assert "max-width: 600px" in html_content

    # 2. Header with platform branding
    assert "AI Campus Placement Agent" in html_content
    assert "#4f46e5" in html_content  # Brand indigo styling

    # 3. Content block with headline
    assert email_data["headline"] in html_content

    # 4. Button CTA Block
    if email_data.get("cta_url") and email_data.get("cta_text"):
        assert email_data["cta_url"] in html_content
        assert email_data["cta_text"] in html_content
        assert "border-radius: 9999px" in html_content  # Pill button styling
        assert "If the button above does not work" in html_content

    # 5. Footer with preferences and unsubscribe links
    assert "Notification Preferences" in html_content
    assert "Unsubscribe" in html_content
    assert "/notification-settings" in html_content

    # --- Plain-Text Structure Validations ---
    # 1. Header
    assert "AI Campus Placement Agent" in plain_text
    assert "=" * 50 in plain_text

    # 2. Headline
    assert email_data["headline"] in plain_text

    # 3. CTA Link
    if email_data.get("cta_url") and email_data.get("cta_text"):
        assert email_data["cta_text"] in plain_text
        assert email_data["cta_url"] in plain_text

    # 4. Footer
    assert "Notification Preferences:" in plain_text
    assert "Unsubscribe:" in plain_text


# ==============================================================================
# 2. HTML-Escaping of Dynamic Values
# ==============================================================================


def test_dynamic_values_are_html_escaped(email_service):
    """
    AC 2:
    - Dynamic values are HTML-escaped in HTML output.
    - Prevents script injection and broken layouts.
    - Natural characters remain intact in plain-text output.
    """
    xss_student_name = "<script>alert('xss_name')</script> & Co."
    xss_company_name = "<b onmouseover=evil()>Google & Microsoft</b>"
    xss_role_title = "Lead <Architect> & \"Fellow\""
    xss_notes = "<img src=x onerror=alert('xss_notes')> & testing"

    email_data = email_service.build_templated_email(
        "status_change",
        student_name=xss_student_name,
        company_name=xss_company_name,
        role_title=xss_role_title,
        previous_status="Applied",
        new_status="Interview Scheduled",
        notes=xss_notes,
    )

    html_out = email_data["html"]
    plain_out = email_data["plain_text"]

    # Verify malicious tags are NEVER rendered unescaped in HTML
    assert "<script>" not in html_out
    assert "</script>" not in html_out
    assert "<b onmouseover" not in html_out
    assert "<img src=x" not in html_out
    assert "<Architect>" not in html_out

    # Verify correctly escaped entities are present in HTML
    assert html.escape(xss_student_name) in html_out
    assert html.escape(xss_company_name) in html_out
    assert html.escape(xss_role_title) in html_out
    assert html.escape(xss_notes) in html_out

    # Plain text should contain the readable characters
    assert "alert('xss_name')" in plain_out
    assert "Google & Microsoft" in plain_out


def test_invitation_dynamic_values_escaped(email_service):
    """Verifies student invitation dynamic parameters are escaped."""
    malicious_college = "Dept of <Cyber> & 'Security'"
    malicious_student = "Candidate <Tester>"

    email_data = email_service.build_templated_email(
        "student_invitation",
        student_name=malicious_student,
        college_name=malicious_college,
        invite_url="http://localhost:5000/register?token=test",
        expires_at_str="2026-12-01 & 10:00 AM",
    )

    html_out = email_data["html"]
    assert "<Cyber>" not in html_out
    assert "<Tester>" not in html_out
    assert html.escape(malicious_college) in html_out
    assert html.escape(malicious_student) in html_out


# ==============================================================================
# 3. Dispatch Methods Pass Both Parts to MIMEMultipart
# ==============================================================================


def test_send_email_attaches_both_html_and_text_parts(email_service):
    """
    Verifies send_email properly attaches both 'text/plain' and 'text/html'
    parts to the outbound MIMEMultipart payload.
    """
    with patch("smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        # Set delivery enabled for live SMTP assertion
        with patch.object(email_service, "smtp_server", "smtp.live.example.com"):
            sent = email_service.send_welcome_email(
                recipient_email="student@campus.edu",
                student_name="Dev Student",
            )
            assert sent is True
            assert mock_server.send_message.called

            sent_msg = mock_server.send_message.call_args[0][0]
            assert sent_msg.is_multipart()

            parts = sent_msg.get_payload()
            assert len(parts) >= 2

            content_types = [p.get_content_type() for p in parts]
            assert "text/plain" in content_types
            assert "text/html" in content_types

            plain_part = [p for p in parts if p.get_content_type() == "text/plain"][0]
            html_part = [p for p in parts if p.get_content_type() == "text/html"][0]

            decoded_plain = plain_part.get_payload(decode=True).decode("utf-8")
            decoded_html = html_part.get_payload(decode=True).decode("utf-8")
            assert "AI Campus Placement Agent" in decoded_plain
            assert "<!DOCTYPE html>" in decoded_html


# ==============================================================================
# 4. Preview Route Functionality & Production Guard
# ==============================================================================


def test_preview_route_available_in_development(client):
    """
    Verifies that the preview route is accessible in development environments
    and serves both the interactive dashboard, raw HTML, raw text, and JSON.
    """
    # 1. Main Dashboard View
    res = client.get("/dev/email-preview?type=welcome")
    assert res.status_code == 200
    assert b"Email Template Preview &amp; Inspector" in res.data
    assert b"Responsive HTML Preview" in res.data
    assert b"Plain-Text Alternative Part" in res.data

    # 2. Raw HTML Part
    res_html = client.get("/dev/email-preview?type=welcome&part=html")
    assert res_html.status_code == 200
    assert "text/html" in res_html.headers.get("Content-Type", "")
    assert b"<!DOCTYPE html>" in res_html.data
    assert b"AI Campus Placement Agent" in res_html.data

    # 3. Raw Plain Text Part
    res_text = client.get("/dev/email-preview?type=welcome&part=text")
    assert res_text.status_code == 200
    assert "text/plain" in res_text.headers.get("Content-Type", "")
    assert b"AI Campus Placement Agent" in res_text.data
    assert b"Notification Preferences:" in res_text.data

    # 4. JSON API Format
    res_json = client.get("/dev/email-preview?type=interview_reminder&part=json")
    assert res_json.status_code == 200
    assert res_json.is_json
    data = res_json.get_json()
    assert data["success"] is True
    assert data["email_type"] == "interview_reminder"
    assert "html" in data
    assert "plain_text" in data
    assert "subject" in data

    # 5. Alias Route /email-preview
    res_alias = client.get("/email-preview?type=deadline_reminder")
    assert res_alias.status_code == 200


def test_preview_route_disabled_in_production(app_instance):
    """
    AC 3:
    - The preview route is disabled in production environments.
    - Verified when FLASK_ENV=production or ENVIRONMENT=production or EMAIL_PREVIEW_ENABLED=False.
    """
    # Test with EMAIL_PREVIEW_ENABLED = False
    app_instance.config["EMAIL_PREVIEW_ENABLED"] = False
    with app_instance.test_client() as prod_client:
        res = prod_client.get("/dev/email-preview")
        assert res.status_code == 404

    # Test with ENV = production
    app_instance.config["EMAIL_PREVIEW_ENABLED"] = True
    app_instance.config["ENV"] = "production"
    with patch.dict("os.environ", {"FLASK_ENV": "production"}):
        with app_instance.test_client() as prod_client:
            res_dev = prod_client.get("/dev/email-preview")
            assert res_dev.status_code == 404

            res_alias = prod_client.get("/email-preview")
            assert res_alias.status_code == 404
