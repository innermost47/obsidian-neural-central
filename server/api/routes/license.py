from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from server.core.database import get_db, User, BuildVersion
from server.api.models import LicenseActivateRequest, LicenseReleaseRequest, VstCheckoutRequest
from server.services.license_service import LicenseService, LicenseActivationError
from server.services.stripe_service import StripeService
from server.api.dependencies import get_current_active_user, get_current_user_optional

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

@router.get("/by-session/{session_id}")
def get_license_by_session(session_id: str, db: Session = Depends(get_db)):
    from server.core.database import License

    license_obj = (
        db.query(License)
        .filter(License.stripe_checkout_session_id == session_id)
        .first()
    )

    if not license_obj:
        return {"ready": False}

    return {
        "ready": True,
        "license_key": license_obj.license_key,
        "email": license_obj.email,
        "max_activations": license_obj.max_activations,
    }


@router.delete("/{license_key}/machine/{machine_id}")
def release_machine_authenticated(
    license_key: str,
    machine_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    from server.core.database import License, LicenseActivation

    license_obj = (
        db.query(License)
        .filter(License.license_key == license_key)
        .first()
    )

    if not license_obj or license_obj.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="License not found")

    activation = (
        db.query(LicenseActivation)
        .filter(
            LicenseActivation.license_id == license_obj.id,
            LicenseActivation.machine_id == machine_id,
        )
        .first()
    )

    if not activation:
        raise HTTPException(status_code=404, detail="Machine not found")

    db.delete(activation)
    db.commit()
    return {"success": True}


@router.get("/download")
async def download_local_edition(
    platform: str = Query(...),
    session_id: str = Query(None),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    from server.core.database import License

    valid_platforms = {"windows", "macos", "linux"}
    if platform not in valid_platforms:
        raise HTTPException(status_code=400, detail="Invalid platform")

    license_obj = None
    if session_id:
        license_obj = (
            db.query(License)
            .filter(License.stripe_checkout_session_id == session_id)
            .first()
        )
    elif current_user:
        license_obj = (
            db.query(License)
            .filter(License.user_id == current_user.id, License.status == "active")
            .first()
        )

    if not license_obj or license_obj.status != "active":
        raise HTTPException(status_code=403, detail="No valid license found")

    asset_url, release, asset = await LicenseService.resolve_github_asset(platform)
    if not asset_url:
        raise HTTPException(status_code=404, detail="Build not available for this platform")
    try:
        LicenseService.upsert_build_version(db, platform, release, asset)
    except Exception:
        pass

    return RedirectResponse(url=asset_url, status_code=302)


@router.get("/version/latest")
def get_latest_version(
    platform: str = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(BuildVersion)
    if platform:
        valid_platforms = {"windows", "macos", "linux"}
        if platform not in valid_platforms:
            raise HTTPException(status_code=400, detail="Invalid platform")
        query = query.filter(BuildVersion.platform == platform)

    rows = query.all()
    if not rows:
        return {"available": False}

    def serialize(row):
        return {
            "platform": row.platform,
            "released_at": row.released_at.isoformat() if row.released_at else None,
            "asset_name": row.asset_name,
        }

    if platform:
        return {"available": True, **serialize(rows[0])}

    return {"available": True, "platforms": [serialize(r) for r in rows]}


