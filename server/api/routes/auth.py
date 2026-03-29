from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import pyotp
import uuid
from datetime import datetime, timedelta
from server.api.models import (
    UserRegister,
    UserLogin,
    Token,
    SubscriptionRequest,
    EmailVerificationRequest,
    PasswordResetRequest,
    PasswordResetConfirm,
    TwoFactorVerify,
    TwoFactorLogin,
    UserPreferencesUpdate,
    PressRegister,
)
import stripe
from server.core.database import get_db, User, PressRequest, Provider
from server.api.dependencies import (
    get_user_from_api_key,
    get_current_active_user,
    get_current_user,
)
from server.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    generate_api_key,
    generate_verification_token,
    verify_verification_token,
    generate_2fa_secret,
    verify_2fa_token,
    get_2fa_qr_code,
    encrypt_api_key,
    decrypt_api_key,
)
from server.services.stripe_service import StripeService
from server.services.email_service import EmailService
from server.services.oauth_service import OAuthService
from server.services.admin_notification_service import AdminNotificationService
from server.services.email_validator import EmailValidator
from server.services.provider_notification_service import ProviderNotificationService
from server.config import settings
import secrets
from fastapi import Header


router = APIRouter(prefix="/auth", tags=["Authentication"])


async def verify_press_key(x_press_secret_key: str = Header(...)):
    if x_press_secret_key != settings.PRESS_REGISTRATION_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Press Registration Key",
        )
    return x_press_secret_key


def generate_unsubscribe_token() -> str:
    token = str(uuid.uuid4())
    return token


@router.post("/register", response_model=Token)
async def register(
    user_data: UserRegister,
    db: Session = Depends(get_db),
):
    is_valid, error_message = EmailValidator.validate_email(user_data.email)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=error_message
        )

    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    hashed_password = get_password_hash(user_data.password)
    api_key = generate_api_key()
    encrypted_key = encrypt_api_key(api_key)
    verification_token = generate_verification_token()

    new_user = User(
        email=user_data.email,
        hashed_password=hashed_password,
        api_key=encrypted_key,
        credits_total=10,
        credits_used=0,
        email_verified=False,
        verification_token=verification_token,
        unsubscribe_token=generate_unsubscribe_token(),
        accept_news_updates=user_data.accept_news_updates,
        verification_token_expires=datetime.utcnow() + timedelta(hours=24),
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    stripe_customer_id = StripeService.create_customer(
        email=new_user.email, user_id=new_user.id
    )
    new_user.stripe_customer_id = stripe_customer_id
    db.commit()

    EmailService.send_verification_email(
        email=new_user.email,
        token=verification_token,
        user_id=new_user.id,
        db=db,
    )

    access_token = create_access_token(
        data={"sub": str(new_user.id), "email": new_user.email}
    )

    AdminNotificationService.notify_new_user_registration(
        email=new_user.email, user_id=new_user.id
    )
    ProviderNotificationService.notify_new_free_user(db)

    return {"access_token": access_token, "token_type": "bearer"}


@router.patch("/preferences")
async def update_preferences(
    preferences: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.accept_news_updates = preferences.accept_news_updates

    db.commit()
    db.refresh(current_user)

    return {
        "message": "Preferences updated successfully",
        "accept_news_updates": current_user.accept_news_updates,
    }


@router.get("/verify-email/{token}")
async def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.verification_token == token).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token"
        )

    if user.verification_token_expires < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token expired"
        )

    user.email_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    db.commit()

    api_key = decrypt_api_key(user.api_key)

    EmailService.send_welcome_email(
        email=user.email,
        api_key=api_key,
        user_id=user.id,
        db=db,
    )

    return {"message": "Email verified successfully"}


@router.post("/resend-verification")
async def resend_verification(
    email_data: EmailVerificationRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email_data.email).first()

    if not user:
        return {"message": "If the email exists, a verification link has been sent"}

    if user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already verified"
        )

    verification_token = generate_verification_token()
    user.verification_token = verification_token
    user.verification_token_expires = datetime.utcnow() + timedelta(hours=24)
    db.commit()

    EmailService.send_verification_email(
        email=user.email,
        token=verification_token,
        user_id=user.id,
        db=db,
    )

    return {"message": "Verification email sent"}


@router.post("/forgot-password")
async def forgot_password(
    reset_request: PasswordResetRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == reset_request.email).first()

    if user:
        reset_token = secrets.token_urlsafe(32)
        user.reset_token = reset_token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.commit()

        EmailService.send_password_reset_email(
            email=user.email, token=reset_token, db=db
        )

    return {"message": "If the email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(
    reset_data: PasswordResetConfirm, db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.reset_token == reset_data.token).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token"
        )

    if user.reset_token_expires < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token expired"
        )

    user.hashed_password = get_password_hash(reset_data.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    return {"message": "Password reset successfully"}


@router.post("/2fa/setup")
async def setup_2fa(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    print(f"🔍 Setup 2FA for user {current_user.id} - {current_user.email}")

    if current_user.two_factor_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="2FA already enabled"
        )

    secret = generate_2fa_secret()
    print(f"✅ Generated secret: {secret[:8]}...")

    qr_code = get_2fa_qr_code(current_user.email, secret)

    current_user.two_factor_secret_temp = secret
    db.commit()
    db.refresh(current_user)

    print(f"✅ Secret saved to DB: {current_user.two_factor_secret_temp[:8]}...")

    return {
        "secret": secret,
        "qr_code": qr_code,
        "message": "Scan QR code with your authenticator app",
    }


@router.post("/2fa/verify-setup")
async def verify_2fa_setup(
    verify_data: TwoFactorVerify,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    print(f"🔍 Verify 2FA for user {current_user.id}")
    print(
        f"   - two_factor_secret_temp exists: {bool(current_user.two_factor_secret_temp)}"
    )
    print(f"   - Provided code: {verify_data.code}")

    if not current_user.two_factor_secret_temp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="2FA setup not initiated"
        )

    totp = pyotp.TOTP(current_user.two_factor_secret_temp)
    expected_code = totp.now()
    print(f"   - Expected code: {expected_code}")

    is_valid = verify_2fa_token(current_user.two_factor_secret_temp, verify_data.code)
    print(f"   - Is valid: {is_valid}")

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code"
        )

    current_user.two_factor_secret = current_user.two_factor_secret_temp
    current_user.two_factor_secret_temp = None
    current_user.two_factor_enabled = True

    backup_codes = [secrets.token_hex(4) for _ in range(10)]
    current_user.backup_codes = ",".join(backup_codes)

    db.commit()

    print(f"✅ 2FA enabled successfully for user {current_user.id}")

    return {"message": "2FA enabled successfully", "backup_codes": backup_codes}


@router.post("/2fa/disable")
async def disable_2fa(
    verify_data: TwoFactorVerify,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if not current_user.two_factor_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="2FA not enabled"
        )

    if not verify_2fa_token(current_user.two_factor_secret, verify_data.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code"
        )

    current_user.two_factor_enabled = False
    current_user.two_factor_secret = None
    current_user.backup_codes = None
    db.commit()

    return {"message": "2FA disabled successfully"}


@router.post("/login", response_model=Token)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )

    if user.two_factor_enabled:
        temp_token = create_access_token(
            data={"sub": str(user.id), "email": user.email, "2fa_pending": True},
            expires_delta=timedelta(minutes=5),
        )
        return {
            "access_token": temp_token,
            "token_type": "bearer",
            "requires_2fa": True,
        }

    access_token = create_access_token(data={"sub": str(user.id), "email": user.email})

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/2fa", response_model=Token)
def login_2fa(two_factor_data: TwoFactorLogin, db: Session = Depends(get_db)):
    payload = verify_verification_token(two_factor_data.temp_token)
    if not payload.get("2fa_pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid temporary token"
        )

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    code_valid = False

    if verify_2fa_token(user.two_factor_secret, two_factor_data.code):
        code_valid = True
    elif user.backup_codes:
        backup_codes = user.backup_codes.split(",")
        if two_factor_data.code in backup_codes:
            backup_codes.remove(two_factor_data.code)
            user.backup_codes = ",".join(backup_codes)
            db.commit()
            code_valid = True

    if not code_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code"
        )

    access_token = create_access_token(data={"sub": str(user.id), "email": user.email})

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/google/login")
async def google_login():
    authorization_url = OAuthService.get_google_authorization_url()
    return {"authorization_url": authorization_url}


@router.get("/google/callback")
async def google_callback(code: str, db: Session = Depends(get_db)):
    try:
        google_user_info = await OAuthService.get_google_user_info(code)

        email = google_user_info.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not provided by Google",
            )

        user = db.query(User).filter(User.email == email).first()

        if not user:
            api_key = generate_api_key()
            encrypted_key = encrypt_api_key(api_key)
            user = User(
                email=email,
                hashed_password=None,
                api_key=encrypted_key,
                credits_total=10,
                credits_used=0,
                email_verified=True,
                oauth_provider="google",
                oauth_id=google_user_info.get("sub"),
                is_active=True,
                accept_news_updates=True,
                unsubscribe_token=generate_unsubscribe_token(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            stripe_customer_id = StripeService.create_customer(
                email=user.email, user_id=user.id
            )
            user.stripe_customer_id = stripe_customer_id
            db.commit()

            EmailService.send_welcome_email(
                email=user.email,
                api_key=api_key,
                user_id=user.id,
                db=db,
            )
            AdminNotificationService.notify_new_user_registration(
                email=user.email, user_id=user.id, oauth_provider="google"
            )
            ProviderNotificationService.notify_new_free_user(db)
        else:
            if not user.oauth_provider:
                user.oauth_provider = "google"
                user.oauth_id = google_user_info.get("sub")
                user.email_verified = True
                db.commit()

        access_token = create_access_token(
            data={"sub": str(user.id), "email": user.email}
        )

        redirect_url = f"{settings.FRONTEND_URL}/dashboard.html?token={access_token}"
        return RedirectResponse(url=redirect_url)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google authentication failed: {str(e)}",
        )


@router.get("/me")
def get_current_user_info(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "api_key": decrypt_api_key(current_user.api_key),
        "subscription_tier": current_user.subscription_tier,
        "subscription_status": current_user.subscription_status,
        "credits_remaining": current_user.credits_total - current_user.credits_used,
        "credits_total": current_user.credits_total,
        "email_verified": current_user.email_verified,
        "two_factor_enabled": current_user.two_factor_enabled,
        "oauth_provider": current_user.oauth_provider,
        "is_admin": current_user.is_admin,
        "accept_news_updates": current_user.accept_news_updates,
        "is_provider": db.query(Provider)
        .filter(
            Provider.user_id == current_user.id,
            Provider.is_active == True,
            Provider.is_banned == False,
        )
        .first()
        is not None,
    }


@router.post("/subscription/create-checkout")
def create_subscription_checkout(
    subscription: SubscriptionRequest,
    current_user: User = Depends(get_current_active_user),
    _: Session = Depends(get_db),
):
    tier_to_price = {
        "base": settings.STRIPE_PRICE_BASE,
        "starter": settings.STRIPE_PRICE_STARTER,
        "pro": settings.STRIPE_PRICE_PRO,
        "studio": settings.STRIPE_PRICE_STUDIO,
    }

    price_id = tier_to_price.get(subscription.tier)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid tier")

    session = StripeService.create_checkout_session(
        customer_id=current_user.stripe_customer_id,
        price_id=price_id,
        user_id=current_user.id,
        tier=subscription.tier,
    )

    return {"checkout_url": session.url}


@router.post("/subscription/cancel")
def cancel_subscription(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    if not current_user.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription")

    StripeService.cancel_subscription(current_user.stripe_subscription_id)
    current_user.subscription_status = "canceled"
    db.commit()

    return {"message": "Subscription will be canceled at period end"}


@router.get("/subscription/portal")
def get_customer_portal(
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer found")

    portal_session = StripeService.create_customer_portal_session(
        customer_id=current_user.stripe_customer_id
    )

    return {"portal_url": portal_session.url}


@router.delete("/account/delete")
async def delete_account(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if current_user.stripe_subscription_id:
        try:
            StripeService.cancel_subscription(current_user.stripe_subscription_id)
            print(
                f"✅ Stripe subscription {current_user.stripe_subscription_id} canceled"
            )
        except Exception as e:
            print(f"⚠️ Error canceling Stripe subscription: {e}")

    if current_user.stripe_customer_id:
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            stripe.Customer.delete(current_user.stripe_customer_id)
            print(f"✅ Stripe customer {current_user.stripe_customer_id} deleted")
        except Exception as e:
            print(f"⚠️ Error deleting Stripe customer: {e}")

    if current_user.oauth_provider == "google" and current_user.oauth_id:
        try:
            print(
                f"✅ OAuth user deleted: {current_user.oauth_provider} - {current_user.oauth_id}"
            )
        except Exception as e:
            print(f"⚠️ Error with OAuth cleanup: {e}")

    user_email = current_user.email
    user_id = current_user.id
    oauth_provider = current_user.oauth_provider

    AdminNotificationService.notify_account_deleted(
        email=current_user.email,
        user_id=current_user.id,
        subscription_tier=current_user.subscription_tier,
    )

    db.delete(current_user)
    db.commit()

    print(
        f"✅ User account deleted: {user_email} (ID: {user_id}, OAuth: {oauth_provider or 'None'})"
    )

    return {
        "message": "Account successfully deleted",
        "email": user_email,
        "oauth_provider": oauth_provider,
    }


@router.get("/credits/check")
def check_credits(current_user: User = Depends(get_current_active_user)):
    credits_remaining = current_user.credits_total - current_user.credits_used

    return {
        "credits_remaining": credits_remaining,
        "credits_total": current_user.credits_total,
        "can_generate_standard": credits_remaining >= settings.CREDIT_STANDARD,
        "cost_standard": settings.CREDIT_STANDARD,
    }


@router.get("/credits/check/vst")
def check_credits_vst(current_user: User = Depends(get_user_from_api_key)):
    credits_remaining = current_user.credits_total - current_user.credits_used
    return {
        "credits_remaining": credits_remaining,
        "credits_total": current_user.credits_total,
        "can_generate_standard": credits_remaining >= settings.CREDIT_STANDARD,
        "cost_standard": settings.CREDIT_STANDARD,
    }


@router.post("/press-register")
async def register_press_request(
    user_data: PressRegister,
    db: Session = Depends(get_db),
    _: str = Depends(verify_press_key),
):
    token = secrets.token_hex(32)

    payload = {
        "email": user_data.email,
        "credits": user_data.credits,
        "tier": user_data.tier,
    }

    new_request = PressRequest(
        token=token,
        email=user_data.email,
        payload=payload,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(new_request)
    db.commit()

    confirmation_link = f"{settings.APP_URL}/press/confirm-press?token={token}"

    return {"success": True, "token": token, "link": confirmation_link}
