from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Header,
    WebSocketDisconnect,
    WebSocket,
)
import time
from sqlalchemy.orm import Session
import hashlib
from datetime import datetime, timezone
from server.core.database import get_db, Provider
from server.core.websocket_manager import manager
from server.services.provider_service import ProviderService

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


@router.websocket("/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    x_provider_key: str = Header(None),
    db: Session = Depends(get_db),
):
    if not x_provider_key:
        await websocket.close(code=4003)
        return

    api_key_hash = hashlib.sha256(x_provider_key.encode()).hexdigest()
    provider = (
        db.query(Provider)
        .filter(Provider.api_key == api_key_hash, Provider.is_banned == False)
        .first()
    )

    if not provider:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket, provider.id)

    provider.last_seen = datetime.now(timezone.utc)
    provider.is_online = True
    db.commit()

    pid = provider.id
    start_time = time.time()

    try:
        while True:
            data = await websocket.receive_text()

    except WebSocketDisconnect:
        duration_minutes = (time.time() - start_time) / 60
        from server.core.database import SessionLocal

        new_db = SessionLocal()
        try:
            p = new_db.query(Provider).filter(Provider.id == pid).first()
            if p:
                p.is_online = False
                p.last_seen = datetime.now(timezone.utc)

            ProviderService._update_daily_stats(new_db, pid, duration_minutes)

            new_db.commit()
            print(f"✅ Session of {duration_minutes:.2f} min recorded for {pid}")
        except Exception as e:
            print(f"❌ Error closing session: {e}")
        finally:
            new_db.close()
            manager.disconnect(pid)
