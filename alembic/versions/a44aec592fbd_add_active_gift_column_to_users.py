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
    pass


def downgrade():
    pass
