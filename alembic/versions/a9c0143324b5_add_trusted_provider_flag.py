"""add trusted provider flag
Revision ID: a9c0143324b5
Revises: 31f39f0a9895
Create Date: 2026-04-03 13:54:59.440516
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "a9c0143324b5"
down_revision: Union[str, Sequence[str], None] = "31f39f0a9895"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verification_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.String(length=255), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("encrypted_fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prompt", "seed", "model", name="_sample_prompt_seed_model_uc"
        ),
    )
    op.create_index("idx_sample_model", "verification_samples", ["model"], unique=False)
    op.create_index(
        op.f("ix_verification_samples_created_at"),
        "verification_samples",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verification_samples_id"), "verification_samples", ["id"], unique=False
    )

    op.add_column("providers", sa.Column("is_trusted", sa.Boolean(), nullable=True))
    op.execute("UPDATE providers SET is_trusted = FALSE WHERE is_trusted IS NULL")
    op.alter_column("providers", "is_trusted", nullable=False)


def downgrade() -> None:
    op.drop_column("providers", "is_trusted")
    op.drop_index(op.f("ix_verification_samples_id"), table_name="verification_samples")
    op.drop_index(
        op.f("ix_verification_samples_created_at"), table_name="verification_samples"
    )
    op.drop_index("idx_sample_model", table_name="verification_samples")
    op.drop_table("verification_samples")
