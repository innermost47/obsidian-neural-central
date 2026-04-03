from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from server.core.database import get_db, OwnershipLog, FinanceReport
from sqlalchemy import desc

router = APIRouter(tags=["Generation", "Public"], prefix="/public")


@router.get("/stats")
async def public_stats(db: Session = Depends(get_db)):
    from server.core.database import User

    paying_users = (
        db.query(User)
        .filter(
            User.subscription_status == "active",
            User.subscription_tier != "none",
            User.is_active == True,
            User.is_admin == False,
        )
        .count()
    )
    return {
        "paying_users": paying_users,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/network")
def get_network_status(db: Session = Depends(get_db)):
    from server.core.database import Provider

    providers = (
        db.query(Provider)
        .filter(Provider.is_active == True, Provider.is_banned == False)
        .order_by(desc(Provider.jobs_done))
        .all()
    )

    return {
        "total": len(providers),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "providers": [
            {
                "name": p.name,
                "is_online": p.is_online,
                "is_trusted": p.is_trusted,
                "jobs_done": p.jobs_done,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
            }
            for p in providers
        ],
    }


@router.get("/ownership")
def get_ownership_logs(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, le=200),
    public_user_id: Optional[str] = Query(default=None),
):
    query = db.query(OwnershipLog)
    if public_user_id:
        query = query.filter(OwnershipLog.public_user_id == public_user_id)
    total = query.count()
    logs = (
        query.order_by(desc(OwnershipLog.generated_at))
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "results": [
            {
                "public_user_id": log.public_user_id,
                "provider": log.provider_name,
                "prompt_hash": log.prompt_hash,
                "duration": log.duration,
                "generated_at": log.generated_at.isoformat(),
                "audio_content_hash": log.audio_content_hash,
            }
            for log in logs
        ],
    }


@router.get("/finances")
def get_finance_reports(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=24, le=100),
    month: Optional[str] = Query(
        default=None, description="Filter by month, e.g. 2026-03"
    ),
):
    query = db.query(FinanceReport).order_by(desc(FinanceReport.month))
    if month:
        query = query.filter(FinanceReport.month == month)
    total = query.count()
    reports = query.offset((page - 1) * limit).limit(limit).all()
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "reports": [
            {
                "month": r.month,
                "total_revenue_eur": r.total_revenue_eur,
                "platform_fee_pct": r.platform_fee_pct,
                "platform_fee_eur": r.platform_fee_eur,
                "distributable_eur": r.distributable_eur,
                "eligible_providers": r.eligible_providers,
                "share_per_provider_eur": r.share_per_provider_eur,
                "remainder_eur": r.remainder_eur,
                "transfers": r.transfers,
                "published_at": r.published_at.isoformat(),
            }
            for r in reports
        ],
    }
