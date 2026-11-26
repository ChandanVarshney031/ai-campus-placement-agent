import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, List, Optional
import config
from utils.output_localization import (
    DEFAULT_OUTPUT_LOCALE,
    output_text,
    resolve_output_locale,
)
from utils.formatters import format_date_ist

logger = logging.getLogger(__name__)

class EmailNotificationService:
    """
    Placement Activity Email Notification Service (#186, #298).
    Handles sending responsive transactional emails to candidates for important placement activities:
      - Welcome registration confirmation
      - New job application logged
      - Application stage/status transitions (e.g. Interview Scheduled, Offer Extended)
      - Interview schedule reminders
      - Application deadline reminders (#298)
      - Account verification & password reset links
      - Configurable user preference toggle (enable/disable email notifications)
      - Non-crashing delivery error handling with fallback logging
      - Never logs email bodies to protect user privacy
    """

    def __init__(self):
        # Email settings from config or environment with sensible local test defaults
        self.smtp_server = getattr(config, "SMTP_SERVER", "localhost")
        self.smtp_port = int(getattr(config, "SMTP_PORT", 587))
        self.smtp_user = getattr(config, "SMTP_USER", "")
        self.smtp_password = getattr(config, "SMTP_PASSWORD", "")
        self.sender_email = getattr(config, "NOTIFICATION_SENDER_EMAIL", "placements@aicampus.edu")
        self.sender_name = "AI Campus Placement Agent"

    def _format_html_layout(
        self,
        title: str,
        headline: str,
        body_content: str,
        cta_url: Optional[str] = None,
        cta_text: Optional[str] = None,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> str:
        """
        Creates a clean, mobile-responsive HTML email template.
        """
        resolved_locale, used_fallback = resolve_output_locale(locale)
        language_notice = (
            output_text("language_fallback", DEFAULT_OUTPUT_LOCALE)
            if used_fallback
            else output_text("language_notice", resolved_locale)
        )
        cta_block = ""
        if cta_url and cta_text:
            cta_block = f"""
            <div style="margin: 28px 0; text-align: center;">
              <a href="{cta_url}" style="background-color: #4f46e5; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 9999px; font-weight: 600; display: inline-block;">
                {cta_text}
              </a>
            </div>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>{title}</title>
        </head>
        <body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
          <div style="max-width: 600px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #4f46e5 0%, #3730a3 100%); padding: 32px 24px; text-align: center; color: #ffffff;">
              <h1 style="margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.025em;">AI Campus Placement Agent</h1>
              <p style="margin: 8px 0 0 0; font-size: 14px; color: #e0e7ff;">{language_notice}</p>
            </div>
            
            <!-- Content -->
            <div style="padding: 32px 24px;">
              <h2 style="margin-top: 0; color: #0f172a; font-size: 20px;">{headline}</h2>
              <div style="color: #475569; font-size: 15px; line-height: 1.6;">
                {body_content}
              </div>
              {cta_block}
            </div>

            <!-- Footer -->
            <div style="background-color: #f1f5f9; padding: 20px 24px; text-align: center; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0;">
              <p style="margin: 0 0 6px 0;">This email was sent by AI Campus Placement Agent to keep you informed of your placement workflow.</p>
              <p style="margin: 0;">You can customize your email notification preferences at any time in your <a href="http://localhost:5000/notification-settings" style="color: #4f46e5; text-decoration: underline;">Notification Settings</a>.</p>
            </div>
          </div>
        </body>
        </html>
        """

    def send_email(
        self,
        recipient_email: str,
        subject: str,
        html_content: str,
        plain_text: Optional[str] = None
    ) -> bool:
        """
        Sends an HTML email with text fallback.
        Catches all network and SMTP failures gracefully without crashing calling workflows.
        Never logs email bodies.
        """
        recipient_email = (recipient_email or "").strip()
        if not recipient_email or "@" not in recipient_email:
            logger.warning(f"Invalid recipient email provided: '{recipient_email}'. Delivery skipped.")
            return False

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{self.sender_name} <{self.sender_email}>"
        message["To"] = recipient_email

        fallback_text = plain_text or "Please view this email in an HTML-compatible client."
        message.attach(MIMEText(fallback_text, "plain", "utf-8"))
        message.attach(MIMEText(html_content, "html", "utf-8"))

        try:
            # Check if running in simulated or live environment
            if getattr(config, "EMAIL_DELIVERY_DISABLED", False) or self.smtp_server in ("localhost", "127.0.0.1", ""):
                # Simulated dispatch mode for local development/testing
                logger.info(f"[SIMULATED EMAIL DISPATCH] To: {recipient_email} | Subject: {subject}")
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

    def send_student_invitation_email(
        self,
        recipient_email: str,
        student_name: str,
        invite_url: str,
        expires_at_str: str,
        college_name: Optional[str] = None
    ) -> bool:
        """
        Dispatches placement batch onboarding invitation to a student candidate (#690).
        """
        title = "Invitation to Join AI Campus Placement Platform"
        headline = f"You're invited to AI Campus Placement Agent!"
        college_note = f" from <strong>{college_name}</strong>" if college_name else ""
        body = f"""
        <p>Dear {student_name or 'Student'},</p>
        <p>Your placement cell{college_note} has invited you to activate your account on the <strong>AI Campus Placement Agent</strong> platform.</p>
        <p>This single-use onboarding link gives you immediate access to:</p>
        <ul style="padding-left: 20px;">
          <li><strong>AI Resume Review & ATS Scoring</strong> customized for leading recruiters</li>
          <li><strong>Interactive Mock Interview Simulator</strong> with instant feedback</li>
          <li><strong>Company Prep Checklists & One-Page Drive Cheat Sheets</strong></li>
          <li><strong>Application Status Tracker</strong> for placement drives</li>
        </ul>
        <p style="color: #dc2626; font-size: 13px;"><em>Note: This secure invitation link is single-use and will expire on {expires_at_str}.</em></p>
        """
        html_layout = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body,
            cta_url=invite_url,
            cta_text="Accept Invitation & Activate Account"
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject="Action Required: Your AI Campus Placement Account Invitation",
            html_content=html_layout,
            plain_text=f"Hello {student_name},\n\nYou have been invited to join AI Campus Placement Agent. Activate your account here: {invite_url}\n\nThis link expires on {expires_at_str}."
        )

    def send_welcome_email(self, recipient_email: str, student_name: str, locale: str = DEFAULT_OUTPUT_LOCALE) -> bool:
        """
        Dispatches welcome email upon student registration (#186).
        """
        title = "Welcome to AI Campus Placement Agent"
        headline = f"Welcome aboard, {student_name or 'Candidate'}!"
        body = f"""
        <p>Congratulations on taking the first proactive step toward your placement preparation!</p>
        <p>With your new account, you can:</p>
        <ul style="padding-left: 20px;">
          <li>Analyze your resume against top tier tech companies (ATS scoring and gap detection).</li>
          <li>Practice realistic AI-driven mock interviews with dynamic STAR behavioral grading.</li>
          <li>Track all your campus job and internship applications through a unified pipeline.</li>
          <li>Access personalized coding roadmap and practice recommendations.</li>
        </ul>
        <p>Best of luck with your preparation!</p>
        """
        html = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body,
            cta_url="http://localhost:5000/",
            cta_text="Start First Placement Analysis",
            locale=locale,
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject="Welcome to AI Campus Placement Agent! 🎓",
            html_content=html
        )

    def send_password_reset_email(
        self,
        recipient_email: str,
        reset_url: str,
        student_name: str = "",
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """Send a secure password reset email."""
        import html

        safe_name = html.escape(student_name or "there")

        body_content = f"""
            <p>Hello {safe_name},</p>
            <p>We received a request to reset your AI Campus Placement Agent password.</p>
            <p>Click the button below to create a new password.</p>
            <p>This link will expire in <strong>30 minutes</strong>.</p>
            <p>If you did not request this reset, you can safely ignore this email.</p>
        """

        html_content = self._format_html_layout(
            title="Reset Your Password",
            headline="Password Reset Request",
            body_content=body_content,
            cta_url=reset_url,
            cta_text="Reset Password",
            locale=locale,
        )

        plain_text = (
            f"Hello {student_name or 'there'},\n\n"
            "We received a request to reset your password.\n"
            f"Use this link to reset it: {reset_url}\n\n"
            "This link expires in 30 minutes.\n"
            "If you did not request this reset, ignore this email."
        )

        return self.send_email(
            recipient_email=recipient_email,
            subject="Reset Your AI Campus Placement Agent Password",
            html_content=html_content,
            plain_text=plain_text
        )

    def send_account_lockout_notification(
        self,
        recipient_email: str,
        student_name: str = "",
        lockout_minutes: int = 15,
        locale: str = DEFAULT_OUTPUT_LOCALE,
    ) -> bool:
        """
        Dispatches a security notification email when an account is temporarily locked (#851).
        """
        import html

        safe_name = html.escape(student_name or "there")
        title = "Security Alert: Account Temporarily Locked"
        headline = "Account Security Alert"
        body_content = f"""
            <p>Hello {safe_name},</p>
            <p>We detected multiple consecutive failed login attempts on your AI Campus Placement Agent account.</p>
            <p>As a security precaution to protect your account against unauthorized access, your account has been temporarily locked for <strong>{lockout_minutes} minutes</strong>.</p>
            <p>If you were attempting to log in, you can try again after the lockout period expires, or reset your password using the link below.</p>
            <p>If this was not you, someone may be attempting to guess your password. We strongly recommend resetting your password immediately to secure your account.</p>
        """

        html_content = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body_content,
            cta_url="http://localhost:5000/forgot-password",
            cta_text="Reset Your Password",
            locale=locale,
        )

        plain_text = (
            f"Hello {student_name or 'there'},\n\n"
            f"We detected multiple consecutive failed login attempts on your account.\n"
            f"Your account has been temporarily locked for {lockout_minutes} minutes for security.\n\n"
            f"If you were trying to log in, please wait {lockout_minutes} minutes before trying again or reset your password at: http://localhost:5000/forgot-password\n\n"
            f"If this was not you, please reset your password immediately."
        )

        return self.send_email(
            recipient_email=recipient_email,
            subject="Security Alert: Your Account Has Been Temporarily Locked",
            html_content=html_content,
            plain_text=plain_text,
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
        """
        Dispatches confirmation when candidate logs a new application (#186).
        """
        resolved_locale, _ = resolve_output_locale(locale)
        title = f"Application Recorded: {company_name}"
        headline = f"New Job Application Recorded for {company_name}"
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
        body = f"""
        <p>{greeting} {student_name or 'there'},</p>
        <p>{recorded_message}</p>
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 16px; border-radius: 8px; margin: 16px 0;">
          <strong>{company_label}:</strong> {company_name}<br>
          <strong>{role_label}:</strong> {role_title}<br>
          <strong>{date_label}:</strong> {applied_date}<br>
          <strong>{stage_label}:</strong> {applied_label}
        </div>
        <p>{follow_up}</p>
        """
        html = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body,
            cta_url="http://localhost:5000/applications",
            cta_text="Ver el flujo de solicitudes" if resolved_locale == "es" else "View Application Pipeline",
            locale=locale,
        )
        subject = (
            f"Solicitud registrada: {role_title} en {company_name}"
            if resolved_locale == "es"
            else f"Application Tracked: {role_title} at {company_name}"
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_content=html
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
        """
        Dispatches notification when application pipeline stage changes (#186).
        """
        status_colors = {
            "Interview": "#4f46e5",
            "Offer": "#16a34a",
            "Selected": "#16a34a",
            "Rejected": "#dc2626",
            "Assessment": "#d97706"
        }
        color = status_colors.get(new_status, "#4f46e5")

        title = f"Application Status Updated: {company_name}"
        headline = f"Status Update: {company_name} - {role_title}"
        resolved_locale, _ = resolve_output_locale(locale)
        previous_label = "Etapa anterior" if resolved_locale == "es" else "Previous Stage"
        new_label = "Nueva etapa" if resolved_locale == "es" else "New Stage"
        notes_label = "Notas" if resolved_locale == "es" else "Notes"
        notes_html = f"<p><strong>{notes_label}:</strong> {notes}</p>" if notes else ""
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
        body = f"""
        <p>{greeting} {student_name or 'there'},</p>
        <p>{transition_message} <strong>{role_title}</strong> at <strong>{company_name}</strong> has transitioned:</p>
        <div style="background-color: #f8fafc; border-left: 4px solid {color}; padding: 16px; margin: 16px 0; border-radius: 4px;">
          <div><span style="color: #64748b; font-size: 13px;">{previous_label}:</span> <strike>{previous_status}</strike></div>
          <div style="font-size: 18px; font-weight: bold; color: {color}; margin-top: 4px;">{new_label}: {new_status}</div>
          {notes_html}
        </div>
        <p>{closing_message}</p>
        """
        html = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body,
            cta_url="http://localhost:5000/applications",
            cta_text="Abrir el seguimiento" if resolved_locale == "es" else "Open Job Tracker",
            locale=locale,
        )
        subject = (
            f"Actualización de etapa [{new_status}]: {company_name} ({role_title})"
            if resolved_locale == "es"
            else f"Stage Update [{new_status}]: {company_name} ({role_title})"
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_content=html
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
        """
        Dispatches reminder for upcoming interview dates (#186).
        """
        title = f"Reminder: Upcoming Interview with {company_name}"
        headline = f"Interview Scheduled: {company_name}"
        resolved_locale, _ = resolve_output_locale(locale)
        link_label = "Enlace de reunión / ronda" if resolved_locale == "es" else "Meeting / Round Link"
        link_html = f"<p><strong>{link_label}:</strong> <a href='{interview_link}'>{interview_link}</a></p>" if interview_link else ""
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
        body = f"""
        <p>{greeting} {student_name or 'there'},</p>
        <p>{reminder_intro}</p>
        <div style="background-color: #eef2ff; border: 1px solid #c7d2fe; padding: 16px; border-radius: 8px; margin: 16px 0;">
          <strong>Target Company:</strong> {company_name}<br>
          <strong>Round Category:</strong> {interview_round}<br>
          <strong>Scheduled Date/Time:</strong> {scheduled_time}<br>
          {link_html}
        </div>
        <p>{warmup}</p>
        """
        html = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body,
            cta_url="http://localhost:5000/interview-history",
            cta_text="Iniciar práctica" if resolved_locale == "es" else "Launch Practice Warmup",
            locale=locale,
        )
        subject = (
            f"Recordatorio: entrevista de {interview_round} con {company_name} a las {scheduled_time}"
            if resolved_locale == "es"
            else f"Reminder: {interview_round} Interview with {company_name} at {scheduled_time}"
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_content=html
        )

    def send_verification_email(
        self,
        recipient_email: str,
        student_name: str,
        verification_token: str,
        base_url: str = "http://localhost:5000"
    ) -> bool:
        """
        Sends an account verification email containing a secure confirmation link (#206).
        """
        verify_url = f"{base_url.rstrip('/')}/verify-email/{verification_token}"
        title = "Verify Your AI Campus Placement Account"
        headline = "Confirm Your Email Address"
        body = f"""
        <p>Hi {student_name or 'there'},</p>
        <p>Thank you for registering with <strong>AI Campus Placement Agent</strong>! Please verify your email address to activate your account and start preparing for placement drives.</p>
        <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; padding: 16px; border-radius: 8px; margin: 16px 0;">
          <strong>Account Email:</strong> {recipient_email}<br>
          <strong>Status:</strong> Pending Email Verification
        </div>
        <p>Click the button below to confirm your email. If the button does not work, copy and paste this link into your browser:</p>
        <p style="word-break: break-all; font-size: 13px; color: #64748b;">{verify_url}</p>
        """
        html = self._format_html_layout(
            title=title,
            headline=headline,
            body_content=body,
            cta_url=verify_url,
            cta_text="Verify My Email Address"
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject="Please verify your email address - AI Campus Placement Agent",
            html_content=html
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
        """
        Dispatches deadline reminder email for upcoming application or interview milestones (#298).
        Never logs email bodies.
        """
        import html
        safe_name = html.escape(student_name or "Candidate")
        safe_company = html.escape(company_name or "Company")
        safe_title = html.escape(title or "Application Deadline")
        formatted_deadline = format_date_ist(deadline_date) or deadline_date
        safe_deadline = html.escape(formatted_deadline)

        resolved_locale, _ = resolve_output_locale(locale)
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

        cta = portal_url or "http://localhost:5000/applications"

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
        body = f"""
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

        html_content = self._format_html_layout(
            title=f"Deadline Reminder: {safe_company}",
            headline="Upcoming Application Deadline",
            body_content=body,
            cta_url=cta,
            cta_text="Ver solicitud en el seguimiento" if resolved_locale == "es" else "View Application in Tracker",
            locale=locale,
        )

        subject = (
            f"Recordatorio: {company_name} - {title} ({subject_timing})"
            if resolved_locale == "es"
            else f"Reminder: {company_name} - {title} ({subject_timing})"
        )
        return self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_content=html_content
        )
