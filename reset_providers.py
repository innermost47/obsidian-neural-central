import hashlib
import secrets
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from server.core.database import SessionLocal, Provider
from server.core.security import encrypt_server_key
from server.services.provider_service import ProviderService


def reset_providers():
    db = SessionLocal()
    try:
        providers = db.query(Provider).filter(Provider.is_banned == False).all()

        if not providers:
            print("No providers found.")
            return

        for provider in providers:
            api_key = ProviderService.generate_api_key()
            api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            api_key_encrypted = encrypt_server_key(api_key)

            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=24)

            provider.api_key = api_key_hash
            provider.encoded_api_key = api_key_encrypted
            provider.activation_token = token
            provider.activation_token_used = False
            provider.activation_token_expires_at = expires_at

            print(f"\n✅ Provider: {provider.name}")
            print(f"   api_key: {api_key}")
            print(f"   OBSIDIAN_TOKEN: {token}")
            print(f"   Expires at: {expires_at.isoformat()}")

        db.commit()
        print(f"\n✅ {len(providers)} provider(s) reset for testing.")
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    reset_providers()
