from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from server.core.database import get_db, User, BroadcastEmail
from server.api.dependencies import get_verified_user
from server.services.email_service import EmailService
from server.templates.email_template import base_template, section_title
from server.api.models import BroadcastEmailRequest
from datetime import datetime, timezone
import logging

router = APIRouter(prefix="/admin/broadcast", tags=["Admin Broadcast"])
logger = logging.getLogger(__name__)


def get_admin_user(current_user: User = Depends(get_verified_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/recipients-count")
async def get_recipients_count(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    count = (
        db.query(User)
        .filter(
            User.accept_news_updates == True,
            User.email_verified == True,
            User.is_active == True,
            User.is_admin == False,
        )
        .count()
    )
    return {"count": count}


@router.post("/send")
async def send_broadcast_email(
    request: BroadcastEmailRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    if not request.subject.strip():
        raise HTTPException(status_code=400, detail="Subject is required")

    if not request.body.strip():
        raise HTTPException(status_code=400, detail="Body is required")

    recipients = (
        db.query(User)
        .filter(
            User.accept_news_updates == True,
            User.email_verified == True,
            User.is_active == True,
            User.is_admin == False,
        )
        .all()
    )

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients found")

    sent_count = 0
    failed_count = 0

    for user in recipients:
        try:
            unsubscribe_token = user.unsubscribe_token or ""
            html_body = base_template(
                content_html=f"""
                    {section_title("Message from OBSIDIAN Neural")}
                    <div style="color:#4a4a4a;font-size:15px;line-height:1.8;">
                        {request.body}
                    </div>
                """,
                preheader=request.subject,
                unsubscribe_token=unsubscribe_token,
            )

            success = EmailService._send_email(
                to_email=user.email,
                subject=request.subject,
                html_body=html_body,
                email_type="broadcast",
                user_id=user.id,
                db=db,
            )

            if success:
                sent_count += 1
            else:
                failed_count += 1
                logger.warning(f"Failed to send broadcast to {user.email}")

        except Exception as e:
            failed_count += 1
            logger.error(f"Error sending broadcast to {user.email}: {e}")

    broadcast = BroadcastEmail(
        subject=request.subject,
        body=request.body,
        recipients_count=len(recipients),
        sent_count=sent_count,
        failed_count=failed_count,
        sent_by_admin_id=admin.id,
        sent_at=datetime.now(timezone.utc),
    )
    db.add(broadcast)
    db.commit()

    logger.info(f"Broadcast by {admin.email}: {sent_count} sent, {failed_count} failed")

    return {
        "message": "Broadcast email sent",
        "sent_count": sent_count,
        "failed_count": failed_count,
        "total_recipients": len(recipients),
    }


@router.get("/history")
async def get_broadcast_history(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    history = (
        db.query(BroadcastEmail).order_by(desc(BroadcastEmail.sent_at)).limit(50).all()
    )
    return {
        "history": [
            {
                "id": e.id,
                "subject": e.subject,
                "recipients_count": e.recipients_count,
                "sent_count": e.sent_count,
                "failed_count": e.failed_count,
                "sent_at": e.sent_at.isoformat() if e.sent_at else None,
            }
            for e in history
        ]
    }


@router.get("/history/{email_id}")
async def get_broadcast_detail(
    email_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    email = db.query(BroadcastEmail).filter(BroadcastEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    return {
        "email": {
            "id": email.id,
            "subject": email.subject,
            "body": email.body,
            "recipients_count": email.recipients_count,
            "sent_count": email.sent_count,
            "failed_count": email.failed_count,
            "sent_at": email.sent_at.isoformat() if email.sent_at else None,
        }
    }
