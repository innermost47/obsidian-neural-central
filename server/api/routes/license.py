from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
from server.core.limiter import limiter
from server.core.database import get_db, User, BuildVersion, License
from server.api.models import LicenseActivateRequest, LicenseReleaseRequest, VstCheckoutRequest, BuildVersionUpdate
from server.services.license_service import LicenseService, LicenseActivationError
from server.services.stripe_service import StripeService
from server.api.dependencies import get_current_active_user, get_current_user_optional
from server.config import settings

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
    session = StripeService.create_vst_checkout_session(
        buyer_email=request.email,
        promo_code=request.promo_code,
    )
    return {"checkout_url": session.url}

@router.get("/by-session/{session_id}")
def get_license_by_session(session_id: str, db: Session = Depends(get_db)):
    license_obj = (
        db.query(License)
        .filter(License.stripe_checkout_session_id == session_id)
        .first()
    )

    if not license_obj:
        return {"ready": False}

    if license_obj.key_retrieved_at is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")

    license_obj.key_retrieved_at = datetime.utcnow()
    db.commit()

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
@limiter.limit("5/minute")
async def download_local_edition(
    request: Request,  
    platform: str = Query(...),
    session_id: str = Query(None),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
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
        try:
            build_number = int(row.version)
        except (TypeError, ValueError):
            build_number = None
        return {
            "platform": row.platform,
            "latest_build_number": build_number,
            "asset_name": row.asset_name,
        }

    if platform:
        return {"available": True, **serialize(rows[0])}
    return {"available": True, "platforms": [serialize(r) for r in rows]}

@router.post("/version/update", status_code=204)
def update_build_version(
    payload: BuildVersionUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    expected_token = settings.BUILD_UPDATE_TOKEN
    if not expected_token:
        raise HTTPException(status_code=500, detail="Server misconfigured")
    if authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    valid_platforms = {"windows", "macos", "linux"}
    if payload.platform not in valid_platforms:
        raise HTTPException(status_code=400, detail="Invalid platform")

    row = db.query(BuildVersion).filter(BuildVersion.platform == payload.platform).first()
    now = datetime.utcnow()

    if row:
        row.version = payload.version
        row.asset_name = payload.asset_name
        row.released_at = now
        row.updated_at = now
    else:
        row = BuildVersion(
            platform=payload.platform,
            version=payload.version,
            asset_name=payload.asset_name,
            released_at=now,
        )
        db.add(row)

    db.commit()
    return

@router.get("/count/under-500")
def check_license_count_threshold(db: Session = Depends(get_db)):

    count = db.query(License).filter(License.status == "active").count()
    return {"under_500": count < 500}