"""added broadcast email

Revision ID: df3813696489
Revises: 2e20053ec716
Create Date: 2025-12-04 16:22:17.135698

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "df3813696489"
down_revision: Union[str, Sequence[str], None] = "2e20053ec716"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "broadcast_emails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("recipients_count", sa.Integer(), nullable=True, default=0),
        sa.Column("sent_count", sa.Integer(), nullable=True, default=0),
        sa.Column("failed_count", sa.Integer(), nullable=True, default=0),
        sa.Column("sent_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["sent_by_admin_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_broadcast_emails_id"), "broadcast_emails", ["id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_broadcast_emails_id"), table_name="broadcast_emails")
    op.drop_table("broadcast_emails")
