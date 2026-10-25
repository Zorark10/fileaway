"""add share token and expiry

Revision ID: 2d2a86cdbee5
Revises:
Create Date: 2026-10-12 17:56:53.590088

This migration is intentionally database-agnostic. It also creates the base
``files`` table when upgrading an empty database, because no earlier migration
exists in this repository.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d2a86cdbee5'
down_revision: Union[str, Sequence[str], None] = None
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


def _has_unique_share_token(bind) -> bool:
    inspector = sa.inspect(bind)

    for constraint in inspector.get_unique_constraints("files"):
        if "share_token" in (constraint.get("column_names") or []):
            return True

    for index in inspector.get_indexes("files"):
        if index.get("unique") and "share_token" in (
            index.get("column_names") or []
        ):
            return True

    return False


def upgrade() -> None:
    """Upgrade schema, preserving rows in pre-existing databases."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = inspector.get_table_names()

    if "files" not in table_names:
        op.create_table(
            "files",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("stored_name", sa.String(length=255), nullable=False),
            sa.Column(
                "uploaded_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.UniqueConstraint("stored_name"),
        )

    columns = _file_columns(bind)

    if "share_token" not in columns:
        op.add_column(
            "files",
            sa.Column("share_token", sa.String(length=255), nullable=True),
        )

    if "expires_at" not in columns:
        op.add_column(
            "files",
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )

    files = sa.table(
        "files",
        sa.column("id", sa.Integer()),
        sa.column("share_token", sa.String(length=255)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("uploaded_at", sa.DateTime(timezone=True)),
    )

    rows = bind.execute(
        sa.select(
            files.c.id,
            files.c.share_token,
            files.c.expires_at,
            files.c.uploaded_at,
        ).where(
            sa.or_(
                files.c.share_token.is_(None),
                files.c.expires_at.is_(None),
            )
        )
    ).fetchall()

    now = datetime.now(timezone.utc)
    for file_id, share_token, expires_at, uploaded_at in rows:
        values = {}
        if share_token is None:
            values["share_token"] = str(uuid.uuid4())

        if expires_at is None:
            base_time = uploaded_at or now
            if base_time.tzinfo is None:
                base_time = base_time.replace(tzinfo=timezone.utc)
            values["expires_at"] = base_time + timedelta(hours=24)

        if values:
            bind.execute(
                files.update()
                .where(files.c.id == file_id)
                .values(**values)
            )

    columns = _file_columns(bind)

    share_token_type = columns["share_token"]["type"]
    share_token_length = getattr(share_token_type, "length", None)
    needs_wider_token = (
        share_token_length is not None and share_token_length < 255
    )

    if columns["share_token"]["nullable"] or needs_wider_token:
        with op.batch_alter_table("files") as batch_op:
            batch_op.alter_column(
                "share_token",
                existing_type=share_token_type,
                type_=sa.String(length=255),
                nullable=False,
            )

    if columns["expires_at"]["nullable"]:
        with op.batch_alter_table("files") as batch_op:
            batch_op.alter_column(
                "expires_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )

    if not _has_unique_share_token(bind):
        op.create_index(
            "ux_files_share_token",
            "files",
            ["share_token"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _file_columns(bind)

    if not columns:
        return

    for constraint in inspector.get_unique_constraints("files"):
        if (
            "share_token" in (constraint.get("column_names") or [])
            and constraint.get("name")
        ):
            op.drop_constraint(
                constraint["name"],
                "files",
                type_="unique",
            )

    for index in inspector.get_indexes("files"):
        if (
            index.get("unique")
            and "share_token" in (index.get("column_names") or [])
            and index.get("name")
            and not index["name"].startswith("sqlite_autoindex")
        ):
            op.drop_index(index["name"], table_name="files")

    with op.batch_alter_table("files") as batch_op:
        if "expires_at" in columns:
            batch_op.drop_column("expires_at")
        if "share_token" in columns:
            batch_op.drop_column("share_token")
