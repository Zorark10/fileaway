from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.models import File
from app.storage import ObjectNotFound, StorageError, StoredObject
from app.upload_tokens import issue_upload_token, verify_upload_token


class FakeStorage:
    prefix = "uploads/"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.upload_lengths: dict[str, int] = {}
        self.last_modified: dict[str, datetime] = {}
        self.presigned_downloads: list[str] = []
        self.delete_failures: set[str] = set()

    def presign_upload(
        self,
        stored_name: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        self.content_types[stored_name] = content_type
        self.upload_lengths[stored_name] = content_length
        return (
            f"https://b2.example.test/{self.prefix}{stored_name}"
            f"?X-Amz-Expires={expires_in}"
        )

    def presign_download(
        self,
        stored_name: str,
        original_name: str,
        content_type: str,
        expires_in: int,
    ) -> str:
        url = f"https://b2.example.test/{self.prefix}{stored_name}?sig=test"
        self.presigned_downloads.append(url)
        return url

    def head_object(self, stored_name: str) -> StoredObject:
        if stored_name not in self.objects:
            raise ObjectNotFound(stored_name)
        return StoredObject(
            key=f"{self.prefix}{stored_name}",
            size=len(self.objects[stored_name]),
            last_modified=self.last_modified.get(
                stored_name,
                datetime.now(timezone.utc),
            ),
        )

    def delete_object(self, stored_name: str) -> None:
        if stored_name in self.delete_failures:
            raise StorageError("simulated delete failure")
        self.objects.pop(stored_name, None)
        self.content_types.pop(stored_name, None)
        self.upload_lengths.pop(stored_name, None)
        self.last_modified.pop(stored_name, None)

    def upload_file(self, path: Path, stored_name: str, content_type: str) -> None:
        self.objects[stored_name] = Path(path).read_bytes()
        self.content_types[stored_name] = content_type
        self.last_modified[stored_name] = datetime.now(timezone.utc)

    def iter_objects(self):
        for stored_name, data in list(self.objects.items()):
            yield StoredObject(
                key=f"{self.prefix}{stored_name}",
                size=len(data),
                last_modified=self.last_modified.get(
                    stored_name,
                    datetime.now(timezone.utc),
                ),
            )

    def delete_keys(self, keys: list[str]) -> int:
        for key in keys:
            if key.startswith(self.prefix):
                self.delete_object(key[len(self.prefix) :])
        return len(keys)


@contextmanager
def b2_client(test_environment, monkeypatch, fake: FakeStorage | None = None):
    TestSessionLocal, upload_dir = test_environment
    storage = fake or FakeStorage()

    monkeypatch.setattr(settings, "storage_backend", "b2")
    monkeypatch.setattr(settings, "upload_signing_secret", "test-signing-secret")
    monkeypatch.setattr(settings, "cleanup_secret", "test-cleanup-secret")
    monkeypatch.setattr(main_module, "get_storage", lambda: storage)

    with TestClient(main_module.app) as client:
        yield client, storage, TestSessionLocal, upload_dir


def _authorize(client, *, name="report.pdf", size=11, content_type="application/pdf"):
    return client.post(
        "/uploads/authorize",
        json={
            "original_name": name,
            "content_type": content_type,
            "file_size": size,
        },
    )


def test_authorize_returns_direct_upload_in_b2_mode(test_environment, monkeypatch):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        _,
        _,
    ):
        response = _authorize(client, size=11)

        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "direct"
        assert data["method"] == "PUT"
        assert data["headers"] == {"Content-Type": "application/pdf"}
        assert data["upload_url"].startswith("https://b2.example.test/uploads/")
        assert data["upload_token"]

        token = verify_upload_token(
            data["upload_token"],
            "test-signing-secret",
        )
        assert token["original_name"] == "report.pdf"
        assert token["content_type"] == "application/pdf"
        assert token["file_size"] == 11
        assert storage.objects == {}
        assert list(storage.upload_lengths.values()) == [11]


def test_authorize_falls_back_to_proxy_in_local_mode(client):
    response = client.post(
        "/uploads/authorize",
        json={
            "original_name": "report.pdf",
            "content_type": "application/pdf",
            "file_size": 11,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "mode": "proxy",
        "max_file_size": 100 * 1024 * 1024,
        "upload_url": None,
        "method": None,
        "headers": None,
        "upload_token": None,
        "upload_expires_at": None,
    }


def test_authorize_rejects_oversized_file(client):
    response = _authorize(client, size=100 * 1024 * 1024 + 1)

    assert response.status_code == 413


def test_complete_direct_upload_and_download_redirect(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        response = _authorize(client, size=11)
        token = response.json()["upload_token"]
        payload = verify_upload_token(token, "test-signing-secret")
        stored_name = payload["stored_name"]

        storage.objects[stored_name] = b"hello world"

        completed = client.post(
            "/uploads/complete",
            json={"upload_token": token},
        )

        assert completed.status_code == 200
        data = completed.json()
        assert data["original_name"] == "report.pdf"
        assert data["content_type"] == "application/pdf"
        assert data["file_size"] == 11
        assert data["share_url"].endswith(
            f"/files/share/{data['share_token']}"
        )

        db = TestSessionLocal()
        try:
            row = db.query(File).one()
            assert row.stored_name == stored_name
            assert row.storage_backend == "b2"
        finally:
            db.close()

        download = client.get(
            f"/files/share/{data['share_token']}",
            follow_redirects=False,
        )

        assert download.status_code == 302
        assert download.headers["location"] == storage.presigned_downloads[-1]
        assert download.headers["cache-control"] == "no-store"

        duplicate = client.post(
            "/uploads/complete",
            json={"upload_token": token},
        )
        assert duplicate.status_code == 409


def test_complete_rejects_missing_object_without_creating_record(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        _,
        TestSessionLocal,
        _,
    ):
        token = _authorize(client, size=11).json()["upload_token"]

        completed = client.post(
            "/uploads/complete",
            json={"upload_token": token},
        )

        assert completed.status_code == 404

        db = TestSessionLocal()
        try:
            assert db.query(File).count() == 0
        finally:
            db.close()


def test_complete_rejects_size_mismatch_and_removes_object(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        response = _authorize(client, size=11)
        token = response.json()["upload_token"]
        stored_name = verify_upload_token(
            token,
            "test-signing-secret",
        )["stored_name"]
        storage.objects[stored_name] = b"short"

        completed = client.post(
            "/uploads/complete",
            json={"upload_token": token},
        )

        assert completed.status_code == 400
        assert stored_name not in storage.objects

        db = TestSessionLocal()
        try:
            assert db.query(File).count() == 0
        finally:
            db.close()


def test_complete_rejects_oversized_stored_object(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        _,
        _,
    ):
        monkeypatch.setattr(main_module, "MAX_FILE_SIZE", 10)
        token = issue_upload_token(
            {
                "stored_name": "oversized-object",
                "original_name": "big.bin",
                "content_type": "application/octet-stream",
                "file_size": 10,
            },
            secret="test-signing-secret",
            ttl_seconds=600,
        )
        storage.objects["oversized-object"] = b"A" * 11

        completed = client.post(
            "/uploads/complete",
            json={"upload_token": token},
        )

        assert completed.status_code == 413
        assert "oversized-object" not in storage.objects


def test_oversized_upload_delete_failure_is_retried_by_orphan_cleanup(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        monkeypatch.setattr(main_module, "MAX_FILE_SIZE", 10)
        monkeypatch.setattr(settings, "orphan_grace_seconds", 0)
        token = issue_upload_token(
            {
                "stored_name": "oversized-retry",
                "original_name": "big.bin",
                "content_type": "application/octet-stream",
                "file_size": 10,
            },
            secret="test-signing-secret",
            ttl_seconds=600,
        )
        storage.objects["oversized-retry"] = b"A" * 11
        storage.last_modified["oversized-retry"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        storage.delete_failures.add("oversized-retry")

        completed = client.post(
            "/uploads/complete",
            json={"upload_token": token},
        )

        assert completed.status_code == 413
        assert "oversized-retry" in storage.objects

        db = TestSessionLocal()
        try:
            assert db.query(File).count() == 0
        finally:
            db.close()

        storage.delete_failures.clear()
        cleanup = client.post(
            "/internal/cleanup",
            headers={"X-Cleanup-Secret": "test-cleanup-secret"},
        )

        assert cleanup.status_code == 200
        assert "oversized-retry" not in storage.objects


def test_complete_rejects_expired_and_invalid_tokens(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        _,
        _,
        _,
    ):
        expired_token = issue_upload_token(
            {
                "stored_name": "expired-object",
                "original_name": "old.bin",
                "content_type": "application/octet-stream",
                "file_size": 1,
            },
            secret="test-signing-secret",
            ttl_seconds=-1,
        )

        expired = client.post(
            "/uploads/complete",
            json={"upload_token": expired_token},
        )
        invalid = client.post(
            "/uploads/complete",
            json={"upload_token": "not-a-token"},
        )
        malformed = client.post(
            "/uploads/complete",
            json={"upload_token": "eA.@@@"},
        )

        assert expired.status_code == 410
        assert invalid.status_code == 400
        assert malformed.status_code == 400


def test_cleanup_endpoint_removes_expired_b2_object(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        db = TestSessionLocal()
        try:
            db.add(
                File(
                    original_name="expired.txt",
                    stored_name="expired-b2",
                    share_token="expired-b2-token",
                    content_type="text/plain",
                    file_size=7,
                    storage_backend="b2",
                    uploaded_at=datetime.now(timezone.utc) - timedelta(days=2),
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
            db.commit()
        finally:
            db.close()

        storage.objects["expired-b2"] = b"expired"

        unauthorized = client.post(
            "/internal/cleanup",
            headers={"X-Cleanup-Secret": "wrong"},
        )
        response = client.post(
            "/internal/cleanup",
            headers={"X-Cleanup-Secret": "test-cleanup-secret"},
        )

        assert unauthorized.status_code == 401
        assert response.status_code == 200
        assert response.json()["expired_removed"] == 1
        assert "expired-b2" not in storage.objects

        db = TestSessionLocal()
        try:
            assert db.query(File).count() == 0
        finally:
            db.close()


def test_expired_b2_link_is_rejected_before_presigning(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        db = TestSessionLocal()
        try:
            db.add(
                File(
                    original_name="expired.txt",
                    stored_name="expired-download",
                    share_token="expired-download-token",
                    content_type="text/plain",
                    file_size=1,
                    storage_backend="b2",
                    uploaded_at=datetime.now(timezone.utc) - timedelta(days=2),
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            "/files/share/expired-download-token",
            follow_redirects=False,
        )

        assert response.status_code == 410
        assert storage.presigned_downloads == []


def test_cleanup_failure_is_observable_and_keeps_row(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        db = TestSessionLocal()
        try:
            db.add(
                File(
                    original_name="stuck.txt",
                    stored_name="stuck-b2",
                    share_token="stuck-b2-token",
                    content_type="text/plain",
                    file_size=5,
                    storage_backend="b2",
                    uploaded_at=datetime.now(timezone.utc) - timedelta(days=2),
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
            db.commit()
        finally:
            db.close()

        storage.objects["stuck-b2"] = b"stuck"
        storage.delete_failures.add("stuck-b2")

        response = client.post(
            "/internal/cleanup",
            headers={"X-Cleanup-Secret": "test-cleanup-secret"},
        )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["expired_failed"] == 1
        assert detail["expired_removed"] == 0
        assert "stuck-b2" in storage.objects

        db = TestSessionLocal()
        try:
            assert (
                db.query(File)
                .filter(File.share_token == "stuck-b2-token")
                .count()
                == 1
            )
        finally:
            db.close()


def test_cleanup_removes_only_old_orphans(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        _,
    ):
        db = TestSessionLocal()
        try:
            db.add(
                File(
                    original_name="keep.txt",
                    stored_name="referenced-b2",
                    share_token="referenced-token",
                    content_type="text/plain",
                    file_size=4,
                    storage_backend="b2",
                    uploaded_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            )
            db.commit()
        finally:
            db.close()

        now = datetime.now(timezone.utc)
        storage.objects["referenced-b2"] = b"keep"
        storage.last_modified["referenced-b2"] = now
        storage.objects["old-orphan"] = b"orphan"
        storage.last_modified["old-orphan"] = now - timedelta(hours=2)
        storage.objects["new-orphan"] = b"pending"
        storage.last_modified["new-orphan"] = now

        response = client.post(
            "/internal/cleanup",
            headers={"X-Cleanup-Secret": "test-cleanup-secret"},
        )

        assert response.status_code == 200
        assert response.json()["orphans_removed"] == 1
        assert "referenced-b2" in storage.objects
        assert "old-orphan" not in storage.objects
        assert "new-orphan" in storage.objects


def test_cleanup_endpoint_requires_configured_secret(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (client, _, _, _):
        monkeypatch.setattr(settings, "cleanup_secret", "")

        response = client.post("/internal/cleanup")

        assert response.status_code == 503


def test_proxy_upload_to_b2_uses_temporary_local_file(
    test_environment,
    monkeypatch,
):
    with b2_client(test_environment, monkeypatch) as (
        client,
        storage,
        TestSessionLocal,
        upload_dir,
    ):
        response = client.post(
            "/upload",
            files={
                "uploaded_file": (
                    "proxy.txt",
                    b"proxied content",
                    "text/plain",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["file_size"] == len(b"proxied content")

        db = TestSessionLocal()
        try:
            row = db.query(File).one()
        finally:
            db.close()

        assert row.storage_backend == "b2"
        assert storage.objects[row.stored_name] == b"proxied content"
        assert list(upload_dir.iterdir()) == []
