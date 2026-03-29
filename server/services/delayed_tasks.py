import asyncio
from server.services.email_service import EmailService
from server.core.database import SessionLocal
from server.config import settings


async def send_delayed_press_kit(email: str, api_key: str):
    if settings.ENVIRONMENT == "dev":
        delay = 20
    else:
        delay = 300
    await asyncio.sleep(delay)

    db = SessionLocal()
    try:
        EmailService.send_press_welcome(email=email, api_key=api_key, db=db)
        print(f"Press Kit sent to {email}")
    except Exception as e:
        print(f"Delayed sending error: {e}")
    finally:
        db.close()
