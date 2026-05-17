from pydantic import BaseModel, EmailStr, validator, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    accept_news_updates: Optional[bool] = True


class PressRegister(BaseModel):
    email: EmailStr
    nom: str
    tier: Optional[str] = None
    credits: Optional[int] = 200
    accept_news_updates: Optional[bool] = True


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserPreferencesUpdate(BaseModel):
    accept_news_updates: bool


class Token(BaseModel):
    access_token: str
    token_type: str
    requires_2fa: Optional[bool] = False


class AIModelName(str, Enum):
    STABLE_AUDIO = "stable-audio-open-1.0"
    FOUNDATION = "foundation-1"
    EDM = "audialab-edm-elements"
    PIANOS = "rc-infinite-pianos"
    VOCALS = "rc-vocal-textures"
    INSTRUMENTAL = "sao-instrumental"
    STABLEBEAT = "stablebeat"
    GLUTEN = "gluten-v1"


class GenerateRequest(BaseModel):
    model: AIModelName = Field(default=AIModelName.STABLE_AUDIO)

    prompt: Optional[str] = None
    bpm: float
    key: Optional[str] = None
    measures: Optional[int] = 4
    generation_duration: Optional[float] = 6.0
    sample_rate: Optional[float] = 48000.00
    use_image: Optional[bool] = False
    image_base64: Optional[str] = None
    image_temperature: Optional[float] = 0.7
    keywords: Optional[List[str]] = []
    bypass_llm: Optional[bool] = False
    sync_on_server: Optional[bool] = True

    model_config = ConfigDict(
        protected_namespaces=(),
        extra="forbid",
        use_enum_values=True,
    )


class SubscriptionRequest(BaseModel):
    tier: str


class EmailVerificationRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class TwoFactorSetup(BaseModel):
    secret: str
    qr_code: str
    message: str


class TwoFactorVerify(BaseModel):
    code: str


class TwoFactorLogin(BaseModel):
    temp_token: str
    code: str


class OAuthCallback(BaseModel):
    code: str
    state: Optional[str] = None


class ContactRequest(BaseModel):
    name: str
    email: EmailStr
    subject: str
    message: str
    website: Optional[str] = ""
    email_confirm: Optional[str] = ""
    phone: Optional[str] = ""
    timestamp: Optional[str] = None

    @validator("name", "message")
    def not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("This field cannot be empty")
        return v.strip()

    @validator("subject")
    def valid_subject(cls, v):
        allowed = ["support", "billing", "feature", "bug", "partnership", "other"]
        if v not in allowed:
            raise ValueError("Invalid subject")
        return v

    @validator("message")
    def message_length(cls, v):
        if len(v) > 2000:
            raise ValueError("Message cannot exceed 2000 characters")
        return v


class GiftPurchaseRequest(BaseModel):
    recipient_email: EmailStr
    recipient_name: Optional[str] = None
    tier: str
    duration_months: int
    gift_message: Optional[str] = None
    activation_date: Optional[datetime] = None
    purchaser_name: Optional[str] = None


class GiftActivationResponse(BaseModel):
    message: str
    tier: str
    expires_at: datetime
    credits_granted: int


class AddProviderRequest(BaseModel):
    name: str
    url: str
    stripe_account_id: Optional[str] = None
    user_id: Optional[int] = None


class UpdateProviderRequest(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    stripe_account_id: Optional[str] = None
    is_active: Optional[bool] = None
    user_id: Optional[int] = None


class BanProviderRequest(BaseModel):
    reason: str


class RedistributionRequest(BaseModel):
    month_revenue_cents: int
    month_start: Optional[str] = None
    dry_run: bool = True


class BroadcastEmailRequest(BaseModel):
    subject: str
    body: str


class EmailLogResponse(BaseModel):
    id: int
    recipient_email: str
    subject: str
    email_type: str
    status: str
    error_message: Optional[str]
    retry_count: int
    user_id: Optional[int]
    created_at: str
    sent_at: Optional[str]
    last_retry_at: Optional[str]

    class Config:
        from_attributes = True


class EmailStatsResponse(BaseModel):
    total_emails: int
    sent: int
    failed: int
    pending: int
    success_rate: float
    by_type: dict


class RetryEmailsRequest(BaseModel):
    email_ids: List[int]


class SupportedModel(str, Enum):
    STABLE_AUDIO = "stable-audio-open-1.0"
    STABLE_AUDIO_SMALL = "stable-audio-open-small"
    FOUNDATION_1 = "foundation-1"
    EDM_ELEMENTS = "audialab-edm-elements"
    INFINITE_PIANOS = "rc-infinite-pianos"
    VOCAL_TEXTURES = "rc-vocal-textures"
    SAO_INSTRUMENTAL = "sao-instrumental"
    STABLEBEAT = "stablebeat"
    GLUTEN_V1 = "gluten-v1"


class SupportedModelId(str, Enum):
    STABLE_AUDIO = "stabilityai/stable-audio-open-1.0"
    STABLE_AUDIO_SMALL = "stabilityai/stable-audio-open-small"
    FOUNDATION_1 = "RoyalCities/Foundation-1"
    EDM_ELEMENTS = "innermost47/obsidian-neural-models"
    INFINITE_PIANOS = "innermost47/obsidian-neural-models"
    VOCAL_TEXTURES = "innermost47/obsidian-neural-models"
    SAO_INSTRUMENTAL = "innermost47/obsidian-neural-models"
    STABLEBEAT = "innermost47/obsidian-neural-models"
    GLUTEN_V1 = "innermost47/obsidian-neural-models"


class SupportedDevice(str, Enum):
    CUDA = "cuda"


class ProviderStatusResponse(BaseModel):
    available: bool
    api_key: str = Field(..., min_length=48, max_length=64)
    model: SupportedModel
    model_id: SupportedModelId
    device: SupportedDevice
    generating: bool
    vram_total_gb: float = Field(..., ge=0, le=999999)
    vram_used_gb: float = Field(..., ge=0, le=999999)
    generating_llm: bool
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")


class HealthStatus(str, Enum):
    OK = "ok"


class ProviderHealthResponse(BaseModel):
    status: HealthStatus = Field(..., description="Must be 'ok'")
    model_loaded: bool
    model: SupportedModel
    model_id: SupportedModelId

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")


class ProviderGenerateResponse(BaseModel):
    api_key: str = Field(..., min_length=48, max_length=64)
    model: SupportedModel
    duration: int = Field(default=0, ge=0, le=30)
    sample_rate: int = Field(default=0, ge=0, le=48000)
    seed: int = Field(..., ge=0, le=2**31 - 1)
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")


class ActivateRequest(BaseModel):
    token: str


class LLMConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str


class ProviderLLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str
    history: list[LLMConversationMessage]
    user_message: str
    response: str
    model: str
    provider_key: str
    audio_model: Optional[SupportedModel] = None
