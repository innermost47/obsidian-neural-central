from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from server.core.database import get_db
from server.api.models import LicenseActivateRequest, LicenseReleaseRequest, VstCheckoutRequest
from server.services.license_service import LicenseService, LicenseActivationError
from server.services.stripe_service import StripeService

router = APIRouter(prefix="/license", tags=["License"])


@router.post("/activate")
def activate_license(
    request: LicenseActivateRequest,
    db: Session = Depends(get_db),
):
    try:
        result = LicenseService.activate(db, request.key.strip(), request.machine_id.strip())
    except LicenseActivationError as e:
        return {"success": False, "error": e.message}

    return {
        "success": True,
        "blob": result["blob"],
        "signature": result["signature"],
    }

@router.post("/release")
def release_license(
    request: LicenseReleaseRequest,
    db: Session = Depends(get_db),
):
    released = LicenseService.release(db, request.key.strip(), request.machine_id.strip())
    return {"success": released}

@router.post("/checkout")
def create_vst_checkout(request: VstCheckoutRequest):
    session = StripeService.create_vst_checkout_session(buyer_email=request.email)
    return {"checkout_url": session.url}