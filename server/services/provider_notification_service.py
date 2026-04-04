import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from server.templates.email_template import (
    base_template,
    info_box,
    stat_row,
    section_title,
)
from server.services.email_service import EmailService
from server.config import settings

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _get_active_provider_emails(db: Session) -> list[str]:
    from server.core.database import Provider, User

    rows = (
        db.query(User.email)
        .join(Provider, Provider.user_id == User.id)
        .filter(
            Provider.is_active,
            Provider.is_banned == False,
            User.is_admin == False,
            Provider.user_id.isnot(None),
        )
        .all()
    )
    return [row.email for row in rows]


def _send_to_all_providers(
    db: Session,
    subject: str,
    content: str,
    email_type: str,
) -> int:
    emails = _get_active_provider_emails(db)
    if not emails:
        logger.info(
            f"[ProviderNotification] No active providers to notify for {email_type}"
        )
        return 0

    sent = 0
    html = base_template(content, preheader=subject)
    for email in emails:
        try:
            ok = EmailService._send_email(email, subject, html, email_type=email_type)
            if ok:
                sent += 1
        except Exception as e:
            logger.error(f"[ProviderNotification] Failed to notify {email}: {e}")

    logger.info(
        f"[ProviderNotification] {sent}/{len(emails)} providers notified for {email_type}"
    )
    return sent


class ProviderNotificationService:

    @staticmethod
    def notify_new_free_user(db: Session) -> int:
        content = f"""
        {section_title("Network activity")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 12px;">
          👤 A new user just joined OBSIDIAN Neural.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          The community keeps growing. Every new user is a potential subscriber —
          and that means a larger revenue pool to share.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Event", "New free account")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#cccccc;font-size:12px;margin:16px 0 0;line-height:1.6;">
          Revenue is split monthly among all active providers.
          Details at <a href="{settings.APP_URL}/api/v1/public/finances" style="color:#b8605c;text-decoration:none;">{settings.API_URL}/public/finances.json</a>
        </p>
        """
        return _send_to_all_providers(
            db,
            subject="👤 New user joined OBSIDIAN Neural",
            content=content,
            email_type="provider_new_free_user",
        )

    @staticmethod
    def notify_new_subscriber(db: Session, tier: str) -> int:
        tier_display = tier.capitalize()
        price = settings.TIER_PRICES_EUR.get(tier, 0)
        share_85 = price * 0.85

        content = f"""
        {section_title("Network activity")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 12px;">
          💳 New <span style="color:#b8605c;">{tier_display}</span> subscriber.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          The revenue pool just grew. Here's what this subscription adds to the network.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Monthly revenue", f"&euro;{price:.2f}")}
            {stat_row("Shared to providers (85%)", f"&euro;{share_85:.2f} / month")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0 0;">
          This amount is split equally among all active providers at the end of the month.
          Keep your node running to stay eligible. 🖥️
        </p>
        <p style="color:#cccccc;font-size:12px;margin:12px 0 0;">
          Full breakdown at <a href="{settings.API_URL}/public/finances.json" style="color:#b8605c;text-decoration:none;">{settings.API_URL}/public/finances.json</a>
        </p>
        """
        return _send_to_all_providers(
            db,
            subject=f"💳 New {tier_display} subscriber — the pool just grew",
            content=content,
            email_type="provider_new_subscriber",
        )

    @staticmethod
    def notify_trial_converted(db: Session, tier: str) -> int:
        tier_display = tier.capitalize()
        price = settings.TIER_PRICES_EUR.get(tier, 0)
        share_85 = price * 0.85

        content = f"""
        {section_title("Network activity")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 12px;">
          🎉 A trial just converted to <span style="color:#b8605c;">{tier_display}</span>.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          A user who tried the plugin decided to stay. That's the best signal.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Monthly revenue", f"&euro;{price:.2f}")}
            {stat_row("Shared to providers (85%)", f"&euro;{share_85:.2f} / month")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#cccccc;font-size:12px;margin:16px 0 0;">
          <a href="{settings.API_URL}/public/finances.json" style="color:#b8605c;text-decoration:none;">{settings.API_URL}/public/finances.json</a>
        </p>
        """
        return _send_to_all_providers(
            db,
            subject=f"🎉 Trial converted to {tier_display} — revenue pool updated",
            content=content,
            email_type="provider_trial_converted",
        )

    @staticmethod
    def notify_subscription_cancelled(db: Session, tier: str) -> int:
        tier_display = tier.capitalize()
        price = settings.TIER_PRICES_EUR.get(tier, 0)

        content = f"""
        {section_title("Network activity")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 12px;">
          A <span style="color:#b8605c;">{tier_display}</span> subscription was cancelled.
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          This happens. The user still has access until the end of their billing period,
          and may come back.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Plan", tier_display)}
            {stat_row("Revenue impact", f"&minus;&euro;{price:.2f} / month at next cycle")}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#cccccc;font-size:12px;margin:16px 0 0;">
          <a href="{settings.API_URL}/public/finances.json" style="color:#b8605c;text-decoration:none;">{settings.API_URL}/public/finances.json</a>
        </p>
        """
        return _send_to_all_providers(
            db,
            subject=f"A {tier_display} subscription was cancelled",
            content=content,
            email_type="provider_subscription_cancelled",
        )

    @staticmethod
    def notify_provider_banned(
        provider_email: str, provider_name: str, reason: str
    ) -> bool:
        content = f"""
        {section_title("Account suspended")}
        <h1 style="color:#1a1a1a;font-size:22px;font-weight:700;margin:0 0 12px;">
          🚫 Your provider account has been suspended
        </h1>
        <p style="color:#4a4a4a;font-size:15px;line-height:1.7;margin:0 0 24px;">
          Your node has been automatically removed from the OBSIDIAN Neural network
          due to integrity violations. This decision is final.
        </p>
        {info_box(f'''
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {stat_row("Provider", provider_name)}
            {stat_row("Status", '<span style="color:#b8605c;font-weight:700;">BANNED</span>')}
            {stat_row("Reason", reason)}
            {stat_row("Time", _now())}
          </table>
        ''')}
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:16px 0 0;">
          <strong>What happened:</strong><br>
          Your provider node failed critical security and integrity checks during operation.
          The OBSIDIAN Neural network enforces strict validation of all responses to protect
          the platform and our users.
        </p>
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:12px 0 0;">
          <strong>Common causes:</strong><br>
          • Invalid or malformed response headers<br>
          • Incorrect seed values in generation responses<br>
          • Code integrity verification failures<br>
          • Invalid content-type on audio responses<br>
          • Unsolicited WebSocket messages
        </p>
        <p style="color:#4a4a4a;font-size:13px;line-height:1.6;margin:12px 0 0;">
          <strong>Next steps:</strong><br>
          Review the reason above and check your provider code against the protocol specification.
          If you believe this is an error, contact support with logs and details.
        </p>
        <p style="color:#cccccc;font-size:12px;margin:16px 0 0;">
          Your subscription tier has been downgraded to free. You may reapply as a provider
          after addressing the issues.
        </p>
        """
        try:
            return EmailService._send_email(
                provider_email,
                f"🚫 Provider account suspended — {provider_name}",
                base_template(
                    content, preheader="Your provider account has been suspended"
                ),
                email_type="provider_banned",
            )
        except Exception as e:
            logger.error(f"Failed to send ban notification to {provider_email}: {e}")
            return False
