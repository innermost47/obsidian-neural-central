from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from calendar import monthrange

from server.core.database import get_db, User, Provider, Generation
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

    global_generations_month = (
        db.query(func.count(Generation.id))
        .filter(
            func.extract("year", Generation.created_at) == year,
            func.extract("month", Generation.created_at) == month,
        )
        .scalar()
        or 0
    )

    my_generations_month = (
        db.query(func.count(Generation.id))
        .filter(
            Generation.user_id == provider.user.id,
            func.extract("year", Generation.created_at) == year,
            func.extract("month", Generation.created_at) == month,
        )
        .scalar()
        or 0
    )

    my_generations_total = (
        db.query(func.count(Generation.id))
        .filter(Generation.user_id == provider.user.id)
        .scalar()
        or 0
    )

    active_providers_count = (
        db.query(func.count(Provider.id))
        .filter(Provider.is_active == True, Provider.is_banned == False)
        .scalar()
        or 1
    )

    tiers = ["free", "starter", "pro", "studio"]
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

    paying_users = sum(users_by_tier[t] for t in ["starter", "pro", "studio"])

    monthly_revenue = sum(
        users_by_tier[tier] * (settings.TIER_PRICES.get(tier, 0) / 100)
        for tier in ["starter", "pro", "studio"]
    )
    providers_pool = monthly_revenue * 0.85
    my_estimated_rev = (
        (providers_pool / active_providers_count) if active_providers_count > 0 else 0
    )

    finances_url = f"{settings.APP_URL}/public/finances.json"

    return {
        "provider": {
            "id": provider.id,
            "name": provider.name,
            "is_active": provider.is_active,
            "uptime_score": round((provider.uptime_score or 0) * 100, 1),
            "jobs_done": provider.jobs_done or 0,
            "billable_jobs": provider.billable_jobs or 0,
            "last_seen": provider.last_seen.isoformat() if provider.last_seen else None,
        },
        "my_generations": {
            "this_month": my_generations_month,
            "all_time": my_generations_total,
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
