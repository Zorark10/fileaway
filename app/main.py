import hmac
import logging
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import (
    Depends,
    FastAPI,
    File as FastAPIFile,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse as FastAPIFileResponse,
    RedirectResponse,
)
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import File
from app.schemas import (
    FileUploadResponse,
    UploadAuthorizationResponse,
    UploadAuthorizeRequest,
    UploadCompleteRequest,
)
from app.storage import (
    B2Storage,
    ObjectNotFound,
    StorageError,
    get_storage,
)
from app.upload_tokens import (
    ExpiredUploadToken,
    InvalidUploadToken,
    issue_upload_token,
    verify_upload_token,
)

logger = logging.getLogger("fileaway")

UPLOAD_DIR = settings.upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = settings.max_file_size
CHUNK_SIZE = 1024 * 1024
_CLEANUP_LOCK = threading.Lock()


@dataclass(frozen=True)
class CleanupResult:
    removed: int
    failed: int


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sanitize_original_name(filename: str | None) -> str:
    normalized = (filename or "unnamed").replace("\\", "/")
    name = Path(normalized).name
    name = "".join(char for char in name if char >= " " and char != "\x7f")
    name = name.strip()

    if not name or name in {".", ".."}:
        return "unnamed"

    return name[:255]


def _sanitize_content_type(value: str | None) -> str:
    candidate = (value or "").strip()
    if (
        not candidate
        or len(candidate) > 255
        or any(ord(char) < 32 or ord(char) >= 127 for char in candidate)
    ):
        return "application/octet-stream"

    return candidate


def _valid_stored_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and "/" not in value
        and "\\" not in value
        and value not in {".", ".."}
    )


def _safe_stored_path(stored_name: str | None) -> Path | None:
    if not _valid_stored_name(stored_name):
        return None

    base_dir = UPLOAD_DIR.resolve()
    candidate = (base_dir / stored_name).resolve()

    if candidate.parent != base_dir:
        return None

    return candidate


def _share_url(share_token: str) -> str:
    base_url = settings.public_base_url.rstrip("/")
    return f"{base_url}/files/share/{share_token}"


def _expiry_cutoff(db: Session, now: datetime) -> datetime:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        return now.replace(tzinfo=None)
    return now


def _delete_local_object(file: File) -> bool:
    file_path = _safe_stored_path(file.stored_name)
    if file_path is None:
        return True

    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        logger.exception(
            "Could not delete local upload id=%s; will retry",
            file.id,
        )
        return False

    return True


def _delete_b2_object(file: File, storage: B2Storage) -> bool:
    try:
        storage.delete_object(file.stored_name)
    except StorageError:
        logger.exception(
            "Could not delete B2 object for id=%s; will retry",
            file.id,
        )
        return False

    return True


def _delete_physical_object(
    file: File,
    storage: B2Storage | None,
) -> bool:
    if (file.storage_backend or "local") == "b2":
        if storage is None:
            logger.error(
                "B2 cleanup requested while B2 storage is disabled for id=%s",
                file.id,
            )
            return False
        return _delete_b2_object(file, storage)

    return _delete_local_object(file)


def _new_file_record(
    *,
    original_name: str,
    stored_name: str,
    content_type: str,
    file_size: int,
    storage_backend: str,
    now: datetime,
) -> File:
    return File(
        original_name=original_name,
        stored_name=stored_name,
        share_token=secrets.token_urlsafe(32),
        content_type=content_type,
        file_size=file_size,
        storage_backend=storage_backend,
        uploaded_at=now,
        expires_at=now + timedelta(hours=settings.share_expiry_hours),
    )


def _upload_response(file: File) -> FileUploadResponse:
    return FileUploadResponse(
        id=file.id,
        original_name=file.original_name,
        stored_name=file.stored_name,
        share_token=file.share_token,
        content_type=file.content_type,
        file_size=file.file_size,
        uploaded_at=file.uploaded_at,
        expires_at=file.expires_at,
        share_url=_share_url(file.share_token),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if (
        "127.0.0.1" in settings.public_base_url
        or "localhost" in settings.public_base_url
    ):
        logger.warning(
            "PUBLIC_BASE_URL is local; generated share links will not be "
            "reachable from the internet"
        )

    if not settings.cleanup_secret:
        logger.warning(
            "CLEANUP_SECRET is not set; /internal/cleanup is disabled. "
            "Expiry cleanup will only run while this process is awake."
        )

    run_cleanup()
    scheduler.start()
    logger.info(
        "FileAway started; storage=%s uploads=%s cleanup_interval_hours=%s",
        settings.storage_backend,
        UPLOAD_DIR,
        settings.cleanup_interval_hours,
    )

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="FileAway API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)


@app.get("/")
def home():
    return {"message": "FileAway API is running"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1 FROM files LIMIT 1"))
    except Exception:
        logger.exception("Health check database query failed")
        raise HTTPException(status_code=503, detail="Database unavailable")

    return {"status": "ok"}


@app.post(
    "/uploads/authorize",
    response_model=UploadAuthorizationResponse,
)
def authorize_upload(payload: UploadAuthorizeRequest):
    if payload.file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    if settings.storage_backend == "local":
        return UploadAuthorizationResponse(
            mode="proxy",
            max_file_size=MAX_FILE_SIZE,
        )

    if not settings.upload_signing_secret:
        raise HTTPException(
            status_code=503,
            detail="Upload authorization is not configured",
        )

    original_name = _sanitize_original_name(payload.original_name)
    content_type = _sanitize_content_type(payload.content_type)
    stored_name = str(uuid.uuid4())

    try:
        storage = get_storage()
        upload_url = storage.presign_upload(
            stored_name=stored_name,
            content_type=content_type,
            content_length=payload.file_size,
            expires_in=settings.b2_presign_upload_seconds,
        )
    except StorageError:
        logger.exception("Could not authorize upload")
        raise HTTPException(
            status_code=503,
            detail="Storage is temporarily unavailable",
        )

    upload_token = issue_upload_token(
        {
            "stored_name": stored_name,
            "original_name": original_name,
            "content_type": content_type,
            "file_size": payload.file_size,
        },
        secret=settings.upload_signing_secret,
        ttl_seconds=settings.upload_token_ttl_seconds,
    )

    return UploadAuthorizationResponse(
        mode="direct",
        max_file_size=MAX_FILE_SIZE,
        upload_url=upload_url,
        method="PUT",
        headers={"Content-Type": content_type},
        upload_token=upload_token,
        upload_expires_at=(
            datetime.now(timezone.utc)
            + timedelta(seconds=settings.upload_token_ttl_seconds)
        ),
    )


@app.post("/uploads/complete", response_model=FileUploadResponse)
def complete_upload(
    payload: UploadCompleteRequest,
    db: Session = Depends(get_db),
):
    try:
        token = verify_upload_token(
            payload.upload_token,
            settings.upload_signing_secret,
        )
    except ExpiredUploadToken:
        raise HTTPException(
            status_code=410,
            detail="Upload authorization has expired",
        )
    except InvalidUploadToken:
        raise HTTPException(
            status_code=400,
            detail="Invalid upload authorization",
        )

    if settings.storage_backend != "b2":
        raise HTTPException(status_code=409, detail="Direct uploads are disabled")

    stored_name = token.get("stored_name")
    declared_size = token.get("file_size")
    if not _valid_stored_name(stored_name) or not isinstance(declared_size, int):
        raise HTTPException(
            status_code=400,
            detail="Invalid upload authorization",
        )

    if declared_size < 0 or declared_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    if db.query(File).filter(File.stored_name == stored_name).first():
        raise HTTPException(status_code=409, detail="Upload already completed")

    original_name = _sanitize_original_name(token.get("original_name"))
    content_type = _sanitize_content_type(token.get("content_type"))

    try:
        storage = get_storage()
    except StorageError:
        logger.exception("B2 storage is not configured correctly")
        raise HTTPException(
            status_code=503,
            detail="Storage is temporarily unavailable",
        )

    try:
        stored_object = storage.head_object(stored_name)
    except ObjectNotFound:
        raise HTTPException(
            status_code=404,
            detail="Upload not found or incomplete",
        )
    except StorageError:
        logger.exception("Could not verify uploaded object")
        raise HTTPException(
            status_code=503,
            detail="Storage is temporarily unavailable",
        )

    if stored_object.size > MAX_FILE_SIZE:
        try:
            storage.delete_object(stored_name)
        except StorageError:
            logger.exception(
                "Could not remove oversized upload; "
                "orphan cleanup will retry"
            )
        raise HTTPException(status_code=413, detail="File too large")

    if stored_object.size != declared_size:
        try:
            storage.delete_object(stored_name)
        except StorageError:
            logger.exception("Could not remove mismatched upload")
        raise HTTPException(
            status_code=400,
            detail="Uploaded file size does not match the authorized size",
        )

    if stored_object.content_type is not None and (
        stored_object.content_type.strip().lower()
        != content_type.strip().lower()
    ):
        try:
            storage.delete_object(stored_name)
        except StorageError:
            logger.exception(
                "Could not remove content-type mismatch; "
                "orphan cleanup will retry"
            )
        raise HTTPException(
            status_code=400,
            detail="Uploaded file type does not match the authorized type",
        )

    new_file = _new_file_record(
        original_name=original_name,
        stored_name=stored_name,
        content_type=content_type,
        file_size=stored_object.size,
        storage_backend="b2",
        now=datetime.now(timezone.utc),
    )

    try:
        db.add(new_file)
        db.commit()
        db.refresh(new_file)
    except Exception:
        db.rollback()

        try:
            already_saved = (
                db.query(File)
                .filter(File.stored_name == stored_name)
                .first()
                is not None
            )
        except Exception:
            already_saved = False

        if already_saved:
            raise HTTPException(
                status_code=409,
                detail="Upload already completed",
            )

        try:
            storage.delete_object(stored_name)
        except StorageError:
            logger.exception(
                "Could not remove object after database failure; "
                "orphan cleanup will retry"
            )
        logger.exception("Failed to persist uploaded file metadata")
        raise HTTPException(
            status_code=500,
            detail="Could not save uploaded file",
        )

    logger.info(
        "Stored upload id=%s size=%s bytes backend=b2",
        new_file.id,
        new_file.file_size,
    )

    return _upload_response(new_file)


@app.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    uploaded_file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
):
    stored_name = str(uuid.uuid4())
    file_path = UPLOAD_DIR / stored_name
    file_size = 0
    content_type = _sanitize_content_type(uploaded_file.content_type)
    use_b2 = settings.storage_backend == "b2"

    storage: B2Storage | None = None
    if use_b2:
        try:
            storage = get_storage()
        except StorageError:
            logger.exception("B2 storage is not configured correctly")
            raise HTTPException(
                status_code=503,
                detail="Storage is temporarily unavailable",
            )

    try:
        with file_path.open("wb") as destination:
            while True:
                chunk = await uploaded_file.read(CHUNK_SIZE)

                if not chunk:
                    break

                file_size += len(chunk)

                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="File too large",
                    )

                destination.write(chunk)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    if use_b2 and storage is not None:
        try:
            await run_in_threadpool(
                storage.upload_file,
                file_path,
                stored_name,
                content_type,
            )
        except StorageError:
            logger.exception("Failed to upload file to B2")
            raise HTTPException(
                status_code=503,
                detail="Storage is temporarily unavailable",
            )
        finally:
            file_path.unlink(missing_ok=True)

    new_file = _new_file_record(
        original_name=_sanitize_original_name(uploaded_file.filename),
        stored_name=stored_name,
        content_type=content_type,
        file_size=file_size,
        storage_backend="b2" if use_b2 else "local",
        now=datetime.now(timezone.utc),
    )

    try:
        db.add(new_file)
        db.commit()
        db.refresh(new_file)
    except Exception:
        db.rollback()
        if use_b2 and storage is not None:
            try:
                storage.delete_object(stored_name)
            except StorageError:
                logger.exception(
                    "Could not remove object after database failure; "
                    "orphan cleanup will retry"
                )
        else:
            file_path.unlink(missing_ok=True)
        logger.exception("Failed to persist uploaded file metadata")
        raise

    logger.info(
        "Stored upload id=%s size=%s bytes backend=%s",
        new_file.id,
        new_file.file_size,
        new_file.storage_backend,
    )

    return _upload_response(new_file)


@app.get("/files/share/{share_token}")
def download_file(share_token: str, db: Session = Depends(get_db)):
    file = db.query(File).filter(File.share_token == share_token).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    expires_at = _as_utc(file.expires_at)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Share link has expired")

    if (file.storage_backend or "local") == "b2":
        try:
            storage = get_storage()
            download_url = storage.presign_download(
                stored_name=file.stored_name,
                original_name=file.original_name,
                content_type=file.content_type or "application/octet-stream",
                expires_in=settings.b2_presign_download_seconds,
            )
        except StorageError:
            logger.exception("Could not authorize download for id=%s", file.id)
            raise HTTPException(
                status_code=503,
                detail="Storage is temporarily unavailable",
            )

        return RedirectResponse(
            url=download_url,
            status_code=302,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    file_path = _safe_stored_path(file.stored_name)
    if file_path is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FastAPIFileResponse(
        path=file_path,
        filename=file.original_name,
        media_type=file.content_type or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def cleanup_expired_files(
    db: Session,
    storage: B2Storage | None = None,
) -> CleanupResult:
    now = datetime.now(timezone.utc)
    cutoff = _expiry_cutoff(db, now)
    removed = 0
    failed_ids: set[int] = set()

    while True:
        query = db.query(File).filter(File.expires_at <= cutoff)
        if failed_ids:
            query = query.filter(~File.id.in_(failed_ids))

        expired_files = (
            query.order_by(File.id)
            .limit(settings.cleanup_batch_size)
            .all()
        )
        if not expired_files:
            break

        removed_this_batch = 0
        for file in expired_files:
            expires_at = _as_utc(file.expires_at)
            if expires_at is None or expires_at > now:
                continue

            if not _delete_physical_object(file, storage):
                failed_ids.add(file.id)
                continue

            try:
                db.delete(file)
                db.commit()
                removed += 1
                removed_this_batch += 1
            except Exception:
                db.rollback()
                failed_ids.add(file.id)
                logger.exception(
                    "Could not delete expired metadata id=%s",
                    file.id,
                )

        if removed_this_batch == 0:
            break

    if removed:
        logger.info("Removed %s expired file(s)", removed)
    if failed_ids:
        logger.error(
            "Could not delete %s expired file(s); they remain in the database",
            len(failed_ids),
        )

    return CleanupResult(removed=removed, failed=len(failed_ids))


def cleanup_orphaned_objects(db: Session, storage: B2Storage) -> int:
    referenced = {
        stored_name
        for (stored_name,) in db.query(File.stored_name)
        .filter(File.storage_backend == "b2")
        .all()
    }

    now = datetime.now(timezone.utc)
    orphan_keys: list[str] = []

    for stored_object in storage.iter_objects():
        if not stored_object.key.startswith(storage.prefix):
            continue

        stored_name = stored_object.key[len(storage.prefix) :]
        if stored_name in referenced:
            continue

        last_modified = _as_utc(stored_object.last_modified)
        if last_modified is not None:
            age_seconds = (now - last_modified).total_seconds()
            if age_seconds < settings.orphan_grace_seconds:
                continue

        orphan_keys.append(stored_object.key)

    removed = storage.delete_keys(orphan_keys)
    if removed:
        logger.info("Removed %s orphaned B2 object(s)", removed)

    return removed


def run_cleanup() -> dict[str, int | bool | str]:
    if not _CLEANUP_LOCK.acquire(blocking=False):
        return {"busy": True, "expired_removed": 0, "orphans_removed": 0}

    db = SessionLocal()
    expired = CleanupResult(removed=0, failed=0)
    orphans_removed = 0
    try:
        storage = get_storage() if settings.storage_backend == "b2" else None
        expired = cleanup_expired_files(db, storage)
        if storage is not None:
            orphans_removed = cleanup_orphaned_objects(db, storage)

        result: dict[str, int | bool | str] = {
            "busy": False,
            "expired_removed": expired.removed,
            "expired_failed": expired.failed,
            "orphans_removed": orphans_removed,
        }
        if expired.failed:
            result["error"] = 1
        return result
    except Exception as exc:
        logger.exception("Expired-file cleanup job failed")
        return {
            "busy": False,
            "expired_removed": expired.removed,
            "expired_failed": expired.failed,
            "orphans_removed": orphans_removed,
            "error": 1,
            "failure": str(exc)[:500],
        }
    finally:
        db.close()
        _CLEANUP_LOCK.release()


@app.post("/internal/cleanup", include_in_schema=False)
def internal_cleanup(
    x_cleanup_secret: str | None = Header(
        default=None,
        alias="X-Cleanup-Secret",
    ),
):
    configured_secret = settings.cleanup_secret
    if not configured_secret:
        raise HTTPException(
            status_code=503,
            detail="Cleanup endpoint is not configured",
        )

    if not x_cleanup_secret or not hmac.compare_digest(
        x_cleanup_secret,
        configured_secret,
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = run_cleanup()
    if result.get("busy"):
        raise HTTPException(status_code=409, detail="Cleanup already running")
    if result.get("error"):
        raise HTTPException(
            status_code=500,
            detail={"message": "Cleanup completed with failures", **result},
        )

    return {"status": "ok", **result}


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(
    run_cleanup,
    "interval",
    hours=settings.cleanup_interval_hours,
    id="cleanup-expired-files",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=300,
)
