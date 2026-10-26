# FileAway

**Temporary file sharing, without the clutter.**

FileAway lets someone upload a file, get a random share link, and send it to a
recipient without an account. Links expire after 24 hours. Production runs on a
$0 stack: Vercel Hobby for the frontend, Render Free for the API, Supabase Free
PostgreSQL for metadata, and Backblaze B2 for files.

## Features

- Upload files up to 100 MB through a responsive React interface.
- Direct browser-to-B2 uploads with progress, so file bodies do not pass through
  the free Render instance.
- Random share tokens and no public file listing.
- Immediate `410` rejection for expired links.
- Automatic cleanup of expired metadata and every version of their B2 objects.
- Local development with SQLite and local disk.
- Mixed storage: old local rows and new B2 rows can coexist.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React, Vite, Tailwind CSS |
| API | Python, FastAPI |
| ORM and migrations | SQLAlchemy, Alembic |
| Local database | SQLite |
| Production database | Supabase Free PostgreSQL |
| Production storage | Backblaze B2 (S3-compatible) |
| Scheduled work | APScheduler plus GitHub Actions |

## How uploads work

1. The browser asks `POST /uploads/authorize` for permission to upload.
2. With B2 enabled, the API returns a 30-minute presigned PUT URL, an upload
   token, and the authorized content type. Only the `host` header is signed.
   B2 validates signed headers during the browser's OPTIONS preflight, which
   cannot carry `Content-Type` or `Content-Length`; signing either header makes
   B2 reject the preflight with `InvalidRequest`.
3. The browser uploads the file directly to B2, sending the returned
   `Content-Type` header on the PUT request. CORS allows that header.
4. The browser calls `POST /uploads/complete`. The API verifies the token, HEADs
   the object, checks that its actual size matches the declared size, is no more
   than 104,857,600 bytes, and that its stored content type matches the
   authorized type. It then stores metadata in PostgreSQL. A mismatched,
   wrong-type, or oversized object is permanently deleted, including all B2
   versions and delete markers. If deletion fails, no share record is created
   and the orphan sweep retries it later.
5. A share URL is returned.

The 100 MiB check happens before upload in the browser, but a modified client
can bypass it. Because `Content-Length` is not signed, B2 can accept an oversized
body before the backend rejects it during completion. The backend then deletes
it. Content type is also checked by the completion HEAD rather than by the
signature. A stricter option is a proxy upload, or an upload method that enforces
`content-length-range` at storage time; neither is used here.

In local mode, `POST /uploads/authorize` returns `{"mode":"proxy"}` and the
frontend falls back to the multipart `POST /upload` endpoint, keeping files in
`uploads/`.

Downloads call `GET /files/share/{share_token}`. The API checks expiry and, for
B2 files, redirects to a 5-minute presigned download URL. The link itself is
rejected immediately at 24 hours, but an already-issued signed URL can remain
valid for up to 5 minutes after expiry.

## Project structure

```text
fileaway/
├── app/
│   ├── main.py             # API, uploads, downloads, cleanup
│   ├── config.py           # Environment-based settings
│   ├── database.py         # SQLite/PostgreSQL engine and sessions
│   ├── models.py           # File metadata model
│   ├── schemas.py          # Request and response models
│   ├── storage.py          # B2 presigning, HEAD, delete, listing
│   └── upload_tokens.py    # HMAC upload authorization tokens
├── frontend/
│   └── src/App.jsx
├── migrations/
├── tests/
├── deployment/
│   ├── README.md           # $0 deployment guide
│   ├── configure_b2_cors.py
│   └── legacy-oracle/      # Archived Oracle Cloud setup
├── fix_b2_cors.py          # Diagnose and safely repair B2 Native CORS
├── render.yaml
├── requirements.txt
└── .env.example
```

`uploads/` and `fileaway.db` are local runtime data and are not committed.

## Local development

### Prerequisites

- Python 3.10 or newer
- Node.js and npm

### Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API is at `http://127.0.0.1:8000` and interactive docs are at
`http://127.0.0.1:8000/docs`.

The default `.env.example` uses:

```dotenv
DATABASE_URL=sqlite:///./fileaway.db
UPLOAD_DIR=./uploads
PUBLIC_BASE_URL=http://127.0.0.1:8000
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
STORAGE_BACKEND=local
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Vite serves the app at `http://localhost:5173`. For local development the
frontend defaults to `http://127.0.0.1:8000` when no API URL is configured.

To point at another API:

```powershell
$env:VITE_API_BASE_URL = "https://your-api.example"
npm run dev
```

## API overview

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Basic API status |
| `GET` | `/health` | Health check with a database query |
| `POST` | `/uploads/authorize` | Get direct-upload permission or proxy mode |
| `POST` | `/uploads/complete` | Verify a direct upload and save metadata |
| `POST` | `/upload` | Multipart upload (local mode or proxy fallback) |
| `GET` | `/files/share/{share_token}` | Download a file or redirect to B2 |
| `POST` | `/internal/cleanup` | Protected cleanup trigger (hidden from OpenAPI) |

The cleanup endpoint returns `503` when `CLEANUP_SECRET` is unset, `401` for a
wrong secret, `409` while another cleanup is running, and `500` when any file
could not be removed. Failed rows remain in the database and are retried.

The multipart form field is named `uploaded_file`.

Example:

```bash
curl -X POST http://127.0.0.1:8000/upload \
  -F "uploaded_file=@example.pdf"
```

## Configuration

Copy `.env.example` to `.env` for local work. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite or PostgreSQL connection string |
| `UPLOAD_DIR` | Local upload directory for local mode |
| `PUBLIC_BASE_URL` | Public API URL used to build share links |
| `CORS_ORIGINS` | Comma-separated allowed browser origins |
| `STORAGE_BACKEND` | `local` or `b2` |
| `B2_ENDPOINT_URL` | B2 S3 endpoint |
| `B2_REGION` | B2 bucket region |
| `B2_BUCKET` | Private bucket name |
| `B2_KEY_ID` | B2 application key ID |
| `B2_APP_KEY` | B2 application key |
| `B2_UPLOAD_PREFIX` | Object key prefix, default `uploads/` |
| `UPLOAD_SIGNING_SECRET` | HMAC secret for direct-upload tokens |
| `CLEANUP_SECRET` | Shared secret for the cleanup endpoint |
| `SHARE_EXPIRY_HOURS` | Link lifetime, default `24` |
| `CLEANUP_INTERVAL_HOURS` | In-process cleanup interval, default `1` |
| `B2_PRESIGN_UPLOAD_SECONDS` | Direct upload URL lifetime, default `1800` |
| `B2_PRESIGN_DOWNLOAD_SECONDS` | Download URL lifetime, default `300` |
| `UPLOAD_TOKEN_TTL_SECONDS` | Upload token lifetime, default `1800` |
| `ORPHAN_GRACE_SECONDS` | Orphan object age before deletion, default `3600` |
| `CLEANUP_BATCH_SIZE` | Rows inspected per cleanup batch, default `200` |

Secrets belong on the backend only. The frontend needs only
`VITE_API_BASE_URL`.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm run lint
npm run build
```

The B2 tests use an in-memory fake, so they do not need cloud credentials.

## Deployment

See [deployment/README.md](deployment/README.md) for the full Vercel + Render +
Supabase + Backblaze B2 setup, CORS configuration, GitHub cleanup workflow, and
free-tier limitations.

The short version:

1. Create a Supabase project and use its session pooler URL.
2. Create a private B2 bucket and run `fix_b2_cors.py` to add the Vercel origin
   through the B2 Native API.
3. Deploy the API on Render with `render.yaml` and the B2/Supabase secrets.
4. Deploy `frontend/` on Vercel with `VITE_API_BASE_URL`.
5. Add the `CLEANUP_URL` and `CLEANUP_SECRET` GitHub Actions secrets.

## Security and limitations

- Anyone with a share link can download the file until expiry.
- There is no authentication, rate limiting, or malware scanning.
- The public authorize endpoint can be abused to request signed upload URLs.
  Add rate limiting or a CAPTCHA before promoting a public instance widely.
- Expired links are rejected immediately, but physical B2 deletion depends on
  the cleanup job.
- Free services can sleep, pause, change their allowances, or lose data. There
  are no backups on the free Supabase plan; export metadata separately if it
  matters.
- Existing local uploads are not migrated to B2 automatically.
- Run a production deployment with HTTPS and exact CORS origins.
