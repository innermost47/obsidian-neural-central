from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
import hashlib
from datetime import datetime, timezone
from server.core.database import get_db, Provider


router = APIRouter(prefix="/providers", tags=["Providers"])


@router.post("/heartbeat")
async def provider_heartbeat(
    x_api_key: str = Header(...),
    db: Session = Depends(get_db),
):

    api_key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    provider = (
        db.query(Provider)
        .filter(
            Provider.api_key == api_key_hash,
            Provider.is_active == True,
            Provider.is_banned == False,
        )
        .first()
    )

    if not provider:
        raise HTTPException(status_code=401, detail="Invalid API key")

    provider.last_seen = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "ok",
        "provider_id": provider.id,
        "name": provider.name,
        "last_seen": provider.last_seen.isoformat(),
    }
