import os
from dotenv import load_dotenv

ENV = os.getenv("ENV", "dev")
env_file = f".env.{ENV}"

load_dotenv(env_file)

from server.core.database import SessionLocal, Provider
from server.core.security import encrypt_server_key, generate_api_key


def migrate_providers():
    db = SessionLocal()
    try:
        providers = (
            db.query(Provider).filter(Provider.encoded_server_auth_key == None).all()
        )

        if not providers:
            print("No providers to migrate.")
            return

        print(f"Migrating {len(providers)} providers...")
        print("-" * 50)

        for p in providers:
            raw_key = generate_api_key()
            p.encoded_server_auth_key = encrypt_server_key(raw_key)

            print(f"Provider: {p.name}")
            print(f"URL: {p.url}")
            print(f"NEW SERVER AUTH KEY: {raw_key}")
            print("-" * 50)

        db.commit()
        print("Migration successful.")

    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    migrate_providers()
