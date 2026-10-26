# Deploy FileAway for $0/month

This guide uses four free tiers together:

| Layer | Service | Free-tier guardrail |
|---|---|---|
| Frontend | Vercel Hobby | Free, non-commercial, fair use |
| API | Render Free web service | 750 instance hours/month; sleeps after 15 minutes idle |
| Metadata database | Supabase Free PostgreSQL | 500 MB database; pauses after 7 days of low activity |
| File storage | Backblaze B2 | 10 GB stored; egress allowance is 3x average stored data |

Free allowances can change, and free services have no availability guarantee.
Check the provider pricing pages before relying on this for anything important.

The old Oracle Cloud files are archived in `deployment/legacy-oracle/` and are
not used.

## Architecture

```text
Browser
  |-- POST /uploads/authorize -----------> Render API
  |<-- presigned B2 PUT + upload token ----|
  |
  |-- PUT file ---------------------------> Backblaze B2 (signed Content-Type; size checked after)
  |
  |-- POST /uploads/complete ------------> Render API
  |                                         |-- HEAD object, verify size
  |                                         |-- insert metadata in Supabase
  |<-- share URL ---------------------------|
  |
Recipient
  |-- GET /files/share/{token} ----------> Render API (checks expiry)
  |<-- 302 to 5-minute presigned B2 URL ---|
  |-- GET file ---------------------------> Backblaze B2
```

- Uploads and downloads do not flow through Render, so the free instance does
  not serve 100 MB file bodies.
- A 302 redirect sends downloads directly to B2. Signed download URLs remain
  usable for up to 5 minutes after the link expiry time; the API itself rejects
  expired links immediately with `410`.
- Share links are random. There is no file listing endpoint.
- Local development keeps using SQLite and `uploads/`; production uses B2.

## Prerequisites

- A GitHub repository containing this project.
- Accounts at Supabase, Backblaze, Render, and Vercel.
- Python 3.10+ locally if you want to test or run helper scripts.

Do not put any secret in the frontend or commit it to the repository.

## 1. Create the Supabase database

1. Create a free Supabase project.
2. Open **Connect** and copy the **Session pooler** connection string. It uses
   port `5432` and an IPv4 host such as
   `aws-0-us-west-1.pooler.supabase.com`.
3. Use a URL shaped like this and add `?sslmode=require`:

```text
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Percent-encode special characters in the password. The application also accepts
`postgres://` and `postgresql://` URLs and converts them to the psycopg driver.

Use the session pooler rather than the transaction pooler on port `6543`;
psycopg can use prepared statements, which transaction pooling does not preserve.
Do not expose this URL to the frontend.

Tables are created by `python -m alembic upgrade head` during the Render start
command. The database only stores file metadata, so it stays small.

Supabase Free pauses a project after about 7 days of low activity. The cleanup
job in step 5 queries the database every 6 hours, which normally prevents this.
If a pause still happens, resume the project from the Supabase dashboard.

## 2. Create the Backblaze B2 bucket

1. Create a **private** bucket. Pick a region close to the Render region, for
   example `us-west-004`.
2. Create an application key restricted to this bucket with these capabilities:
   `listBuckets`, `listFiles`, `readFiles`, `writeFiles`, `deleteFiles`.
   `listFiles` is also required by `ListObjectVersions`, and `deleteFiles` is
   required to permanently remove every version.
3. Note the endpoint, region, bucket name, key ID, and application key. B2's S3
   endpoint looks like `https://s3.us-west-004.backblazeb2.com`.
4. Do not make the bucket public. Downloads use short-lived signed URLs.

The runtime key is used on Render only. It cannot change bucket CORS settings.

### Configure CORS

The browser uploads directly to B2, so B2 must allow the Vercel origin. Create a
temporary setup key with `writeBuckets` and `writeBucketCors` capabilities, then
run the helper from the project root with the B2 variables set:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:B2_ENDPOINT_URL = "https://s3.us-west-004.backblazeb2.com"
$env:B2_REGION = "us-west-004"
$env:B2_BUCKET = "your-bucket"
$env:B2_SETUP_KEY_ID = "your-setup-key-id"
$env:B2_SETUP_APP_KEY = "your-setup-application-key"
.\.venv\Scripts\python.exe deployment\configure_b2_cors.py `
  --origin "https://fileaway-share.vercel.app"
```

The rule allows `GET`, `PUT`, and `HEAD`, the exact origin
`https://fileaway-share.vercel.app`, and the `content-type` request header, then
caches the preflight for one hour. Add each Vercel preview or custom origin with
another `--origin` flag. Origins must be exact; CORS does not support subdomain
wildcards. Delete the temporary setup key after configuring CORS.

To verify the rule without changing it, use a key that can read bucket settings:

```powershell
python -c "import boto3, os; c=boto3.client('s3', endpoint_url=os.environ['B2_ENDPOINT_URL'], region_name=os.environ['B2_REGION'], aws_access_key_id=os.environ['B2_SETUP_KEY_ID'], aws_secret_access_key=os.environ['B2_SETUP_APP_KEY']); print(c.get_bucket_cors(Bucket=os.environ['B2_BUCKET']))"
```

Confirm the result includes `https://fileaway-share.vercel.app` in
`AllowedOrigins`, `PUT` in `AllowedMethods`, and `content-type` in
`AllowedHeaders`.

## 3. Deploy the API on Render

1. Push the repository to GitHub.
2. In Render, choose **New > Blueprint** and select the repository. Render reads
   `render.yaml`.
3. Fill in the environment variables marked as secrets in the blueprint:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase session pooler URL |
| `PUBLIC_BASE_URL` | `https://<service-name>.onrender.com` |
| `CORS_ORIGINS` | `https://fileaway-share.vercel.app` |
| `B2_ENDPOINT_URL` | `https://s3.<region>.backblazeb2.com` |
| `B2_REGION` | B2 region, for example `us-west-004` |
| `B2_BUCKET` | Private bucket name |
| `B2_KEY_ID` | Runtime key ID |
| `B2_APP_KEY` | Runtime application key |

The blueprint generates `UPLOAD_SIGNING_SECRET` and `CLEANUP_SECRET`. The start
command runs database migrations before starting one Uvicorn worker:

```text
python -m alembic upgrade head && python -m uvicorn app.main:app \
  --host 0.0.0.0 --port $PORT --workers 1 --no-access-log \
  --proxy-headers --forwarded-allow-ips=*
```

The free service sleeps after 15 minutes of inactivity and takes roughly a
minute to wake. The health check is `GET /health`.

If the `PUBLIC_BASE_URL` or `CORS_ORIGINS` value is wrong, uploads still work but
share links or browser API calls fail. Set the Render URL before deploying, or
update the variables and redeploy after Render assigns the URL.

## 4. Deploy the frontend on Vercel

1. Import the same GitHub repository into Vercel.
2. Set **Root Directory** to `frontend`. The Vite preset builds `npm run build`
   into `dist`.
3. Add this environment variable for Production, Preview, and Development:

```text
VITE_API_BASE_URL=https://<service-name>.onrender.com
```

4. Deploy, then add the resulting Vercel origin, for example
   `https://fileaway-share.vercel.app`, to `CORS_ORIGINS` on Render and to the B2
   CORS rule. Redeploy Render after changing environment variables.

Vercel Hobby is intended for non-commercial use and is subject to fair-use
limits.

## 5. Schedule cleanup on GitHub Actions

The API removes expired metadata and B2 objects at startup and hourly while it
is awake. A GitHub Actions workflow also calls the protected cleanup endpoint
every 6 hours, which wakes the free Render instance and keeps Supabase active.
The workflow retries transient network errors, `5xx` responses, and cold starts
three times. Wrong secrets and missing endpoints fail immediately with a clear
message.

1. In GitHub, open **Settings > Secrets and variables > Actions**.
2. Add `CLEANUP_URL` as `https://<service-name>.onrender.com/internal/cleanup`.
3. Add `CLEANUP_SECRET` with the value Render generated for `CLEANUP_SECRET`.
4. Enable Actions if the repository has them disabled.

The workflow is `.github/workflows/cleanup.yml`. You can run it manually from the
Actions tab. GitHub's scheduled workflows can be delayed; the in-process hourly
job covers most normal operation.

Manual check:

```bash
curl -X POST \
  -H "X-Cleanup-Secret: $CLEANUP_SECRET" \
  https://<service-name>.onrender.com/internal/cleanup
```

The endpoint returns `503` when `CLEANUP_SECRET` is unset, `401` for a wrong
secret, `409` if another cleanup is already running, and `500` when any expired
file could not be removed. A `500` response includes `expired_failed` and keeps
the metadata row so the next run retries instead of hiding the failure.

## Verify the deployment

1. Open `https://<service>.onrender.com/health`. The first request may take about
   a minute while Render wakes.
2. Upload a small file in the Vercel app.
3. Open the share link in a private window and confirm the download.
4. Confirm the B2 bucket contains an object under `uploads/`.
5. Check that the database row has `storage_backend = 'b2'`.
6. Wait for the link to expire or test a deliberately expired row. The API
   should return `410`, and cleanup should remove the B2 object and row. Confirm
   no old versions or delete markers remain.

## Operational notes

- **Cleanup is not exact-time.** Expired links are rejected immediately by the
  API, but physical deletion waits for the next cleanup run. If Render is asleep
  and the GitHub cron is delayed, objects can remain in B2 temporarily.
- **Orphan cleanup** removes every version of unreferenced B2 objects under
  `uploads/` older than `ORPHAN_GRACE_SECONDS` (1 hour). It never scans local
  storage, so local files are not deleted by the production job.
- **Mixed storage** is supported. Each row records `storage_backend`, so old
  local rows and new B2 rows can coexist.
- **Existing local data does not move automatically.** If you have old files in
  `uploads/`, copy them to B2 and update their rows before switching that
  deployment to B2; otherwise those links will return `404`.
- **Backups:** Supabase Free does not provide backups. Export metadata
  periodically with `pg_dump` if the links matter. Losing the database means the
  share tokens are lost; the orphan sweep will eventually delete unreferenced
  B2 objects.
- **Render disk is ephemeral.** Production files belong in B2, not on Render.
- **GitHub can disable scheduled workflows** after a long period of repository
  inactivity. If cleanup stops, open the Actions tab and re-enable the workflow.
- **B2 lifecycle rules are not used for deletion.** B2's S3 lifecycle expiration
  hides objects with delete markers rather than permanently deleting them.
  Application cleanup lists object versions and sends `DeleteObjects` with each
  `VersionId`, including delete markers. This permanently removes versioned
  objects and does not leave recoverable versions behind.
- **B2 file versions are handled.** B2 keeps file versions, so cleanup removes
  every version rather than relying on a name-only delete that would leave older
  versions behind. `DeleteObjects` per-object errors are treated as failures, so
  metadata is retained and retried instead of being reported as a successful
  deletion.

## Free-tier limits and limitations

- 100 MiB (104,857,600 bytes) is the application upload limit. The browser
  rejects larger files before uploading, and each presigned PUT signs the
  content type. `Content-Length` is deliberately not signed because browsers
  cannot set it explicitly; signing it makes B2 fail the CORS preflight.
  `/uploads/complete` performs a `HEAD` check before creating metadata. A
  mismatched or oversized object is deleted immediately, and an object that is
  never completed is removed by the orphan sweep after the grace period.
- Because `Content-Length` is not part of the signature, B2 may store an
  oversized body before `/uploads/complete` rejects it. The backend then deletes
  it and does not create a share record. If deletion fails, the unreferenced
  object is retried by orphan cleanup. A hard limit before upload would require
  a proxy upload or a storage upload mechanism that supports a signed
  `content-length-range`; this design does not provide that guarantee.
- The authorize endpoint is public and has no authentication or rate limiting.
  A malicious client can request signed upload URLs and store data in B2 for up
  to 30 minutes, or longer if it never completes and the cleanup job is down.
  Add rate limiting, a CAPTCHA, or authentication before promoting this widely.
- B2 free egress is 3x average monthly stored data. Heavy download traffic can
  exceed the free allowance.
- Render Free has 0.1 CPU and 512 MB RAM. It sleeps when idle and has no SLA.
- Supabase Free has 500 MB database, 5 GB egress, two active projects, and no
  backups. The database is metadata-only, so 500 MB is normally ample.
- Vercel Hobby is free for non-commercial use and subject to fair use.
- There is no malware scanning, password protection, or download analytics.
  Anyone with a link can download until expiry.

## Local development

Local mode needs no cloud accounts:

```dotenv
DATABASE_URL=sqlite:///./fileaway.db
UPLOAD_DIR=./uploads
PUBLIC_BASE_URL=http://127.0.0.1:8000
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
STORAGE_BACKEND=local
```

Install dependencies and run the API:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run the frontend in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

The frontend calls `/uploads/authorize`; in local mode the API returns
`{"mode":"proxy"}` and the browser uses the existing multipart `/upload`
endpoint.

## Troubleshooting

**Direct upload fails in the browser.** Confirm the B2 CORS rule contains the
exact origin `https://fileaway-share.vercel.app` and allows `PUT`, `GET`, `HEAD`,
and `content-type`. Check the B2 key has `writeFiles`. A `403` mentioning
`content-length` in the signed headers means the presigned URL was created by
older code; deploy the current `app/storage.py`. Also do not change the file or
its `Content-Type` between authorization and upload, because `Content-Type` is
signed.

**Share link returns 503.** The API could not create a presigned download URL.
Check the B2 runtime key, bucket name, region, and endpoint on Render.

**Share link returns 404.** The metadata row may point to a missing object, or an
old local row has not been moved to B2.

**"Storage is temporarily unavailable".** B2 credentials are missing or wrong,
or B2 rejected the request. Check Render logs. Never print the application key.

**Database connection errors.** Use the Supabase session pooler URL on port
`5432`, include `sslmode=require`, and percent-encode the password.

**First request is slow.** Render Free is waking from sleep. Retry after about a
minute.
