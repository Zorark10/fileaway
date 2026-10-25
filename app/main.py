import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File as FastAPIFile, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import File
from app.schemas import FileUploadResponse

logger = logging.getLogger("fileaway")

UPLOAD_DIR = settings.upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = settings.max_file_size
CHUNK_SIZE = 1024 * 1024


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


def _safe_stored_path(stored_name: str | None) -> Path | None:
    if not stored_name or "/" in stored_name or "\\" in stored_name:
        return None

    base_dir = UPLOAD_DIR.resolve()
    candidate = (base_dir / stored_name).resolve()

    if candidate.parent != base_dir:
        return None

    return candidate


def _share_url(share_token: str) -> str:
    base_url = settings.public_base_url.rstrip("/")
    return f"{base_url}/files/share/{share_token}"


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

    run_cleanup()
    scheduler.start()
    logger.info(
        "FileAway started; uploads=%s cleanup_interval_hours=%s",
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


@app.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    uploaded_file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
):
    stored_name = str(uuid.uuid4())
    file_path = UPLOAD_DIR / stored_name
    file_size = 0

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

    new_file = File(
        original_name=_sanitize_original_name(uploaded_file.filename),
        stored_name=stored_name,
        share_token=secrets.token_urlsafe(32),
        content_type=(
            uploaded_file.content_type or "application/octet-stream"
        )[:255],
        file_size=file_size,
        expires_at=(
            datetime.now(timezone.utc)
            + timedelta(hours=settings.share_expiry_hours)
        ),
    )

    try:
        db.add(new_file)
        db.commit()
        db.refresh(new_file)
    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        logger.exception("Failed to persist uploaded file metadata")
        raise

    logger.info(
        "Stored upload id=%s size=%s bytes",
        new_file.id,
        new_file.file_size,
    )

    return FileUploadResponse(
        id=new_file.id,
        original_name=new_file.original_name,
        stored_name=new_file.stored_name,
        share_token=new_file.share_token,
        content_type=new_file.content_type,
        file_size=new_file.file_size,
        uploaded_at=new_file.uploaded_at,
        expires_at=new_file.expires_at,
        share_url=_share_url(new_file.share_token),
    )


@app.get("/files/share/{share_token}")
def download_file(share_token: str, db: Session = Depends(get_db)):
    file = db.query(File).filter(File.share_token == share_token).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    expires_at = _as_utc(file.expires_at)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Share link has expired")

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


def cleanup_expired_files(db: Session) -> int:
    now = datetime.now(timezone.utc)
    removed = 0

    for file in db.query(File).all():
        expires_at = _as_utc(file.expires_at)
        if expires_at is None or expires_at > now:
            continue

        file_path = _safe_stored_path(file.stored_name)
        if file_path is not None:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "Could not delete stored file for id=%s; will retry",
                    file.id,
                )
                continue

        try:
            db.delete(file)
            db.commit()
            removed += 1
        except Exception:
            db.rollback()
            logger.exception("Could not delete expired metadata id=%s", file.id)

    if removed:
        logger.info("Removed %s expired file(s)", removed)

    return removed


def run_cleanup() -> int:
    db = SessionLocal()
    try:
        return cleanup_expired_files(db)
    except Exception:
        logger.exception("Expired-file cleanup job failed")
        return 0
    finally:
        db.close()


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
