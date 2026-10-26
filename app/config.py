import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

MAX_FILE_SIZE = 100 * 1024 * 1024  # 104,857,600 bytes (100 MiB)
DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
SUPPORTED_STORAGE_BACKENDS = ("local", "b2")


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


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")

    return value


def _storage_backend() -> str:
    backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    if backend not in SUPPORTED_STORAGE_BACKENDS:
        raise ValueError(
            "STORAGE_BACKEND must be one of: "
            + ", ".join(SUPPORTED_STORAGE_BACKENDS)
        )
    return backend


def _normalize_database_url(raw_value: str) -> str:
    """Make platform-provided PostgreSQL URLs usable by SQLAlchemy + psycopg."""
    value = raw_value.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


def _optional_env(name: str) -> str:
    return os.getenv(name, "").strip()


def _upload_prefix() -> str:
    prefix = _optional_env("B2_UPLOAD_PREFIX").strip("/")
    return f"{prefix}/" if prefix else "uploads/"


public_base_url = (
    os.getenv("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")
    or DEFAULT_PUBLIC_BASE_URL
)
if not public_base_url.startswith(("http://", "https://")):
    raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")

storage_backend = _storage_backend()

b2_endpoint_url = _optional_env("B2_ENDPOINT_URL").rstrip("/")
b2_region = _optional_env("B2_REGION")
b2_bucket = _optional_env("B2_BUCKET")
b2_key_id = _optional_env("B2_KEY_ID")
b2_app_key = _optional_env("B2_APP_KEY")

if storage_backend == "b2":
    missing = [
        name
        for name, value in (
            ("B2_ENDPOINT_URL", b2_endpoint_url),
            ("B2_REGION", b2_region),
            ("B2_BUCKET", b2_bucket),
            ("B2_KEY_ID", b2_key_id),
            ("B2_APP_KEY", b2_app_key),
            ("UPLOAD_SIGNING_SECRET", _optional_env("UPLOAD_SIGNING_SECRET")),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "STORAGE_BACKEND=b2 requires: " + ", ".join(missing)
        )
    if not b2_endpoint_url.startswith("https://"):
        raise ValueError("B2_ENDPOINT_URL must start with https://")


@dataclass
class Settings:
    database_url: str
    upload_dir: Path
    public_base_url: str
    cors_origins: tuple[str, ...]
    max_file_size: int = MAX_FILE_SIZE
    share_expiry_hours: int = 24
    cleanup_interval_hours: int = 1
    storage_backend: str = "local"
    b2_endpoint_url: str = ""
    b2_region: str = ""
    b2_bucket: str = ""
    b2_key_id: str = ""
    b2_app_key: str = ""
    b2_upload_prefix: str = "uploads/"
    b2_presign_upload_seconds: int = 1800
    b2_presign_download_seconds: int = 300
    upload_token_ttl_seconds: int = 1800
    upload_signing_secret: str = ""
    cleanup_secret: str = ""
    orphan_grace_seconds: int = 3600
    cleanup_batch_size: int = 200


settings = Settings(
    database_url=_normalize_database_url(
        os.getenv("DATABASE_URL", "sqlite:///./fileaway.db")
    ),
    upload_dir=_upload_dir(),
    public_base_url=public_base_url,
    cors_origins=_parse_origins(
        os.getenv("CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS))
    ),
    share_expiry_hours=_positive_int("SHARE_EXPIRY_HOURS", 24),
    cleanup_interval_hours=_positive_int("CLEANUP_INTERVAL_HOURS", 1),
    storage_backend=storage_backend,
    b2_endpoint_url=b2_endpoint_url,
    b2_region=b2_region,
    b2_bucket=b2_bucket,
    b2_key_id=b2_key_id,
    b2_app_key=b2_app_key,
    b2_upload_prefix=_upload_prefix(),
    b2_presign_upload_seconds=_positive_int(
        "B2_PRESIGN_UPLOAD_SECONDS", 1800
    ),
    b2_presign_download_seconds=_positive_int(
        "B2_PRESIGN_DOWNLOAD_SECONDS", 300
    ),
    upload_token_ttl_seconds=_positive_int("UPLOAD_TOKEN_TTL_SECONDS", 1800),
    upload_signing_secret=_optional_env("UPLOAD_SIGNING_SECRET"),
    cleanup_secret=_optional_env("CLEANUP_SECRET"),
    orphan_grace_seconds=_positive_int("ORPHAN_GRACE_SECONDS", 3600),
    cleanup_batch_size=_positive_int("CLEANUP_BATCH_SIZE", 200),
)
