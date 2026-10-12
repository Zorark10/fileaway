from datetime import datetime, timezone

from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import File
from app.schemas import FileCreate, FileResponse

app = FastAPI(title="FileAway API")

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
        uploaded_at=datetime.now(timezone.utc),
    )
    db.add(new_file)
    db.commit()
    db.refresh(new_file)
    return new_file


