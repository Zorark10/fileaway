
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import File  # Ensure the model is registered with Base


@pytest.fixture
def test_environment(tmp_path, monkeypatch):
    test_database_url = os.getenv("TEST_DATABASE_URL")

    if not test_database_url:
        pytest.fail("Set TEST_DATABASE_URL in your .env first")

    engine = create_engine(test_database_url)
    TestSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(bind=engine)

    test_upload_dir = tmp_path / "uploads"
    test_upload_dir.mkdir()

    monkeypatch.setattr("app.main.UPLOAD_DIR", test_upload_dir)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        yield TestSessionLocal, test_upload_dir
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def client(test_environment):
    with TestClient(app) as test_client:
        yield test_client
