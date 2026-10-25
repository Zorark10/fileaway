# FileAway frontend

React + Vite + Tailwind UI for the FileAway API.

## Local development

```bash
npm install
cp .env.example .env
npm run dev
```

`VITE_API_BASE_URL` defaults to `http://127.0.0.1:8000` when the page is opened
on localhost. The Vite dev proxy is only a convenience; uploads use the explicit
API base.

## Checks

```bash
npm run lint
npm run build
```

## Vercel

| Setting | Value |
| --- | --- |
| Root Directory | `frontend` |
| Framework Preset | Vite |
| Install Command | `npm install` |
| Build Command | `npm run build` |
| Output Directory | `dist` |

Add this environment variable for Production, then redeploy:

```text
VITE_API_BASE_URL=https://<name>.duckdns.org
```

Vite embeds variables at build time. The backend must allow the exact Vercel
origin in `CORS_ORIGINS`. See `../deployment/README.md` for the full deployment
guide.
