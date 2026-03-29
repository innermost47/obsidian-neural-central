"""add active_gift_column to users

Revision ID: a44aec592fbd
Revises: 24f94ba4ada1
Create Date: 2025-11-28 14:22:54.290421

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a44aec592fbd"
down_revision: Union[str, Sequence[str], None] = "24f94ba4ada1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Add column to users table
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("active_gift_subscription_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_users_active_gift",
            "gift_subscriptions",
            ["active_gift_subscription_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("fk_users_active_gift", type_="foreignkey")
        batch_op.drop_column("active_gift_subscription_id")
