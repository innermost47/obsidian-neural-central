import os
from dotenv import load_dotenv

ENV = os.getenv("ENV", "dev")
env_file = f".env.{ENV}"


load_dotenv(env_file)


class Settings:
    API_HOST = os.getenv("API_HOST", "127.0.0.1")
    API_PORT = int(os.getenv("API_PORT", 8000))
    ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

    GA4_PROPERTY_ID: str = os.getenv("GA4_PROPERTY_ID", "")
    GOOGLE_ANALYTICS_CREDENTIALS_PATH: str = os.getenv(
        "GOOGLE_ANALYTICS_CREDENTIALS_PATH", ""
    )

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./obsidian_neural.db")

    FRONTEND_URL = os.getenv("FRONTEND_URL")

    SECRET_KEY = os.getenv("SECRET_KEY")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 43200))

    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
    STRIPE_PRICE_BASE = os.getenv("STRIPE_PRICE_BASE")
    STRIPE_PRICE_STARTER = os.getenv("STRIPE_PRICE_STARTER")
    STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO")
    STRIPE_PRICE_STUDIO = os.getenv("STRIPE_PRICE_STUDIO")

    FAL_KEY = os.getenv("FAL_KEY")

    CREDIT_STANDARD = int(os.getenv("CREDIT_STANDARD", 1))
    CREDIT_LLM = int(os.getenv("CREDIT_LLM", 1))

    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Obsidian Neural")
    SMTP_TO_EMAIL = os.getenv("SMTP_TO_EMAIL")

    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

    EMAIL_VERIFICATION_EXPIRE_HOURS = int(
        os.getenv("EMAIL_VERIFICATION_EXPIRE_HOURS", 24)
    )

    PASSWORD_RESET_EXPIRE_HOURS = int(os.getenv("PASSWORD_RESET_EXPIRE_HOURS", 1))

    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

    PRESS_REGISTRATION_KEY = os.getenv("PRESS_REGISTRATION_KEY")
    APP_URL = os.environ.get("APP_URL")

    REPO_URL = os.environ.get("REPO_URL")

    PING_PROBABILITY = float(os.getenv("PING_PROBABILITY", "1.0"))
    PING_TIMEOUT = float(os.getenv("PING_TIMEOUT", "5.0"))
    MIN_UPTIME_SCORE = float(os.getenv("MIN_UPTIME_SCORE", "0.30"))
    MIN_BILLABLE_JOBS = int(os.getenv("MIN_BILLABLE_JOBS", "1"))
    RANDOM_DELAY_MAX_MINUTES = int(os.getenv("RANDOM_DELAY_MAX_MINUTES", "50"))
    PLATFORM_FEE_PCT = float(os.getenv("PLATFORM_FEE_PCT", "0.15"))
    SERVER_TO_PROVIDER_KEY = os.getenv("SERVER_TO_PROVIDER_KEY")

    BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    }

    API_URL = os.environ.get("API_URL")

    TIER_CREDITS = {
        "base": 200,
        "starter": 500,
        "pro": 1500,
        "studio": 4000,
        "provider": 500,
    }

    TIER_PRICES = {
        "base": 599,
        "starter": 1499,
        "pro": 2999,
        "studio": 5999,
    }

    TRIAL_CONFIG = {
        "duration_days": 7,
        "credits": {
            "base": 100,
            "starter": 100,
            "pro": 100,
            "studio": 100,
        },
        "payment_method": "if_required",
    }

    @property
    def TIER_PRICES_EUR(self) -> dict:
        return {tier: amount / 100 for tier, amount in self.TIER_PRICES.items()}


settings = Settings()

STRIPE_PRICE_IDS = {
    "starter": settings.STRIPE_PRICE_STARTER,
    "pro": settings.STRIPE_PRICE_PRO,
    "studio": settings.STRIPE_PRICE_STUDIO,
}
