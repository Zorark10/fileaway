import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

MAX_FILE_SIZE = 100 * 1024 * 1024
DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _upload_dir() -> Path:
    raw_value = os.getenv("UPLOAD_DIR")
    if not raw_value:
        return (BASE_DIR / "uploads").resolve()

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _parse_origins(raw_value: str) -> tuple[str, ...]:
    origins = tuple(
        origin.strip().rstrip("/")
        for origin in raw_value.split(",")
        if origin.strip()
    )

    if "*" in origins:
        raise ValueError("CORS_ORIGINS must not contain a wildcard origin")

    return origins or DEFAULT_CORS_ORIGINS


@dataclass
class Settings:
    database_url: str
    upload_dir: Path
    public_base_url: str
    cors_origins: tuple[str, ...]
    max_file_size: int = MAX_FILE_SIZE
    share_expiry_hours: int = 24
    cleanup_interval_hours: int = 1


public_base_url = (
    os.getenv("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")
    or DEFAULT_PUBLIC_BASE_URL
)
if not public_base_url.startswith(("http://", "https://")):
    raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")

settings = Settings(
    database_url=os.getenv("DATABASE_URL", "sqlite:///./fileaway.db"),
    upload_dir=_upload_dir(),
    public_base_url=public_base_url,
    cors_origins=_parse_origins(
        os.getenv("CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS))
    ),
)
