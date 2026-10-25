"""Add storage backend

Revision ID: 7b1c9d4e2f10
Revises: e50500e24234
Create Date: 2026-10-25 10:00:00.000000

This migration only adds the column when it is missing, so it can also run
against databases created previously with SQLAlchemy's ``create_all``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7b1c9d4e2f10"
down_revision: Union[str, Sequence[str], None] = "e50500e24234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _file_columns(bind) -> dict:
    inspector = sa.inspect(bind)
    if "files" not in inspector.get_table_names():
        return {}
    return {
        column["name"]: column
        for column in inspector.get_columns("files")
    }


def upgrade() -> None:
    bind = op.get_bind()
    columns = _file_columns(bind)

    if "storage_backend" not in columns:
        op.add_column(
            "files",
            sa.Column(
                "storage_backend",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'local'"),
            ),
        )

    op.execute(
        """
        UPDATE files
        SET storage_backend = 'local'
        WHERE storage_backend IS NULL OR storage_backend = ''
        """
    )


def downgrade() -> None:
    columns = _file_columns(op.get_bind())

    if "storage_backend" in columns:
        with op.batch_alter_table("files") as batch_op:
            batch_op.drop_column("storage_backend")
