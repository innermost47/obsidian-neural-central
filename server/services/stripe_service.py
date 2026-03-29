import stripe
from datetime import datetime
import secrets
from server.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY


class StripeService:

    @staticmethod
    def get_trial_credits(tier: str) -> int:
        return settings.TRIAL_CONFIG["credits"].get(tier, 10)

    @staticmethod
    def calculate_gift_price(tier: str, duration_months: int) -> int:
        monthly_price = settings.TIER_PRICES.get(tier)
        if not monthly_price:
            return 0
        return monthly_price * duration_months

    @staticmethod
    def create_customer(email: str, user_id: int):
        customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})
        return customer.id

    @staticmethod
    def create_checkout_session(
        customer_id: str, price_id: str, user_id: int, tier: str
    ):
        trial_days = settings.TRIAL_CONFIG["duration_days"]
        trial_credits = StripeService.get_trial_credits(tier)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            mode="subscription",
            subscription_data={
                "trial_period_days": trial_days,
                "trial_settings": {
                    "end_behavior": {"missing_payment_method": "cancel"}
                },
                "metadata": {
                    "trial_credits": str(trial_credits),
                },
            },
            payment_method_collection=settings.TRIAL_CONFIG["payment_method"],
            success_url=f"{settings.FRONTEND_URL}/dashboard.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.FRONTEND_URL}/dashboard.html",
            metadata={
                "user_id": user_id,
                "tier": tier,
                "trial_credits": str(trial_credits),
            },
            allow_promotion_codes=True,
        )
        return session

    @staticmethod
    def generate_gift_code():
        return f"OBSIDIAN-{secrets.token_urlsafe(12).upper()[:12]}"

    @staticmethod
    def create_gift_checkout_session(
        purchaser_email: str,
        purchaser_user_id: int,
        recipient_email: str,
        tier: str,
        duration_months: int,
        gift_code: str,
        gift_message: str = None,
        activation_date: datetime = None,
    ):
        amount = StripeService.calculate_gift_price(tier, duration_months)
        if amount == 0:
            raise ValueError(f"Invalid tier '{tier}' or duration '{duration_months}'")

        tier_display = tier.capitalize()

        session = stripe.checkout.Session.create(
            customer_email=purchaser_email,
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "unit_amount": amount,
                        "product_data": {
                            "name": f"🎁 OBSIDIAN Neural Gift - {tier_display}",
                            "description": f"Subscription to {tier_display} for {duration_months} months for {recipient_email}",
                        },
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=f"{settings.FRONTEND_URL}/gift-success.html?gift_code={gift_code}",
            cancel_url=f"{settings.FRONTEND_URL}/gift.html",
            metadata={
                "type": "gift_subscription",
                "purchaser_user_id": str(purchaser_user_id),
                "purchaser_email": purchaser_email,
                "recipient_email": recipient_email,
                "tier": tier,
                "duration_months": str(duration_months),
                "gift_code": gift_code,
                "gift_message": gift_message or "",
                "activation_date": (activation_date or datetime.utcnow()).isoformat(),
            },
        )
        return session

    @staticmethod
    def get_subscription(subscription_id: str):
        return stripe.Subscription.retrieve(subscription_id)

    @staticmethod
    def cancel_subscription(subscription_id: str):
        try:
            subscription = stripe.Subscription.delete(subscription_id)
            return subscription
        except Exception as e:
            print(f"Stripe error: {e}")
            raise Exception(f"Failed to cancel subscription: {str(e)}")

    @staticmethod
    def construct_webhook_event(payload: bytes, sig_header: str):
        return stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )

    @staticmethod
    def create_customer_portal_session(customer_id: str):
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{settings.FRONTEND_URL}/dashboard.html",
        )
        return session
