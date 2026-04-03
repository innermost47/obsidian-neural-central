"""add is_generating is_disposable to provider

Revision ID: 53a24b186e5c
Revises: a9c0143324b5
Create Date: 2026-04-03 14:04:30.235453

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "53a24b186e5c"
down_revision: Union[str, Sequence[str], None] = "a9c0143324b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("is_generating", sa.Boolean(), nullable=True))
    op.execute("UPDATE providers SET is_generating = FALSE WHERE is_generating IS NULL")
    op.alter_column("providers", "is_generating", nullable=False)

    op.add_column("providers", sa.Column("is_disposable", sa.Boolean(), nullable=True))
    op.execute("UPDATE providers SET is_disposable = TRUE WHERE is_disposable IS NULL")
    op.alter_column("providers", "is_disposable", nullable=False)


def downgrade() -> None:
    op.drop_column("providers", "is_generating")
    op.drop_column("providers", "is_disposable")
