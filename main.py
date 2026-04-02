from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging
import asyncio
from server.config import settings
from server.core.database import init_db, SessionLocal
from server.services.provider_ping_service import ProviderPingService
from server.api.routes import (
    auth,
    generation,
    webhooks,
    payments,
    contact,
    admin,
    status,
    gift,
    health,
    admin_broadcast,
    admin_email,
    press,
    admin_providers,
    public,
    unsubscribe,
    provider_stats,
    provider_heartbeat,
)
from server.services.provider_verification_service import ProviderVerificationService


logger = logging.getLogger(__name__)


async def run_provider_ping():
    db = SessionLocal()
    try:
        await ProviderPingService.check_and_ping(db)
    except Exception as e:
        logger.error(f"Provider ping error: {e}")
    finally:
        db.close()


async def run_provider_verification_forever():
    try:
        await ProviderVerificationService.run_forever()
    except Exception as e:
        logger.error(f"Provider verification loop crashed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("✅ Database initialized")

    provider_scheduler = None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        provider_scheduler = AsyncIOScheduler()
        provider_scheduler.add_job(
            run_provider_ping,
            trigger="interval",
            hours=1,
            id="provider_ping",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
        )

        provider_scheduler._executors.default.executor.submit = lambda *a, **kw: None

        provider_scheduler.start()

        asyncio.create_task(run_provider_verification_forever())

        logger.info("✅ Provider verification loop started (random interval 1h–5h)")
        logger.info(
            "✅ Provider ping scheduler started (every hour, 60% random probability)"
        )
    except Exception as e:
        logger.error(f"Failed to start provider ping scheduler: {e}")

    yield

    try:
        if provider_scheduler:
            provider_scheduler.shutdown()
            logger.info("🛑 Provider ping scheduler stopped")
    except Exception as e:
        logger.error(f"Error stopping provider ping scheduler: {e}")


app = FastAPI(
    title="Obsidian Neural API",
    description="API for the Obsidian Neural VST",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(generation.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
app.include_router(contact.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(webhooks.legacy_router, prefix="/api/v1")
app.include_router(status.router, prefix="/api/v1")
app.include_router(gift.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(admin_broadcast.router, prefix="/api/v1")
app.include_router(admin_email.router, prefix="/api/v1")
app.include_router(admin_providers.router, prefix="/api/v1")
app.include_router(public.router, prefix="/api/v1")
app.include_router(unsubscribe.router, prefix="/api/v1")
app.include_router(provider_stats.router, prefix="/api/v1")
app.include_router(provider_heartbeat.router, prefix="/api/v1")
app.include_router(press.router)


@app.get("/")
def root():
    return {
        "name": "Obsidian Neural API",
        "version": "2.0.0",
        "status": "operational",
        "provider_ping_scheduler": "active",
        "email_tasks": "cron — see cron_daily.py",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
    )
