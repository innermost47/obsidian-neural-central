from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Header,
    WebSocketDisconnect,
    WebSocket,
)
import asyncio
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from calendar import monthrange
import hashlib
from server.core.database import get_db, User, Provider, ProviderJob
from server.api.dependencies import get_verified_user
from server.config import settings
from server.core.websocket_manager import manager
from server.services.provider_service import ProviderService

router = APIRouter(prefix="/providers", tags=["Providers"])


class TimeoutError(Exception):
    pass


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
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    uptime_data = ProviderService.calculate_uptime(db, provider, now, month_start)

    days_elapsed = now.day
    required_hours_total = days_elapsed * 8

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
            "is_online": uptime_data["is_online"],
            "month_hours": uptime_data["month_hours"],
            "month_required_hours": required_hours_total,
            "month_progress_pct": (
                round((uptime_data["month_hours"] / required_hours_total) * 100, 1)
                if required_hours_total > 0
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


@router.post("/heartbeat")
async def provider_heartbeat(
    x_api_key: str = Header(...),
    db: Session = Depends(get_db),
):

    api_key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    provider = (
        db.query(Provider)
        .filter(
            Provider.api_key == api_key_hash,
            Provider.is_active == True,
            Provider.is_banned == False,
        )
        .first()
    )

    if not provider:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return {
        "status": "ok",
        "provider_id": provider.id,
        "name": provider.name,
        "last_seen": provider.last_seen.isoformat(),
    }


@router.websocket("/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    x_provider_key: str = Header(None),
    db: Session = Depends(get_db),
):
    if not x_provider_key:
        await websocket.close(code=4003)
        return

    api_key_hash = hashlib.sha256(x_provider_key.encode()).hexdigest()
    provider = (
        db.query(Provider)
        .filter(Provider.api_key == api_key_hash, Provider.is_banned == False)
        .first()
    )

    if not provider:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket, provider.id)

    pid = provider.id
    provider.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
    provider.is_online = True
    db.commit()

    db.close()

    last_flush = datetime.now(timezone.utc)
    FLUSH_INTERVAL = 900

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=FLUSH_INTERVAL
                )

            except asyncio.TimeoutError:
                now = datetime.now(timezone.utc)
                diff = (now - last_flush).total_seconds() / 60

                from server.core.database import SessionLocal

                short_lived_db = SessionLocal()
                try:
                    ProviderService._update_daily_stats(short_lived_db, pid, diff)
                    short_lived_db.commit()
                    print(f"⏳ Auto-flush: {diff:.2f} min recorded for {pid}")
                finally:
                    short_lived_db.close()

                last_flush = now

    except WebSocketDisconnect:
        end_dt = datetime.now(timezone.utc)
        duration_minutes = (end_dt - last_flush).total_seconds() / 60

        from server.core.database import SessionLocal

        new_db = SessionLocal()
        try:
            p = new_db.query(Provider).filter(Provider.id == pid).first()
            if p:
                p.is_online = False
                p.last_seen = end_dt.replace(tzinfo=None)

            ProviderService._update_daily_stats(new_db, pid, duration_minutes)
            new_db.commit()
            print(f"✅ Session of {duration_minutes:.2f} min recorded for {pid}")
        except Exception as e:
            print(f"❌ Error closing session: {e}")
        finally:
            new_db.close()
            manager.disconnect(pid)
