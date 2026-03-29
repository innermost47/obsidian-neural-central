from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy.orm import Session
from server.api.models import ContactRequest
from server.services.email_service import EmailService
from server.config import settings
from server.core.database import get_db
import time
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["Contact"])


@router.post("")
async def send_contact_message(
    request: ContactRequest,
    req: Request,
    db: Session = Depends(get_db),
):
    if request.website or request.email_confirm or request.phone:
        print(f"🚫 Bot detected from {req.client.host}: honeypot filled")
        return {"status": "success", "message": "Message received"}

    if request.timestamp:
        try:
            submitted_time = int(request.timestamp)
            current_time = int(time.time() * 1000)
            time_diff = current_time - submitted_time

            if time_diff < 3000:
                print(
                    f"🚫 Bot detected from {req.client.host}: too fast ({time_diff}ms)"
                )
                return {"status": "success", "message": "Message received"}
        except (ValueError, TypeError):
            pass

    spam_keywords = [
        "viagra",
        "casino",
        "lottery",
        "prize",
        "bitcoin",
        "crypto",
        "investment",
    ]
    message_lower = request.message.lower()

    if any(keyword in message_lower for keyword in spam_keywords):
        print(f"🚫 Spam detected from {req.client.host}")
        return {"status": "success", "message": "Message received"}

    try:
        admin_email_sent = EmailService.send_contact_notification(
            admin_email=settings.SMTP_TO_EMAIL,
            name=request.name,
            email=request.email,
            subject=request.subject,
            message=request.message,
            ip=req.client.host,
            db=db,
        )

        if not admin_email_sent:
            print("Failed to send admin notification")
            raise HTTPException(status_code=500, detail="Failed to send notification")

        user_email_sent = EmailService.send_contact_confirmation(
            email=request.email,
            name=request.name,
            subject=request.subject,
            message=request.message,
            db=db,
        )

        if not user_email_sent:
            print(f"Failed to send confirmation email to {request.email}")

        print(f"✅ Contact message sent from {request.email} ({req.client.host})")

        return {
            "status": "success",
            "message": "Your message has been sent successfully!",
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error sending contact message: {str(e)}")
        raise HTTPException(
            status_code=500, detail="An error occurred while sending the message"
        )
