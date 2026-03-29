from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from server.services.stripe_service import StripeService
from server.config import STRIPE_PRICE_IDS
from server.api.dependencies import get_verified_user
from server.core.database import get_db, User
from sqlalchemy.orm import Session

router = APIRouter()


class CheckoutRequest(BaseModel):
    tier: str


@router.post("/create-checkout")
async def create_checkout(
    request: CheckoutRequest,
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    if request.tier not in STRIPE_PRICE_IDS:
        raise HTTPException(status_code=400, detail="Invalid tier")

    if not current_user.stripe_customer_id:
        customer_id = StripeService.create_customer(current_user.email, current_user.id)
        current_user.stripe_customer_id = customer_id
        db.commit()
    else:
        customer_id = current_user.stripe_customer_id

    price_id = STRIPE_PRICE_IDS[request.tier]
    session = StripeService.create_checkout_session(
        customer_id, price_id, current_user.id, request.tier
    )

    return {"checkout_url": session.url}
