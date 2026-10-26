from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings


class StorageError(Exception):
    """The storage provider could not complete an operation."""


class ObjectNotFound(StorageError):
    """The requested object does not exist."""


class PartialDeletionError(StorageError):
    """Some object versions could not be deleted and should be retried."""

    def __init__(
        self,
        message: str,
        *,
        deleted: int,
        failed_keys: set[str],
        errors: list[dict],
    ) -> None:
        super().__init__(message)
        self.deleted = deleted
        self.failed_keys = failed_keys
        self.errors = errors


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    last_modified: datetime
    content_type: str | None = None


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _chunked(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def build_content_disposition(original_name: str) -> str:
    safe_ascii = "".join(
        char
        for char in original_name
        if 32 <= ord(char) < 127 and char not in {'"', "\\"}
    )
    safe_ascii = safe_ascii.strip() or "download"
    encoded = quote(original_name, safe="")
    return (
        f'attachment; filename="{safe_ascii[:150]}"; '
        f"filename*=UTF-8''{encoded}"
    )


def _is_not_found(error: ClientError) -> bool:
    code = str(error.response.get("Error", {}).get("Code", ""))
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


class B2Storage:
    def __init__(
        self,
        endpoint_url: str,
        region: str,
        bucket: str,
        key_id: str,
        app_key: str,
        prefix: str,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=key_id,
            aws_secret_access_key=app_key,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=30,
            ),
        )

    def object_key(self, stored_name: str) -> str:
        return f"{self.prefix}{stored_name}"

    def presign_upload(
        self,
        stored_name: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        if content_length < 0 or content_length > settings.max_file_size:
            raise StorageError("Upload size is outside the allowed range")

        try:
            return self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": self.object_key(stored_name),
                    # Only the host is signed. B2 validates X-Amz-SignedHeaders
                    # on the CORS OPTIONS preflight, which cannot carry
                    # Content-Type or Content-Length. Signing either header
                    # makes B2 reject the preflight with InvalidRequest.
                    # /uploads/complete verifies the actual size and type.
                },
                ExpiresIn=expires_in,
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Could not create upload URL") from exc

    def presign_download(
        self,
        stored_name: str,
        original_name: str,
        content_type: str,
        expires_in: int,
    ) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": self.object_key(stored_name),
                    "ResponseContentDisposition": build_content_disposition(
                        original_name
                    ),
                    "ResponseContentType": content_type,
                },
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Could not create download URL") from exc

    def head_object(self, stored_name: str) -> StoredObject:
        key = self.object_key(stored_name)
        try:
            response = self.client.head_object(
                Bucket=self.bucket,
                Key=key,
            )
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(key) from exc
            raise StorageError("Could not inspect uploaded object") from exc
        except BotoCoreError as exc:
            raise StorageError("Could not inspect uploaded object") from exc

        return StoredObject(
            key=key,
            size=int(response.get("ContentLength", 0)),
            last_modified=response.get("LastModified") or datetime.min,
            content_type=response.get("ContentType"),
        )

    def _list_versions(
        self,
        prefix: str,
        wanted: set[str],
    ) -> dict[str, list[str]]:
        if not wanted:
            return {}

        versions: dict[str, set[str]] = {}
        paginator = self.client.get_paginator("list_object_versions")
        try:
            for page in paginator.paginate(
                Bucket=self.bucket,
                Prefix=prefix,
            ):
                for collection in ("Versions", "DeleteMarkers"):
                    for entry in page.get(collection, []):
                        key = entry.get("Key")
                        if key not in wanted:
                            continue

                        version_id = entry.get("VersionId")
                        if not version_id:
                            raise StorageError(
                                f"Object version for {key!r} has no VersionId"
                            )

                        versions.setdefault(key, set()).add(version_id)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Could not list object versions") from exc

        return {
            key: sorted(version_ids)
            for key, version_ids in versions.items()
        }

    def _delete_versions(
        self,
        versions: dict[str, list[str]],
    ) -> int:
        objects = [
            {"Key": key, "VersionId": version_id}
            for key, version_ids in versions.items()
            for version_id in version_ids
        ]
        if not objects:
            return 0

        failed_keys: set[str] = set()
        failures: list[dict] = []

        for chunk in _chunked(objects, 1000):
            chunk_keys = {item["Key"] for item in chunk}
            try:
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={
                        "Objects": chunk,
                        "Quiet": True,
                    },
                )
            except (BotoCoreError, ClientError) as exc:
                failed_keys.update(chunk_keys)
                failures.extend(
                    {
                        "Key": key,
                        "Code": "RequestFailed",
                        "Message": str(exc),
                    }
                    for key in chunk_keys
                )
                continue

            for error in response.get("Errors", []):
                key = error.get("Key")
                if key:
                    failed_keys.add(key)
                else:
                    failed_keys.update(chunk_keys)
                failures.append(
                    {
                        "Key": key or "",
                        "VersionId": error.get("VersionId", ""),
                        "Code": error.get("Code", "DeleteFailed"),
                        "Message": error.get("Message", ""),
                    }
                )

        deleted = sum(1 for key in versions if key not in failed_keys)
        if failures:
            details = ", ".join(
                f"{item.get('Code', 'Error')}:{item.get('Key', '?')}"
                for item in failures[:10]
            )
            raise PartialDeletionError(
                f"Failed to delete {len(failed_keys)} object key(s): {details}",
                deleted=deleted,
                failed_keys=failed_keys,
                errors=failures,
            )

        return deleted

    def delete_object(self, stored_name: str) -> None:
        key = self.object_key(stored_name)
        versions = self._list_versions(key, {key})
        if versions:
            self._delete_versions(versions)

    def upload_file(
        self,
        path,
        stored_name: str,
        content_type: str,
    ) -> None:
        try:
            self.client.upload_file(
                str(path),
                self.bucket,
                self.object_key(stored_name),
                ExtraArgs={"ContentType": content_type},
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageError("Could not upload object") from exc

    def iter_objects(self) -> Iterator[StoredObject]:
        paginator = self.client.get_paginator("list_object_versions")
        latest: dict[str, StoredObject] = {}

        try:
            for page in paginator.paginate(
                Bucket=self.bucket,
                Prefix=self.prefix,
            ):
                for collection in ("Versions", "DeleteMarkers"):
                    for entry in page.get(collection, []):
                        key = entry.get("Key")
                        if not key or not key.startswith(self.prefix):
                            continue

                        last_modified = _as_utc(entry.get("LastModified"))
                        candidate = StoredObject(
                            key=key,
                            size=int(entry.get("Size", 0)),
                            last_modified=last_modified,
                        )
                        current = latest.get(key)
                        if (
                            current is None
                            or last_modified > current.last_modified
                        ):
                            latest[key] = candidate
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Could not list object versions") from exc

        yield from latest.values()

    def delete_keys(self, keys: list[str]) -> int:
        unique_keys = list(dict.fromkeys(keys))
        if not unique_keys:
            return 0

        wanted = set(unique_keys)
        inside_prefix = {
            key for key in unique_keys if key.startswith(self.prefix)
        }
        outside_prefix = wanted - inside_prefix

        versions: dict[str, list[str]] = {}
        if inside_prefix:
            versions.update(
                self._list_versions(self.prefix, inside_prefix)
            )
        for key in outside_prefix:
            versions.update(self._list_versions(key, {key}))

        return self._delete_versions(versions)


_storage_cache: dict[tuple, B2Storage] = {}


def get_storage() -> B2Storage:
    if settings.storage_backend != "b2":
        raise StorageError("B2 storage is not enabled")

    cache_key = (
        settings.b2_endpoint_url,
        settings.b2_region,
        settings.b2_bucket,
        settings.b2_key_id,
        settings.b2_app_key,
        settings.b2_upload_prefix,
    )
    storage = _storage_cache.get(cache_key)
    if storage is None:
        storage = B2Storage(
            endpoint_url=settings.b2_endpoint_url,
            region=settings.b2_region,
            bucket=settings.b2_bucket,
            key_id=settings.b2_key_id,
            app_key=settings.b2_app_key,
            prefix=settings.b2_upload_prefix,
        )
        _storage_cache[cache_key] = storage

    return storage
