import html
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, List, Optional, Tuple

import config
from utils.output_localization import (
    DEFAULT_OUTPUT_LOCALE,
    output_text,
    resolve_output_locale,
)
from utils.formatters import format_date_ist

logger = logging.getLogger(__name__)


def _html_to_plain_text(html_str: str) -> str:
    """Fallback utility to strip HTML tags and normalize spacing for plain-text fallback."""
    if not html_str:
        return ""
    # Replace block tags and breaks with newlines
    text = re.sub(r"<(?:br|/p|/div|/tr|/h[1-6]|/li)\s*>", "\n", html_str, flags=re.IGNORECASE)
    text = re.sub(r"<li\s*>", "  * ", text, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Clean up excessive blank lines
    lines = [line.strip() for line in text.split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned


class EmailNotificationService:
    """
    Placement Activity Email Notification Service (#186, #298, #889).
    Handles sending responsive transactional emails to candidates for important placement activities:
      - Welcome registration confirmation
      - Student invitation / onboarding links
      - Password reset requests
      - Account lockout security alerts
      - Email verification links
      - New job application logged
      - Application stage/status transitions (e.g. Interview Scheduled, Offer Extended)
      - Interview schedule reminders
      - Application deadline reminders (#298)
      - Inactive account retention notices (#888)
      - All emails render with a unified base HTML template and plain-text alternative (#889)
      - Dynamic inputs are safely HTML-escaped (#889)
      - Non-crashing delivery error handling with fallback logging
      - Never logs email bodies to protect user privacy
    """

    SUPPORTED_EMAIL_TYPES: List[str] = [
        "welcome",
        "student_invitation",
        "password_reset",
        "account_lockout",
        "verification",
        "application_created",
        "status_change",
        "interview_reminder",
        "deadline_reminder",
        "inactive_account_warning",
    ]

    def __init__(self):
        # Email settings from config or environment with sensible local test defaults
        self.smtp_server = getattr(config, "SMTP_SERVER", "localhost")
        self.smtp_port = int(getattr(config, "SMTP_PORT", 587))
        self.smtp_user = getattr(config, "SMTP_USER", "")
        self.smtp_password = getattr(config, "SMTP_PASSWORD", "")
        self.sender_email = getattr(
            config, "NOTIFICATION_SENDER_EMAIL", "placements@aicampus.edu"
        )
        self.sender_name = "AI Campus Placement Agent"
        self.base_url = getattr(config, "BASE_URL", "http://localhost:5000").rstrip("/")

    @property
    def default_preferences_url(self) -> str:
        return f"{self.base_url}/notification-settings"

    @property
    def default_unsubscribe_url(self) -> str:
        return f"{self.base_url}/notification-settings?action=unsubscribe"

    # --------------------------------------------------------------------------
    # Base Templates & Rendering Helpers (#889)
    # --------------------------------------------------------------------------

    def _format_html_layout(
        self,
        title: str,
        headline: str,
        body_content: str,
        cta_url: Optional[str] = None,
        cta_text: Optional[str] = None,
        preferences_url: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> str:
        """
        Creates a mobile-responsive, client-compatible HTML email with inline styles (#889).
        Includes:
          - Consistent header branding with gradient accent
          - Content block with headline and body
          - Distinct call-to-action button with inline styles and fallback link
          - Footer with notification preferences and unsubscribe links
        """
        resolved_locale, used_fallback = resolve_output_locale(locale)
        language_notice = (
            output_text("language_fallback", DEFAULT_OUTPUT_LOCALE)
            if used_fallback
            else output_text("language_notice", resolved_locale)
        )
        lang_notice_html = (
            f'<p style="margin: 8px 0 0 0; font-size: 13px; color: #e0e7ff;">{language_notice}</p>'
            if language_notice
            else ""
        )

        pref_url = preferences_url or self.default_preferences_url
        unsub_url = unsubscribe_url or self.default_unsubscribe_url

        cta_block = ""
        if cta_url and cta_text:
            cta_block = f"""
            <div style="margin: 28px 0; text-align: center;">
              <a href="{cta_url}" style="background-color: #4f46e5; color: #ffffff; padding: 13px 28px; text-decoration: none; border-radius: 9999px; font-weight: 600; display: inline-block; font-size: 15px; box-shadow: 0 2px 4px rgba(79, 70, 229, 0.2);">
                {cta_text}
              </a>
            </div>
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin-top: -12px; margin-bottom: 0;">
              If the button above does not work, copy and paste this link into your browser:<br>
              <a href="{cta_url}" style="color: #4f46e5; text-decoration: underline; word-break: break-all;">{cta_url}</a>
            </p>
            """

        return f"""<!DOCTYPE html>
<html lang="{resolved_locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;">
  <div style="background-color: #f8fafc; padding: 32px 12px; min-height: 100%;">
    <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
      <!-- Header -->
      <tr>
        <td style="background: linear-gradient(135deg, #4f46e5 0%, #3730a3 100%); background-color: #4f46e5; padding: 32px 24px; text-align: center; color: #ffffff;">
          <h1 style="margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.025em; color: #ffffff;">
            AI Campus Placement Agent
          </h1>
          {lang_notice_html}
        </td>
      </tr>

      <!-- Content Block -->
      <tr>
        <td style="padding: 32px 24px; background-color: #ffffff;">
          <h2 style="margin-top: 0; margin-bottom: 20px; color: #0f172a; font-size: 20px; font-weight: 700; line-height: 1.3;">
            {headline}
          </h2>
          <div style="color: #334155; font-size: 15px; line-height: 1.6;">
            {body_content}
          </div>
          {cta_block}
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background-color: #f1f5f9; padding: 22px 24px; text-align: center; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; line-height: 1.6;">
          <p style="margin: 0 0 8px 0;">This email was sent by AI Campus Placement Agent to keep you informed of your placement workflow.</p>
          <p style="margin: 0;">
            <a href="{pref_url}" style="color: #4f46e5; text-decoration: underline; font-weight: 500;">Notification Preferences</a>
            <span style="color: #cbd5e1; margin: 0 8px;">|</span>
            <a href="{unsub_url}" style="color: #64748b; text-decoration: underline;">Unsubscribe</a>
          </p>
        </td>
      </tr>
    </table>
  </div>
</body>
</html>"""

    def _format_plain_text(
        self,
        headline: str,
        plain_body: str,
        cta_url: Optional[str] = None,
        cta_text: Optional[str] = None,
        preferences_url: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> str:
        """
        Formats a clean, readable plain-text alternative matching the shared template (#889).
        """
        pref_url = preferences_url or self.default_preferences_url
        unsub_url = unsubscribe_url or self.default_unsubscribe_url

        lines = [
            "=" * 50,
            "AI Campus Placement Agent",
            "=" * 50,
            "",
            headline.strip(),
            "-" * len(headline.strip()),
            "",
            plain_body.strip(),
            "",
        ]

        if cta_url and cta_text:
            lines.extend([
                f"[ {cta_text.strip()} ]",
                f"Link: {cta_url.strip()}",
                "",
            ])

        lines.extend([
            "-" * 50,
            "This email was sent by AI Campus Placement Agent.",
            f"Notification Preferences: {pref_url}",
            f"Unsubscribe: {unsub_url}",
            "=" * 50,
        ])

        return "\n".join(lines)

    def render_email(
        self,
        title: str,
        headline: str,
        body_content: str,
        plain_content: Optional[str] = None,
        cta_url: Optional[str] = None,
        cta_text: Optional[str] = None,
        preferences_url: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> Tuple[str, str]:
        """
        Unified helper producing both the HTML layout and plain-text alternative (#889).
        Returns:
            (html_content, plain_text)
        """
        html_out = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body_content,
            cta_url=cta_url,
            cta_text=cta_text,
            preferences_url=preferences_url,
            unsubscribe_url=unsubscribe_url,
            locale=locale,
        )

        effective_plain = plain_content or _html_to_plain_text(body_content)
        plain_out = self._format_plain_text(
            headline=headline,
            plain_body=effective_plain,
            cta_url=cta_url,
            cta_text=cta_text,
            preferences_url=preferences_url,
            unsubscribe_url=unsubscribe_url,
            locale=locale,
        )

        return html_out, plain_out

    # --------------------------------------------------------------------------
    # Centralized Templated Email Builder (#889)
    # --------------------------------------------------------------------------

    def build_templated_email(
        self,
        email_type: str,
        locale: str = DEFAULT_OUTPUT_LOCALE,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Builds the subject, HTML content, and plain-text alternative for any email type.
        Ensures all dynamic text values are safely HTML-escaped (#889).
        Returns a dictionary containing:
          - email_type: str
          - subject: str
          - html: str
          - plain_text: str
          - cta_url: Optional[str]
          - cta_text: Optional[str]
        """
        etype = (email_type or "").strip().lower()
        if etype == "invitation":
            etype = "student_invitation"
        elif etype in ("lockout", "account_lockout_notification"):
            etype = "account_lockout"
        elif etype in ("application", "application_logged"):
            etype = "application_created"

        resolved_locale, _ = resolve_output_locale(locale)

        # 1. Welcome Registration Confirmation
        if etype == "welcome":
            raw_name = kwargs.get("student_name") or "Candidate"
            safe_name = html.escape(str(raw_name))
            title = "Welcome to AI Campus Placement Agent"
            headline = f"Welcome aboard, {safe_name}!"
            subject = "Welcome to AI Campus Placement Agent! 🎓"
            cta_url = kwargs.get("cta_url") or f"{self.base_url}/"
            cta_text = "Start First Placement Analysis"

            body_html = """
            <p>Congratulations on taking the first proactive step toward your placement preparation!</p>
            <p>With your new account, you can:</p>
            <ul style="padding-left: 20px; margin: 12px 0;">
              <li style="margin-bottom: 6px;">
                Analyze your resume against top tier tech companies (ATS scoring and gap detection).
              </li>
              <li style="margin-bottom: 6px;">
                Practice realistic AI-driven mock interviews with dynamic STAR behavioral grading.
              </li>
              <li style="margin-bottom: 6px;">
                Track all your campus job and internship applications through a unified pipeline.
              </li>
              <li style="margin-bottom: 6px;">
                Access personalized coding roadmap and practice recommendations.
              </li>
            </ul>
            <p>Best of luck with your preparation!</p>
            """

            plain_body = (
                f"Hello {raw_name},\n\n"
                "Congratulations on taking the first proactive step toward your preparation!\n\n"
                "With your new account, you can:\n"
                "- Analyze your resume against top tier tech companies (ATS scoring & gap detection)\n"
                "- Practice realistic AI-driven mock interviews with dynamic STAR behavioral grading\n"
                "- Track all your campus job and internship applications through a unified pipeline\n"
                "- Access personalized coding roadmap and practice recommendations\n\n"
                "Best of luck with your preparation!"
            )

        # 2. Student Batch Invitation
        elif etype == "student_invitation":
            raw_name = kwargs.get("student_name") or "Student"
            safe_name = html.escape(str(raw_name))
            raw_college = kwargs.get("college_name") or ""
            safe_college = html.escape(str(raw_college))
            college_html = f" from <strong>{safe_college}</strong>" if safe_college else ""
            college_plain = f" from {raw_college}" if raw_college else ""

            raw_expires = kwargs.get("expires_at_str") or "48 hours"
            safe_expires = html.escape(str(raw_expires))

            invite_url = kwargs.get("invite_url") or f"{self.base_url}/register"

            title = "Invitation to Join AI Campus Placement Platform"
            headline = "You're invited to AI Campus Placement Agent!"
            subject = "Action Required: Your AI Campus Placement Account Invitation"
            cta_url = invite_url
            cta_text = "Accept Invitation & Activate Account"

            body_html = f"""
            <p>Dear {safe_name},</p>
            <p>Your placement cell{college_html} has invited you to activate your account on the <strong>AI Campus Placement Agent</strong> platform.</p>
            <p>This single-use onboarding link gives you immediate access to:</p>
            <ul style="padding-left: 20px; margin: 12px 0;">
              <li style="margin-bottom: 6px;"><strong>AI Resume Review & ATS Scoring</strong> customized for leading recruiters</li>
              <li style="margin-bottom: 6px;"><strong>Interactive Mock Interview Simulator</strong> with instant feedback</li>
              <li style="margin-bottom: 6px;"><strong>Company Prep Checklists & One-Page Drive Cheat Sheets</strong></li>
              <li style="margin-bottom: 6px;"><strong>Application Status Tracker</strong> for placement drives</li>
            </ul>
            <p style="color: #dc2626; font-size: 13px;"><em>Note: This secure invitation link is single-use and will expire on {safe_expires}.</em></p>
            """

            plain_body = (
                f"Dear {raw_name},\n\n"
                f"Your placement cell{college_plain} has invited you to activate your account on AI Campus Placement Agent.\n\n"
                "This single-use onboarding link gives you immediate access to:\n"
                "- AI Resume Review & ATS Scoring customized for leading recruiters\n"
                "- Interactive Mock Interview Simulator with instant feedback\n"
                "- Company Prep Checklists & One-Page Drive Cheat Sheets\n"
                "- Application Status Tracker for placement drives\n\n"
                f"Note: This secure invitation link is single-use and will expire on {raw_expires}."
            )

        # 3. Password Reset Request
        elif etype == "password_reset":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            reset_url = kwargs.get("reset_url") or f"{self.base_url}/reset-password"
            title = "Reset Your Password"
            headline = "Password Reset Request"
            subject = "Reset Your AI Campus Placement Agent Password"
            cta_url = reset_url
            cta_text = "Reset Password"

            body_html = f"""
            <p>Hello {safe_name},</p>
            <p>We received a request to reset your AI Campus Placement Agent password.</p>
            <p>Click the button below to create a new password. This link will expire in <strong>30 minutes</strong>.</p>
            <p>If you did not request this reset, you can safely ignore this email; your existing password will remain unchanged.</p>
            """

            plain_body = (
                f"Hello {raw_name},\n\n"
                "We received a request to reset your AI Campus Placement Agent password.\n"
                "Use the link below to create a new password. This link expires in 30 minutes:\n"
                f"{reset_url}\n\n"
                "If you did not request this reset, you can safely ignore this email."
            )

        # 4. Account Lockout Security Alert
        elif etype == "account_lockout":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            lockout_min = int(kwargs.get("lockout_minutes", 15))
            title = "Security Alert: Account Temporarily Locked"
            headline = "Account Security Alert"
            subject = "Security Alert: Your Account Has Been Temporarily Locked"
            cta_url = f"{self.base_url}/forgot-password"
            cta_text = "Reset Your Password"

            body_html = f"""
            <p>Hello {safe_name},</p>
            <p>We detected multiple consecutive failed login attempts on your AI Campus Placement Agent account.</p>
            <p>As a security precaution to protect your account against unauthorized access, your account has been temporarily locked for <strong>{lockout_min} minutes</strong>.</p>
            <p>If you were attempting to log in, you can try again after the lockout period expires, or reset your password using the link below.</p>
            <p>If this was not you, someone may be attempting to guess your password. We strongly recommend resetting your password immediately to secure your account.</p>
            """

            plain_body = (
                f"Hello {raw_name},\n\n"
                "We detected multiple consecutive failed login attempts on your AI Campus Placement Agent account.\n"
                f"Your account has been temporarily locked for {lockout_min} minutes for security.\n\n"
                f"If you were trying to log in, please wait {lockout_min} minutes before trying again or reset your password.\n"
                f"If this was not you, please reset your password immediately."
            )

        # 5. Email Verification Link
        elif etype == "verification":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            raw_email = kwargs.get("recipient_email") or ""
            safe_email = html.escape(str(raw_email))
            raw_token = kwargs.get("verification_token") or "token"
            base_url = kwargs.get("base_url") or self.base_url
            verify_url = f"{base_url.rstrip('/')}/verify-email/{raw_token}"

            title = "Verify Your AI Campus Placement Account"
            headline = "Confirm Your Email Address"
            subject = "Please verify your email address - AI Campus Placement Agent"
            cta_url = verify_url
            cta_text = "Verify My Email Address"

            body_html = f"""
            <p>Hi {safe_name},</p>
            <p>Thank you for registering with <strong>AI Campus Placement Agent</strong>! Please verify your email address to activate your account and start preparing for placement drives.</p>
            <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; padding: 16px; border-radius: 8px; margin: 16px 0;">
              <strong>Account Email:</strong> {safe_email}<br>
              <strong>Status:</strong> Pending Email Verification
            </div>
            <p>Click the button below to confirm your email. If the button does not work, copy and paste this link into your browser:</p>
            """

            plain_body = (
                f"Hi {raw_name},\n\n"
                "Thank you for registering with AI Campus Placement Agent!\n"
                f"Please verify your email address ({raw_email}) to activate your account.\n\n"
                f"Confirmation link: {verify_url}\n\n"
                "If you did not create this account, you can safely ignore this email."
            )

        # 6. Application Created Confirmation
        elif etype == "application_created":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            raw_company = kwargs.get("company_name") or "Company"
            safe_company = html.escape(str(raw_company))
            raw_role = kwargs.get("role_title") or "Role"
            safe_role = html.escape(str(raw_role))
            raw_date = kwargs.get("applied_date") or ""
            safe_date = html.escape(str(raw_date))

            company_label = "Empresa" if resolved_locale == "es" else "Company"
            role_label = "Puesto" if resolved_locale == "es" else "Role"
            date_label = "Fecha de solicitud" if resolved_locale == "es" else "Date Applied"
            stage_label = "Etapa inicial" if resolved_locale == "es" else "Initial Stage"
            applied_label = "Postulado" if resolved_locale == "es" else "Applied"
            greeting = "Hola" if resolved_locale == "es" else "Hi"
            recorded_message = (
                "Has registrado correctamente una nueva solicitud de empleo en tu seguimiento:"
                if resolved_locale == "es"
                else "You have successfully logged a new job application in your Placement Tracker:"
            )
            follow_up = (
                "Puedes seguir las etapas, guardar notas y adjuntar listas de preparación desde el panel de seguimiento."
                if resolved_locale == "es"
                else "You can track stages, record notes, and attach preparation checklists directly from the tracker dashboard."
            )

            title = f"Application Recorded: {safe_company}"
            headline = f"New Job Application Recorded for {safe_company}"
            subject = (
                f"Solicitud registrada: {raw_role} en {raw_company}"
                if resolved_locale == "es"
                else f"Application Tracked: {raw_role} at {raw_company}"
            )
            cta_url = f"{self.base_url}/applications"
            cta_text = "Ver el flujo de solicitudes" if resolved_locale == "es" else "View Application Pipeline"

            body_html = f"""
            <p>{greeting} {safe_name},</p>
            <p>{recorded_message}</p>
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 16px; border-radius: 8px; margin: 16px 0;">
              <strong>{company_label}:</strong> {safe_company}<br>
              <strong>{role_label}:</strong> {safe_role}<br>
              <strong>{date_label}:</strong> {safe_date}<br>
              <strong>{stage_label}:</strong> {applied_label}
            </div>
            <p>{follow_up}</p>
            """

            plain_body = (
                f"{greeting} {raw_name},\n\n"
                f"{recorded_message}\n\n"
                f"- {company_label}: {raw_company}\n"
                f"- {role_label}: {raw_role}\n"
                f"- {date_label}: {raw_date}\n"
                f"- {stage_label}: {applied_label}\n\n"
                f"{follow_up}"
            )

        # 7. Application Status / Stage Transition
        elif etype == "status_change":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            raw_company = kwargs.get("company_name") or "Company"
            safe_company = html.escape(str(raw_company))
            raw_role = kwargs.get("role_title") or "Role"
            safe_role = html.escape(str(raw_role))
            raw_prev = kwargs.get("previous_status") or "Applied"
            safe_prev = html.escape(str(raw_prev))
            raw_new = kwargs.get("new_status") or "Updated"
            safe_new = html.escape(str(raw_new))
            raw_notes = kwargs.get("notes") or ""
            safe_notes = html.escape(str(raw_notes))

            status_colors = {
                "Interview": "#4f46e5",
                "Offer": "#16a34a",
                "Selected": "#16a34a",
                "Rejected": "#dc2626",
                "Assessment": "#d97706",
            }
            color = status_colors.get(raw_new, "#4f46e5")

            previous_label = "Etapa anterior" if resolved_locale == "es" else "Previous Stage"
            new_label = "Nueva etapa" if resolved_locale == "es" else "New Stage"
            notes_label = "Notas" if resolved_locale == "es" else "Notes"
            greeting = "Hola" if resolved_locale == "es" else "Hi"
            transition_message = (
                "El estado de tu solicitud para"
                if resolved_locale == "es"
                else "The status of your application for"
            )
            closing_message = (
                "Revisa las próximas fechas límite y practica rondas de entrevista adaptadas en tu panel."
                if resolved_locale == "es"
                else "Make sure to review upcoming deadlines and practice tailored interview rounds on your dashboard."
            )

            notes_html = f"<div style='margin-top: 8px;'><span style='color: #64748b; font-size: 13px;'>{notes_label}:</span> {safe_notes}</div>" if safe_notes else ""
            notes_plain = f"\n- {notes_label}: {raw_notes}" if raw_notes else ""

            title = f"Application Status Updated: {safe_company}"
            headline = f"Status Update: {safe_company} - {safe_role}"
            subject = (
                f"Actualización de etapa [{raw_new}]: {raw_company} ({raw_role})"
                if resolved_locale == "es"
                else f"Stage Update [{raw_new}]: {raw_company} ({raw_role})"
            )
            cta_url = f"{self.base_url}/applications"
            cta_text = "Abrir el seguimiento" if resolved_locale == "es" else "Open Job Tracker"

            body_html = f"""
            <p>{greeting} {safe_name},</p>
            <p>{transition_message} <strong>{safe_role}</strong> at <strong>{safe_company}</strong> has transitioned:</p>
            <div style="background-color: #f8fafc; border-left: 4px solid {color}; padding: 16px; margin: 16px 0; border-radius: 4px;">
              <div><span style="color: #64748b; font-size: 13px;">{previous_label}:</span> <strike>{safe_prev}</strike></div>
              <div style="font-size: 18px; font-weight: bold; color: {color}; margin-top: 4px;">{new_label}: {safe_new}</div>
              {notes_html}
            </div>
            <p>{closing_message}</p>
            """

            plain_body = (
                f"{greeting} {raw_name},\n\n"
                f"{transition_message} {raw_role} at {raw_company} has transitioned:\n"
                f"- {previous_label}: {raw_prev}\n"
                f"- {new_label}: {raw_new}"
                f"{notes_plain}\n\n"
                f"{closing_message}"
            )

        # 8. Interview Reminder
        elif etype == "interview_reminder":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            raw_company = kwargs.get("company_name") or "Company"
            safe_company = html.escape(str(raw_company))
            raw_round = kwargs.get("interview_round") or "Technical Round"
            safe_round = html.escape(str(raw_round))
            raw_time = kwargs.get("scheduled_time") or "Upcoming"
            safe_time = html.escape(str(raw_time))
            raw_link = kwargs.get("interview_link") or ""
            safe_link = html.escape(str(raw_link))

            link_label = "Enlace de reunión / ronda" if resolved_locale == "es" else "Meeting / Round Link"
            link_html = f"<div style='margin-top: 6px;'><strong>{link_label}:</strong> <a href='{safe_link}'>{safe_link}</a></div>" if safe_link else ""
            link_plain = f"\n- {link_label}: {raw_link}" if raw_link else ""

            greeting = "Hola" if resolved_locale == "es" else "Hi"
            reminder_intro = (
                "Este es un recordatorio de tu próxima ronda de entrevista de colocación:"
                if resolved_locale == "es"
                else "This is a reminder about your upcoming placement interview round:"
            )
            warmup = (
                "Consejo: realiza una sesión de entrevista simulada de 5 minutos antes de tu llamada."
                if resolved_locale == "es"
                else "Quick Tip: Warm up your communication by doing a 5-minute simulated mock interview session before your real call."
            )

            title = f"Reminder: Upcoming Interview with {safe_company}"
            headline = f"Interview Scheduled: {safe_company}"
            subject = (
                f"Recordatorio: entrevista de {raw_round} con {raw_company} a las {raw_time}"
                if resolved_locale == "es"
                else f"Reminder: {raw_round} Interview with {raw_company} at {raw_time}"
            )
            cta_url = f"{self.base_url}/interview-history"
            cta_text = "Iniciar práctica" if resolved_locale == "es" else "Launch Practice Warmup"

            body_html = f"""
            <p>{greeting} {safe_name},</p>
            <p>{reminder_intro}</p>
            <div style="background-color: #eef2ff; border: 1px solid #c7d2fe; padding: 16px; border-radius: 8px; margin: 16px 0;">
              <strong>Target Company:</strong> {safe_company}<br>
              <strong>Round Category:</strong> {safe_round}<br>
              <strong>Scheduled Date/Time:</strong> {safe_time}<br>
              {link_html}
            </div>
            <p>{warmup}</p>
            """

            plain_body = (
                f"{greeting} {raw_name},\n\n"
                f"{reminder_intro}\n\n"
                f"- Target Company: {raw_company}\n"
                f"- Round Category: {raw_round}\n"
                f"- Scheduled Date/Time: {raw_time}"
                f"{link_plain}\n\n"
                f"{warmup}"
            )

        # 9. Application Deadline Reminder (#298)
        elif etype == "deadline_reminder":
            raw_name = kwargs.get("student_name") or "Candidate"
            safe_name = html.escape(str(raw_name))
            raw_company = kwargs.get("company_name") or "Company"
            safe_company = html.escape(str(raw_company))
            raw_title = kwargs.get("title") or "Application Deadline"
            safe_title = html.escape(str(raw_title))
            raw_date = kwargs.get("deadline_date") or ""
            formatted_deadline = format_date_ist(raw_date) or raw_date
            safe_deadline = html.escape(str(formatted_deadline))

            days_remaining = int(kwargs.get("days_remaining", 0))
            if days_remaining <= 0:
                urgency_text = "is due TODAY"
                subject_timing = "DUE TODAY"
                badge_color = "#dc2626"
                badge_bg = "#fee2e2"
            elif days_remaining == 1:
                urgency_text = "is due tomorrow (1 day left)"
                subject_timing = "1 day left"
                badge_color = "#d97706"
                badge_bg = "#fef3c7"
            else:
                urgency_text = f"is due in {days_remaining} days"
                subject_timing = f"{days_remaining} days left"
                badge_color = "#2563eb"
                badge_bg = "#dbeafe"

            target_label = "Fecha objetivo" if resolved_locale == "es" else "Target Date"
            remaining_label = "Tiempo restante" if resolved_locale == "es" else "Time Remaining"
            greeting = "Hola" if resolved_locale == "es" else "Hi"
            reminder_message = (
                "Este es un recordatorio automático de que tu fecha límite de solicitud registrada"
                if resolved_locale == "es"
                else "This is an automated reminder that your tracked application deadline"
            )
            preparation_message = (
                "Asegúrate de completar a tiempo los materiales, evaluaciones en línea y tareas de preparación."
                if resolved_locale == "es"
                else "Make sure all required materials, online assessments, and interview prep milestones are completed on time."
            )

            title = f"Deadline Reminder: {safe_company}"
            headline = "Upcoming Application Deadline"
            subject = (
                f"Recordatorio: {raw_company} - {raw_title} ({subject_timing})"
                if resolved_locale == "es"
                else f"Reminder: {raw_company} - {raw_title} ({subject_timing})"
            )
            cta_url = kwargs.get("portal_url") or f"{self.base_url}/applications"
            cta_text = "Ver solicitud en el seguimiento" if resolved_locale == "es" else "View Application in Tracker"

            body_html = f"""
            <p>{greeting} {safe_name},</p>
            <p>{reminder_message} <strong>{urgency_text}</strong>:</p>
            <div style="background-color: {badge_bg}; border: 1px solid {badge_color}; padding: 16px; border-radius: 8px; margin: 16px 0;">
              <div style="font-size: 16px; font-weight: bold; color: {badge_color}; margin-bottom: 8px;">
                {safe_company} — {safe_title}
              </div>
              <div style="font-size: 14px; color: #1e293b;">
                <strong>{target_label}:</strong> {safe_deadline}<br>
                <strong>{remaining_label}:</strong> {days_remaining} day(s)
              </div>
            </div>
            <p>{preparation_message}</p>
            """

            plain_body = (
                f"{greeting} {raw_name},\n\n"
                f"{reminder_message} {urgency_text}:\n\n"
                f"- {raw_company} — {raw_title}\n"
                f"- {target_label}: {formatted_deadline}\n"
                f"- {remaining_label}: {days_remaining} day(s)\n\n"
                f"{preparation_message}"
            )

        # 10. Inactive Account Warning (#888)
        elif etype == "inactive_account_warning":
            raw_name = kwargs.get("student_name") or "there"
            safe_name = html.escape(str(raw_name))
            days_left = int(kwargs.get("days_remaining", 30))
            login_url = kwargs.get("login_url") or f"{self.base_url}/login"

            title = f"Action Required: Account Deletion in {days_left} Days"
            headline = "Notice of Impending Account Deletion"
            subject = f"Action Required: Your account will be deleted in {days_left} days due to inactivity"
            cta_url = login_url
            cta_text = "Log In to Keep Account"

            body_html = f"""
            <p>Hello {safe_name},</p>
            <p>Your AI Campus Placement Agent account has been inactive for an extended period.</p>
            <p>To uphold data privacy standards and reduce unnecessary retention of personal records,
            your account and all associated resumes, mock interviews, and application history are scheduled
            for permanent deletion in <strong>{days_left} days</strong>.</p>
            <p>If you wish to keep your account and retain your placement data, simply log in before the deadline.
            Logging into your account immediately cancels this deletion request.</p>
            """

            plain_body = (
                f"Hello {raw_name},\n\n"
                "Your AI Campus Placement Agent account has been inactive for an extended period.\n\n"
                f"To uphold data privacy standards, your account and all associated resumes, mock interviews, "
                f"and application history are scheduled for permanent deletion in {days_left} days.\n\n"
                f"To keep your account active, simply log in before the deadline at:\n"
                f"{login_url}\n\n"
                "Logging into your account immediately cancels this deletion request."
            )

        else:
            # Generic fallback
            raw_name = kwargs.get("student_name") or "Candidate"
            safe_name = html.escape(str(raw_name))
            raw_headline = kwargs.get("headline") or "Notification from AI Campus Placement Agent"
            safe_headline = html.escape(str(raw_headline))
            raw_content = kwargs.get("body_content") or "You have a new update regarding your placement preparation."
            safe_content = html.escape(str(raw_content))

            title = kwargs.get("title") or "Placement Platform Notification"
            headline = safe_headline
            subject = kwargs.get("subject") or "Notification from AI Campus Placement Agent"
            cta_url = kwargs.get("cta_url") or f"{self.base_url}/"
            cta_text = kwargs.get("cta_text") or "Visit Platform"

            body_html = f"<p>Hello {safe_name},</p><p>{safe_content}</p>"
            plain_body = f"Hello {raw_name},\n\n{raw_content}"

        # Render HTML and plain text using shared layout
        html_content, plain_text = self.render_email(
            title=title,
            headline=headline,
            body_content=body_html,
            plain_content=plain_body,
            cta_url=cta_url,
            cta_text=cta_text,
            locale=locale,
        )

        return {
            "email_type": etype,
            "subject": subject,
            "title": title,
            "headline": headline,
            "html": html_content,
            "plain_text": plain_text,
            "cta_url": cta_url,
            "cta_text": cta_text,
        }

    @classmethod
    def get_sample_email_data(cls, email_type: str) -> Dict[str, Any]:
        """Provides realistic sample data for template previewing and automated tests (#889)."""
        etype = (email_type or "").strip().lower()
        samples: Dict[str, Dict[str, Any]] = {
            "welcome": {
                "student_name": "Priya Sharma",
            },
            "student_invitation": {
                "student_name": "Rahul Verma",
                "college_name": "Indian Institute of Technology",
                "invite_url": "http://localhost:5000/register?invite=demo_invitation_token_123",
                "expires_at_str": "Sunday, Nov 30, 2026 at 11:59 PM IST",
            },
            "password_reset": {
                "student_name": "Ananya Patel",
                "reset_url": "http://localhost:5000/reset-password/sample_reset_token_456",
            },
            "account_lockout": {
                "student_name": "Vikram Singh",
                "lockout_minutes": 15,
            },
            "verification": {
                "student_name": "Sneha Gupta",
                "recipient_email": "sneha.gupta@campus.edu",
                "verification_token": "sample_verify_token_789",
                "base_url": "http://localhost:5000",
            },
            "application_created": {
                "student_name": "Priya Sharma",
                "company_name": "Microsoft",
                "role_title": "Software Development Engineer (Intern)",
                "applied_date": "2026-11-26",
            },
            "status_change": {
                "student_name": "Priya Sharma",
                "company_name": "Google",
                "role_title": "Associate Product Manager",
                "previous_status": "Applied",
                "new_status": "Interview Scheduled",
                "notes": "Technical Round 1 scheduled with Campus Recruiting Team.",
            },
            "interview_reminder": {
                "student_name": "Rahul Verma",
                "company_name": "Amazon",
                "interview_round": "Data Structures & System Design",
                "scheduled_time": "Tomorrow at 10:30 AM IST",
                "interview_link": "https://meet.amazon.jobs/interview/demo-call-99",
            },
            "deadline_reminder": {
                "student_name": "Ananya Patel",
                "title": "Online Coding Assessment Deadline",
                "company_name": "Goldman Sachs",
                "deadline_date": "2026-11-28",
                "days_remaining": 2,
                "portal_url": "http://localhost:5000/applications",
            },
            "inactive_account_warning": {
                "student_name": "Arjun Das",
                "days_remaining": 30,
                "login_url": "http://localhost:5000/login",
            },
        }
        return samples.get(etype, {"student_name": "Student Candidate"})

    # --------------------------------------------------------------------------
    # Transactional Email Dispatch Methods (#186, #298, #888, #889)
    # --------------------------------------------------------------------------

    def send_email(
        self,
        recipient_email: str,
        subject: str,
        html_content: str,
        plain_text: Optional[str] = None,
    ) -> bool:
        """
        Sends an HTML email with guaranteed plain-text alternative (#889).
        Catches all network and SMTP failures gracefully without crashing calling workflows.
        Never logs email bodies.
        """
        recipient_email = (recipient_email or "").strip()
        if not recipient_email or "@" not in recipient_email:
            logger.warning(
                f"Invalid recipient email provided: '{recipient_email}'. Delivery skipped."
            )
            return False

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{self.sender_name} <{self.sender_email}>"
        message["To"] = recipient_email

        fallback_text = plain_text or _html_to_plain_text(html_content) or "Please view this email in an HTML-compatible client."
        message.attach(MIMEText(fallback_text, "plain", "utf-8"))
        message.attach(MIMEText(html_content, "html", "utf-8"))

        try:
            # Check if running in simulated or live environment
            if (
                getattr(config, "EMAIL_DELIVERY_DISABLED", False)
                or self.smtp_server in ("localhost", "127.0.0.1", "")
            ):
                logger.info(
                    f"[SIMULATED EMAIL DISPATCH] To: {recipient_email} | Subject: {subject}"
                )
                return True

            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=8.0) as server:
                if self.smtp_port in (587, 465):
                    server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(message)
                logger.info(f"Successfully sent email notification to {recipient_email}")
                return True

        except smtplib.SMTPException as smtpe:
            logger.error(f"SMTP error transmitting email to {recipient_email}: {smtpe}")
            return False
        except Exception as ex:
            logger.error(f"Unexpected error in email dispatch to {recipient_email}: {ex}")
            return False

    def send_welcome_email(
        self,
        recipient_email: str,
        student_name: str,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches welcome email upon student registration (#186, #889)."""
        data = self.build_templated_email(
            "welcome",
            locale=locale,
            student_name=student_name,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_student_invitation_email(
        self,
        recipient_email: str,
        student_name: str,
        invite_url: str,
        expires_at_str: str,
        college_name: Optional[str] = None,
    ) -> bool:
        """Dispatches placement batch onboarding invitation to a student candidate (#690, #889)."""
        data = self.build_templated_email(
            "student_invitation",
            student_name=student_name,
            invite_url=invite_url,
            expires_at_str=expires_at_str,
            college_name=college_name,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_password_reset_email(
        self,
        recipient_email: str,
        reset_url: str,
        student_name: str = "",
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Sends a secure password reset email (#889)."""
        data = self.build_templated_email(
            "password_reset",
            locale=locale,
            student_name=student_name,
            reset_url=reset_url,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_account_lockout_notification(
        self,
        recipient_email: str,
        student_name: str = "",
        lockout_minutes: int = 15,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches a security notification email when an account is temporarily locked (#851, #889)."""
        data = self.build_templated_email(
            "account_lockout",
            locale=locale,
            student_name=student_name,
            lockout_minutes=lockout_minutes,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_verification_email(
        self,
        recipient_email: str,
        student_name: str,
        verification_token: str,
        base_url: str = "http://localhost:5000",
    ) -> bool:
        """Sends an account verification email containing a secure confirmation link (#206, #889)."""
        data = self.build_templated_email(
            "verification",
            student_name=student_name,
            recipient_email=recipient_email,
            verification_token=verification_token,
            base_url=base_url,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_inactive_account_warning(
        self,
        recipient_email: str,
        student_name: str = "",
        days_remaining: int = 30,
        login_url: str = "http://localhost:5000/login",
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """
        Dispatches an inactivity warning email before permanent account deletion (#888).
        Informs the candidate of impending account & data purge, and that logging in cancels deletion.
        """
        import html

        safe_name = html.escape(student_name or "there")
        title = f"Action Required: Account Deletion in {days_remaining} Days"
        headline = "Notice of Impending Account Deletion"
        body_content = f"""
            <p>Hello {safe_name},</p>
            <p>Your AI Campus Placement Agent account has been inactive for an extended period.</p>
            <p>To uphold data privacy standards and reduce unnecessary retention of personal records,
            your account and all associated resumes, mock interviews, and application history are scheduled
            for permanent deletion in <strong>{days_remaining} days</strong>.</p>
            <p>If you wish to keep your account and retain your placement data, simply log in before the deadline.
            Logging into your account immediately cancels this deletion request.</p>
        """

        html_content = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body_content,
            cta_url=login_url,
            cta_text="Log In to Keep Account",
            locale=locale,
        )

        plain_text = (
            f"Hello {student_name or 'there'},\n\n"
            f"Your AI Campus Placement Agent account has been inactive for an extended period.\n"
            f"Your account and all associated resumes, mock interviews, and personal data are scheduled "
            f"for permanent deletion in {days_remaining} days.\n\n"
            f"To keep your account, simply log in before the deadline at: {login_url}\n"
            f"Logging in immediately cancels this deletion request.\n"
        )

        subject = (
            f"Action Required: Your account will be deleted in {days_remaining} days due to inactivity"
        )

        return self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_content=html_content,
            plain_text=plain_text,
        )

    def send_application_created_email(
        self,
        recipient_email: str,
        student_name: str,
        company_name: str,
        role_title: str,
        applied_date: str,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches confirmation when candidate logs a new application (#186, #889)."""
        data = self.build_templated_email(
            "application_created",
            locale=locale,
            student_name=student_name,
            company_name=company_name,
            role_title=role_title,
            applied_date=applied_date,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_status_change_email(
        self,
        recipient_email: str,
        student_name: str,
        company_name: str,
        role_title: str,
        previous_status: str,
        new_status: str,
        notes: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches notification when application pipeline stage changes (#186, #889)."""
        data = self.build_templated_email(
            "status_change",
            locale=locale,
            student_name=student_name,
            company_name=company_name,
            role_title=role_title,
            previous_status=previous_status,
            new_status=new_status,
            notes=notes,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_interview_reminder_email(
        self,
        recipient_email: str,
        student_name: str,
        company_name: str,
        interview_round: str,
        scheduled_time: str,
        interview_link: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches reminder for upcoming interview dates (#186, #889)."""
        data = self.build_templated_email(
            "interview_reminder",
            locale=locale,
            student_name=student_name,
            company_name=company_name,
            interview_round=interview_round,
            scheduled_time=scheduled_time,
            interview_link=interview_link,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_deadline_reminder_email(
        self,
        recipient_email: str,
        student_name: str,
        title: str,
        company_name: str,
        deadline_date: str,
        days_remaining: int,
        portal_url: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches deadline reminder email for upcoming milestones (#298, #889)."""
        data = self.build_templated_email(
            "deadline_reminder",
            locale=locale,
            student_name=student_name,
            title=title,
            company_name=company_name,
            deadline_date=deadline_date,
            days_remaining=days_remaining,
            portal_url=portal_url,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )

    def send_inactive_account_warning(
        self,
        recipient_email: str,
        student_name: str = "",
        days_remaining: int = 30,
        login_url: str = "http://localhost:5000/login",
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Dispatches an inactivity warning email before permanent deletion (#888, #889)."""
        data = self.build_templated_email(
            "inactive_account_warning",
            locale=locale,
            student_name=student_name,
            days_remaining=days_remaining,
            login_url=login_url,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=data["subject"],
            html_content=data["html"],
            plain_text=data["plain_text"],
        )
