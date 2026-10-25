"""Add content type and file size

Revision ID: e50500e24234
Revises: 2d2a86cdbee5
Create Date: 2026-10-13 20:10:28.608315

This migration only adds columns that are missing, so it can also run against
databases created previously with SQLAlchemy's ``create_all``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e50500e24234'
down_revision: Union[str, Sequence[str], None] = '2d2a86cdbee5'
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

    if "content_type" not in columns:
        op.add_column(
            "files",
            sa.Column(
                "content_type",
                sa.String(length=255),
                nullable=True,
            ),
        )

    if "file_size" not in columns:
        op.add_column(
            "files",
            sa.Column("file_size", sa.Integer(), nullable=True),
        )

    op.execute(
        """
        UPDATE files
        SET content_type = 'application/octet-stream'
        WHERE content_type IS NULL
        """
    )
    op.execute(
        """
        UPDATE files
        SET file_size = 0
        WHERE file_size IS NULL
        """
    )

    columns = _file_columns(bind)

    if columns["content_type"]["nullable"]:
        with op.batch_alter_table("files") as batch_op:
            batch_op.alter_column(
                "content_type",
                existing_type=sa.String(length=255),
                nullable=False,
            )

    if columns["file_size"]["nullable"]:
        with op.batch_alter_table("files") as batch_op:
            batch_op.alter_column(
                "file_size",
                existing_type=sa.Integer(),
                nullable=False,
            )


def downgrade() -> None:
    columns = _file_columns(op.get_bind())

    with op.batch_alter_table("files") as batch_op:
        if "file_size" in columns:
            batch_op.drop_column("file_size")
        if "content_type" in columns:
            batch_op.drop_column("content_type")
