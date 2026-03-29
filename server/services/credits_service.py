from sqlalchemy.orm import Session
from server.core.database import User, Generation


class CreditsService:
    @staticmethod
    def get_user_credits(db: Session, user_id: int):
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
        return user.credits_total - user.credits_used

    @staticmethod
    def create_generation(
        db: Session,
        user_id: int,
        generation_details: dict,
        credits_cost: int,
        status: str = "completed",
        commit: bool = False,
    ) -> Generation:
        generation = Generation(
            user_id=user_id,
            prompt=generation_details.get("prompt"),
            bpm=generation_details.get("bpm"),
            duration=generation_details.get("duration"),
            credits_cost=credits_cost,
            status=status,
            error_message=generation_details.get("error_message"),
        )
        db.add(generation)
        if commit:
            try:
                db.commit()
                db.refresh(generation)
                return generation
            except Exception as e:
                db.rollback()
                raise e

    @staticmethod
    def consume_credits(
        db: Session, user_id: int, amount: int, generation_details: dict
    ) -> bool:
        user = db.query(User).filter(User.id == user_id).with_for_update().first()
        if not user:
            return False
        remaining = user.credits_total - user.credits_used
        if remaining < amount:
            return False
        CreditsService.create_generation(
            db=db,
            user_id=user_id,
            generation_details=generation_details,
            credits_cost=amount,
            status="completed",
            commit=False,
        )
        user.credits_used += amount
        try:
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            raise e

    @staticmethod
    def refill_credits(db: Session, user_id: int, tier: str):
        from server.services.stripe_service import StripeService

        user = db.query(User).filter(User.id == user_id).with_for_update().first()
        if not user:
            return False

        credits = StripeService.TIER_CREDITS.get(tier, 0)
        user.credits_total = credits
        user.credits_used = 0
        user.subscription_tier = tier
        try:
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            raise e

    @staticmethod
    def set_credits(db: Session, user_id: int, credits_amount: int):
        user = db.query(User).filter(User.id == user_id).with_for_update().first()
        if not user:
            return False

        user.credits_total = credits_amount
        user.credits_used = 0

        try:
            db.commit()
            print(f"✅ Set {credits_amount} credits for user {user_id} ({user.email})")
            return True
        except Exception as e:
            db.rollback()
            print(f"❌ Error setting credits for user {user_id}: {e}")
            raise e

    @staticmethod
    def add_credits(db: Session, user_id: int, credits_amount: int):
        user = db.query(User).filter(User.id == user_id).with_for_update().first()
        if not user:
            return False

        user.credits_total += credits_amount

        try:
            db.commit()
            print(
                f"✅ Added {credits_amount} credits to user {user_id} ({user.email}). New total: {user.credits_total}"
            )
            return True
        except Exception as e:
            db.rollback()
            print(f"❌ Error adding credits for user {user_id}: {e}")
            raise e
