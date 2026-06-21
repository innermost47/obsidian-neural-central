import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from server.core.database import get_db, User
from server.api.models import LicenseActivateRequest, LicenseReleaseRequest, VstCheckoutRequest
from server.services.license_service import LicenseService, LicenseActivationError
from server.services.stripe_service import StripeService
from server.api.dependencies import get_current_active_user
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
    session_id: str = Query(...),
    platform: str = Query(...),
    db: Session = Depends(get_db),
):
    from server.core.database import License

    valid_platforms = {"windows", "macos", "linux"}
    if platform not in valid_platforms:
        raise HTTPException(status_code=400, detail="Invalid platform")

    license_obj = (
        db.query(License)
        .filter(License.stripe_checkout_session_id == session_id)
        .first()
    )

    if not license_obj or license_obj.status != "active":
        raise HTTPException(status_code=403, detail="No valid license for this session")

    asset_url = await _resolve_github_asset(platform)
    if not asset_url:
        raise HTTPException(status_code=404, detail="Build not available for this platform")

    return RedirectResponse(url=asset_url, status_code=302)


async def _resolve_github_asset(platform: str) -> str | None:
    platform_markers = {
        "windows": [".exe", "win", "windows"],
        "macos": [".pkg", "mac", "macos", "osx"], 
        "linux": [".tar.gz", ".tgz", "linux"],     
    }
    markers = platform_markers[platform]

    headers = {
        "Authorization": f"Bearer {settings.GITHUB_RELEASE_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    api_url = f"https://api.github.com/repos/{settings.GITHUB_COMMERCIAL_REPO}/releases/latest"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(api_url, headers=headers)
        if resp.status_code != 200:
            return None

        release = resp.json()
        for asset in release.get("assets", []):
            name = asset.get("name", "").lower()
            if any(marker.lower() in name for marker in markers):
                return await _get_asset_download_url(client, asset, headers)

    return None


async def _get_asset_download_url(client, asset, headers) -> str | None:
    asset_api_url = asset.get("url")
    if not asset_api_url:
        return None

    octet_headers = {**headers, "Accept": "application/octet-stream"}
    resp = await client.get(asset_api_url, headers=octet_headers, follow_redirects=False)

    if resp.status_code in (301, 302, 307):
        return resp.headers.get("location")

    return None