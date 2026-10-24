# Outage Concierge backend

A small FastAPI service that adds the things a static page can't do safely:

- **`POST /api/chat`** — proxies to Anthropic Claude with a server-side key, so the
  site can offer AI chat without asking every visitor for their own key.
- **`GET /api/geocode?q=`** and **`GET /api/reverse?lat=&lon=`** — proxy OpenStreetMap
  Nominatim with a proper User-Agent.
- **`POST /api/subscribe`** — sign up for email (SMTP) and/or SMS (Twilio) alerts.
- **`GET /api/health`** — shows which features are configured.
- Also serves the static app in `../outage-concierge/`.

## Run it

```bash
# from the repo root
cd backend
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your keys (optional)
uvicorn app:app --reload --port 8080
```

Open http://localhost:8080 — the app is served by the backend, and the chat/geocode
calls go through `/api/*`.

## Credentials

Everything is read from environment variables (see `.env.example`). `.env` is
gitignored and must never be committed. The service runs with **no** credentials:
chat returns a 503 until `ANTHROPIC_API_KEY` is set, and email/SMS log what they
*would* send instead of delivering (handy for demos).

## Deploy to Render (fully hosted)

The repo includes `render.yaml` (a Render Blueprint) at the root. Steps:

1. Push the repo to GitHub (already done: branch `outage-concierge`).
2. In Render: **New +  ->  Blueprint**, pick this repo/branch. Render reads
   `render.yaml`, installs `backend/requirements.txt`, and starts
   `uvicorn app:app --host 0.0.0.0 --port $PORT`.
3. After the first build, open the service's **Environment** tab and fill in the
   secret vars (they're defined in `render.yaml` with `sync:false`, so they start
   blank and are set only in the dashboard):
   - `ANTHROPIC_API_KEY` — enables server-side chat (optional).
   - `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASS`,
     `SMTP_FROM` — enables real email.
   - `ADMIN_TOKEN` — any long random string; guards the admin/test endpoints.
4. Save; Render redeploys. The app is live at `https://<name>.onrender.com`.

Notes:
- A 1 GB persistent disk is mounted at `/var/data` and `DB_PATH` points the SQLite
  database there, so subscribers survive redeploys.
- The free plan sleeps after inactivity; the first request after idle is slow.
- Gmail SMTP can be rate-limited from cloud hosts; a provider like Resend or
  SendGrid is more reliable for production email.
- **Never commit secrets.** They live only in Render's dashboard and local `.env`.

## Admin endpoints

Guarded by `ADMIN_TOKEN` (send it as the `X-Admin-Token` header):
`GET /api/subscribers`, `GET /api/sent-log`, `POST /api/send-test`,
`POST /api/broadcast`, `POST /api/storm-alert`.
