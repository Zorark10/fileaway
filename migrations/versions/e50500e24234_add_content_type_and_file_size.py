"""Add content type and file size

Revision ID: e50500e24234
Revises: 2d2a86cdbee5
Create Date: 2026-10-13 20:10:28.608315

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e50500e24234'
down_revision: Union[str, Sequence[str], None] = '2d2a86cdbee5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the columns as nullable first
    op.add_column(
        'files',
        sa.Column('content_type', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'files',
        sa.Column('file_size', sa.Integer(), nullable=True)
    )

    # Fill values for existing records
    op.execute("""
        UPDATE files
        SET content_type = 'application/octet-stream',
            file_size = 0
        WHERE content_type IS NULL OR file_size IS NULL
    """)

    # Make both columns required
    op.alter_column(
        'files', 'content_type',
        existing_type=sa.String(length=255),
        nullable=False
    )
    op.alter_column(
        'files', 'file_size',
        existing_type=sa.Integer(),
        nullable=False
    )


def downgrade() -> None:
    op.drop_column('files', 'file_size')
    op.drop_column('files', 'content_type')
