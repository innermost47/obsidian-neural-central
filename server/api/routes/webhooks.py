from fastapi import APIRouter, Request, HTTPException
from server.core.database import User, GiftSubscription, GiftSubscriptionStatus
from server.services.stripe_service import StripeService
from server.services.credits_service import CreditsService
from server.services.email_service import EmailService
from server.services.admin_notification_service import AdminNotificationService
from server.services.provider_notification_service import ProviderNotificationService
from server.core.database import SessionLocal
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

        if session.get("metadata", {}).get("type") == "gift_subscription":
            handle_gift_checkout_completed(session)
        else:
            handle_successful_checkout(session)

    elif event["type"] == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        handle_successful_payment(invoice)

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        handle_payment_failed(invoice)

    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        handle_subscription_updated(subscription)

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        handle_subscription_canceled(subscription)

    elif event["type"] == "customer.subscription.trial_will_end":
        subscription = event["data"]["object"]
        handle_trial_will_end(subscription)

    elif event["type"] == "customer.subscription.created":
        subscription = event["data"]["object"]
        handle_subscription_created(subscription)

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
                print(f"Applying pending tier change: {user.pending_tier}")

                old_tier = user.subscription_tier
                new_tier = user.pending_tier

                user.subscription_tier = user.pending_tier
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
            elif user.subscription_status == "trialing":
                print(f"✅ Trial converted to paid: {user.email}")

                user.subscription_status = "active"

                CreditsService.refill_credits(db, user.id, user.subscription_tier)

                EmailService.send_trial_converted(
                    email=user.email, tier=user.subscription_tier, db=db
                )

                AdminNotificationService.notify_trial_converted(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )
                ProviderNotificationService.notify_trial_converted(
                    db, tier=user.subscription_tier
                )
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
            was_trial = user.subscription_status == "trialing"

            if was_trial:
                EmailService.send_trial_not_converted(email=user.email, db=db)

                AdminNotificationService.notify_trial_not_converted(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )

                print(
                    f"❌ Trial not converted: {user.email} - {user.subscription_tier}"
                )
            else:
                EmailService.send_subscription_cancelled(email=user.email, db=db)

                AdminNotificationService.notify_subscription_cancelled(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )
                ProviderNotificationService.notify_subscription_cancelled(
                    db, tier=user.subscription_tier
                )
                print(
                    f"❌ Subscription cancelled: {user.email} - {user.subscription_tier}"
                )

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
        trial_end = subscription.get("trial_end")

        trial_credits_str = subscription.get("metadata", {}).get("trial_credits")

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

                        if trial_credits_str:
                            trial_credits = int(trial_credits_str)
                        else:
                            trial_credits = StripeService.get_trial_credits(tier)

                        CreditsService.set_credits(db, user.id, trial_credits)

                        print(
                            f"✨ Trial started: {user.email} - {tier} - {trial_credits} credits"
                        )
                        break

            db.commit()

            if trial_end:
                trial_end_date = datetime.fromtimestamp(trial_end)
                EmailService.send_trial_started(
                    email=user.email,
                    tier=user.subscription_tier,
                    trial_end_date=trial_end_date,
                    trial_credits=trial_credits if trial_credits_str else None,
                    db=db,
                )

                AdminNotificationService.notify_trial_started(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )

    except Exception as e:
        print(f"Error in handle_subscription_created: {e}")
        db.rollback()
    finally:
        db.close()


def handle_trial_will_end(subscription):
    db = SessionLocal()
    try:
        customer_id = subscription["customer"]
        trial_end = subscription.get("trial_end")

        default_payment_method = subscription.get("default_payment_method")
        has_payment_method = default_payment_method is not None

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

        if user:
            trial_end_date = datetime.fromtimestamp(trial_end) if trial_end else None

            EmailService.send_trial_ending_reminder(
                email=user.email,
                tier=user.subscription_tier,
                trial_end_date=trial_end_date,
                has_payment_method=has_payment_method,
                db=db,
            )

            if has_payment_method:
                AdminNotificationService.notify_trial_ending(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )
            else:
                AdminNotificationService.notify_trial_ending_no_payment(
                    email=user.email, user_id=user.id, tier=user.subscription_tier
                )

            print(
                f"⏰ Trial ending in 3 days: {user.email} - {user.subscription_tier} - Payment method: {has_payment_method}"
            )

    except Exception as e:
        print(f"Error in handle_trial_will_end: {e}")
    finally:
        db.close()
