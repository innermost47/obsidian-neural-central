from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta
from calendar import monthrange

from server.core.database import get_db, User, Provider, ProviderPing, ProviderJob
from server.api.dependencies import get_verified_user
from server.config import settings

router = APIRouter(prefix="/providers", tags=["Providers"])


def calculate_uptime(
    db: Session, provider: Provider, now: datetime, month_start: datetime
):
    p_created_at = (
        provider.created_at.replace(tzinfo=timezone.utc)
        if provider.created_at.tzinfo is None
        else provider.created_at
    )
    effective_month_start = max(month_start, p_created_at)
    last_24h_limit = now - timedelta(hours=24)
    effective_24h_start = max(last_24h_limit, p_created_at)

    hours_elapsed_month = (now - effective_month_start).total_seconds() / 3600
    hours_elapsed_24h = (now - effective_24h_start).total_seconds() / 3600

    def get_count(start_date):
        return (
            db.query(func.count(ProviderPing.id))
            .filter(
                ProviderPing.provider_id == provider.id,
                ProviderPing.pinged_at >= start_date,
                ProviderPing.responded == True,
            )
            .scalar()
            or 0
        )

    actual_pings_month = get_count(month_start)
    actual_pings_24h = get_count(last_24h_limit)

    PINGS_PER_HOUR_EXPECTED = 1.5

    expected_month = max(0.5, hours_elapsed_month * PINGS_PER_HOUR_EXPECTED)
    expected_24h = max(0.5, hours_elapsed_24h * PINGS_PER_HOUR_EXPECTED)

    ratio_month = min(1.0, actual_pings_month / expected_month)
    ratio_24h = min(1.0, actual_pings_24h / expected_24h)

    return {
        "month_hours": round(hours_elapsed_month * ratio_month, 1),
        "last_24h_hours": round(hours_elapsed_24h * ratio_24h, 1),
        "hours_elapsed_ref": hours_elapsed_month,
    }


@router.get("/my-stats")
def get_my_provider_stats(
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    provider = (
        db.query(Provider)
        .filter(Provider.user_id == current_user.id, Provider.is_banned == False)
        .first()
    )
    if not provider:
        raise HTTPException(
            status_code=404, detail="No provider account linked to your user."
        )

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    _, days_in_month = monthrange(year, month)
    days_elapsed = now.day
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    uptime_data = calculate_uptime(db, provider, now, month_start)
    required_hours = max(1, (uptime_data["hours_elapsed_ref"] / 24) * 8)

    global_generations_month = (
        db.query(func.count(ProviderJob.id))
        .filter(
            ProviderJob.status == "done",
            ProviderJob.used_fallback == False,
            func.extract("year", ProviderJob.created_at) == year,
            func.extract("month", ProviderJob.created_at) == month,
        )
        .scalar()
        or 0
    )

    active_providers_count = (
        db.query(func.count(Provider.id))
        .filter(Provider.is_active == True, Provider.is_banned == False)
        .scalar()
        or 1
    )

    tiers = list(settings.TIER_PRICES.keys())
    users_by_tier = {
        tier: (
            db.query(func.count(User.id))
            .filter(
                User.subscription_tier == tier,
                User.is_active == True,
                User.is_admin == False,
            )
            .scalar()
            or 0
        )
        for tier in tiers
    }
    monthly_revenue = sum(
        users_by_tier[t] * (settings.TIER_PRICES[t] / 100) for t in settings.TIER_PRICES
    )
    providers_pool = monthly_revenue * 0.85
    my_estimated_rev = (
        (providers_pool / active_providers_count) if active_providers_count > 0 else 0
    )

    return {
        "provider": {
            "id": provider.id,
            "name": provider.name,
            "is_active": provider.is_active,
            "jobs_done": provider.jobs_done or 0,
            "billable_jobs": provider.billable_jobs or 0,
            "last_seen": provider.last_seen.isoformat() if provider.last_seen else None,
        },
        "uptime": {
            "last_24h_hours": uptime_data["last_24h_hours"],
            "last_24h_target": 8,
            "month_hours": uptime_data["month_hours"],
            "month_required_hours": int(required_hours),
            "month_progress_pct": (
                round((uptime_data["month_hours"] / required_hours) * 100, 1)
                if required_hours > 0
                else 0
            ),
        },
        "network": {
            "global_generations_this_month": global_generations_month,
            "active_providers": active_providers_count,
        },
        "users": {
            "by_tier": users_by_tier,
            "paying_total": sum(users_by_tier.values()),
        },
        "revenue": {
            "platform_monthly_eur": round(monthly_revenue, 2),
            "providers_pool_eur": round(providers_pool, 2),
            "my_estimated_share_eur": round(my_estimated_rev, 2),
            "platform_fee_pct": 15,
            "providers_share_pct": 85,
            "active_providers": active_providers_count,
            "finances_url": f"{settings.APP_URL}/api/v1/public/finances",
        },
        "period": {
            "year": year,
            "month": month,
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
        },
    }
