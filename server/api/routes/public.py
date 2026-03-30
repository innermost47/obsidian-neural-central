from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from server.core.database import get_db, OwnershipLog
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
