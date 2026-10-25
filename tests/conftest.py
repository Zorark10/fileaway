import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.database import Base, get_db
from app.models import File  # noqa: F401 - registers the model with Base

@pytest.fixture
def test_environment(tmp_path, monkeypatch):
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        database_path = (tmp_path / "test.db").as_posix()
        test_database_url = f"sqlite:///{database_path}"

    connect_args = (
        {"check_same_thread": False}
        if test_database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(test_database_url, connect_args=connect_args)
    TestSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(bind=engine)

    test_upload_dir = tmp_path / "uploads"
    test_upload_dir.mkdir()

    monkeypatch.setattr(main_module, "UPLOAD_DIR", test_upload_dir)
    monkeypatch.setattr(main_module, "SessionLocal", TestSessionLocal)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main_module.app.dependency_overrides[get_db] = override_get_db

    try:
        yield TestSessionLocal, test_upload_dir
    finally:
        main_module.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def client(test_environment):
    with TestClient(main_module.app) as test_client:
        yield test_client
