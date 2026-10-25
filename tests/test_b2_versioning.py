from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import MAX_FILE_SIZE
from app.storage import (
    B2Storage,
    PartialDeletionError,
    StorageError,
)


class FakePaginator:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.kwargs = None

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.pages)


class FakeS3Client:
    def __init__(
        self,
        pages: list[dict],
        *,
        fail_keys: set[str] | None = None,
    ) -> None:
        self.pages = pages
        self.fail_keys = fail_keys or set()
        self.delete_calls: list[list[dict]] = []

    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "list_object_versions"
        return FakePaginator(self.pages)

    def delete_objects(self, *, Bucket, Delete):
        assert Bucket == "test-bucket"
        self.delete_calls.append(Delete["Objects"])
        errors = [
            {
                "Key": item["Key"],
                "VersionId": item["VersionId"],
                "Code": "InternalError",
                "Message": "simulated failure",
            }
            for item in Delete["Objects"]
            if item["Key"] in self.fail_keys
        ]
        return {"Errors": errors}


def _storage_with(client: FakeS3Client) -> B2Storage:
    storage = object.__new__(B2Storage)
    storage.bucket = "test-bucket"
    storage.prefix = "uploads/"
    storage.client = client
    return storage


def _version(key: str, version_id: str, when: datetime) -> dict:
    return {
        "Key": key,
        "VersionId": version_id,
        "Size": 10,
        "LastModified": when,
    }


def test_delete_object_permanently_deletes_all_versions():
    now = datetime.now(timezone.utc)
    client = FakeS3Client(
        [
            {
                "Versions": [
                    _version("uploads/thing", "v1", now - timedelta(hours=2)),
                    _version("uploads/thing2", "other", now),
                ],
                "DeleteMarkers": [
                    _version("uploads/thing", "v2", now - timedelta(hours=1)),
                ],
            }
        ]
    )

    _storage_with(client).delete_object("thing")

    deleted = {
        (item["Key"], item["VersionId"])
        for chunk in client.delete_calls
        for item in chunk
    }
    assert deleted == {
        ("uploads/thing", "v1"),
        ("uploads/thing", "v2"),
    }


def test_delete_keys_reports_partial_failure_without_counting_failed_key():
    now = datetime.now(timezone.utc)
    client = FakeS3Client(
        [
            {
                "Versions": [
                    _version("uploads/ok", "v1", now),
                    _version("uploads/bad", "v2", now),
                ],
                "DeleteMarkers": [],
            }
        ],
        fail_keys={"uploads/bad"},
    )

    with pytest.raises(PartialDeletionError) as error:
        _storage_with(client).delete_keys(["uploads/ok", "uploads/bad"])

    assert error.value.deleted == 1
    assert error.value.failed_keys == {"uploads/bad"}
    assert error.value.errors[0]["Key"] == "uploads/bad"
    assert sum(len(chunk) for chunk in client.delete_calls) == 2


def test_delete_keys_succeeds_and_counts_only_deleted_keys():
    now = datetime.now(timezone.utc)
    client = FakeS3Client(
        [
            {
                "Versions": [
                    _version("uploads/a", "v1", now),
                    _version("uploads/b", "v2", now),
                ],
                "DeleteMarkers": [],
            }
        ]
    )

    deleted = _storage_with(client).delete_keys(
        ["uploads/a", "uploads/b", "uploads/missing"]
    )

    assert deleted == 2
    assert len(client.delete_calls) == 1


def test_missing_versions_are_already_deleted():
    client = FakeS3Client([{"Versions": [], "DeleteMarkers": []}])

    assert _storage_with(client).delete_keys(["uploads/missing"]) == 0
    assert client.delete_calls == []


def test_iter_objects_includes_hidden_delete_markers():
    now = datetime.now(timezone.utc)
    hidden_time = now - timedelta(hours=2)
    client = FakeS3Client(
        [
            {
                "Versions": [
                    _version("uploads/hidden", "v1", now - timedelta(hours=3)),
                ],
                "DeleteMarkers": [
                    _version("uploads/hidden", "v2", hidden_time),
                ],
            }
        ]
    )

    objects = list(_storage_with(client).iter_objects())

    assert len(objects) == 1
    assert objects[0].key == "uploads/hidden"
    assert objects[0].last_modified == hidden_time


def test_presigned_upload_signs_exact_content_length():
    storage = B2Storage(
        "https://s3.us-west-004.backblazeb2.com",
        "us-west-004",
        "test-bucket",
        "key-id",
        "app-key",
        "uploads/",
    )

    url = storage.presign_upload("object", "text/plain", 123, 1800)
    query = parse_qs(urlparse(url).query)

    assert "content-length" in query["X-Amz-SignedHeaders"][0].split(";")
    assert "content-type" in query["X-Amz-SignedHeaders"][0].split(";")


def test_presigned_upload_rejects_size_above_limit():
    storage = B2Storage(
        "https://s3.us-west-004.backblazeb2.com",
        "us-west-004",
        "test-bucket",
        "key-id",
        "app-key",
        "uploads/",
    )

    with pytest.raises(StorageError):
        storage.presign_upload(
            "object",
            "text/plain",
            MAX_FILE_SIZE + 1,
            1800,
        )
