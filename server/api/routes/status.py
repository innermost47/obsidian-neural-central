from fastapi import APIRouter
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["Status"])


@router.get("/services")
async def check_services_status():
    raise HTTPException(
        status_code=503,
        detail="Status monitoring temporarily disabled for optimization",
    )
