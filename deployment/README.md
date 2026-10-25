# FileAway: $0/month deployment guide

This guide deploys the existing React frontend to Vercel and the existing
FastAPI backend to an Oracle Cloud Always Free Ampere A1 VM. SQLite and uploaded
files live on the VM's boot volume. Nginx terminates HTTPS with a free Let's
Encrypt certificate and systemd supervises FastAPI.

```
Browser -> Vercel (React/Vite)
        -> https://<name>.duckdns.org (DuckDNS -> OCI public IP)
        -> Nginx :443
        -> FastAPI 127.0.0.1:8000
        -> SQLite + /var/lib/fileaway/uploads
```

Port 8000 is never exposed publicly.

## 1. Verified free-tier facts (checked 2026-10-25)

Sources:

- Oracle [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- Oracle [Always Free Ampere A1 allocation update (July 2026)](https://community.oracle.com/customerconnect/discussion/970310/oci-always-free-updated-ampere-a1-compute-allocation)
- Oracle [Cloud Free Tier](https://www.oracle.com/cloud/free/)
- [DuckDNS API specification](https://www.duckdns.org/spec.jsp)

Key points:

- Always Free Ampere A1 is currently **2 OCPUs and 12 GB RAM total**, expressed
  as 1,500 OCPU-hours and 9,000 GB-hours per month. This is lower than the older
  4 OCPU / 24 GB figure. Do not size the VM above the documented allocation.
- Always Free block storage is **200 GB total**, including boot volumes. The
  default boot volume is 50 GB (minimum depends on image, commonly 47-50 GB).
  Five volume backups are included.
- Always Free instances must be created in the tenancy's **home region**.
  South Korea North (Chuncheon) is excluded for Always Free A1.
- Capacity is **not guaranteed**. "Out of host capacity" is possible even in the
  home region. Retry later or try another availability domain. Do not pay to
  bypass it under this plan.
- Oracle may reclaim idle Always Free compute. As of the cited documentation, a
  VM is idle when, over 7 days, **95th-percentile CPU < 20%, network < 20%, and
  memory < 20%** (memory applies to A1). This app may legitimately be idle.
  **No artificial traffic, CPU burners, or health-check loops are included.**
  The risk is documented in section 13.
- Accounts idle for 30 days or more may be suspended or terminated. One free
  account per person is permitted.
- Vercel's free plan, DuckDNS, and Let's Encrypt certificates are free. This
  repository does not create paid resources.

Cost safety:

- Do not upgrade to Pay As You Go. Do not add paid block volumes, extra volume
  backups, load balancers, NAT gateways, paid DNS, or Vercel add-ons.
- A public subnet with an Internet Gateway is used. No NAT Gateway is needed.
- Free-tier limits, capacity, and reclamation policies can change. Check the
  Oracle console before creating resources.

## 2. Prerequisites

- Oracle Cloud account with Always Free eligibility and your home region known.
- DuckDNS subdomain and token (free).
- Vercel account connected to the Git repository.
- SSH key pair for the VM.
- The repository pushed to a Git remote.
- Optional: a local copy of an existing `fileaway.db` and `uploads/` directory.

## 3. Create the Oracle Cloud VM

In the OCI Console:

1. Compute -> Instances -> Create instance.
2. Shape: `VM.Standard.A1.Flex`, **within 2 OCPU / 12 GB total**. An image
   labelled "Always Free Eligible" is required.
3. Image: Ubuntu 24.04 LTS (or a currently supported Ubuntu LTS).
4. Boot volume: default (about 50 GB) is enough and stays within 200 GB.
5. Networking: create/select a VCN with a **public subnet** and Internet
   Gateway; assign a public IPv4 address.
6. Add your SSH public key.
7. Do not add paid block volumes or extra services.

After creation, the VCN Security List (or NSG) must allow:

| Direction | Port | Source |
| --- | --- | --- |
| Ingress | 22/tcp | Your IP only, not `0.0.0.0/0` |
| Ingress | 80/tcp | `0.0.0.0/0` (HTTP-01 + redirect) |
| Ingress | 443/tcp | `0.0.0.0/0` |
| Egress | all | Default |

Do **not** open 8000, 5432, or any database port.

Connect:

```bash
ssh -i /path/to/key ubuntu@<VM_PUBLIC_IP>
sudo apt-get update
sudo apt-get upgrade -y
sudo timedatectl set-timezone UTC
```

The VM's ephemeral public IP normally survives reboots and stop/start. If the
instance is recreated, the IP may change; update DuckDNS (section 7).

## 4. Get the code onto the VM

Run this on the VM (use an SSH deploy key for a private repository; never embed
a token in a command that will be stored in shell history):

```bash
sudo mkdir -p /opt
sudo git clone https://github.com/<you>/<repo>.git /opt/fileaway
```

The install script expects `/opt/fileaway`. A different path is supported only
if you also set `APP_DIR` consistently for the scripts and systemd units.

`update-app.sh` runs `git pull` as root. For a **private** repository, configure
a read-only deploy key in `/root/.ssh` (or a root credential helper) before
cloning, use the SSH clone URL, and never put a token in the repository URL.
For a public repository, the HTTPS URL works as shown. Do not commit secrets.

## 5. Install backend dependencies

From `/opt/fileaway`:

```bash
sudo bash deployment/scripts/install-backend.sh
```

The script:

- installs Python, venv, Nginx, certbot, SQLite CLI, UFW, git, and curl;
- creates the non-root `fileaway` system user;
- creates `/var/lib/fileaway`, `/etc/fileaway`, `/var/backups/fileaway`;
- creates a virtual environment and installs `requirements.txt`;
- installs systemd units and the Nginx site config;
- installs and enables the daily backup timer;
- enables, but does **not** start, `fileaway.service`;
- does not open the firewall or obtain certificates.

## 6. Configure the backend environment

Edit the environment file. It is created with `root:fileaway` and mode `0640`.

```bash
sudoedit /etc/fileaway/backend.env
```

Set:

```dotenv
DATABASE_URL=sqlite:////var/lib/fileaway/fileaway.db
UPLOAD_DIR=/var/lib/fileaway/uploads
PUBLIC_BASE_URL=https://<name>.duckdns.org
CORS_ORIGINS=https://<your-project>.vercel.app
TZ=UTC
```

Rules:

- `PUBLIC_BASE_URL` must be the public HTTPS backend origin, without a trailing
  slash. This is used to build every share link.
- `CORS_ORIGINS` is an exact comma-separated allowlist. No `*`.
- Keep the file outside the repository. Never commit it.
- After changing environment values, restart: `sudo systemctl restart fileaway`.

If you have an existing local database and uploads to migrate, copy them before
running migrations:

```bash
# From your local machine
scp fileaway.db ubuntu@<VM_PUBLIC_IP>:/tmp/fileaway.db
scp -r uploads ubuntu@<VM_PUBLIC_IP>:/tmp/uploads

# On the VM
sudo install -o fileaway -g fileaway -m 0640 /tmp/fileaway.db /var/lib/fileaway/fileaway.db
sudo cp -a /tmp/uploads/. /var/lib/fileaway/uploads/
sudo chown -R fileaway:fileaway /var/lib/fileaway
sudo chmod 0750 /var/lib/fileaway /var/lib/fileaway/uploads
```

Do not delete the local copy until the VM is verified.

## 7. Database migrations

```bash
sudo /opt/fileaway/deployment/scripts/migrate.sh
```

The script backs up an existing database first, then runs `alembic upgrade head`
as the `fileaway` user. The migrations were made database-agnostic:

- They create the base `files` table on a fresh database (there is no older
  create-table revision in this repository).
- They skip columns that already exist. Existing databases created by SQLAlchemy
  `create_all` without an `alembic_version` row are stamped safely by running
  the no-op upgrades.
- Legacy rows get generated share tokens, 24-hour expiry, content type, and file
  size without deleting rows.
- No PostgreSQL-only `gen_random_uuid()` or `INTERVAL` SQL remains.

The migration was tested against fresh SQLite, a legacy 4-column SQLite database
with data, and an existing current-schema SQLite database with data.

Do not use `app/create_tables.py` in production; it bypasses migration history.

## 8. Start FastAPI

```bash
sudo systemctl start fileaway
sudo systemctl status fileaway
curl -fsS http://127.0.0.1:8000/health
```

Logs:

```bash
sudo journalctl -u fileaway -f
```

The unit binds only to `127.0.0.1:8000`, runs one worker (the cleanup scheduler
must not be duplicated), restarts on failure, and uses systemd sandboxing.
Startup performs one immediate expired-file cleanup, then runs every hour.

## 9. Configure Nginx and the firewall

Edit `/etc/nginx/sites-available/fileaway` and replace
`your-subdomain.duckdns.org` with your DuckDNS hostname (the HTTPS helper does
this automatically too).

```bash
sudo ln -s /etc/nginx/sites-available/fileaway /etc/nginx/sites-enabled/fileaway
sudo nginx -t
sudo systemctl reload nginx
```

The Nginx limit is `101m` on purpose: FastAPI enforces exactly 100 MiB per file,
while Nginx allows a small amount of multipart overhead. Frontend, FastAPI, and
Nginx were checked against the 100 MiB requirement.

Enable UFW. SSH is restricted to one source IP/CIDR; the script refuses to open
SSH to the world:

```bash
sudo SSH_ALLOW_FROM=<YOUR_IP>/32 \
  bash /opt/fileaway/deployment/scripts/configure-firewall.sh
```

If you are already connected over SSH and omit `SSH_ALLOW_FROM`, the script uses
your current client IP. If SSH is on another port:

```bash
sudo SSH_PORT=<port> SSH_ALLOW_FROM=<YOUR_IP>/32 \
  bash /opt/fileaway/deployment/scripts/configure-firewall.sh
```

This allows only SSH (default 22) from that source, plus 80 and 443. Mirror the
rules in the OCI Security List. Restrict SSH to your IP there as well.

## 10. DuckDNS

1. Sign in at <https://www.duckdns.org>.
2. Create a subdomain and copy the account token.
3. On the VM:

```bash
sudo install -m 0600 -o root -g root \
  /opt/fileaway/deployment/env/duckdns.env.example \
  /etc/fileaway/duckdns.env
sudoedit /etc/fileaway/duckdns.env
sudo /opt/fileaway/deployment/scripts/update-duckdns.sh
```

Verify:

```bash
curl -fsS https://api.ipify.org; echo
getent ahostsv4 <name>.duckdns.org | head
```

Both addresses must match. If you recreate the VM or its public IP changes,
rerun `update-duckdns.sh`. DuckDNS is free; do not use a paid DNS provider.

## 11. HTTPS with Let's Encrypt

```bash
sudo /opt/fileaway/deployment/scripts/enable-https.sh \
  <name>.duckdns.org you@example.com
```

The script checks DNS, updates `server_name`, enables the site, obtains a
certificate with the Nginx plugin, redirects HTTP to HTTPS, reloads Nginx, tests
`https://<domain>/health`, and runs `certbot renew --dry-run`.

Certificate renewal is handled by the certbot systemd timer:

```bash
systemctl list-timers | grep certbot
sudo certbot certificates
sudo certbot renew --dry-run
```

If issuance fails, check DNS, ports 80/443 in OCI and UFW, any stale AAAA
records, and Let's Encrypt rate limits. Do not disable HTTPS verification.

Finally confirm `PUBLIC_BASE_URL=https://<domain>` and restart the backend:

```bash
sudo systemctl restart fileaway
curl -fsS https://<domain>/health
```

## 12. Deploy the frontend to Vercel

Vercel project settings:

| Setting | Value |
| --- | --- |
| Root Directory | `frontend` |
| Framework Preset | Vite |
| Install Command | `npm install` |
| Build Command | `npm run build` |
| Output Directory | `dist` |

Environment variable (Production):

```text
VITE_API_BASE_URL=https://<name>.duckdns.org
```

Vite embeds environment variables at build time, so change the value and
redeploy. The frontend must not contain backend secrets; there are none.

Add the exact Vercel production origin to `CORS_ORIGINS` on the VM, for example:

```dotenv
CORS_ORIGINS=https://<your-project>.vercel.app
```

Restart `fileaway` after editing the env file. Preview deployments use different
hostnames and will be blocked unless explicitly added.

Do not add a paid custom domain; the free `*.vercel.app` domain and DuckDNS are
sufficient.

## 13. Verify the application

Backend:

```bash
curl -fsS https://<name>.duckdns.org/health
printf 'hello\n' > /tmp/fileaway-test.txt
curl -fsS -F uploaded_file=@/tmp/fileaway-test.txt \
  https://<name>.duckdns.org/upload
```

Expected response contains a `share_url` beginning with
`https://<name>.duckdns.org/files/share/`. Open it from a different network or
phone. It must download; it must not point at localhost.

Confirm:

- Invalid token -> HTTP 404.
- Expired link -> HTTP 410 until cleanup, then 404.
- `GET /files` and `POST /files` -> 404. There is no public listing endpoint.
- Share tokens are 256-bit `secrets.token_urlsafe(32)` values and are not logged.
- Uploaded filename is reduced to its basename; stored filenames are UUIDs.
- A 100 MiB file is accepted; over 100 MiB is rejected with 413. Nginx's
  transport limit is slightly higher to account for multipart overhead.
- CORS allows only the configured Vercel origin (and optional local origins).
- Cleanup runs at startup and hourly. Restarting the service does not lose data.

## 14. Backups and restore

The backup timer runs daily at 03:15 UTC with a random delay. It creates:

- a consistent SQLite copy using `sqlite3 .backup`;
- the `uploads/` directory;
- a SHA-256 checksum;
- 7-day local retention (configurable with `RETENTION_DAYS`).

Manual backup:

```bash
sudo /opt/fileaway/deployment/scripts/backup.sh
ls -l /var/backups/fileaway
```

Test restoration into a separate directory. This does not touch live data:

```bash
sudo /opt/fileaway/deployment/scripts/test-restore.sh
```

Restore an archive to a test location:

```bash
sudo /opt/fileaway/deployment/scripts/restore.sh \
  /var/backups/fileaway/fileaway-YYYYmmddTHHMMSSZ.tar.gz \
  /tmp/fileaway-restore
```

The restore script refuses a non-empty target, checks the checksum, extracts,
validates the database with `PRAGMA integrity_check`, and prints the exact
promotion commands. It never writes directly to `/var/lib/fileaway`.

Backups on the same VM are **not disaster recovery**. Copy them off the VM and
keep at least one copy on your PC or password-protected storage:

```bash
# On the VM: make it readable by your SSH user, then copy from your PC
sudo install -m 0600 -o ubuntu -g ubuntu \
  /var/backups/fileaway/fileaway-YYYYmmddTHHMMSSZ.tar.gz* /home/ubuntu/
# From your PC
scp ubuntu@<VM_PUBLIC_IP>:/home/ubuntu/fileaway-YYYYmmddTHHMMSSZ.tar.gz* .
```

The environment file is intentionally not in backups because it can contain
deployment-specific values. Store a copy of `/etc/fileaway/backend.env` and the
DuckDNS token in a password manager, never in Git.

To promote a verified restore, follow the commands printed by `restore.sh`:
stop the service, copy the database and uploads, preserve `fileaway` ownership,
start the service, and run `healthcheck.sh`.

## 15. Monitoring and health checks

```bash
sudo /opt/fileaway/deployment/scripts/healthcheck.sh <name>.duckdns.org
```

It checks systemd, Nginx configuration, SQLite via `/health`, optional public
HTTPS, and disk usage. Diagnostics:

```bash
sudo systemctl status fileaway nginx
sudo journalctl -u fileaway -n 100 --no-pager
sudo nginx -t
sudo certbot certificates
df -h /var/lib/fileaway
```

These are manual diagnostics, not an automated traffic generator.

## 16. Updating

Frontend: push to the connected Git branch; Vercel rebuilds automatically.

Backend:

```bash
cd /opt/fileaway
sudo bash deployment/scripts/update-app.sh
```

The script creates a backup, pulls fast-forward only, installs dependencies, runs
migrations, restarts the service, and runs the health check. It never deletes
`/var/lib/fileaway` or uploads. Existing uploaded files are removed only by the
24-hour expiry cleanup.

Manual equivalent:

```bash
sudo /opt/fileaway/deployment/scripts/backup.sh
cd /opt/fileaway
sudo git -c safe.directory=/opt/fileaway pull --ff-only
sudo .venv/bin/python -m pip install -r requirements.txt
sudo deployment/scripts/migrate.sh --no-backup
sudo systemctl restart fileaway
sudo deployment/scripts/healthcheck.sh
```

OS updates and certificate renewals are independent:

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo reboot
```

After a reboot, verify `fileaway`, `nginx`, and the certbot timer are active.

## 17. Idle reclamation risk and recovery

This deployment deliberately does **not** keep the VM artificially busy. Oracle
may stop or reclaim an Always Free instance that is idle under its documented
thresholds. There is no way to guarantee 24/7 free uptime.

- If the instance is stopped but the boot volume exists, try starting it again
  in the OCI Console. Data on the boot volume normally remains.
- If the instance is reclaimed, create a new Always Free A1 VM in the home
  region when capacity is available. You can try using the existing boot volume,
  or provision Ubuntu and restore the latest off-VM backup.
- After any recreate or IP change, rerun `update-duckdns.sh`.
- Certificates live on the boot volume. If you rebuild from scratch, rerun
  `enable-https.sh`.
- If the same DuckDNS hostname is kept, the Vercel configuration does not need
  to change.
- Out-of-capacity errors are an OCI capacity issue, not an application issue.
  Do not upgrade to Pay As You Go to work around them under this plan.

Recommended recovery order:

1. Download the newest off-VM backup.
2. Provision a new Ubuntu VM.
3. Run `install-backend.sh`.
4. Restore `/etc/fileaway/backend.env` (or recreate it).
5. Restore database and uploads into `/var/lib/fileaway` using the verified
   restore procedure.
6. Start `fileaway`, check `/health`, update DuckDNS, and obtain HTTPS.

## 18. Security notes

- No public file listing or token enumeration endpoint exists.
- Share tokens are random 256-bit URL-safe strings; tokens are not written to
  application logs. Uvicorn access logging is disabled and Nginx does not log
  `/files/share/` request URIs, which contain tokens.
- Stored filenames are UUIDs. Download paths are resolved and rejected if they
  escape the upload directory.
- Original filenames are reduced to a basename and control characters removed.
- CORS uses an exact allowlist and disables credentialed requests.
- Uploads are streamed in 1 MiB chunks and partial files are deleted on error.
- The 100 MiB limit is enforced in the browser, FastAPI, and Nginx.
- FastAPI binds to localhost; only Nginx exposes 80/443.
- `backend.env` is `root:fileaway` mode `0640`; DuckDNS secrets are root-only.
- systemd runs the app as a dedicated non-root user with filesystem hardening.
- Upload rate limiting in Nginx reduces trivial disk-filling abuse.
- Residual risks: no user authentication, no per-file encryption, and no overall
  storage quota. Monitor disk usage and keep the 24-hour cleanup running. Do not
  publish the backend URL as an anonymous general-purpose file host.

## 19. Troubleshooting

| Symptom | Check |
| --- | --- |
| 502 Bad Gateway | `systemctl status fileaway`, `journalctl -u fileaway` |
| 413 on a file under 100 MB | multipart overhead; Nginx must be at least `101m` |
| Share URL says `127.0.0.1` | `PUBLIC_BASE_URL` unset or service not restarted |
| CORS error in browser | exact origin in `CORS_ORIGINS`; restart backend |
| HTTPS issuance fails | DNS match, ports 80/443, DuckDNS, AAAA records, rate limits |
| `database is locked` | long backup/migration or another writer; check processes |
| Permission denied in `/var/lib/fileaway` | `chown -R fileaway:fileaway`, mode `0750` |
| Expired rows not deleted | service logs, single worker, scheduler started |
| Out of host capacity | OCI capacity; retry later/another AD; do not pay |

## 20. Local checks before deploying

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip
cp .env.example .env
.venv/bin/python -m alembic upgrade head        # fresh local database
.venv/bin/python -m pytest -q                   # Windows: .venv\Scripts\python

cd frontend
npm install
npm run lint
npm run build
```

The test suite covers upload/download, invalid and expired tokens, the 100 MiB
limit, filename sanitization, path traversal, CORS, cleanup success/failure,
public URL generation, removed listing endpoints, and Alembic upgrades for
fresh, legacy, short-token, and current-schema SQLite databases.

The deployment scripts were syntax-checked with Bash and the backup/restore flow
was exercised in a temporary directory. Nginx configuration and systemd units
still need a final `nginx -t`, `systemctl daemon-reload`, and service start on
the actual Ubuntu VM; those cannot be validated from Windows.

## 21. Manual checklist

- [ ] Create the Always Free Ubuntu 24.04 A1 VM within 2 OCPU / 12 GB.
- [ ] Configure OCI Security List: SSH from your IP, 80, 443 only.
- [ ] Clone the repository to `/opt/fileaway`.
- [ ] Run `install-backend.sh`.
- [ ] Edit `/etc/fileaway/backend.env` with real paths, public URL, CORS origin.
- [ ] Copy existing `fileaway.db` and `uploads/` if needed.
- [ ] Run `migrate.sh`.
- [ ] Start `fileaway` and check local `/health`.
- [ ] Configure Nginx `server_name`, enable the site, reload.
- [ ] Enable UFW with `configure-firewall.sh`.
- [ ] Configure DuckDNS and run `update-duckdns.sh`.
- [ ] Run `enable-https.sh` with a real email.
- [ ] Deploy the frontend on Vercel with `VITE_API_BASE_URL`.
- [ ] Add the Vercel origin to `CORS_ORIGINS` and restart the backend.
- [ ] Test upload and share link from outside your home network.
- [ ] Test 100 MiB acceptance and rejection.
- [ ] Run `backup.sh`, `test-restore.sh`, and copy a backup off the VM.
- [ ] Confirm certbot renewal timer and run `certbot renew --dry-run`.
- [ ] Review Oracle usage/billing monthly; stay on Always Free.
- [ ] Accept the idle-reclamation risk and keep off-VM backups.
