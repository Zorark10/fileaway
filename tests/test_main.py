from datetime import datetime, timedelta, timezone
from app.models import File



def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200


def test_upload_file(client):
    response = client.post(
        "/upload",
        files={
            "uploaded_file": (
                "test.txt",
                b"Hello FileAway!",
                "text/plain",
            )
        },
    )

    assert response.status_code == 200

    data = response.json()
    assert data["original_name"] == "test.txt"
    assert data["content_type"] == "text/plain"
    assert data["file_size"] == len(b"Hello FileAway!")
    assert data["share_token"]
    assert data["share_url"]


def test_download_shared_file(client):
    upload_response = client.post(
        "/upload",
        files={
            "uploaded_file": (
                "download_test.txt",
                b"FileAway download test",
                "text/plain",
            )
        },
    )

    assert upload_response.status_code == 200

    share_token = upload_response.json()["share_token"]

    download_response = client.get(
        f"/files/share/{share_token}"
    )

    assert download_response.status_code == 200
    assert download_response.content == b"FileAway download test"
    assert "download_test.txt" in download_response.headers[
        "content-disposition"
    ]


def test_invalid_share_token(client):
    response = client.get(
        "/files/share/invalid-token-that-does-not-exist"
    )

    assert response.status_code == 404


def test_expired_share_link(client, test_environment):
    TestSessionLocal, _ = test_environment
    token = "test-expired-token"

    db = TestSessionLocal()
    try:
        expired_file = File(
            original_name="expired.txt",
            stored_name="expired-test-file",
            share_token=token,
            content_type="text/plain",
            file_size=10,
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=2),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        db.add(expired_file)
        db.commit()
    finally:
        db.close()

    response = client.get(f"/files/share/{token}")

    assert response.status_code == 410


def test_upload_file_too_large(client, monkeypatch):
    from app.main import MAX_FILE_SIZE

    monkeypatch.setattr("app.main.MAX_FILE_SIZE", 10)

    response = client.post(
        "/upload",
        files={
            "uploaded_file": (
                "large.txt",
                b"A" * 11,
                "text/plain",
            )
        },
    )

    assert response.status_code == 413
