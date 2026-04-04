from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Float,
    func,
    Index,
    Text,
    ForeignKey,
    Enum as SQLEnum,
    JSON,
    BigInteger,
    Date,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.engine.url import make_url
from server.config import settings
import enum
import uuid
from datetime import datetime, timezone

url = make_url(settings.DATABASE_URL)
if url.get_backend_name() == "sqlite":
    engine = create_engine(
        settings.DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class GiftSubscriptionStatus(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class EmailLogStatus(enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    api_key = Column(String, unique=True, index=True, nullable=False)
    subscription_tier = Column(String, default="none")
    subscription_status = Column(String, default="inactive")
    stripe_customer_id = Column(String, unique=True)
    stripe_subscription_id = Column(String)
    pending_tier = Column(String, nullable=True)
    credits_total = Column(Integer, default=0)
    credits_used = Column(Integer, default=0)
    email_verified = Column(Boolean, default=False)
    accept_news_updates = Column(Boolean, default=True)
    verification_token = Column(String, nullable=True)
    verification_token_expires = Column(DateTime, nullable=True)
    reset_token = Column(String, nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)
    two_factor_enabled = Column(Boolean, default=False)
    two_factor_secret = Column(String, nullable=True)
    two_factor_secret_temp = Column(String, nullable=True)
    backup_codes = Column(String, nullable=True)
    oauth_provider = Column(String, nullable=True)
    unsubscribe_token = Column(String(36), nullable=True)
    oauth_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    public_id = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()))

    provider = relationship("Provider", back_populates="user", uselist=False)

    active_gift_subscription_id = Column(
        Integer, ForeignKey("gift_subscriptions.id"), nullable=True
    )

    active_gift = relationship(
        "GiftSubscription",
        foreign_keys=[active_gift_subscription_id],
        uselist=False,
    )


class GiftSubscription(Base):
    __tablename__ = "gift_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    gift_code = Column(String(50), unique=True, index=True, nullable=False)

    purchaser_email = Column(String(255), nullable=False)
    purchaser_name = Column(String(255), nullable=True)

    recipient_email = Column(String(255), nullable=False, index=True)
    recipient_name = Column(String(255), nullable=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    tier = Column(String(50), nullable=False)
    duration_months = Column(Integer, nullable=False)

    purchased_at = Column(DateTime, default=func.now(), nullable=False)
    activation_date = Column(DateTime, nullable=False)
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    stripe_checkout_session_id = Column(String(255), nullable=True, unique=True)
    amount_paid = Column(Integer, nullable=False)

    status = Column(
        SQLEnum(GiftSubscriptionStatus),
        default=GiftSubscriptionStatus.PENDING,
        nullable=False,
    )

    gift_message = Column(Text, nullable=True)
    last_credit_refill_at = Column(DateTime, nullable=True)

    recipient_user = relationship("User", foreign_keys=[recipient_user_id])

    def __repr__(self):
        return f"<GiftSubscription {self.gift_code} - {self.tier} {self.duration_months}mo for {self.recipient_email}>"


class Generation(Base):
    __tablename__ = "generations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    prompt = Column(String, nullable=False)
    bpm = Column(Float)
    duration = Column(Float)
    credits_cost = Column(Integer, nullable=False)
    status = Column(String, default="pending")
    error_message = Column(String)
    created_at = Column(DateTime, default=func.now())


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (Index("idx_user_created", "user_id", "created_at"),)


class BroadcastEmail(Base):
    __tablename__ = "broadcast_emails"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    recipients_count = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    sent_by_admin_id = Column(Integer, ForeignKey("users.id"))
    sent_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    sent_by_admin = relationship("User", foreign_keys=[sent_by_admin_id])


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)
    recipient_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    email_type = Column(String(100), nullable=False, index=True)
    status = Column(
        SQLEnum(EmailLogStatus),
        default=EmailLogStatus.PENDING,
        nullable=False,
        index=True,
    )
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
    last_retry_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_email_status_created", "status", "created_at"),
        Index("idx_email_type_status", "email_type", "status"),
    )


class PressRequest(Base):
    __tablename__ = "press_requests"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    email = Column(String, index=True)
    payload = Column(JSON)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(String(255), nullable=False, unique=True)
    api_key = Column(String(255), nullable=False, unique=True)
    stripe_account_id = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(String(500), nullable=True)
    jobs_done = Column(Integer, default=0)
    jobs_failed = Column(Integer, default=0)
    billable_jobs = Column(Integer, default=0)
    uptime_score = Column(Float, default=0.0)
    last_ping = Column(DateTime, nullable=True)
    is_online = Column(Boolean, default=True)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    verification_failures = Column(Integer, nullable=False, default=0)
    is_trusted = Column(Boolean, default=False, nullable=False)
    is_generating = Column(Boolean, default=False, nullable=False)
    is_disposable = Column(Boolean, default=True, nullable=False)
    encoded_server_auth_key = Column(String(500), nullable=True)
    activation_token = Column(String(255), nullable=True, unique=True)
    activation_token_used = Column(Boolean, default=False, nullable=False)
    activation_token_expires_at = Column(DateTime, nullable=True)
    encoded_api_key = Column(String(500), nullable=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    jobs = relationship("ProviderJob", back_populates="provider")
    pings = relationship("ProviderPing", back_populates="provider")
    user = relationship("User", back_populates="provider", foreign_keys=[user_id])
    verifications = relationship("ProviderVerification", back_populates="provider")

    def __repr__(self):
        return f"<Provider {self.name} ({'banned' if self.is_banned else 'active' if self.is_active else 'inactive'})>"


class ProviderJob(Base):
    __tablename__ = "provider_jobs"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)
    user_id = Column(Integer, nullable=False, index=True)
    prompt = Column(String, nullable=False)
    duration = Column(Integer, nullable=False)
    status = Column(String, default="pending")
    used_fallback = Column(Boolean, default=False)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    completed_at = Column(DateTime, nullable=True)

    provider = relationship("Provider", back_populates="jobs")

    __table_args__ = (
        Index("idx_provider_job_status", "provider_id", "status"),
        Index("idx_provider_job_user", "user_id", "created_at"),
    )


class ProviderPing(Base):
    __tablename__ = "provider_pings"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(
        Integer, ForeignKey("providers.id"), nullable=False, index=True
    )
    pinged_at = Column(DateTime, default=func.now(), nullable=False)
    responded = Column(Boolean, nullable=False)
    response_time_ms = Column(Integer, nullable=True)

    provider = relationship("Provider", back_populates="pings")

    __table_args__ = (Index("idx_ping_provider_date", "provider_id", "pinged_at"),)


class ProviderVerification(Base):
    __tablename__ = "provider_verifications"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(
        Integer,
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt = Column(String(255), nullable=False)
    seed = Column(BigInteger, nullable=False)
    similarity_score = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=False, default=False)
    verified_at = Column(DateTime, nullable=False)

    provider = relationship("Provider", back_populates="verifications")


class VerificationSample(Base):
    __tablename__ = "verification_samples"

    id = Column(Integer, primary_key=True, index=True)
    prompt = Column(String(255), nullable=False)
    seed = Column(BigInteger, nullable=False)
    model = Column(String(255), nullable=False)
    encrypted_fingerprint = Column(Text, nullable=False)
    duration = Column(Integer, nullable=False, default=5)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "prompt", "seed", "model", name="_sample_prompt_seed_model_uc"
        ),
        Index("idx_sample_model", "model"),
    )

    def __repr__(self):
        return f"<VerificationSample prompt='{self.prompt[:30]}' seed={self.seed} model={self.model}>"


class OwnershipLog(Base):
    __tablename__ = "ownership_logs"

    id = Column(Integer, primary_key=True, index=True)
    public_user_id = Column(String(36), nullable=False, index=True)
    provider_name = Column(String(255), nullable=False)
    prompt_hash = Column(String(8), nullable=False)
    duration = Column(Float, nullable=False)
    audio_content_hash = Column(String(64), nullable=False)
    generated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("idx_ownership_user_date", "public_user_id", "generated_at"),
    )

    def __repr__(self):
        return f"<OwnershipLog {self.public_user_id} - {self.audio_content_hash[:8]}>"


class FinanceReport(Base):
    __tablename__ = "finance_reports"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String(7), nullable=False, unique=True, index=True)  # "2026-03"
    total_revenue_eur = Column(Float, nullable=False)
    platform_fee_pct = Column(Float, nullable=False)
    platform_fee_eur = Column(Float, nullable=False)
    distributable_eur = Column(Float, nullable=False)
    eligible_providers = Column(Integer, nullable=False)
    share_per_provider_eur = Column(Float, nullable=False)
    remainder_eur = Column(Float, nullable=False)
    transfers = Column(JSON, nullable=False)
    published_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<FinanceReport {self.month} — {self.total_revenue_eur}€>"


class ProviderDailyStats(Base):
    __tablename__ = "provider_daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(
        Integer,
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date = Column(Date, nullable=False, index=True)
    total_presence_minutes = Column(Float, default=0.0)
    is_eligible_for_payout = Column(Boolean, default=False)
    avg_similarity_score = Column(Float, default=0.0)
    total_verifications = Column(Integer, default=0)
    successful_verifications = Column(Integer, default=0)

    provider = relationship("Provider")

    __table_args__ = (
        UniqueConstraint("provider_id", "date", name="_provider_date_uc"),
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
