from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models import File

ROOT_DIR = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    return Config(str(ROOT_DIR / "alembic.ini"))


def _upgrade(database_url: str, monkeypatch):
    monkeypatch.setattr(settings, "database_url", database_url)
    command.upgrade(_alembic_config(), "head")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _column_names(engine) -> set[str]:
    return {
        column["name"]
        for column in inspect(engine).get_columns("files")
    }


def test_migrations_create_fresh_schema(tmp_path, monkeypatch):
    database_url = _sqlite_url(tmp_path / "fresh.db")
    _upgrade(database_url, monkeypatch)

    engine = create_engine(database_url)
    try:
        columns = _column_names(engine)
        assert columns == {
            "id",
            "original_name",
            "stored_name",
            "share_token",
            "uploaded_at",
            "expires_at",
            "content_type",
            "file_size",
        }

        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == "e50500e24234"
    finally:
        engine.dispose()


def test_migrations_preserve_legacy_rows(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    database_url = _sqlite_url(database_path)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE files (
                        id INTEGER NOT NULL PRIMARY KEY,
                        original_name VARCHAR(255) NOT NULL,
                        stored_name VARCHAR(255) NOT NULL UNIQUE,
                        uploaded_at DATETIME NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO files
                        (id, original_name, stored_name, uploaded_at)
                    VALUES
                        (1, 'legacy.txt', 'legacy-stored', :uploaded_at)
                    """
                ),
                {"uploaded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
            )
    finally:
        engine.dispose()

    _upgrade(database_url, monkeypatch)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT original_name, share_token, expires_at,
                           content_type, file_size
                    FROM files
                    WHERE id = 1
                    """
                )
            ).mappings().one()

        assert row["original_name"] == "legacy.txt"
        assert row["share_token"]
        assert row["expires_at"] is not None
        assert row["content_type"] == "application/octet-stream"
        assert row["file_size"] == 0

        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("files")
        }
        assert not columns["share_token"]["nullable"]
        assert not columns["expires_at"]["nullable"]
        assert not columns["content_type"]["nullable"]
        assert not columns["file_size"]["nullable"]
    finally:
        engine.dispose()


def test_migrations_widen_legacy_short_token_column(tmp_path, monkeypatch):
    database_path = tmp_path / "short-token.db"
    database_url = _sqlite_url(database_path)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE files (
                        id INTEGER NOT NULL PRIMARY KEY,
                        original_name VARCHAR(255) NOT NULL,
                        stored_name VARCHAR(255) NOT NULL UNIQUE,
                        share_token VARCHAR(36) NOT NULL UNIQUE,
                        uploaded_at DATETIME NOT NULL,
                        expires_at DATETIME NOT NULL,
                        content_type VARCHAR(255) NOT NULL,
                        file_size INTEGER NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO files
                        (id, original_name, stored_name, share_token,
                         uploaded_at, expires_at, content_type, file_size)
                    VALUES
                        (1, 'short.txt', 'short-stored', 'old-token',
                         :uploaded_at, :expires_at, 'text/plain', 10)
                    """
                ),
                {
                    "uploaded_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "expires_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                },
            )
    finally:
        engine.dispose()

    _upgrade(database_url, monkeypatch)

    engine = create_engine(database_url)
    try:
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("files")
        }
        assert columns["share_token"]["type"].length == 255

        with engine.connect() as connection:
            token = connection.execute(
                text("SELECT share_token FROM files")
            ).scalar_one()
        assert token == "old-token"
    finally:
        engine.dispose()


def test_migrations_accept_existing_current_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "current.db"
    database_url = _sqlite_url(database_path)

    engine = create_engine(database_url)
    TestSessionLocal = sessionmaker(bind=engine)
    try:
        Base.metadata.create_all(bind=engine)
        db = TestSessionLocal()
        try:
            db.add(
                File(
                    original_name="current.txt",
                    stored_name="current-stored",
                    share_token="current-token",
                    content_type="text/plain",
                    file_size=12,
                    uploaded_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
        finally:
            db.close()
    finally:
        engine.dispose()

    _upgrade(database_url, monkeypatch)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM files")
            ).scalar_one()
            token = connection.execute(
                text("SELECT share_token FROM files")
            ).scalar_one()

        assert count == 1
        assert token == "current-token"
        assert _column_names(engine) == {
            "id",
            "original_name",
            "stored_name",
            "share_token",
            "uploaded_at",
            "expires_at",
            "content_type",
            "file_size",
        }
    finally:
        engine.dispose()
