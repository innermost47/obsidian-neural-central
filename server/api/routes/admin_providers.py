from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
import hashlib
import stripe
from datetime import datetime, timezone
from server.config import settings
from server.core.database import get_db, User, Provider, ProviderJob
from server.api.dependencies import get_verified_user
from server.services.provider_service import ProviderService
from server.services.provider_ping_service import ProviderPingService
from server.api.models import (
    AddProviderRequest,
    UpdateProviderRequest,
    BanProviderRequest,
)
import logging
from server.core.security import encrypt_server_key

router = APIRouter(prefix="/admin/providers", tags=["Admin Providers"])
logger = logging.getLogger(__name__)


def get_admin_user(current_user: User = Depends(get_verified_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/pool/status")
async def pool_status(db: Session = Depends(get_db)):
    return ProviderService.get_pool_status(db)


@router.get("/")
async def list_providers(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    from server.core.database import ProviderDailyStats

    providers = db.query(Provider).order_by(desc(Provider.created_at)).all()
    provider_ids = [p.id for p in providers]

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    all_stats = (
        db.query(ProviderDailyStats)
        .filter(
            ProviderDailyStats.provider_id.in_(provider_ids),
            ProviderDailyStats.date >= month_start.date(),
        )
        .all()
    )
    stats_by_provider = {}
    for s in all_stats:
        stats_by_provider.setdefault(s.provider_id, []).append(s)

    jobs_this_month = (
        db.query(ProviderJob.provider_id, func.count(ProviderJob.id))
        .filter(
            ProviderJob.provider_id.in_(provider_ids),
            ProviderJob.status == "done",
            ProviderJob.used_fallback == False,
            func.extract("year", ProviderJob.created_at) == year,
            func.extract("month", ProviderJob.created_at) == month,
        )
        .group_by(ProviderJob.provider_id)
        .all()
    )
    jobs_by_provider = {provider_id: count for provider_id, count in jobs_this_month}

    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "url": p.url,
                "stripe_account_id": p.stripe_account_id,
                "is_online": getattr(p, "is_online", False),
                "is_active": p.is_active,
                "is_banned": p.is_banned,
                "ban_reason": p.ban_reason,
                "jobs_done": p.jobs_done,
                "jobs_done_this_month": jobs_by_provider.get(p.id, 0),
                "jobs_failed": p.jobs_failed,
                "billable_jobs": p.billable_jobs,
                "uptime": ProviderService.calculate_uptime(
                    db,
                    p,
                    now,
                    month_start,
                    preloaded_stats=stats_by_provider.get(p.id, []),
                ),
                "user_email": p.user.email if p.user else None,
                "last_ping": p.last_ping.isoformat() if p.last_ping else None,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in providers
        ]
    }


@router.post("/")
async def add_provider(
    request: AddProviderRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    existing = db.query(Provider).filter(Provider.url == request.url).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A provider with URL {request.url} already exists",
        )

    result = ProviderService.add_provider(
        db=db,
        name=request.name,
        url=request.url,
        stripe_account_id=request.stripe_account_id,
        user_id=request.user_id or None,
    )

    logger.info(f"Provider added: {request.name} ({request.url})")

    return {
        "message": "Provider added successfully",
        "provider": result,
        "warning": "Save the api_key now — it will never be displayed again",
    }


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    uptime_data = ProviderService.calculate_uptime(db, provider, now, month_start)

    recent_jobs = (
        db.query(ProviderJob)
        .filter(ProviderJob.provider_id == provider_id)
        .order_by(desc(ProviderJob.created_at))
        .limit(10)
        .all()
    )

    return {
        "provider": {
            "id": provider.id,
            "name": provider.name,
            "url": provider.url,
            "stripe_account_id": provider.stripe_account_id,
            "is_active": provider.is_active,
            "is_banned": provider.is_banned,
            "ban_reason": provider.ban_reason,
            "jobs_done": provider.jobs_done,
            "jobs_failed": provider.jobs_failed,
            "billable_jobs": provider.billable_jobs,
            "user_email": provider.user.email if provider.user else None,
            "uptime_score": provider.uptime_score,
            "uptime": uptime_data,
            "last_ping": provider.last_ping.isoformat() if provider.last_ping else None,
            "last_seen": provider.last_seen.isoformat() if provider.last_seen else None,
            "created_at": (
                provider.created_at.isoformat() if provider.created_at else None
            ),
        },
        "recent_jobs": [
            {
                "id": j.id,
                "status": j.status,
                "used_fallback": j.used_fallback,
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in recent_jobs
        ],
    }


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    request: UpdateProviderRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if request.name is not None:
        provider.name = request.name
    if request.url is not None:
        provider.url = request.url
    if request.stripe_account_id is not None:
        provider.stripe_account_id = request.stripe_account_id
    if request.is_active is not None:
        provider.is_active = request.is_active
    if request.user_id is not None:
        provider.user_id = request.user_id

    db.commit()

    return {"message": "Provider updated", "provider_id": provider_id}


@router.post("/{provider_id}/activate")
async def activate_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.is_banned:
        raise HTTPException(
            status_code=400,
            detail="Cannot activate a banned provider — unban it first",
        )

    provider.is_active = True
    db.commit()

    return {"message": f"Provider {provider.name} activated"}


@router.post("/{provider_id}/deactivate")
async def deactivate_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider.is_active = False
    db.commit()

    return {"message": f"Provider {provider.name} deactivated"}


@router.post("/{provider_id}/ban")
async def ban_provider(
    provider_id: int,
    request: BanProviderRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.is_banned:
        raise HTTPException(status_code=400, detail="Provider is already banned")

    ProviderService._ban_provider(db, provider_id, request.reason)

    return {"message": f"Provider {provider.name} banned", "reason": request.reason}


@router.post("/{provider_id}/unban")
async def unban_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not provider.is_banned:
        raise HTTPException(status_code=400, detail="Provider is not banned")

    provider.is_banned = False
    provider.ban_reason = None
    provider.is_active = False
    db.commit()

    return {
        "message": f"Provider {provider.name} unbanned — activate it manually to re-add to pool",
    }


@router.post("/{provider_id}/regenerate-key")
async def regenerate_api_key(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    new_key = ProviderService.generate_api_key()
    api_key_hash = hashlib.sha256(new_key.encode()).hexdigest()

    provider.api_key = api_key_hash
    db.commit()

    return {
        "message": f"API key regenerated for {provider.name}",
        "api_key": new_key,
        "warning": "Save this key now — it will never be displayed again",
    }


@router.post("/{provider_id}/regenerate-server-key")
async def regenerate_server_auth_key(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    new_raw_server_key = ProviderService.generate_api_key()

    encrypted_key = encrypt_server_key(new_raw_server_key)

    provider.encoded_server_auth_key = encrypted_key

    db.commit()

    return {
        "status": "success",
        "provider_name": provider.name,
        "new_server_auth_key": new_raw_server_key,
        "action_required": "L'admin du provider doit mettre à jour son header 'X-API-Key' avec cette valeur.",
        "note": "Cette clé est stockée de manière chiffrée en base de données.",
    }


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    db.delete(provider)
    db.commit()

    return {"message": f"Provider {provider.name} deleted"}


@router.get("/{provider_id}/ping-stats")
async def get_provider_ping_stats(
    provider_id: int,
    days: int = 30,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    return ProviderPingService.get_ping_stats(db, provider_id, days)


@router.post("/{provider_id}/onboarding-link")
async def create_onboarding_link(
    provider_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):

    stripe.api_key = settings.STRIPE_SECRET_KEY

    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not provider.stripe_account_id or provider.stripe_account_id.strip() == "":
        account = stripe.Account.create(
            type="express",
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
        )
        provider.stripe_account_id = account.id
        db.commit()

    link = stripe.AccountLink.create(
        account=provider.stripe_account_id,
        refresh_url=f"{settings.FRONTEND_URL}/admin/providers",
        return_url=f"{settings.FRONTEND_URL}/admin/providers",
        type="account_onboarding",
    )

    return {"onboarding_url": link.url}
