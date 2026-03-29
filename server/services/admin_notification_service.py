import logging
from typing import Optional
from datetime import datetime, timezone
from server.services.email_service import EmailService
from server.templates.email_template import (
    base_template,
    info_box,
    stat_row,
    section_title,
    btn_secondary,
)
from server.config import settings

logger = logging.getLogger(__name__)

ADMIN_EMAIL = settings.SMTP_TO_EMAIL


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _admin_link() -> str:
    url = f"{settings.FRONTEND_URL}/dashboard.html?section=admin"
    return btn_secondary("View in admin dashboard", url)


def _send(subject: str, content: str, email_type: str) -> bool:
    try:
        return EmailService._send_email(
            ADMIN_EMAIL,
            subject,
            base_template(content, preheader=subject),
            email_type=email_type,
        )
    except Exception as e:
        logger.error(f"Failed to send admin notification [{email_type}]: {e}")
        return False


class AdminNotificationService:

    @staticmethod
    def notify_new_user_registration(
        email: str, user_id: int, oauth_provider: Optional[str] = None
    ) -> bool:
        auth_method = (
            f"OAuth — {oauth_provider.upper()}"
            if oauth_provider
            else "Email / Password"
        )

        content = f"""
        {section_title("New user registration")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          🎉 New user registered
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Auth method", auth_method)}
            {stat_row("Time", _now())}
          </table>
        ''')}
        {_admin_link()}
        """
        return _send(f"🎉 New user — {email}", content, "admin_new_user")

    @staticmethod
    def notify_new_subscription(
        email: str, user_id: int, tier: str, amount: str = None
    ) -> bool:
        amount_str = f"€{amount}" if amount else "—"

        content = f"""
        {section_title("New subscription")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          💳 New subscription activated
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Amount", amount_str)}
            {stat_row("Time", _now())}
          </table>
        ''')}
        {_admin_link()}
        """
        return _send(
            f"💳 New subscription — {tier.upper()} — {email}",
            content,
            "admin_new_subscription",
        )

    @staticmethod
    def notify_subscription_cancelled(email: str, user_id: int, tier: str) -> bool:
        content = f"""
        {section_title("Subscription cancelled")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          ⚠️ Subscription cancelled
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Status", '<span style="color:#b8605c;font-weight:700;">Cancelled</span>')}
            {stat_row("Time", _now())}
          </table>
        ''')}
        {_admin_link()}
        """
        return _send(
            f"⚠️ Subscription cancelled — {tier.upper()} — {email}",
            content,
            "admin_subscription_cancelled",
        )

    @staticmethod
    def notify_payment_failed(
        email: str, user_id: int, tier: str, error: str = None
    ) -> bool:
        error_row = stat_row("Error", error) if error else ""

        content = f"""
        {section_title("Payment failed")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          ❌ Payment failed
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Status", '<span style="color:#b8605c;font-weight:700;">Payment failed</span>')}
            {error_row}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0;">
          Action required — check Stripe dashboard or contact user.
        </p>
        {_admin_link()}
        """
        return _send(f"❌ Payment failed — {email}", content, "admin_payment_failed")

    @staticmethod
    def notify_account_deleted(
        email: str, user_id: int, subscription_tier: str = None
    ) -> bool:
        had_plan = subscription_tier and subscription_tier not in ["none", "free"]
        plan_row = (
            stat_row("Had plan", subscription_tier.capitalize()) if had_plan else ""
        )

        content = f"""
        {section_title("Account deleted")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          🗑️ Account deleted
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {plan_row}
            {stat_row("Time", _now())}
          </table>
        ''')}
        """
        return _send(f"🗑️ Account deleted — {email}", content, "admin_account_deleted")

    @staticmethod
    def notify_trial_started(email: str, user_id: int, tier: str) -> bool:
        content = f"""
        {section_title("Trial started")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          ✨ New trial started
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Duration", "7 days")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0;">
          Reminder will be sent to user at day 4. Conversion or cancellation in 7 days.
        </p>
        {_admin_link()}
        """
        return _send(
            f"✨ Trial started — {tier.upper()} — {email}",
            content,
            "admin_trial_started",
        )

    @staticmethod
    def notify_trial_ending(email: str, user_id: int, tier: str) -> bool:
        content = f"""
        {section_title("Trial ending soon")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          ⏰ Trial ending in 3 days
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0;">
          Reminder email has been sent to user.
        </p>
        {_admin_link()}
        """
        return _send(
            f"⏰ Trial ending — {tier.upper()} — {email}", content, "admin_trial_ending"
        )

    @staticmethod
    def notify_trial_ending_no_payment(email: str, user_id: int, tier: str) -> bool:
        content = f"""
        {section_title("Trial ending — no payment method")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          ⚠️ Trial ending — no payment method
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Payment method", '<span style="color:#b8605c;font-weight:700;">Not provided</span>')}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0;">
          Subscription will be cancelled automatically in 3 days unless user adds payment. Reminder sent.
        </p>
        {_admin_link()}
        """
        return _send(
            f"⚠️ Trial ending no payment — {tier.upper()} — {email}",
            content,
            "admin_trial_ending_no_payment",
        )

    @staticmethod
    def notify_trial_converted(email: str, user_id: int, tier: str) -> bool:
        content = f"""
        {section_title("Trial converted")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          🎉 Trial converted to paid subscription
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Status", '<span style="color:#b8605c;font-weight:700;">Active</span>')}
            {stat_row("Time", _now())}
          </table>
        ''')}
        {_admin_link()}
        """
        return _send(
            f"🎉 Trial converted — {tier.upper()} — {email}",
            content,
            "admin_trial_converted",
        )

    @staticmethod
    def notify_trial_not_converted(email: str, user_id: int, tier: str) -> bool:
        content = f"""
        {section_title("Trial not converted")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          😔 Trial ended without conversion
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", email)}
            {stat_row("User ID", str(user_id))}
            {stat_row("Plan", tier.capitalize())}
            {stat_row("Reason", "No payment method at end of trial")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0;">
          Consider a follow-up or feedback survey.
        </p>
        {_admin_link()}
        """
        return _send(
            f"😔 Trial not converted — {tier.upper()} — {email}",
            content,
            "admin_trial_not_converted",
        )

    @staticmethod
    def notify_admin_press_activation(journalist_email: str) -> bool:
        content = f"""
        {section_title("Press activation")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 16px;">
          📰 New press access activated
        </h1>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Email", journalist_email)}
            {stat_row("Credits", "200 (Press VIP)")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0;">
          Monitor your logs to see if they start generating content.
        </p>
        {_admin_link()}
        """
        return _send(
            f"📰 Press activation — {journalist_email}", content, "admin_notification"
        )
