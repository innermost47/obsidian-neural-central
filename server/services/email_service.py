import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from server.config import settings
from server.templates.email_template import (
    base_template,
    btn_primary,
    btn_secondary,
    info_box,
    stat_row,
    section_title,
    download_buttons,
)

logger = logging.getLogger(__name__)


class EmailService:

    @staticmethod
    def _get_unsubscribe_token(user_id: int, db: Session) -> str:
        from server.core.database import User

        user = db.query(User).filter(User.id == user_id).first()
        if user and user.unsubscribe_token:
            return user.unsubscribe_token
        return ""

    @staticmethod
    def _send_email(
        to_email: str,
        subject: str,
        html_body: str,
        email_type: str = "unknown",
        user_id: int = None,
        db: Session = None,
    ) -> bool:
        from server.core.database import EmailLog, EmailLogStatus

        email_log_id = None
        if db:
            try:
                email_log = EmailLog(
                    recipient_email=to_email,
                    subject=subject,
                    body=html_body,
                    email_type=email_type,
                    status=EmailLogStatus.PENDING,
                    user_id=user_id,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(email_log)
                db.commit()
                db.refresh(email_log)
                email_log_id = email_log.id
            except Exception as e:
                logger.warning(f"Error logging email: {e}")

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM_EMAIL
            msg["To"] = to_email
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=30
            ) as server:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)

            logger.info(f"✅ Email sent to {to_email} [{email_type}]")

            if db and email_log_id:
                log = db.query(EmailLog).filter(EmailLog.id == email_log_id).first()
                if log:
                    log.status = EmailLogStatus.SENT
                    log.sent_at = datetime.now(timezone.utc)
                    log.error_message = None
                    db.commit()
            return True

        except Exception as e:
            logger.error(f"❌ Failed to send email to {to_email}: {e}")
            if db and email_log_id:
                try:
                    log = db.query(EmailLog).filter(EmailLog.id == email_log_id).first()
                    if log:
                        log.status = EmailLogStatus.FAILED
                        log.error_message = str(e)
                        log.last_retry_at = datetime.now(timezone.utc)
                        db.commit()
                except Exception:
                    pass
            return False

    @staticmethod
    def send_welcome_email(
        email: str, api_key: str, user_id: int, db: Session = None
    ) -> bool:
        unsub = EmailService._get_unsubscribe_token(user_id, db) if db else ""

        content = f"""
        {section_title("Welcome to OBSIDIAN Neural")}
        <h1 style="color:#1a1a1a;font-size:26px;font-weight:700;margin:0 0 16px;line-height:1.3;">
          Your account is ready.<br/>
          <span style="color:#b8605c;">Let's make some music.</span>
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          You have <strong>10 free credits</strong> to get started. Here's everything you need.
        </p>

        {section_title("Your credentials")}
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Server URL", f'<code style="font-family:Courier Prime,monospace;color:#b8605c;">{settings.API_URL}</code>')}
            {stat_row("API Key", f'<code style="font-family:Courier Prime,monospace;color:#1a1a1a;font-weight:700;font-size:13px;">{api_key}</code>')}
          </table>
        ''')}

        {section_title("Quick start — 3 minutes")}
        <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px;">
          <tr>
            <td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
              <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:22px;height:22px;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:10px;">1</span>
              Download and install the VST3 plugin in your DAW
            </td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
              <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:22px;height:22px;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:10px;">2</span>
              Open Settings &rarr; paste your Server URL and API Key
            </td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
              <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:22px;height:22px;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:10px;">3</span>
              Type a prompt — <em>"dark techno kick 140bpm"</em> — and hit Generate
            </td>
          </tr>
        </table>

        {section_title("Download")}
        {download_buttons(f"{settings.REPO_URL}/releases/latest")}

        {btn_primary("Open my dashboard →", f"{settings.FRONTEND_URL}/dashboard.html")}

        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;line-height:1.6;">
          Questions? Just reply to this email &mdash; I read everything.<br/>
          <span style="color:#b8605c;font-weight:600;">— Anthony, creator of OBSIDIAN Neural</span>
        </p>
        """

        return EmailService._send_email(
            email,
            "🎉 Welcome to OBSIDIAN Neural — Your API Key Inside",
            base_template(
                content,
                preheader="Your API key and everything you need to get started.",
                unsubscribe_token=unsub,
            ),
            email_type="welcome",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_verification_email(
        email: str, token: str, user_id: int, db: Session = None
    ) -> bool:
        unsub = EmailService._get_unsubscribe_token(user_id, db) if db else ""
        verify_url = f"{settings.FRONTEND_URL}/verify-email.html?token={token}"

        content = f"""
        {section_title("Email verification")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Verify your email address
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 28px;">
          Click the button below to verify your address and activate your account.
          This link expires in <strong>24 hours</strong>.
        </p>
        {btn_primary("Verify my email →", verify_url)}
        <p style="color:#cccccc;font-size:12px;margin:20px 0 0;line-height:1.6;">
          If you didn't create an account, you can safely ignore this email.
        </p>
        """

        return EmailService._send_email(
            email,
            "Verify your OBSIDIAN Neural account",
            base_template(
                content,
                preheader="Please verify your email to activate your account.",
                unsubscribe_token=unsub,
            ),
            email_type="verification",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_password_reset_email(email: str, token: str, db: Session = None) -> bool:
        reset_url = f"{settings.FRONTEND_URL}/reset-password.html?token={token}"

        content = f"""
        {section_title("Password reset")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Reset your password
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 28px;">
          We received a request to reset your OBSIDIAN Neural password.
          This link expires in <strong>1 hour</strong>.
        </p>
        {btn_primary("Reset my password →", reset_url)}
        <p style="color:#cccccc;font-size:12px;margin:20px 0 0;line-height:1.6;">
          If you didn't request this, please ignore this email. Your password won't change.
        </p>
        """

        return EmailService._send_email(
            email,
            "Reset your OBSIDIAN Neural password",
            base_template(
                content,
                preheader="Click to reset your password. Link expires in 1 hour.",
            ),
            email_type="password_reset",
            db=db,
        )

    @staticmethod
    def send_subscription_confirmation(
        email: str, tier: str, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )
        tier_display = tier.capitalize()
        credits = settings.TIER_CREDITS.get(tier, 0)

        content = f"""
        {section_title("Subscription confirmed")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          You're on the <span style="color:#b8605c;">{tier_display}</span> plan.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          Your subscription is active and your credits have been added.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Credits", f"{credits} / month")}
            {stat_row("Status", '<span style="color:#b8605c;font-weight:700;">Active</span>')}
          </table>
        ''')}
        {btn_primary("Open my dashboard →", f"{settings.FRONTEND_URL}/dashboard.html")}
        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;">
          Thank you for your support &mdash; it genuinely helps keep this project alive. 🙏
        </p>
        """

        return EmailService._send_email(
            email,
            f"Subscription confirmed — {tier_display} plan",
            base_template(
                content,
                preheader=f"Your {tier_display} plan is now active.",
                unsubscribe_token=unsub,
            ),
            email_type="subscription_confirmation",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_subscription_cancelled(
        email: str, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )

        content = f"""
        {section_title("Subscription cancelled")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Your subscription has been cancelled.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          You'll continue to have access to all features until the end of your current billing period.
          After that, your account will revert to the free plan.
        </p>
        {btn_secondary("Reactivate my subscription", f"{settings.FRONTEND_URL}/dashboard.html?section=subscription")}
        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;line-height:1.6;">
          Changed your mind or have feedback? Just reply to this email.
        </p>
        """

        return EmailService._send_email(
            email,
            "Your OBSIDIAN Neural subscription has been cancelled",
            base_template(
                content,
                preheader="You still have access until the end of your billing period.",
                unsubscribe_token=unsub,
            ),
            email_type="subscription_cancelled",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_gift_notification(
        recipient_email: str,
        recipient_name: str,
        purchaser_name: str,
        tier: str,
        duration_months: int,
        gift_code: str,
        gift_message: str = None,
        activation_date: str = None,
        db: Session = None,
    ) -> bool:
        tier_display = tier.capitalize()
        credits = settings.TIER_CREDITS.get(tier, 0)
        total_value = duration_months * settings.TIER_PRICES_EUR.get(tier, 0)
        activate_url = f"{settings.FRONTEND_URL}/gift-activate.html?code={gift_code}"

        personal_msg = ""
        if gift_message:
            personal_msg = f"""
            {section_title("Personal message")}
            <p style="color:#4a4a4a;font-size:15px;font-style:italic;line-height:1.7;margin:0 0 24px;border-left:3px solid #b8605c;padding-left:16px;">
              &ldquo;{gift_message}&rdquo;
            </p>"""

        activation_note = ""
        if activation_date:
            try:
                date_obj = datetime.fromisoformat(activation_date)
                activation_note = f'<p style="color:#4a4a4a;font-size:13px;margin:8px 0 0;">Available from <strong>{date_obj.strftime("%B %d, %Y")}</strong></p>'
            except Exception:
                pass

        content = f"""
        {section_title("You received a gift")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 8px;">
          {purchaser_name} gifted you OBSIDIAN Neural 🎁
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          You have <strong>{duration_months} month{"s" if duration_months > 1 else ""}</strong> of the <strong>{tier_display}</strong> plan — worth <strong>&euro;{total_value:.2f}</strong>.
        </p>

        {personal_msg}

        {section_title("Your gift")}
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Duration", f"{duration_months} month{'s' if duration_months > 1 else ''}")}
            {stat_row("Credits", f"{credits} / month")}
            {stat_row("Gift code", f'<code style="font-family:Courier Prime,monospace;font-size:15px;font-weight:700;color:#b8605c;">{gift_code}</code>')}
          </table>
          {activation_note}
        ''')}

        {btn_primary("Activate my gift →", activate_url)}

        <p style="color:#cccccc;font-size:12px;margin:20px 0 0;line-height:1.6;">
          Your gift code is valid for 1 year from the purchase date.
        </p>
        """

        return EmailService._send_email(
            recipient_email,
            f"🎁 {purchaser_name} gifted you OBSIDIAN Neural!",
            base_template(
                content,
                preheader=f"{purchaser_name} sent you {duration_months} month(s) of OBSIDIAN Neural.",
            ),
            email_type="gift_notification",
            db=db,
        )

    @staticmethod
    def send_trial_started(
        email: str,
        tier: str,
        trial_end_date: datetime,
        trial_credits: int = None,
        user_id: int = None,
        db: Session = None,
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )
        tier_display = tier.capitalize()
        trial_credits = trial_credits or settings.TRIAL_CONFIG["credits"].get(tier, 50)
        monthly = settings.TIER_CREDITS.get(tier, 0)
        end_str = trial_end_date.strftime("%B %d, %Y")

        content = f"""
        {section_title("Free trial")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Your 7-day <span style="color:#b8605c;">{tier_display}</span> trial starts now.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          You have full access to all <strong>{tier_display}</strong> features until <strong>{end_str}</strong>.
        </p>

        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Trial credits", str(trial_credits))}
            {stat_row("After trial", f"{monthly} credits / month")}
            {stat_row("Trial ends", end_str)}
          </table>
        ''')}

        {section_title("Get started")}
        {download_buttons(f"{settings.REPO_URL}/releases/latest")}

        {btn_primary("Open my dashboard →", f"{settings.FRONTEND_URL}/dashboard.html")}

        <p style="color:#cccccc;font-size:12px;margin:20px 0 0;line-height:1.6;">
          No charge until {end_str}. Cancel anytime before then &mdash; no questions asked.
        </p>
        """

        return EmailService._send_email(
            email,
            f"🎉 Your {tier_display} 7-day free trial starts now",
            base_template(
                content,
                preheader=f"Full {tier_display} access for 7 days. No charge until {end_str}.",
                unsubscribe_token=unsub,
            ),
            email_type="trial_started",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_trial_ending_reminder(
        email: str,
        tier: str,
        trial_end_date: datetime,
        has_payment_method: bool = False,
        user_id: int = None,
        db: Session = None,
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )
        tier_display = tier.capitalize()
        end_str = trial_end_date.strftime("%B %d, %Y")
        price = settings.TIER_PRICES_EUR.get(tier, 0)
        dashboard = f"{settings.FRONTEND_URL}/dashboard.html?section=subscription"

        if has_payment_method:
            body = f"""
            <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
              You're all set &mdash; your payment method is saved. On <strong>{end_str}</strong> you'll be charged
              <strong>&euro;{price}/month</strong> and your subscription will continue automatically.
            </p>
            {btn_secondary("Manage my subscription", dashboard)}
            """
            preheader = f"Your trial ends {end_str}. You're all set — subscription continues automatically."
        else:
            body = f"""
            <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
              Your trial ends in <strong>3 days</strong> ({end_str}). Add a payment method to keep your
              <strong>{tier_display}</strong> plan at <strong>&euro;{price}/month</strong>.
            </p>
            {btn_primary("Add payment method →", dashboard)}
            <p style="color:#cccccc;font-size:12px;margin:16px 0 0;line-height:1.6;">
              No payment added? Your trial simply ends &mdash; no charge, zero commitment.
            </p>
            """
            preheader = f"Your trial ends in 3 days ({end_str}). Add payment to keep your {tier_display} plan."

        content = f"""
        {section_title("Trial ending soon")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          3 days left on your <span style="color:#b8605c;">{tier_display}</span> trial.
        </h1>
        {body}
        """

        return EmailService._send_email(
            email,
            f"⏰ Your {tier_display} trial ends in 3 days",
            base_template(content, preheader=preheader, unsubscribe_token=unsub),
            email_type="trial_ending_reminder",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_trial_converted(
        email: str, tier: str, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )
        tier_display = tier.capitalize()
        credits = settings.TIER_CREDITS.get(tier, 0)

        content = f"""
        {section_title("Subscription active")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          You're on the <span style="color:#b8605c;">{tier_display}</span> plan. 🚀
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          Your trial converted successfully. Credits have been refreshed and you're ready to create.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Credits", f"{credits} / month")}
            {stat_row("Status", '<span style="color:#b8605c;font-weight:700;">Active</span>')}
          </table>
        ''')}
        {btn_primary("Open my dashboard →", f"{settings.FRONTEND_URL}/dashboard.html")}
        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;">
          Thank you for your support. 🙏
        </p>
        """

        return EmailService._send_email(
            email,
            f"🎉 Welcome to {tier_display} — subscription active",
            base_template(
                content,
                preheader=f"Your {tier_display} subscription is now active.",
                unsubscribe_token=unsub,
            ),
            email_type="trial_converted",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_trial_not_converted(
        email: str, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )

        content = f"""
        {section_title("Trial ended")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Your trial has ended.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          Your account is back on the free plan (10 credits/month). All your settings and data are saved.
        </p>
        {btn_secondary("Reactivate a subscription", f"{settings.FRONTEND_URL}/dashboard.html?section=subscription")}
        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;line-height:1.6;">
          Have feedback on why you didn't continue? Just reply &mdash; it genuinely helps.
        </p>
        """

        return EmailService._send_email(
            email,
            "Your OBSIDIAN Neural trial has ended",
            base_template(
                content,
                preheader="Your trial ended. You're on the free plan — come back anytime.",
                unsubscribe_token=unsub,
            ),
            email_type="trial_not_converted",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_expiration_warning(
        recipient_email: str,
        tier: str,
        days_left: int,
        user_id: int = None,
        db: Session = None,
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )
        tier_display = tier.capitalize()
        time_label = "Tomorrow" if days_left == 1 else f"in {days_left} days"
        dashboard = f"{settings.FRONTEND_URL}/dashboard.html?section=subscription"

        content = f"""
        {section_title("Subscription expiring")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Your <span style="color:#b8605c;">{tier_display}</span> plan expires {time_label}.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          After expiry, your account reverts to the free plan. All your data and settings are saved.
        </p>
        {btn_primary("Keep my subscription →", dashboard)}
        {btn_secondary("Manage billing", dashboard)}
        """

        subject = (
            f"⚠️ Your {tier_display} plan expires tomorrow"
            if days_left == 1
            else f"🔔 Your {tier_display} plan expires in {days_left} days"
        )

        return EmailService._send_email(
            recipient_email,
            subject,
            base_template(
                content,
                preheader=f"Your {tier_display} subscription expires {time_label}.",
                unsubscribe_token=unsub,
            ),
            email_type="expiration_warning",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_no_generation_help(
        email: str, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )

        content = f"""
        {section_title("Getting started")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Need a hand getting started?
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          You haven't generated any samples yet &mdash; no worries. Here's the quickest path.
        </p>

        {section_title("3 steps to your first sample")}
        <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px;">
          <tr><td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
            <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:22px;height:22px;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:10px;">1</span>
            Open OBSIDIAN Neural in your DAW
          </td></tr>
          <tr><td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
            <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:22px;height:22px;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:10px;">2</span>
            Settings &rarr; paste your API key &amp; server URL
          </td></tr>
          <tr><td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
            <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:22px;height:22px;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:10px;">3</span>
            Type <em>"dark techno kick 140bpm"</em> and hit Generate 🎵
          </td></tr>
        </table>

        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Server URL", f'<code style="font-family:Courier Prime,monospace;color:#b8605c;">{settings.API_URL}</code>')}
          </table>
          <p style="color:#4a4a4a;font-size:12px;margin:8px 0 0;">Your API key is in your dashboard.</p>
        ''')}

        {btn_primary("Go to my dashboard →", f"{settings.FRONTEND_URL}/dashboard.html")}

        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;line-height:1.6;">
          Something not working? Just reply &mdash; I'll help you get it running.
        </p>
        """

        return EmailService._send_email(
            email,
            "🆘 Need help getting started with OBSIDIAN Neural?",
            base_template(
                content,
                preheader="You haven't generated anything yet. Here's how to get started in 3 steps.",
                unsubscribe_token=unsub,
            ),
            email_type="no_generation_help",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_weekly_inspiration(
        email: str, week_number: int, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )

        templates = {
            2: {
                "subject": "🎵 5 creative ways to use OBSIDIAN Neural",
                "preheader": "Ideas to integrate AI generation into your real workflow.",
                "h1": "5 ideas for your workflow",
                "body": f"""
                <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px;">
                  {''.join([f'<tr><td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;"><span style="color:#b8605c;font-weight:700;margin-right:8px;">{n}.</span> {t}</td></tr>' for n, t in [
                    (1, "Generate drum loops live on stage — react to the crowd in real time"),
                    (2, "Build a personal sample library of AI basslines and melodies"),
                    (3, "Blend genres you'd never think to mix"),
                    (4, "Use it as a creative trigger when you're stuck on a beat"),
                    (5, "Sketch an arrangement idea in 2 minutes before building it out"),
                  ]])}
                </table>
                {btn_primary("Generate something now →", f"{settings.FRONTEND_URL}/dashboard.html")}
                """,
            },
            3: {
                "subject": "🎛️ Features you might have missed",
                "preheader": "BPM sync, multi-output, MIDI learn — hidden power inside the plugin.",
                "h1": "You might have missed these",
                "body": f"""
                <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px;">
                  {''.join([f'<tr><td style="padding:8px 0;color:#4a4a4a;font-size:14px;line-height:1.6;"><span style="color:#b8605c;font-weight:700;margin-right:8px;">→</span> {t}</td></tr>' for t in [
                    "<strong>BPM Auto-Sync</strong> — samples stretch automatically to your DAW tempo",
                    "<strong>Multi-output routing</strong> — 8 individual outputs for advanced mixing",
                    "<strong>MIDI Learn</strong> — map any parameter to your controller",
                    "<strong>Draw to audio</strong> — sketch a shape on canvas → generates audio",
                    "<strong>Background generation</strong> — generate while playing other samples",
                  ]])}
                </table>
                {btn_primary("Open the plugin →", f"{settings.FRONTEND_URL}/dashboard.html")}
                """,
            },
            4: {
                "subject": "💬 How's it going?",
                "preheader": "One last check-in — I'd love to hear how you're using OBSIDIAN Neural.",
                "h1": "How's it going?",
                "body": f"""
                <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
                  It's been almost a month. I'd genuinely love to know:
                </p>
                <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px;">
                  <tr><td style="padding:6px 0;color:#4a4a4a;font-size:14px;">→ What are you using the plugin for?</td></tr>
                  <tr><td style="padding:6px 0;color:#4a4a4a;font-size:14px;">→ Anything that's not working the way you expected?</td></tr>
                  <tr><td style="padding:6px 0;color:#4a4a4a;font-size:14px;">→ Features you'd like to see?</td></tr>
                </table>
                <p style="color:#4a4a4a;font-size:14px;line-height:1.6;margin:0 0 24px;">
                  Just hit reply. I read every message. This is also my last automated email &mdash; no more from me unless you reach out. 🙏
                </p>
                {btn_secondary("View my dashboard", f"{settings.FRONTEND_URL}/dashboard.html")}
                """,
            },
        }

        if week_number not in templates:
            return False

        t = templates[week_number]
        content = f"""
        {section_title(f"Week {week_number}")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">{t['h1']}</h1>
        {t['body']}
        """

        return EmailService._send_email(
            email,
            t["subject"],
            base_template(content, preheader=t["preheader"], unsubscribe_token=unsub),
            email_type=f"week{week_number}_inspiration",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_press_welcome(
        email: str, api_key: str, user_id: int = None, db: Session = None
    ) -> bool:
        unsub = (
            EmailService._get_unsubscribe_token(user_id, db) if (db and user_id) else ""
        )
        reset_url = f"{settings.FRONTEND_URL}/forgot-password.html"

        content = f"""
        {section_title("Press & media access")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Your journalist access is active.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          We've credited your account with <strong>200 units</strong> for testing.
        </p>

        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Server URL", f'<code style="font-family:Courier Prime,monospace;color:#b8605c;">{settings.API_URL}</code>')}
            {stat_row("API Key", f'<code style="font-family:Courier Prime,monospace;font-size:14px;font-weight:700;color:#1a1a1a;">{api_key}</code>')}
            {stat_row("Credits", "200 (press VIP)")}
          </table>
        ''')}

        {section_title("Download the plugin")}
        {download_buttons(f"{settings.REPO_URL}/releases/latest")}

        {section_title("Quick setup")}
        <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px;">
          <tr><td style="padding:6px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
            <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:20px;height:20px;text-align:center;line-height:20px;font-size:11px;font-weight:700;margin-right:10px;">1</span>
            Load OBSIDIAN Neural in your DAW
          </td></tr>
          <tr><td style="padding:6px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
            <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:20px;height:20px;text-align:center;line-height:20px;font-size:11px;font-weight:700;margin-right:10px;">2</span>
            Settings &rarr; paste Server URL + API Key
          </td></tr>
          <tr><td style="padding:6px 0;color:#4a4a4a;font-size:14px;line-height:1.6;">
            <span style="display:inline-block;background:#b8605c;color:white;border-radius:50%;width:20px;height:20px;text-align:center;line-height:20px;font-size:11px;font-weight:700;margin-right:10px;">3</span>
            Type a prompt — <em>"Techno kick 140bpm"</em> — hit Generate
          </td></tr>
        </table>

        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:0 0 16px;">
          Optional: <a href="{reset_url}" style="color:#b8605c;text-decoration:none;font-weight:600;">Set a personal password</a> for your account (not required to use the plugin).
        </p>

        <p style="color:#4a4a4a;font-size:14px;margin:24px 0 0;line-height:1.6;">
          Questions or technical issues? Just reply to this email.<br/>
          <span style="color:#b8605c;font-weight:600;">— Anthony Charretier, creator of OBSIDIAN Neural</span>
        </p>
        """

        return EmailService._send_email(
            email,
            "Exclusive press access: OBSIDIAN Neural VST",
            base_template(
                content,
                preheader="Your API key, download links and setup guide for OBSIDIAN Neural.",
                unsubscribe_token=unsub,
            ),
            email_type="press_welcome",
            user_id=user_id,
            db=db,
        )

    @staticmethod
    def send_contact_notification(
        admin_email: str,
        name: str,
        email: str,
        subject: str,
        message: str,
        ip: str = "Unknown",
        db: Session = None,
    ) -> bool:
        subject_labels = {
            "support": "🔧 Technical Support",
            "billing": "💳 Billing",
            "feature": "✨ Feature Request",
            "bug": "🐛 Bug Report",
            "partnership": "🤝 Partnership",
            "other": "💬 Other",
        }
        label = subject_labels.get(subject, "💬 Contact")

        content = f"""
        {section_title("Contact form")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          {label}
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("From", f"{name} &lt;{email}&gt;")}
            {stat_row("Subject", label)}
            {stat_row("IP", ip)}
          </table>
        ''')}
        {section_title("Message")}
        <p style="color:#4a4a4a;font-size:14px;line-height:1.7;background:#f5f5f5;padding:16px;border-radius:8px;margin:0;">
          {message.replace(chr(10), '<br/>')}
        </p>
        <p style="color:#cccccc;font-size:12px;margin:16px 0 0;">Reply-To is set to {email} &mdash; just hit reply.</p>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Contact] {label} — {name}"
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = admin_email
        msg["Reply-To"] = email
        msg.attach(
            MIMEText(
                base_template(content, preheader=f"New contact: {label} from {name}"),
                "html",
                "utf-8",
            )
        )

        try:
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=30
            ) as server:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
            return True
        except Exception as e:
            logger.error(f"Contact notification failed: {e}")
            return False

    @staticmethod
    def send_contact_confirmation(
        email: str, name: str, subject: str, message: str, db: Session = None
    ) -> bool:
        subject_labels = {
            "support": "Technical Support",
            "billing": "Billing",
            "feature": "Feature Request",
            "bug": "Bug Report",
            "partnership": "Partnership",
            "other": "Other",
        }
        label = subject_labels.get(subject, "Contact")

        content = f"""
        {section_title("Message received")}
        <h1 style="color:#1a1a1a;font-size:24px;font-weight:700;margin:0 0 16px;">
          Thanks, {name}.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          We received your message about <strong>{label}</strong> and will get back to you
          within 24&ndash;48 hours on business days.
        </p>
        {section_title("Your message")}
        <p style="color:#4a4a4a;font-size:14px;line-height:1.7;background:#f5f5f5;padding:16px;border-radius:8px;margin:0;">
          {message.replace(chr(10), '<br/>')}
        </p>
        """

        return EmailService._send_email(
            email,
            f"Message received — {label}",
            base_template(
                content,
                preheader=f"We got your message about {label}. We'll be in touch soon.",
            ),
            email_type="contact_confirmation",
            db=db,
        )

    @staticmethod
    def send_admin_followup_report(
        emails_sent: dict, admin_email: str = None, db: Session = None
    ) -> bool:
        if admin_email is None:
            admin_email = settings.SMTP_FROM_EMAIL

        total = sum(v for v in emails_sent.values() if isinstance(v, int))
        if total == 0:
            return True

        keys = {
            "day2": "Day 2 promo reminder",
            "day7_promo": "Day 7 final promo",
            "day7_help": "Day 7 help (no generation)",
            "week2": "Week 2 inspiration",
            "week3": "Week 3 advanced features",
            "week4": "Week 4 final contact",
        }

        rows = "".join(
            [
                stat_row(label, str(emails_sent.get(key, 0)))
                for key, label in keys.items()
                if emails_sent.get(key, 0) > 0
            ]
        )

        recipients_html = ""
        if emails_sent.get("recipients"):
            items = "".join(
                [
                    f'<tr><td style="padding:4px 0;color:#4a4a4a;font-size:13px;">&bull; {e}</td></tr>'
                    for e in emails_sent["recipients"]
                ]
            )
            recipients_html = f"""
            {section_title("Recipients")}
            <table cellpadding="0" cellspacing="0" border="0" width="100%">{items}</table>
            """

        content = f"""
        {section_title("Daily email report")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          {total} email{"s" if total > 1 else ""} sent today
        </h1>
        {info_box(f'<table cellpadding="0" cellspacing="0" border="0" width="100%">{rows}</table>')}
        {recipients_html}
        """

        return EmailService._send_email(
            admin_email,
            f"📊 Daily email report — {total} sent",
            base_template(content, preheader=f"{total} automated emails sent today."),
            email_type="admin_report",
            db=db,
        )
