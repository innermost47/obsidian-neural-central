from pathlib import Path
from dotenv import load_dotenv
import os
import sys

ENV = os.getenv("ENV", "prod")
env_file = Path(__file__).resolve().parent.parent / f".env.{ENV}"
load_dotenv(dotenv_path=env_file, override=True)

import argparse
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.core.database import (
    SessionLocal,
    User,
    GiftSubscription,
    GiftSubscriptionStatus,
)
from server.services.email_service import EmailService
from server.services.credits_service import CreditsService
from dateutil.relativedelta import relativedelta

CODE_VALIDITY_DAYS = 365


def log(msg: str):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)


def check_and_send_followup_emails():
    db = SessionLocal()
    now = datetime.utcnow()

    emails_sent = {
        "day2": 0,
        "day7_promo": 0,
        "day7_help": 0,
        "week2": 0,
        "week3": 0,
        "week4": 0,
        "recipients": [],
    }

    try:
        log("Starting followup email check...")

        day2_start = now - timedelta(days=2, hours=1)
        day2_end = now - timedelta(days=2)
        for user in (
            db.query(User)
            .filter(
                User.created_at.between(day2_end, day2_start),
                User.subscription_tier == "none",
                User.accept_news_updates == True,
                User.email_verified == True,
                User.last_login != None,
            )
            .all()
        ):
            try:
                if EmailService.send_day2_promo_reminder(user.email, db=db):
                    emails_sent["day2"] += 1
                    emails_sent["recipients"].append(f"{user.email} (Day 2)")
                    log(f"✅ J+2 sent to {user.email}")
            except Exception as e:
                log(f"❌ J+2 failed for {user.email}: {e}")

        day7_start = now - timedelta(days=7, hours=1)
        day7_end = now - timedelta(days=7)
        for user in (
            db.query(User)
            .filter(
                User.created_at.between(day7_end, day7_start),
                User.subscription_tier == "none",
                User.accept_news_updates == True,
                User.email_verified == True,
            )
            .all()
        ):
            try:
                if EmailService.send_day7_final_promo_reminder(user.email, db=db):
                    emails_sent["day7_promo"] += 1
                    emails_sent["recipients"].append(f"{user.email} (Day 7 Promo)")
                    log(f"✅ J+7 promo sent to {user.email}")
            except Exception as e:
                log(f"❌ J+7 promo failed for {user.email}: {e}")

        for user in (
            db.query(User)
            .filter(
                User.created_at.between(day7_end, day7_start),
                User.credits_used == 0,
                User.accept_news_updates == True,
                User.email_verified == True,
            )
            .all()
        ):
            try:
                if EmailService.send_no_generation_help(user.email, db=db):
                    emails_sent["day7_help"] += 1
                    emails_sent["recipients"].append(f"{user.email} (Day 7 Help)")
                    log(f"✅ J+7 help sent to {user.email}")
            except Exception as e:
                log(f"❌ J+7 help failed for {user.email}: {e}")

        for week_number in [2, 3, 4]:
            days = week_number * 7
            week_start = now - timedelta(days=days, hours=1)
            week_end = now - timedelta(days=days)
            for user in (
                db.query(User)
                .filter(
                    User.created_at.between(week_end, week_start),
                    User.subscription_tier == "none",
                    User.accept_news_updates == True,
                    User.email_verified == True,
                )
                .all()
            ):
                try:
                    if EmailService.send_weekly_inspiration(
                        user.email, week_number, db=db
                    ):
                        emails_sent[f"week{week_number}"] += 1
                        emails_sent["recipients"].append(
                            f"{user.email} (Week {week_number})"
                        )
                        log(f"✅ Week {week_number} sent to {user.email}")
                except Exception as e:
                    log(f"❌ Week {week_number} failed for {user.email}: {e}")

        for admin in db.query(User).filter(User.is_admin == True).all():
            EmailService.send_admin_followup_report(
                emails_sent=emails_sent, admin_email=admin.email, db=db
            )

        total = sum(v for k, v in emails_sent.items() if isinstance(v, int))
        log(f"✅ Followup check done — {total} emails sent")

    except Exception as e:
        log(f"❌ Error in followup task: {e}")
        db.rollback()
    finally:
        db.close()


def send_gift_expiration_warnings():
    db = SessionLocal()
    now = datetime.utcnow()
    count = 0

    try:
        log("Starting gift expiration warning check...")

        for days_left in [7, 3, 1]:
            start_time = now + timedelta(days=days_left)
            end_time = now + timedelta(days=days_left, hours=1)

            for gift in (
                db.query(GiftSubscription)
                .filter(
                    GiftSubscription.status == GiftSubscriptionStatus.ACTIVE,
                    GiftSubscription.expires_at.between(start_time, end_time),
                )
                .all()
            ):
                user = db.query(User).filter(User.id == gift.recipient_user_id).first()
                if user:
                    try:
                        EmailService.send_expiration_warning(
                            recipient_email=user.email,
                            tier=gift.tier,
                            days_left=days_left,
                            db=db,
                        )
                        count += 1
                        log(f"✅ J-{days_left} warning sent to {user.email}")
                    except Exception as e:
                        log(f"❌ J-{days_left} warning failed for {user.email}: {e}")

        db.commit()
        log(f"✅ Expiration warnings done — {count} emails sent")

    except Exception as e:
        log(f"❌ Error in expiration warnings task: {e}")
        db.rollback()
    finally:
        db.close()


def check_and_expire_gifts():
    db = SessionLocal()
    now = datetime.utcnow()
    gifts_expired = 0
    codes_expired = 0

    try:
        log("Starting gift expiration check...")

        for gift in (
            db.query(GiftSubscription)
            .filter(
                GiftSubscription.status == GiftSubscriptionStatus.ACTIVE,
                GiftSubscription.expires_at <= now,
            )
            .all()
        ):
            user = db.query(User).filter(User.id == gift.recipient_user_id).first()
            gift.status = GiftSubscriptionStatus.EXPIRED
            gifts_expired += 1
            if user and user.active_gift_subscription_id == gift.id:
                user.subscription_tier = "free"
                user.subscription_status = "inactive"
                user.active_gift_subscription_id = None
                log(f"✅ Gift {gift.gift_code} expired → {user.email} reset to free")

        purchase_limit = now - timedelta(days=CODE_VALIDITY_DAYS)
        for gift in (
            db.query(GiftSubscription)
            .filter(
                GiftSubscription.status == GiftSubscriptionStatus.PENDING,
                GiftSubscription.purchased_at <= purchase_limit,
            )
            .all()
        ):
            gift.status = GiftSubscriptionStatus.EXPIRED
            codes_expired += 1
            log(f"🚫 Pending code {gift.gift_code} expired (>{CODE_VALIDITY_DAYS}d)")

        db.commit()
        log(
            f"✅ Gift expiration done — {gifts_expired} active, {codes_expired} pending expired"
        )

    except Exception as e:
        log(f"❌ Error in expire gifts task: {e}")
        db.rollback()
    finally:
        db.close()


def refill_gift_subscription_credits():
    db = SessionLocal()
    now = datetime.utcnow()
    count = 0

    try:
        log("Starting gift credit refill check...")

        from server.core.database import GiftSubscription as GS

        for user in (
            db.query(User)
            .filter(
                User.active_gift_subscription_id != None,
                User.subscription_status == "active",
            )
            .all()
        ):
            gift = db.query(GS).get(user.active_gift_subscription_id)
            if not gift or gift.status != GiftSubscriptionStatus.ACTIVE:
                continue

            next_refill = (
                gift.last_credit_refill_at + relativedelta(months=1)
                if gift.last_credit_refill_at
                else gift.activated_at + relativedelta(months=1)
            )

            if now >= next_refill < gift.expires_at:
                CreditsService.refill_credits(db, user.id, user.subscription_tier)
                gift.last_credit_refill_at = now
                count += 1
                log(f"✅ Credits refilled for {user.email} (gift {gift.gift_code})")

        db.commit()
        log(f"✅ Gift credit refill done — {count} users refilled")

    except Exception as e:
        log(f"❌ Error in refill gifts task: {e}")
        db.rollback()
    finally:
        db.close()


def refill_provider_credits():
    db = SessionLocal()
    count = 0

    try:
        log("Starting provider credits refill...")

        from server.core.database import Provider, User

        providers = (
            db.query(Provider)
            .filter(
                Provider.is_active == True,
                Provider.is_banned == False,
                Provider.user_id != None,
            )
            .all()
        )

        for provider in providers:
            user = db.query(User).filter(User.id == provider.user_id).first()
            if user and user.subscription_tier == "provider":
                CreditsService.refill_credits(db, user.id, "provider")
                count += 1
                log(f"✅ Credits refilled for provider {provider.name} ({user.email})")

        db.commit()
        log(f"✅ Provider credits refill done — {count} providers refilled")

    except Exception as e:
        log(f"❌ Error in provider credits refill: {e}")
        db.rollback()
    finally:
        db.close()


def compute_and_redistribute():
    import asyncio
    import stripe
    from server.config import settings
    from server.services.provider_ping_service import ProviderPingService
    from server.core.database import Provider, ProviderPing

    db = SessionLocal()
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = month_start
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)

    try:
        log(f"Starting redistribution for {last_month_start.strftime('%Y-%m')}...")
        stripe.api_key = settings.STRIPE_SECRET_KEY

        total_cents = 0
        has_more = True
        starting_after = None

        while has_more:
            params = {
                "created": {
                    "gte": int(last_month_start.timestamp()),
                    "lt": int(last_month_end.timestamp()),
                },
                "limit": 100,
            }
            if starting_after:
                params["starting_after"] = starting_after

            charges = stripe.Charge.list(**params)
            for charge in charges.data:
                if charge.status == "succeeded" and not charge.refunded:
                    total_cents += charge.amount

            has_more = charges.has_more
            if has_more:
                starting_after = charges.data[-1].id

        log(
            f"Stripe revenue for {last_month_start.strftime('%Y-%m')}: {total_cents/100:.2f}€"
        )

        REQUIRED_DAILY_HOURS = 8
        providers = db.query(Provider).filter(Provider.is_banned == False).all()

        log(f"Calculating prorated uptime scores for {len(providers)} providers...")

        for provider in providers:
            successful_pings = (
                db.query(ProviderPing)
                .filter(
                    ProviderPing.provider_id == provider.id,
                    ProviderPing.pinged_at >= last_month_start,
                    ProviderPing.pinged_at < last_month_end,
                    ProviderPing.responded == True,
                )
                .count()
            )

            provider_start = last_month_start

            if provider.created_at and provider.created_at > last_month_start:
                provider_start = provider.created_at

            total_hours_in_period = (
                last_month_end - provider_start
            ).total_seconds() / 3600

            required_hours_for_him = max(
                1, (total_hours_in_period / 24) * REQUIRED_DAILY_HOURS
            )

            provider.uptime_score = successful_pings / required_hours_for_him

            log(
                f"📊 {provider.name}: {successful_pings}h real / {int(required_hours_for_him)}h required = {provider.uptime_score*100:.1f}%"
            )

        db.commit()

        report = asyncio.run(
            ProviderPingService.compute_monthly_redistribution(
                db=db,
                month_revenue_cents=total_cents,
                month_start=last_month_start,
                dry_run=False,
            )
        )
        log(f"✅ Redistribution done — {len(report['transfers'])} transfers")

    except Exception as e:
        log(f"❌ Error in redistribution task: {e}")
        db.rollback()
    finally:
        db.close()


TASKS = {
    "followup_emails": check_and_send_followup_emails,
    "expiration_warnings": send_gift_expiration_warnings,
    "expire_gifts": check_and_expire_gifts,
    "refill_gifts": refill_gift_subscription_credits,
    "refill_provider_credits": refill_provider_credits,
    "redistribution": compute_and_redistribute,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OBSIDIAN Neural cron tasks")
    parser.add_argument(
        "--task",
        choices=list(TASKS.keys()),
        default="followup_emails",
        help="Task to run (default: followup_emails)",
    )
    args = parser.parse_args()

    log(f"=== Running task: {args.task} ===")
    TASKS[args.task]()
    log(f"=== Task {args.task} completed ===")
