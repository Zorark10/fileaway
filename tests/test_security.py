from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import MAX_FILE_SIZE, settings
from app.main import cleanup_expired_files
from app.models import File


def _upload(client, filename="test.txt", content=b"hello"):
    return client.post(
        "/upload",
        files={
            "uploaded_file": (
                filename,
                content,
                "text/plain",
            )
        },
    )


def test_max_file_size_is_100_mb():
    assert MAX_FILE_SIZE == 100 * 1024 * 1024


def test_share_url_uses_public_base_url(client, monkeypatch):
    monkeypatch.setattr(
        settings,
        "public_base_url",
        "https://files.example.test",
    )

    response = _upload(client)

    assert response.status_code == 200
    assert response.json()["share_url"].startswith(
        "https://files.example.test/files/share/"
    )


def test_public_file_listing_endpoints_are_absent(client):
    assert client.get("/files").status_code == 404
    assert client.post("/files", json={}).status_code == 404

    paths = client.get("/openapi.json").json()["paths"]
    assert "/files" not in paths
    assert "/files/share/{share_token}" in paths


def test_upload_filename_is_sanitized(client):
    response = _upload(client, filename="../../evil.txt")

    assert response.status_code == 200
    assert response.json()["original_name"] == "evil.txt"


def test_oversized_upload_is_rejected_and_partial_file_removed(
    client, test_environment, monkeypatch
):
    _, upload_dir = test_environment
    monkeypatch.setattr("app.main.MAX_FILE_SIZE", 10)

    response = _upload(client, content=b"A" * 11)

    assert response.status_code == 413
    assert list(upload_dir.iterdir()) == []


def test_traversal_in_stored_name_is_not_served(client, test_environment):
    TestSessionLocal, _ = test_environment
    db = TestSessionLocal()
    try:
        db.add(
            File(
                original_name="passwd",
                stored_name="../../etc/passwd",
                share_token="traversal-token",
                content_type="text/plain",
                file_size=1,
                uploaded_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/files/share/traversal-token")

    assert response.status_code == 404


def test_cleanup_removes_expired_row_and_file(
    client, test_environment
):
    TestSessionLocal, upload_dir = test_environment
    expired_path = upload_dir / "cleanup-me"
    expired_path.write_bytes(b"expired")

    db = TestSessionLocal()
    try:
        db.add(
            File(
                original_name="expired.txt",
                stored_name="cleanup-me",
                share_token="cleanup-token",
                content_type="text/plain",
                file_size=7,
                uploaded_at=datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db.add(
            File(
                original_name="keep.txt",
                stored_name="keep-me",
                share_token="keep-token",
                content_type="text/plain",
                file_size=4,
                uploaded_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()

        removed = cleanup_expired_files(db)

        assert removed == 1
        assert db.query(File).filter(File.share_token == "cleanup-token").count() == 0
        assert db.query(File).filter(File.share_token == "keep-token").count() == 1
    finally:
        db.close()

    assert not expired_path.exists()


def test_cleanup_retries_when_file_delete_fails(
    client, test_environment, monkeypatch
):
    TestSessionLocal, upload_dir = test_environment
    stuck_path = upload_dir / "stuck-file"
    stuck_path.write_bytes(b"stuck")

    db = TestSessionLocal()
    try:
        db.add(
            File(
                original_name="stuck.txt",
                stored_name="stuck-file",
                share_token="stuck-token",
                content_type="text/plain",
                file_size=5,
                uploaded_at=datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db.commit()

        original_unlink = Path.unlink

        def failing_unlink(self, missing_ok=False):
            if self.name == "stuck-file":
                raise OSError("simulated failure")
            return original_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", failing_unlink)
        removed = cleanup_expired_files(db)

        assert removed == 0
        assert db.query(File).filter(File.share_token == "stuck-token").count() == 1
    finally:
        db.close()

    assert stuck_path.exists()


def test_cors_allows_configured_origin_and_rejects_others(client):
    allowed_origin = settings.cors_origins[0]
    allowed = client.options(
        "/upload",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == allowed_origin

    rejected = client.options(
        "/upload",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
