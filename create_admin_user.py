import os
import sys
import secrets
import uuid
from dotenv import load_dotenv

env_file = ".env.dev"

load_dotenv(env_file)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")

if not ADMIN_EMAIL or not ADMIN_PASSWORD or not DATABASE_URL:
    print(
        "❌ Missing required .env variables: ADMIN_EMAIL, ADMIN_PASSWORD, DATABASE_URL"
    )
    sys.exit(1)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from server.core.database import User, Base
from server.core.security import get_password_hash, encrypt_api_key

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)
db = Session()

try:
    existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()

    if existing:
        if existing.is_admin:
            print(f"ℹ️  {ADMIN_EMAIL} is already an admin")
        else:
            existing.is_admin = True
            existing.is_active = True
            existing.email_verified = True
            existing.credits_total = 999999
            existing.credits_used = 0
            db.commit()
            print(f"✅ {ADMIN_EMAIL} promoted to admin with unlimited credits")
        sys.exit(0)

    raw_api_key = f"sk-{secrets.token_urlsafe(32)}"
    encrypted_api_key = encrypt_api_key(raw_api_key)

    user = User(
        email=ADMIN_EMAIL,
        hashed_password=get_password_hash(ADMIN_PASSWORD),
        api_key=encrypted_api_key,
        public_id=str(uuid.uuid4()),
        is_active=True,
        is_admin=True,
        email_verified=True,
        subscription_tier="none",
        subscription_status="inactive",
        credits_total=999999,
        credits_used=0,
        accept_news_updates=False,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    print(f"✅ Admin user created: {ADMIN_EMAIL} (id={user.id})")
    print(f"   API key (plain): {raw_api_key}")

except Exception as e:
    db.rollback()
    print(f"❌ Error: {e}")
    sys.exit(1)
finally:
    db.close()
