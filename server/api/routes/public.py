from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from server.core.database import get_db

router = APIRouter(tags=["Generation"], prefix="/public")


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
