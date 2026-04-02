from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta
from calendar import monthrange

from server.core.database import get_db, User, Provider, ProviderPing, ProviderJob
from server.api.dependencies import get_verified_user
from server.config import settings

router = APIRouter(prefix="/providers", tags=["Providers"])


@router.get("/my-stats")
def get_my_provider_stats(
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    provider = (
        db.query(Provider)
        .filter(
            Provider.user_id == current_user.id,
            Provider.is_banned == False,
        )
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

    pings_last_24h = (
        db.query(func.count(ProviderPing.id))
        .filter(
            ProviderPing.provider_id == provider.id,
            ProviderPing.pinged_at >= now - timedelta(hours=24),
            ProviderPing.responded == True,
        )
        .scalar()
        or 0
    )

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    pings_this_month = (
        db.query(func.count(ProviderPing.id))
        .filter(
            ProviderPing.provider_id == provider.id,
            ProviderPing.pinged_at >= month_start,
            ProviderPing.responded == True,
        )
        .scalar()
        or 0
    )

    p_created_at = provider.created_at
    if p_created_at and p_created_at.tzinfo is None:
        p_created_at = p_created_at.replace(tzinfo=timezone.utc)

    provider_start = month_start
    if p_created_at and p_created_at > month_start:
        provider_start = p_created_at

    total_hours_in_period = (now - provider_start).total_seconds() / 3600
    required_hours = max(1, (total_hours_in_period / 24) * 8)

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
    users_by_tier = {}
    for tier in tiers:
        count = (
            db.query(func.count(User.id))
            .filter(
                User.subscription_tier == tier,
                User.is_active == True,
                User.is_admin == False,
            )
            .scalar()
            or 0
        )
        users_by_tier[tier] = count

    paying_tiers = [t for t in settings.TIER_PRICES]

    paying_users = sum(users_by_tier[t] for t in paying_tiers)
    monthly_revenue = sum(
        users_by_tier[tier] * (settings.TIER_PRICES[tier] / 100)
        for tier in paying_tiers
    )
    providers_pool = monthly_revenue * 0.85
    my_estimated_rev = (
        (providers_pool / active_providers_count) if active_providers_count > 0 else 0
    )

    finances_url = f"{settings.APP_URL}/api/v1/public/finances"

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
            "last_24h_hours": pings_last_24h,
            "last_24h_target": 8,
            "month_hours": pings_this_month,
            "month_required_hours": int(required_hours),
            "month_progress_pct": (
                round((pings_this_month / required_hours) * 100, 1)
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
            "paying_total": paying_users,
        },
        "revenue": {
            "platform_monthly_eur": round(monthly_revenue, 2),
            "providers_pool_eur": round(providers_pool, 2),
            "my_estimated_share_eur": round(my_estimated_rev, 2),
            "platform_fee_pct": 15,
            "providers_share_pct": 85,
            "active_providers": active_providers_count,
            "finances_url": finances_url,
        },
        "period": {
            "year": year,
            "month": month,
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
        },
    }
