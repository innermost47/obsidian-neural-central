from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from server.core.database import get_db, User, PressRequest
from server.services.admin_notification_service import AdminNotificationService
from server.core.security import (
    get_password_hash,
    generate_api_key,
    encrypt_api_key,
)
from server.services.delayed_tasks import send_delayed_press_kit
from server.config import settings
import secrets


router = APIRouter(prefix="/press", tags=["Authentication"])


@router.get("/confirm-press")
async def confirm_press_request(
    token: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    request = (
        db.query(PressRequest)
        .filter(
            PressRequest.token == token,
            PressRequest.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )

    if not request:
        raise HTTPException(status_code=404, detail="Invalid or expired token")

    existing_user = db.query(User).filter(User.email == request.email).first()

    if not existing_user:
        raw_api_key = generate_api_key()
        new_user = User(
            email=request.email,
            hashed_password=get_password_hash(secrets.token_urlsafe(16)),
            api_key=encrypt_api_key(raw_api_key),
            credits_total=request.payload.get("credits", 200),
            email_verified=True,
            is_active=True,
            accept_news_updates=False,
            subscription_tier=request.payload.get("tier", "press_vip"),
        )
        db.add(new_user)
        db.commit()

        background_tasks.add_task(
            send_delayed_press_kit,
            email=new_user.email,
            api_key=raw_api_key,
        )

        background_tasks.add_task(
            AdminNotificationService.notify_admin_press_activation,
            journalist_email=new_user.email,
        )

    db.delete(request)
    db.commit()

    return RedirectResponse(url=f"{settings.FRONTEND_URL}/press-success.html")
