import traceback
import os
from fastapi.security import APIKeyHeader
from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.orm import Session
from sqlalchemy import func
from server.core.database import get_db, User, Generation
from server.services.google_analytics_service import google_analytics_service
from server.api.dependencies import get_verified_user
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/admin", tags=["Admin"])


def get_admin_user(current_user: User = Depends(get_verified_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


def get_internal_key(key: str = Security(API_KEY_HEADER)):
    expected = os.getenv("INTERNAL_API_KEY")
    if not key or key != expected:
        raise HTTPException(status_code=403, detail="Invalid internal key")
    return key


@router.get("/users/check-email")
async def check_email_exists(
    email: str, db: Session = Depends(get_db), _: str = Depends(get_internal_key)
):
    user = db.query(User).filter(User.email == email).first()
    return {"exists": user is not None}


@router.get("/users")
async def get_all_users(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    users = db.query(User).order_by(User.created_at.desc()).all()

    user_list = []
    for user in users:
        total_generations = (
            db.query(Generation).filter(Generation.user_id == user.id).count()
        )

        last_generation = (
            db.query(Generation)
            .filter(Generation.user_id == user.id)
            .order_by(Generation.created_at.desc())
            .first()
        )

        user_data = {
            "id": user.id,
            "email": user.email,
            "subscription_tier": user.subscription_tier,
            "subscription_status": user.subscription_status,
            "credits_total": user.credits_total,
            "credits_used": user.credits_used,
            "credits_remaining": user.credits_total - user.credits_used,
            "email_verified": user.email_verified,
            "two_factor_enabled": user.two_factor_enabled,
            "oauth_provider": user.oauth_provider,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "stripe_customer_id": user.stripe_customer_id,
            "total_generations": total_generations,
            "last_generation": (
                last_generation.created_at.isoformat() if last_generation else None
            ),
            "accept_news_updates": user.accept_news_updates,
        }
        user_list.append(user_data)

    return {"users": user_list, "total": len(user_list)}


@router.get("/stats")
async def get_admin_stats(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    total_users = db.query(User).filter(User.is_admin == False).count()
    verified_users = (
        db.query(User)
        .filter(User.email_verified == True, User.is_admin == False)
        .count()
    )
    paid_users = (
        db.query(User)
        .filter(User.subscription_tier.notin_(["none", "free"]), User.is_admin == False)
        .count()
    )

    total_generations = (
        db.query(Generation)
        .join(User, Generation.user_id == User.id)
        .filter(User.is_admin == False)
        .count()
    )
    total_credits_used = (
        db.query(func.sum(User.credits_used)).filter(User.is_admin == False).scalar()
        or 0
    )

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    active_users = (
        db.query(User)
        .filter(User.last_login >= thirty_days_ago, User.is_admin == False)
        .count()
    )

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_users = (
        db.query(User)
        .filter(User.created_at >= seven_days_ago, User.is_admin == False)
        .count()
    )

    tier_stats = {}
    for tier in ["free", "starter", "pro", "studio"]:
        count = (
            db.query(User)
            .filter(User.subscription_tier == tier, User.is_admin == False)
            .count()
        )
        tier_stats[tier] = count

    return {
        "total_users": total_users,
        "verified_users": verified_users,
        "paid_users": paid_users,
        "active_users_30d": active_users,
        "new_users_7d": new_users,
        "total_generations": total_generations,
        "total_credits_used": total_credits_used,
        "tier_distribution": tier_stats,
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    recent_generations = (
        db.query(Generation)
        .filter(Generation.user_id == user_id)
        .order_by(Generation.created_at.desc())
        .limit(10)
        .all()
    )

    generations_list = [
        {
            "id": gen.id,
            "prompt": gen.prompt,
            "bpm": gen.bpm,
            "duration": gen.duration,
            "credits_cost": gen.credits_cost,
            "status": gen.status,
            "created_at": gen.created_at.isoformat() if gen.created_at else None,
        }
        for gen in recent_generations
    ]

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "subscription_tier": user.subscription_tier,
            "subscription_status": user.subscription_status,
            "credits_total": user.credits_total,
            "credits_used": user.credits_used,
            "credits_remaining": user.credits_total - user.credits_used,
            "email_verified": user.email_verified,
            "two_factor_enabled": user.two_factor_enabled,
            "oauth_provider": user.oauth_provider,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "stripe_customer_id": user.stripe_customer_id,
            "stripe_subscription_id": user.stripe_subscription_id,
        },
        "recent_generations": generations_list,
        "total_generations": db.query(Generation)
        .filter(Generation.user_id == user_id)
        .count(),
    }


@router.patch("/users/{user_id}/credits")
async def update_user_credits(
    user_id: int,
    credits: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.credits_total = credits
    db.commit()

    return {
        "message": f"Credits updated for {user.email}",
        "new_total": credits,
        "remaining": credits - user.credits_used,
    }


@router.patch("/users/{user_id}/toggle-active")
async def toggle_user_active(
    user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_admin and user.id != admin.id:
        raise HTTPException(status_code=403, detail="Cannot disable other admins")

    user.is_active = not user.is_active
    db.commit()

    return {
        "message": f"User {user.email} {'activated' if user.is_active else 'deactivated'}",
        "is_active": user.is_active,
    }


@router.get("/analytics/overview")
async def get_analytics_overview(
    days: int = 30,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_overview_stats(days)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/daily")
async def get_analytics_daily(
    days: int = 30,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_daily_stats(days)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")


@router.get("/analytics/pages")
async def get_top_pages(
    days: int = 30,
    limit: int = 10,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_top_pages(days, limit)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")


@router.get("/analytics/sources")
async def get_traffic_sources(
    days: int = 30,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_traffic_sources(days)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")


@router.get("/analytics/devices")
async def get_device_breakdown(
    days: int = 30,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_device_breakdown(days)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")


@router.get("/analytics/countries")
async def get_countries(
    days: int = 30,
    limit: int = 10,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_countries(days, limit)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")


@router.get("/analytics/funnel")
async def get_conversion_funnel(
    days: int = 30,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_conversion_funnel(days)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")


@router.get("/analytics/social")
async def get_social_referrals(
    days: int = 30,
    _: User = Depends(get_admin_user),
):
    try:
        return google_analytics_service.get_social_referrals(days)
    except Exception as e:
        print("ERROR:", str(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")
