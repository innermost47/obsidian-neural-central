import asyncio
import httpx
import os
import hashlib
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

PING_PROBABILITY = float(os.getenv("PING_PROBABILITY", "1.0"))
PING_TIMEOUT = float(os.getenv("PING_TIMEOUT", "5.0"))
MIN_UPTIME_SCORE = float(os.getenv("MIN_UPTIME_SCORE", "0.30"))
MIN_BILLABLE_JOBS = int(os.getenv("MIN_BILLABLE_JOBS", "1"))
RANDOM_DELAY_MAX_MINUTES = int(os.getenv("RANDOM_DELAY_MAX_MINUTES", "50"))
PLATFORM_FEE_PCT = float(os.getenv("PLATFORM_FEE_PCT", "0.15"))


class ProviderPingService:

    @staticmethod
    async def check_and_ping(db: Session):
        from server.core.database import Provider

        delay_seconds = random.randint(0, 9 * 60)
        print(f"🥷 Stealth ping — striking in {delay_seconds // 60} min...")
        await asyncio.sleep(delay_seconds)

        providers = (
            db.query(Provider)
            .filter(Provider.is_active == True, Provider.is_banned == False)
            .all()
        )

        if not providers:
            return

        half_of_providers = max(1, len(providers) // 2)
        number_of_targets = max(1, random.randint(1, half_of_providers))
        targets = random.sample(providers, min(number_of_targets, len(providers)))

        print(f"🎯 Pinging {len(targets)} random target(s)...")

        await ProviderPingService._ping_all_providers(db, targets)

    @staticmethod
    async def _ping_all_providers(db: Session, targets: list = None):
        from server.core.database import Provider, ProviderPing

        if targets is None:
            targets = (
                db.query(Provider)
                .filter(Provider.is_active == True, Provider.is_banned == False)
                .all()
            )

        if not targets:
            return

        print(f"📡 Executing ping for {len(targets)} provider(s)...")

        for provider in targets:
            start_time = datetime.now(timezone.utc)
            responded = False
            response_time_ms = None

            try:
                async with httpx.AsyncClient(timeout=PING_TIMEOUT) as client:
                    t0 = asyncio.get_event_loop().time()
                    response = await client.get(
                        f"{provider.url.rstrip('/')}/status",
                    )
                    if response.status_code == 200:
                        data = response.json()
                        returned_key = data.get("api_key", "")
                        key_hash = hashlib.sha256(returned_key.encode()).hexdigest()
                        if key_hash != provider.api_key:
                            print(
                                f"⚠️ {provider.name} — invalid API key in response, ignoring"
                            )
                            responded = False
                        else:
                            responded = True
                    t1 = asyncio.get_event_loop().time()
                    response_time_ms = int((t1 - t0) * 1000)
                    responded = response.status_code == 200
            except Exception as e:
                print(f"⚠️  Ping failed for {provider.name}: {e}")
                responded = False

            ping_log = ProviderPing(
                provider_id=provider.id,
                pinged_at=start_time,
                responded=responded,
                response_time_ms=response_time_ms,
            )
            db.add(ping_log)

            provider.last_ping = start_time
            if responded:
                provider.last_seen = start_time

            status = "✅" if responded else "❌"
            rt = f"{response_time_ms}ms" if response_time_ms else "timeout"
            print(f"  {status} {provider.name} — {rt}")

        db.commit()
        await ProviderPingService._update_uptime_scores(db)

    @staticmethod
    async def _update_uptime_scores(db: Session):
        from server.core.database import Provider, ProviderPing

        now = datetime.now(timezone.utc)
        twenty_four_hours_ago = now - timedelta(hours=24)

        providers = db.query(Provider).filter(Provider.is_banned == False).all()

        for provider in providers:
            total_pings = (
                db.query(ProviderPing)
                .filter(
                    ProviderPing.provider_id == provider.id,
                    ProviderPing.pinged_at >= twenty_four_hours_ago,
                )
                .count()
            )

            if total_pings == 0:
                provider.uptime_score = 0.0
                continue

            responded_pings = (
                db.query(ProviderPing)
                .filter(
                    ProviderPing.provider_id == provider.id,
                    ProviderPing.pinged_at >= twenty_four_hours_ago,
                    ProviderPing.responded == True,
                )
                .count()
            )

            provider.uptime_score = round(responded_pings / total_pings, 4)

        db.commit()

    @staticmethod
    def get_eligible_providers(db: Session, month_start: datetime) -> List[Dict]:
        from server.core.database import Provider, ProviderJob

        providers = db.query(Provider).filter(Provider.is_banned == False).all()

        eligible = []
        for provider in providers:
            if provider.uptime_score < 0.8:
                print(
                    f"⛔ {provider.name} ineligible — uptime {provider.uptime_score*100:.1f}% "
                    f"< {0.8*100:.0f}%"
                )
                continue

            billable_this_month = (
                db.query(ProviderJob)
                .filter(
                    ProviderJob.provider_id == provider.id,
                    ProviderJob.status == "done",
                    ProviderJob.used_fallback == False,
                    ProviderJob.created_at >= month_start,
                )
                .count()
            )

            if billable_this_month < MIN_BILLABLE_JOBS:
                print(
                    f"⛔ {provider.name} ineligible — "
                    f"only {billable_this_month} billable job(s) this month"
                )
                continue

            eligible.append(
                {
                    "id": provider.id,
                    "name": provider.name,
                    "stripe_account_id": provider.stripe_account_id,
                    "uptime_score": provider.uptime_score,
                    "billable_jobs": billable_this_month,
                }
            )
            print(
                f"✅ {provider.name} eligible — "
                f"uptime {provider.uptime_score*100:.1f}%, "
                f"{billable_this_month} billable job(s)"
            )

        return eligible

    @staticmethod
    async def compute_monthly_redistribution(
        db: Session,
        month_revenue_cents: int,
        month_start: Optional[datetime] = None,
        dry_run: bool = True,
    ) -> Dict:
        if month_start is None:
            now = datetime.now(timezone.utc)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        platform_fee_cents = int(month_revenue_cents * PLATFORM_FEE_PCT)
        distributable_cents = month_revenue_cents - platform_fee_cents

        eligible = ProviderPingService.get_eligible_providers(db, month_start)

        if not eligible:
            return {
                "status": "no_eligible_providers",
                "month_revenue_cents": month_revenue_cents,
                "platform_fee_pct": round(PLATFORM_FEE_PCT * 100, 1),
                "platform_fee_cents": platform_fee_cents,
                "distributable_cents": distributable_cents,
                "eligible_count": 0,
                "share_per_provider_cents": 0,
                "transfers": [],
                "dry_run": dry_run,
            }

        nb_eligible = len(eligible)
        share_cents = distributable_cents // nb_eligible
        remainder_cents = distributable_cents - (share_cents * nb_eligible)

        print(f"\n💰 Monthly redistribution")
        print(f"   Revenue      : {month_revenue_cents/100:.2f}€")
        print(
            f"   Platform fee : {platform_fee_cents/100:.2f}€ ({PLATFORM_FEE_PCT*100:.0f}%)"
        )
        print(f"   Distributable: {distributable_cents/100:.2f}€")
        print(f"   Eligible     : {nb_eligible} providers")
        print(f"   Share each   : {share_cents/100:.2f}€")
        print(f"   Remainder    : {remainder_cents/100:.2f}€ (kept on platform)")

        transfers = []
        for provider in eligible:
            transfer = {
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "stripe_account_id": provider["stripe_account_id"],
                "amount_cents": share_cents,
                "uptime_score": provider["uptime_score"],
                "billable_jobs": provider["billable_jobs"],
                "status": "pending",
            }

            if not dry_run:
                if not provider["stripe_account_id"]:
                    transfer["status"] = "skipped_no_stripe"
                    print(f"⚠️  Skipped {provider['name']} — no Stripe account")
                else:
                    stripe_result = await ProviderPingService._stripe_transfer(
                        amount_cents=share_cents,
                        stripe_account_id=provider["stripe_account_id"],
                        provider_name=provider["name"],
                        month=month_start.strftime("%Y-%m"),
                    )
                    transfer["status"] = "sent" if stripe_result else "failed"
                    transfer["stripe_transfer_id"] = stripe_result

            transfers.append(transfer)

        report = {
            "status": "computed" if dry_run else "executed",
            "month": month_start.strftime("%Y-%m"),
            "month_revenue_cents": month_revenue_cents,
            "platform_fee_pct": round(PLATFORM_FEE_PCT * 100, 1),
            "platform_fee_cents": platform_fee_cents,
            "distributable_cents": distributable_cents,
            "eligible_count": nb_eligible,
            "share_per_provider_cents": share_cents,
            "remainder_cents": remainder_cents,
            "transfers": transfers,
            "dry_run": dry_run,
        }

        ProviderPingService._write_public_finance_report(report=report, db=db)

        return report

    @staticmethod
    async def _stripe_transfer(
        amount_cents: int,
        stripe_account_id: str,
        provider_name: str,
        month: str,
    ) -> Optional[str]:
        try:
            import stripe
            from server.config import settings

            stripe.api_key = settings.STRIPE_SECRET_KEY

            transfer = stripe.Transfer.create(
                amount=amount_cents,
                currency="eur",
                destination=stripe_account_id,
                description=f"OBSIDIAN Neural — provider share {month}",
                metadata={
                    "provider": provider_name,
                    "month": month,
                    "type": "provider_redistribution",
                },
            )
            print(
                f"💸 Transfer sent to {provider_name}: {amount_cents/100:.2f}€ ({transfer.id})"
            )
            return transfer.id

        except Exception as e:
            print(f"❌ Stripe transfer failed for {provider_name}: {e}")
            return None

    @staticmethod
    def _write_public_finance_report(report: Dict, db: Session):
        from server.core.database import FinanceReport

        public_transfers = [
            {
                "provider_name": t["provider_name"],
                "amount_eur": round(t["amount_cents"] / 100, 2),
                "uptime_score_pct": round(t["uptime_score"] * 100, 1),
                "billable_jobs": t["billable_jobs"],
                "status": t["status"],
            }
            for t in report["transfers"]
        ]

        try:
            existing = (
                db.query(FinanceReport)
                .filter(FinanceReport.month == report["month"])
                .first()
            )
            if existing:
                existing.total_revenue_eur = round(
                    report["month_revenue_cents"] / 100, 2
                )
                existing.platform_fee_pct = report["platform_fee_pct"]
                existing.platform_fee_eur = round(report["platform_fee_cents"] / 100, 2)
                existing.distributable_eur = round(
                    report["distributable_cents"] / 100, 2
                )
                existing.eligible_providers = report["eligible_count"]
                existing.share_per_provider_eur = round(
                    report["share_per_provider_cents"] / 100, 2
                )
                existing.remainder_eur = round(report["remainder_cents"] / 100, 2)
                existing.transfers = public_transfers
                existing.published_at = datetime.now(timezone.utc)
            else:
                entry = FinanceReport(
                    month=report["month"],
                    total_revenue_eur=round(report["month_revenue_cents"] / 100, 2),
                    platform_fee_pct=report["platform_fee_pct"],
                    platform_fee_eur=round(report["platform_fee_cents"] / 100, 2),
                    distributable_eur=round(report["distributable_cents"] / 100, 2),
                    eligible_providers=report["eligible_count"],
                    share_per_provider_eur=round(
                        report["share_per_provider_cents"] / 100, 2
                    ),
                    remainder_eur=round(report["remainder_cents"] / 100, 2),
                    transfers=public_transfers,
                    published_at=datetime.now(timezone.utc),
                )
                db.add(entry)
            db.commit()
            print(f"📊 Public finance report saved for {report['month']}")
        except Exception as e:
            db.rollback()
            print(f"⚠️  Failed to save public finance report: {e}")

    @staticmethod
    def get_ping_stats(db: Session, provider_id: int, days: int = 30) -> Dict:
        from server.core.database import ProviderPing

        since = datetime.now(timezone.utc) - timedelta(days=days)

        pings = (
            db.query(ProviderPing)
            .filter(
                ProviderPing.provider_id == provider_id,
                ProviderPing.pinged_at >= since,
            )
            .order_by(ProviderPing.pinged_at)
            .all()
        )

        if not pings:
            return {"total": 0, "responded": 0, "uptime_pct": 0, "pings": []}

        responded = sum(1 for p in pings if p.responded)
        rt_values = [p.response_time_ms for p in pings if p.response_time_ms]
        avg_rt = round(sum(rt_values) / len(rt_values)) if rt_values else None

        return {
            "total": len(pings),
            "responded": responded,
            "uptime_pct": round(responded / len(pings) * 100, 1),
            "avg_response_time_ms": avg_rt,
            "pings": [
                {
                    "pinged_at": p.pinged_at.isoformat(),
                    "responded": p.responded,
                    "response_time_ms": p.response_time_ms,
                }
                for p in pings[-100:]
            ],
        }
