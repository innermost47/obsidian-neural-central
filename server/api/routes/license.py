from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
from server.core.limiter import limiter
from server.core.database import get_db, User, BuildVersion, License, LicenseActivation
from server.api.models import LicenseActivateRequest, LicenseReleaseRequest, VstCheckoutRequest, BuildVersionsUpdate
from server.services.license_service import LicenseService, LicenseActivationError
from server.services.stripe_service import StripeService
from server.api.dependencies import get_current_active_user, get_current_user_optional
from server.config import settings

router = APIRouter(prefix="/license", tags=["License"])


@router.post("/activate")
@limiter.limit("10/minute")
def activate_license(request: Request, request_body: LicenseActivateRequest, db: Session = Depends(get_db)):
    try:
        result = LicenseService.activate(db, request_body.key.strip(), request_body.machine_id.strip())
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
@limiter.limit("5/minute")
def create_vst_checkout(request: Request, request_body: VstCheckoutRequest):
    session = StripeService.create_vst_checkout_session(
        buyer_email=request_body.email,
        promo_code=request_body.promo_code,
    )
    return {"checkout_url": session.url}

@router.get("/by-session/{session_id}")
@limiter.limit("10/minute")
def get_license_by_session(request: Request, session_id: str, db: Session = Depends(get_db)):
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

@router.get("/download/check")
@limiter.limit("5/minute")
async def check_local_edition_download(
    request: Request,
    platform: str = Query(...),
    version: str = Query(None),
    session_id: str = Query(None),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    asset_url = await LicenseService.resolve_local_edition_download(
        platform, session_id, current_user, db, version
    )
    return {"url": asset_url}

@router.get("/download")
@limiter.limit("5/minute")
async def download_local_edition(
    request: Request,
    platform: str = Query(...),
    version: str = Query(None),
    session_id: str = Query(None),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    asset_url = await LicenseService.resolve_local_edition_download(
        platform, session_id, current_user, db, version
    )
    return RedirectResponse(url=asset_url, status_code=302)

@router.get("/versions")
@limiter.limit("30/minute")
async def list_versions(
    request: Request,
    platform: str = Query(None),
    include_prereleases: bool = Query(False),
    db: Session = Depends(get_db),
):
    if platform and platform not in LicenseService.PLATFORM_MARKERS:
        raise HTTPException(status_code=400, detail="Invalid platform")

    query = db.query(BuildVersion)
    if platform:
        query = query.filter(BuildVersion.platform == platform)
    current_versions = {row.version for row in query.all()}

    releases = await LicenseService.list_releases()

    versions = []
    for r in releases:
        if r["prerelease"] and not include_prereleases:
            continue
        if platform and platform not in r["platforms"]:
            continue
        versions.append({
            "version": r["version"],
            "build_number": r["build_number"],
            "released_at": r["released_at"],
            "prerelease": r["prerelease"],
            "notes": r["notes"],
            "platforms": sorted(r["platforms"].keys()),
            "is_current": r["version"] in current_versions,
        })

    return {
        "current": next((v for v in versions if v["is_current"]), None),
        "versions": versions,
    }

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
def update_build_versions(
    payload: BuildVersionsUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    expected_token = settings.BUILD_UPDATE_TOKEN
    if not expected_token:
        raise HTTPException(status_code=500, detail="Server misconfigured")
    if authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    valid_platforms = {"windows", "macos", "linux"}
    seen = set()
    for build in payload.builds:
        if build.platform not in valid_platforms:
            raise HTTPException(status_code=400, detail=f"Invalid platform: {build.platform}")
        if build.platform in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate platform: {build.platform}")
        seen.add(build.platform)

    now = datetime.utcnow()

    try:
        for build in payload.builds:
            row = db.query(BuildVersion).filter(BuildVersion.platform == build.platform).first()
            if row:
                row.version = payload.version
                row.asset_name = build.asset_name
                row.released_at = now
                row.updated_at = now
            else:
                db.add(
                    BuildVersion(
                        platform=build.platform,
                        version=payload.version,
                        asset_name=build.asset_name,
                        released_at=now,
                    )
                )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return

@router.get("/count/under-500")
def check_license_count_threshold(db: Session = Depends(get_db)):

    count = db.query(License).filter(License.status == "active").count()
    return {"under_500": count < 500}

@router.get("/count/total")
def check_license_count_threshold(db: Session = Depends(get_db)):

    count = db.query(License).filter(License.status == "active").count()
    return {"total": count}