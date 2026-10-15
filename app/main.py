import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone ,timedelta
from datetime import datetime, timezone
from pathlib import Path
from fastapi import UploadFile, File as FastAPIFile
from fastapi import FastAPI, Depends, UploadFile
from fastapi import HTTPException
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy.orm import Session
from app.database import get_db, SessionLocal
from app.models import File
from app.schemas import FileCreate, FileResponse
from apscheduler.schedulers.background import BackgroundScheduler
from app.schemas import FileCreate, FileResponse, FileUploadResponse

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 100 * 1024 * 1024

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()

app = FastAPI(title="FileAway API", lifespan=lifespan)

@app.get("/")
def home():
    return {"message": "FileAway API is running"}

@app.get("/files", response_model=list[FileResponse])
def get_files(db: Session = Depends(get_db)):
    files = db.query(File).all()
    return files

@app.post("/files")
def create_file(file: FileCreate, db: Session = Depends(get_db)):
    new_file = File(
        original_name=file.original_name,
        stored_name=file.stored_name,
        share_token = str(uuid.uuid4()),
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    )
    db.add(new_file)
    db.commit()
    db.refresh(new_file)
    return new_file

@app.post("/upload", response_model=FileUploadResponse)
async def upload_file(uploaded_file: UploadFile = FastAPIFile(...),
                      db: Session = Depends(get_db)):
    stored_name = str(uuid.uuid4())
    file_path = UPLOAD_DIR / stored_name
    file_size = 0

    try:
        with file_path.open("wb") as destination:
            while True:
                chunk = await uploaded_file.read(1024 * 1024)

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
        original_name = Path(uploaded_file.filename or "unnamed").name,
        stored_name = stored_name,
        share_token = str(uuid.uuid4()),
        content_type=uploaded_file.content_type or "application/octet-stream",
        file_size=file_size,
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    )

    try:
        db.add(new_file)
        db.commit()
        db.refresh(new_file)
    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise

    return FileUploadResponse(
        id=new_file.id,
        original_name=new_file.original_name,
        stored_name=new_file.stored_name,
        share_token=new_file.share_token,
        content_type=new_file.content_type,
        file_size=new_file.file_size,
        uploaded_at=new_file.uploaded_at,
        expires_at=new_file.expires_at,
        share_url=f"http://127.0.0.1:8000/files/share/{new_file.share_token}",
    )

@app.get("/files/share/{share_token}")
def download_file(share_token: str, db: Session = Depends(get_db)):
    file = db.query(File).filter(File.share_token == share_token).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Share link has expired")

    file_path = UPLOAD_DIR / file.stored_name
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FastAPIFileResponse(path = file_path, filename=file.original_name)

def cleanup_expired_files(db: Session):
    now = datetime.now(timezone.utc)
    expired_files = db.query(File).filter(File.expires_at <= now).all()

    for file in expired_files:
        file_path = UPLOAD_DIR / file.stored_name
        try:
            if file_path.is_file():
                file_path.unlink()

            db.delete(file)
            db.commit()

        except Exception as error:
            db.rollback()
            print(f"Failed to clean up file {file.id}: {error}")

def run_cleanup():
    # print("Cleanup job is running")
    db = SessionLocal()
    try:
        cleanup_expired_files(db)
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(run_cleanup, "interval", hours=1)

