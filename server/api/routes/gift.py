from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from server.api.models import GiftActivationResponse, GiftPurchaseRequest
from sqlalchemy.orm import Session
from server.services.stripe_service import StripeService
from server.core.database import get_db, User, GiftSubscription, GiftSubscriptionStatus
from server.api.dependencies import get_verified_user
from dateutil.relativedelta import relativedelta

router = APIRouter(prefix="/gifts", tags=["Gifts"])


@router.post("/purchase")
async def purchase_gift(
    request: GiftPurchaseRequest,
    db: Session = Depends(get_db),
):

    recipient = (
        db.query(User).filter(User.email == request.recipient_email.lower()).first()
    )

    if (
        recipient
        and recipient.subscription_status == "active"
        and recipient.subscription_tier not in ["none", "free"]
    ):
        raise HTTPException(
            status_code=400,
            detail=f"{request.recipient_email} already has an active subscription. Gift purchases are only available for users without an active subscription.",
        )
    if request.tier not in ["starter", "pro", "studio"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid tier. Must be 'starter', 'pro', or 'studio'",
        )

    if request.duration_months not in [1, 3, 6]:
        raise HTTPException(
            status_code=400, detail="Invalid duration. Must be 1, 3, or 6 months"
        )

    gift_code = StripeService.generate_gift_code()

    activation_date = request.activation_date or datetime.utcnow()

    try:
        purchaser_email = request.recipient_email

        session = StripeService.create_gift_checkout_session(
            purchaser_email=purchaser_email,
            purchaser_user_id=0,
            recipient_email=request.recipient_email,
            tier=request.tier,
            duration_months=request.duration_months,
            gift_code=gift_code,
            gift_message=request.gift_message,
            activation_date=activation_date,
        )

        return {
            "checkout_url": session.url,
            "gift_code": gift_code,
            "session_id": session.id,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create checkout session: {str(e)}"
        )


@router.get("/check/{gift_code}")
async def check_gift(
    gift_code: str,
    db: Session = Depends(get_db),
):
    gift = (
        db.query(GiftSubscription)
        .filter(GiftSubscription.gift_code == gift_code)
        .first()
    )

    if not gift:
        raise HTTPException(status_code=404, detail="Gift code not found")

    if gift.status == GiftSubscriptionStatus.ACTIVE:
        raise HTTPException(
            status_code=400, detail="This gift has already been activated"
        )

    if gift.status == GiftSubscriptionStatus.EXPIRED:
        raise HTTPException(status_code=400, detail="This gift has expired")

    if gift.status == GiftSubscriptionStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="This gift has been cancelled")

    return {
        "valid": True,
        "tier": gift.tier,
        "duration_months": gift.duration_months,
        "purchaser_name": gift.purchaser_name or "Someone",
        "gift_message": gift.gift_message,
        "activation_date": gift.activation_date,
        "recipient_email": gift.recipient_email,
    }


@router.post("/activate/{gift_code}")
async def activate_gift(
    gift_code: str,
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):

    gift = (
        db.query(GiftSubscription)
        .filter(
            GiftSubscription.gift_code == gift_code,
            GiftSubscription.status == GiftSubscriptionStatus.PENDING,
        )
        .first()
    )

    if not gift:
        raise HTTPException(
            status_code=404, detail="Gift not found or already activated"
        )

    if gift.recipient_email.lower() != current_user.email.lower():
        raise HTTPException(
            status_code=403,
            detail="This gift is not for you. Please check the email address.",
        )

    if (
        current_user.subscription_status == "active"
        and current_user.subscription_tier not in ["none", "free"]
    ):
        raise HTTPException(
            status_code=400,
            detail="You already have an active subscription. Please cancel it first or contact support.",
        )

    expires_at = gift.activation_date + relativedelta(months=gift.duration_months)

    gift.recipient_user_id = current_user.id
    gift.activated_at = datetime.utcnow()
    gift.expires_at = expires_at
    gift.status = GiftSubscriptionStatus.ACTIVE

    current_user.subscription_tier = gift.tier
    current_user.subscription_status = "active"
    current_user.active_gift_subscription_id = gift.id

    from server.services.credits_service import CreditsService

    CreditsService.refill_credits(db, current_user.id, gift.tier)

    db.commit()

    return GiftActivationResponse(
        message="Gift activated successfully!",
        tier=gift.tier,
        expires_at=expires_at,
        credits_granted=StripeService.TIER_CREDITS[gift.tier],
    )
