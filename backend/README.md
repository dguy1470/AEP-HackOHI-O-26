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

## Deploy

Any host that runs a Python ASGI app works (Render, Railway, Fly.io, a container,
etc.). Set the same environment variables there and point the frontend at the
deployed origin. Do not bake secrets into the image.
