import uuid
from datetime import datetime, timezone ,timedelta
from datetime import datetime, timezone
from pathlib import Path
from fastapi import UploadFile, File as FastAPIFile
from fastapi import FastAPI, Depends, UploadFile
from fastapi import HTTPException
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import File
from app.schemas import FileCreate, FileResponse

app = FastAPI(title="FileAway API")
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 100 * 1024 * 1024
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

@app.post("/upload", response_model=FileResponse)
async def upload_file(uploaded_file: UploadFile = FastAPIFile(...),
                      db: Session = Depends(get_db)):
    stored_name = str(uuid.uuid4())
    file_path = UPLOAD_DIR / stored_name
    content = await uploaded_file.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    with file_path.open(mode="wb") as destination:
        destination.write(content)

    with file_path.open(mode="wb") as destination:
        destination.write(await uploaded_file.read())

    new_file = File(
        original_name = Path(uploaded_file.filename or "unnamed").name,
        stored_name = stored_name,
        share_token = str(uuid.uuid4()),
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    )
    db.add(new_file)
    db.commit()
    db.refresh(new_file)
    return new_file

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