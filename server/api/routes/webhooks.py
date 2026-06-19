from fastapi import APIRouter, Request, HTTPException
from server.core.database import User, GiftSubscription, GiftSubscriptionStatus, License
from server.services.stripe_service import StripeService
from server.services.credits_service import CreditsService
from server.services.email_service import EmailService
from server.services.license_service import LicenseService
from server.services.admin_notification_service import AdminNotificationService
from server.services.provider_notification_service import ProviderNotificationService
from server.core.database import SessionLocal
from server.config import settings
from datetime import datetime

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
legacy_router = APIRouter(tags=["Webhooks"])


@router.post("/stripe")
async def stripe_webhook(request: Request):
    return await handle_stripe_webhook(request)


@legacy_router.post("/webhook")
async def stripe_webhook_legacy(request: Request):
    return await handle_stripe_webhook(request)


async def handle_stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = StripeService.construct_webhook_event(payload, sig_header)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_type = session.get("metadata", {}).get("type")
        if session_type == "gift_subscription":
            handle_gift_checkout_completed(session)
        elif session_type == "vst_license":
            handle_vst_license_completed(session)
        else:
            handle_successful_checkout(session)

    elif event["type"] == "invoice.payment_succeeded":
        handle_successful_payment(event["data"]["object"])

    elif event["type"] == "invoice.payment_failed":
        handle_payment_failed(event["data"]["object"])

    elif event["type"] == "customer.subscription.updated":
        handle_subscription_updated(event["data"]["object"])

    elif event["type"] == "customer.subscription.deleted":
        handle_subscription_canceled(event["data"]["object"])

    elif event["type"] == "customer.subscription.created":
        handle_subscription_created(event["data"]["object"])

    return {"status": "success"}


def handle_successful_checkout(session):
    db = SessionLocal()
    try:
        user_id = int(session["metadata"]["user_id"])
        tier = session["metadata"]["tier"]
        subscription_id = session["subscription"]

        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.subscription_tier = tier
            user.subscription_status = "pending"
            user.stripe_subscription_id = subscription_id
            db.commit()
    finally:
        db.close()


def handle_gift_checkout_completed(session):
    db = SessionLocal()
    try:
        metadata = session.get("metadata", {})
        gift_code = metadata.get("gift_code")
        purchaser_email = metadata.get("purchaser_email")
        recipient_email = metadata.get("recipient_email")
        tier = metadata.get("tier")
        duration_months_str = metadata.get("duration_months")
        if duration_months_str is None:
            raise ValueError("Missing 'duration_months' in session metadata for gift.")

        duration_months = int(duration_months_str)
        gift_message = metadata.get("gift_message")
        activation_date_str = metadata.get("activation_date")

        activation_date = (
            datetime.fromisoformat(activation_date_str)
            if activation_date_str
            else datetime.utcnow()
        )

        gift = GiftSubscription(
            gift_code=gift_code,
            purchaser_email=purchaser_email,
            purchaser_name=metadata.get("purchaser_name"),
            recipient_email=recipient_email,
            recipient_name=metadata.get("recipient_name"),
            tier=tier,
            duration_months=duration_months,
            purchased_at=datetime.utcnow(),
            activation_date=activation_date,
            stripe_checkout_session_id=session["id"],
            amount_paid=session["amount_total"],
            status=GiftSubscriptionStatus.PENDING,
            gift_message=gift_message,
        )

        db.add(gift)
        db.commit()

        EmailService.send_gift_notification(
            recipient_email=recipient_email,
            recipient_name=metadata.get("recipient_name", ""),
            purchaser_name=metadata.get("purchaser_name", purchaser_email),
            tier=tier,
            duration_months=duration_months,
            gift_code=gift_code,
            gift_message=gift_message,
            activation_date=activation_date_str,
            db=db,
        )

        print(f"🎁 Gift created: {gift_code} for {recipient_email}")

        AdminNotificationService.notify_new_subscription(
            email=purchaser_email,
            user_id=0,
            tier=tier,
            amount=f"Gift {duration_months}mo for {recipient_email}",
        )
        ProviderNotificationService.notify_new_subscriber(db, tier=tier)
    except Exception as e:
        print(f"Error creating gift: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def handle_successful_payment(invoice):
    db = SessionLocal()
    try:
        customer_id = invoice["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

        if user:
            if hasattr(user, "pending_tier") and user.pending_tier:
                old_tier = user.subscription_tier
                new_tier = user.pending_tier
                user.subscription_tier = new_tier
                user.pending_tier = None
                user.subscription_status = "active"
                CreditsService.refill_credits(db, user.id, user.subscription_tier)
                AdminNotificationService.notify_new_subscription(
                    email=user.email,
                    user_id=user.id,
                    tier=new_tier,
                    amount=f"Upgrade from {old_tier} to {new_tier}",
                )
                ProviderNotificationService.notify_new_subscriber(db, tier=new_tier)

            elif user.subscription_tier not in ["none", "free"]:
                user.subscription_status = "active"
                CreditsService.refill_credits(db, user.id, user.subscription_tier)

            db.commit()
    finally:
        db.close()


def handle_payment_failed(invoice):
    db = SessionLocal()
    try:
        customer_id = invoice["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

        if user:
            error_message = None
            if invoice.get("last_payment_error"):
                error_message = invoice["last_payment_error"].get("message")

            AdminNotificationService.notify_payment_failed(
                email=user.email,
                user_id=user.id,
                tier=user.subscription_tier,
                error=error_message,
            )

            print(f"⚠️ Payment failed for user {user.email}: {error_message}")
    finally:
        db.close()


def handle_subscription_updated(subscription):
    db = SessionLocal()
    try:
        subscription_id = subscription["id"]
        status = subscription["status"]
        cancel_at_period_end = subscription.get("cancel_at_period_end", False)

        items = subscription.get("items", {}).get("data", [])
        current_price_id = items[0]["price"]["id"] if items else None

        from server.config import STRIPE_PRICE_IDS

        detected_tier = None
        for tier, pid in STRIPE_PRICE_IDS.items():
            if pid == current_price_id:
                detected_tier = tier
                break

        user = (
            db.query(User)
            .filter(User.stripe_subscription_id == subscription_id)
            .first()
        )

        if user:
            if detected_tier and detected_tier != user.subscription_tier:
                print(
                    f"Plan change detected: {user.subscription_tier} → {detected_tier}"
                )

                user.pending_tier = detected_tier
                user.subscription_status = f"changing_to_{detected_tier}"

            elif cancel_at_period_end:
                user.subscription_status = "canceling"

                EmailService.send_subscription_cancelled(email=user.email, db=db)

                AdminNotificationService.notify_subscription_cancelled(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )
                ProviderNotificationService.notify_subscription_cancelled(db, tier=tier)
            elif status == "canceled":
                user.subscription_status = "canceled"
                user.subscription_tier = "free"
                user.stripe_subscription_id = None

                EmailService.send_subscription_cancelled(email=user.email, db=db)
            elif status == "active":
                user.subscription_status = "active"

            db.commit()

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


def handle_subscription_canceled(subscription):
    db = SessionLocal()
    try:
        subscription_id = subscription["id"]
        user = (
            db.query(User)
            .filter(User.stripe_subscription_id == subscription_id)
            .first()
        )

        if user:
            EmailService.send_subscription_cancelled(email=user.email, db=db)
            AdminNotificationService.notify_subscription_cancelled(
                email=user.email, user_id=user.id, tier=user.subscription_tier
            )
            ProviderNotificationService.notify_subscription_cancelled(
                db, tier=user.subscription_tier
            )
            print(f"❌ Subscription cancelled: {user.email} - {user.subscription_tier}")

            user.subscription_status = "canceled"
            user.subscription_tier = "free"
            user.stripe_subscription_id = None
            db.commit()
    finally:
        db.close()


def handle_subscription_created(subscription):
    db = SessionLocal()
    try:
        subscription_id = subscription["id"]
        customer_id = subscription["customer"]
        status = subscription["status"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

        if user:
            user.subscription_status = status
            user.stripe_subscription_id = subscription_id

            items = subscription.get("items", {}).get("data", [])
            if items:
                price_id = items[0]["price"]["id"]
                from server.config import STRIPE_PRICE_IDS

                for tier, pid in STRIPE_PRICE_IDS.items():
                    if pid == price_id:
                        user.subscription_tier = tier
                        print(f"✨ Subscription created: {user.email} - {tier}")
                        break

            db.commit()

            AdminNotificationService.notify_new_subscription(
                email=user.email,
                user_id=user.id,
                tier=user.subscription_tier,
                amount=None,
            )

    except Exception as e:
        print(f"Error in handle_subscription_created: {e}")
        db.rollback()
    finally:
        db.close()

def handle_vst_license_completed(session):
    db = SessionLocal()
    try:
        email = session.get("customer_details", {}).get("email") or session.get(
            "customer_email"
        )
        if not email:
            raise ValueError("No email found in VST checkout session.")

        email = email.strip().lower()

        existing_license = (
            db.query(License)
            .filter(License.stripe_checkout_session_id == session["id"])
            .first()
        )
        if existing_license:
            print(f"⏩ VST license already created for session {session['id']}")
            return

        user = db.query(User).filter(User.email == email).first()
        if not user:
            from server.core.security import generate_api_key, encrypt_api_key

            api_key = generate_api_key()
            user = User(
                email=email,
                hashed_password=None,
                api_key=encrypt_api_key(api_key),
                credits_total=20,
                credits_used=0,
                email_verified=True,
                accept_news_updates=True,
                unsubscribe_token=str(__import__("uuid").uuid4()),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            stripe_customer_id = StripeService.create_customer(
                email=user.email, user_id=user.id
            )
            user.stripe_customer_id = stripe_customer_id
            db.commit()

        license_key = LicenseService.generate_license_key()
        while db.query(License).filter(License.license_key == license_key).first():
            license_key = LicenseService.generate_license_key()

        new_license = License(
            license_key=license_key,
            user_id=user.id,
            email=email,
            tier="standard",
            status="active",
            max_activations=settings.LICENSE_MAX_ACTIVATIONS,
            stripe_checkout_session_id=session["id"],
            stripe_payment_intent_id=session.get("payment_intent"),
            amount_paid=session.get("amount_total"),
        )
        db.add(new_license)
        db.commit()

        EmailService.send_vst_license_email(
            email=email,
            license_key=license_key,
            user_id=user.id,
            db=db,
        )

        print(f"🔑 VST license created: {license_key} for {email}")

        AdminNotificationService.notify_new_vst_license(
            email=email,
            user_id=user.id,
            license_key=license_key,
            amount=f"{session.get('amount_total', 0) / 100:.2f}€ one-time",
        )
    except Exception as e:
        print(f"Error creating VST license: {e}")
        db.rollback()
        raise
    finally:
        db.close()