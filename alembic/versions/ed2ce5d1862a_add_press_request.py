"""add press request

Revision ID: ed2ce5d1862a
Revises: df3813696489
Create Date: 2026-03-26 17:07:55.117209

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ed2ce5d1862a"
down_revision: Union[str, Sequence[str], None] = "df3813696489"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the table first
    op.create_table(
        "press_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Use batch_alter_table for indexes (Best practice for SQLite compatibility)
    with op.batch_alter_table("press_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_press_requests_email"), ["email"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_press_requests_id"), ["id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_press_requests_token"), ["token"], unique=True
        )


def downgrade() -> None:
    # Use batch_alter_table to drop indexes before dropping the table
    with op.batch_alter_table("press_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_press_requests_token"))
        batch_op.drop_index(batch_op.f("ix_press_requests_id"))
        batch_op.drop_index(batch_op.f("ix_press_requests_email"))

    op.drop_table("press_requests")
