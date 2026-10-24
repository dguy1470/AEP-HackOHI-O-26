"""
Outage Concierge backend.

A small FastAPI service that:
  - proxies chat requests to Anthropic's Claude using a SERVER-SIDE key
    (so the static site no longer needs a bring-your-own-key field),
  - proxies address lookups to OpenStreetMap Nominatim with a proper User-Agent,
  - accepts notification sign-ups (email via Resend API),
  - serves the static app in outage-concierge/.

Nothing secret is committed: all credentials come from environment variables
(see .env.example). The service runs fine with no credentials — email then
logs what it *would* send instead of failing, which keeps local demos simple.
"""
import os
import time
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

import db

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv is optional
    pass

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("concierge")

# ---- Configuration (all via environment; see .env.example) ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

NOMINATIM = "https://nominatim.openstreetmap.org"
USER_AGENT = os.getenv("NOMINATIM_USER_AGENT", "OutageConcierge/1.0 (hackathon prototype)")

# Resend API (https://resend.com) — much simpler than raw SMTP.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
# Default sender. Resend's onboarding address works on free tier without a custom domain.
RESEND_FROM = os.getenv("RESEND_FROM", "Outage Concierge <onboarding@resend.dev>")
RESEND_URL = "https://api.resend.com/emails"

# Allowed browser origins. "*" is fine for a local prototype; lock this down in prod.
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# Token guarding admin endpoints (list subscribers, broadcast). Set in .env.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

EMAIL_LIVE = bool(RESEND_API_KEY)

SYSTEM_PROMPT = (
    "You are the AI Outage Concierge for an electric utility, talking with a customer who may be "
    "worried about a storm and losing power. Be warm, calm and reassuring, like a caring human "
    "support agent. Keep replies short (2-4 sentences). Always end by asking one gentle, relevant "
    "follow-up question to help them feel supported. Use the SITUATION context for facts; never "
    "invent outage data or promise exact restoration times — call numbers estimates. For any "
    "life-threatening emergency or downed power line, tell them to call 911 and stay 30 feet away. "
    "Do not collect names, addresses or account numbers."
)

app = FastAPI(title="Outage Concierge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()
    log.info("DB ready. email_live=%s chat=%s", EMAIL_LIVE, bool(ANTHROPIC_API_KEY))
    if not EMAIL_LIVE:
        log.info("Email is in DEMO mode — set RESEND_API_KEY in .env to send for real.")


# ---- Models ----
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    situation: str = ""


class Subscription(BaseModel):
    email: Optional[EmailStr] = None
    channels: list[str] = Field(default_factory=lambda: ["email"])
    area: str = ""  # a free-text label like "P-ABC123" or a town


# ---- Chat proxy ----
@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "Chat is not configured on the server (missing ANTHROPIC_API_KEY).")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT + ("\n\nSITUATION: " + req.situation if req.situation else ""),
        "messages": [m.model_dump() for m in req.messages][-10:],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Upstream error: {e}")
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise HTTPException(r.status_code, f"Claude error: {msg}")
    data = r.json()
    text = ""
    if isinstance(data.get("content"), list) and data["content"]:
        text = data["content"][0].get("text", "")
    return {"reply": text}


# ---- Geocoding proxies ----
@app.get("/api/geocode")
async def geocode(q: str):
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(f"{NOMINATIM}/search", params={"format": "json", "limit": 1, "q": q})
    return JSONResponse(r.json())


@app.get("/api/reverse")
async def reverse(lat: float, lon: float):
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(
            f"{NOMINATIM}/reverse",
            params={"format": "json", "zoom": 18, "lat": lat, "lon": lon},
        )
    return JSONResponse(r.json())


# ---- Email via Resend ----
async def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email via the Resend API. Returns True if delivered, False in demo mode.
    Raises on API errors so callers can surface them."""
    if not EMAIL_LIVE:
        log.info("[email demo] would send to %s: %s", to, subject)
        db.log_send("email", to, subject, body, "demo", "RESEND_API_KEY not set")
        return False
    payload = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(RESEND_URL, headers=headers, json=payload)
    if r.status_code in (200, 201, 202):
        db.log_send("email", to, subject, body, "sent")
        log.info("[email sent] to=%s", to)
        return True
    # Surface the error
    try:
        err = r.json().get("message") or r.json().get("name") or r.text
    except Exception:
        err = r.text
    db.log_send("email", to, subject, body, "error", err)
    log.warning("[email error] to=%s status=%d err=%s", to, r.status_code, err)
    raise HTTPException(502, f"Resend error ({r.status_code}): {err}")


@app.post("/api/subscribe")
async def subscribe(sub: Subscription):
    if not sub.email:
        raise HTTPException(400, "An email address is required to sign up.")
    welcome = (
        "You're signed up for Outage Concierge alerts"
        + (f" for {sub.area}" if sub.area else "")
        + ". We'll let you know before and during any outage. Reply STOP to opt out."
    )
    db.upsert_subscriber(sub.email, None, ["email"], sub.area)
    results = {}
    try:
        results["email"] = await send_email(sub.email, "Outage alerts are on ⚡", welcome)
    except HTTPException as e:
        results["email"] = False
        results["email_error"] = e.detail
    demo = not EMAIL_LIVE
    note = ""
    if demo:
        note = "Server is in demo mode (RESEND_API_KEY not set) — no real email was sent."
    elif not results.get("email"):
        note = "Email failed: " + results.get("email_error", "unknown error")
    return {"ok": True, "saved": True, "delivered": results, "note": note}


@app.post("/api/unsubscribe")
async def unsubscribe(sub: Subscription):
    db.deactivate(email=sub.email)
    return {"ok": True}


# Public test-email endpoint — lightly rate-limited per address.
_last_test: dict = {}
TEST_COOLDOWN_S = int(os.getenv("TEST_COOLDOWN_S", "30"))


class PublicTest(BaseModel):
    email: EmailStr


@app.post("/api/test-email")
async def public_test_email(req: PublicTest):
    now = time.time()
    if now - _last_test.get(req.email, 0) < TEST_COOLDOWN_S:
        raise HTTPException(429, "Please wait a moment before sending another test.")
    _last_test[req.email] = now
    body = ("This is a test message from the Outage Concierge. If you received it, "
            "email alerts are working. We'll only message you about outages you signed up for.")
    if not EMAIL_LIVE:
        db.log_send("email", req.email, "Outage Concierge test", body, "demo", "RESEND_API_KEY not set")
        return {"ok": True, "sent": False, "note": "Server is in demo mode — set RESEND_API_KEY in .env."}
    try:
        await send_email(req.email, "Outage Concierge test email ⚡", body)
    except HTTPException as e:
        raise HTTPException(502, f"Send failed: {e.detail}")
    return {"ok": True, "sent": True}


# ---- Storm alert ----
_last_alert: dict = {}
ALERT_COOLDOWN_S = int(os.getenv("ALERT_COOLDOWN_S", "120"))


class StormAlert(BaseModel):
    area: str = ""
    message: str = "⚡ A storm is reaching your area and may cause outages. Charge your phone, keep water and a flashlight handy, and stay 30 ft from any downed line."
    subject: str = "⚡ Storm heading your way"


@app.post("/api/storm-alert")
async def storm_alert(a: StormAlert):
    now = time.time()
    key = a.area or "*all*"
    if now - _last_alert.get(key, 0) < ALERT_COOLDOWN_S:
        return {"ok": True, "skipped": "cooldown", "sent": {"email": 0}}
    _last_alert[key] = now
    sent = {"email": 0, "errors": 0}
    for s in db.list_subscribers():
        if a.area and s.get("area") and s["area"] != a.area:
            continue
        try:
            if s["email"] and await send_email(s["email"], a.subject, a.message):
                sent["email"] += 1
        except Exception:
            sent["errors"] += 1
    return {"ok": True, "sent": sent}


# ---- Admin (guarded by ADMIN_TOKEN) ----
def _require_admin(x_admin_token: Optional[str]):
    if not ADMIN_TOKEN:
        raise HTTPException(403, "Admin endpoints are disabled (set ADMIN_TOKEN).")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Bad admin token.")


@app.get("/api/subscribers")
async def subscribers(x_admin_token: Optional[str] = Header(default=None)):
    _require_admin(x_admin_token)
    return {"subscribers": db.list_subscribers()}


@app.get("/api/sent-log")
async def sent_log(x_admin_token: Optional[str] = Header(default=None)):
    _require_admin(x_admin_token)
    return {"log": db.recent_log()}


class Broadcast(BaseModel):
    subject: str = "Outage alert"
    message: str


@app.post("/api/broadcast")
async def broadcast(b: Broadcast, x_admin_token: Optional[str] = Header(default=None)):
    """Email a message to every active subscriber."""
    _require_admin(x_admin_token)
    sent = {"email": 0, "errors": 0}
    for s in db.list_subscribers():
        try:
            if s["email"] and await send_email(s["email"], b.subject, b.message):
                sent["email"] += 1
        except Exception:
            sent["errors"] += 1
    return {"ok": True, "sent": sent}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "chat": bool(ANTHROPIC_API_KEY),
        "email": EMAIL_LIVE,
    }


# ---- Serve the static app (keep this mount last so /api/* wins) ----
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "outage-concierge")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

import db

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv is optional
    pass

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("concierge")

# ---- Configuration (all via environment; see .env.example) ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

NOMINATIM = "https://nominatim.openstreetmap.org"
USER_AGENT = os.getenv("NOMINATIM_USER_AGENT", "OutageConcierge/1.0 (hackathon prototype)")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "noreply@example.com")

# Allowed browser origins. "*" is fine for a local prototype; lock this down in prod.
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# Token guarding admin endpoints (list subscribers, broadcast). Set in .env.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

EMAIL_LIVE = bool(SMTP_HOST and SMTP_FROM)

SYSTEM_PROMPT = (
    "You are the AI Outage Concierge for an electric utility, talking with a customer who may be "
    "worried about a storm and losing power. Be warm, calm and reassuring, like a caring human "
    "support agent. Keep replies short (2-4 sentences). Always end by asking one gentle, relevant "
    "follow-up question to help them feel supported. Use the SITUATION context for facts; never "
    "invent outage data or promise exact restoration times — call numbers estimates. For any "
    "life-threatening emergency or downed power line, tell them to call 911 and stay 30 feet away. "
    "Do not collect names, addresses or account numbers."
)

app = FastAPI(title="Outage Concierge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()
    log.info("DB ready. email_live=%s chat=%s",
             EMAIL_LIVE, bool(ANTHROPIC_API_KEY))
    if not EMAIL_LIVE:
        log.info("Email is in DEMO mode — set SMTP_HOST/SMTP_USER/SMTP_PASS in .env to send for real.")


# ---- Models ----
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    situation: str = ""


class Subscription(BaseModel):
    email: Optional[EmailStr] = None
    channels: list[str] = Field(default_factory=lambda: ["email"])  # email only
    area: str = ""  # a free-text label like "P-ABC123" or a town


# ---- Chat proxy ----
@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "Chat is not configured on the server (missing ANTHROPIC_API_KEY).")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT + ("\n\nSITUATION: " + req.situation if req.situation else ""),
        "messages": [m.model_dump() for m in req.messages][-10:],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Upstream error: {e}")
    if r.status_code != 200:
        # Pass through Anthropic's error message without leaking the key.
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise HTTPException(r.status_code, f"Claude error: {msg}")
    data = r.json()
    text = ""
    if isinstance(data.get("content"), list) and data["content"]:
        text = data["content"][0].get("text", "")
    return {"reply": text}


# ---- Geocoding proxies (server adds the required User-Agent) ----
@app.get("/api/geocode")
async def geocode(q: str):
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(f"{NOMINATIM}/search", params={"format": "json", "limit": 1, "q": q})
    return JSONResponse(r.json())


@app.get("/api/reverse")
async def reverse(lat: float, lon: float):
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(
            f"{NOMINATIM}/reverse",
            params={"format": "json", "zoom": 18, "lat": lat, "lon": lon},
        )
    return JSONResponse(r.json())


# ---- Sending (records every attempt in the DB) ----
def send_email(to: str, subject: str, body: str) -> bool:
    """Send a real email via SMTP. Returns True if delivered, False if in demo mode.
    Raises on a genuine SMTP failure so the caller can surface it."""
    if not EMAIL_LIVE:
        log.info("[email demo] would send to %s: %s", to, subject)
        db.log_send("email", to, subject, body, "demo", "SMTP not configured")
        return False
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    last_err = None
    for attempt in range(1, 3):  # retry once on transient failures
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
            db.log_send("email", to, subject, body, "sent")
            log.info("[email sent] attempt=%d to=%s", attempt, to)
            return True
        except smtplib.SMTPAuthenticationError as e:
            # Auth errors won't resolve on retry — fail fast.
            db.log_send("email", to, subject, body, "error", str(e))
            log.warning("[email auth error] %s: %s", to, e)
            raise
        except Exception as e:
            last_err = e
            log.warning("[email error attempt=%d] %s: %s", attempt, to, e)
    db.log_send("email", to, subject, body, "error", str(last_err))
    raise last_err


@app.post("/api/subscribe")
async def subscribe(sub: Subscription):
    if not sub.email:
        raise HTTPException(400, "An email address is required to sign up.")
    welcome = (
        "You're signed up for Outage Concierge alerts"
        + (f" for {sub.area}" if sub.area else "")
        + ". We'll let you know before and during any outage. Reply STOP to opt out."
    )
    # Persist the subscriber first, so they're saved even if the welcome email fails.
    db.upsert_subscriber(sub.email, None, ["email"], sub.area)
    results = {}
    try:
        results["email"] = send_email(sub.email, "Outage alerts are on", welcome)
    except Exception as e:
        results["email"] = False
        results["email_error"] = str(e)
        log.warning("[subscribe email error] %s: %s", sub.email, e)
    demo = not EMAIL_LIVE
    return {"ok": True, "saved": True, "delivered": results,
            "note": "Server is in demo mode (SMTP not configured) — no real email was sent." if demo else (
                "Email failed: " + results.get("email_error", "") if not results.get("email") else "")}


@app.post("/api/unsubscribe")
async def unsubscribe(sub: Subscription):
    db.deactivate(email=sub.email)
    return {"ok": True}


# Public test-email endpoint (no admin token). Lightly rate-limited per address.
_last_test = {}
TEST_COOLDOWN_S = int(os.getenv("TEST_COOLDOWN_S", "30"))


class PublicTest(BaseModel):
    email: EmailStr


@app.post("/api/test-email")
async def public_test_email(req: PublicTest):
    now = time.time()
    if now - _last_test.get(req.email, 0) < TEST_COOLDOWN_S:
        raise HTTPException(429, "Please wait a moment before sending another test.")
    _last_test[req.email] = now
    body = ("This is a test message from the Outage Concierge. If you received it, "
            "email alerts are working. We'll only message you about outages you signed up for.")
    if not EMAIL_LIVE:
        db.log_send("email", req.email, "Outage Concierge test", body, "demo", "SMTP not configured")
        return {"ok": True, "sent": False, "note": "Server is in demo mode — no SMTP configured."}
    try:
        send_email(req.email, "Outage Concierge test email", body)
    except Exception as e:
        raise HTTPException(502, f"Send failed: {e}")
    return {"ok": True, "sent": True}


# ---- Storm alert: the frontend simulation calls this when the storm reaches an area ----
_last_alert = {}  # area -> timestamp, for a simple cooldown
ALERT_COOLDOWN_S = int(os.getenv("ALERT_COOLDOWN_S", "120"))


class StormAlert(BaseModel):
    area: str = ""          # e.g. "P-ABC123"; empty means alert everyone
    message: str = "⚡ A storm is reaching your area and may cause outages. Charge your phone, keep water and a flashlight handy, and stay 30 ft from any downed line."
    subject: str = "⚡ Storm heading your way"


@app.post("/api/storm-alert")
async def storm_alert(a: StormAlert):
    """Email subscribers that a (simulated) storm is reaching their area.
    Throttled per-area so repeated calls during one pass don't send duplicates."""
    now = time.time()
    key = a.area or "*all*"
    if now - _last_alert.get(key, 0) < ALERT_COOLDOWN_S:
        return {"ok": True, "skipped": "cooldown", "sent": {"email": 0}}
    _last_alert[key] = now
    sent = {"email": 0, "errors": 0}
    for s in db.list_subscribers():
        # Match the subscriber's area if one was given on both sides; otherwise alert all.
        if a.area and s.get("area") and s["area"] != a.area:
            continue
        try:
            if s["email"] and send_email(s["email"], a.subject, a.message):
                sent["email"] += 1
        except Exception:
            sent["errors"] += 1
    return {"ok": True, "sent": sent}


# ---- Admin (guarded by ADMIN_TOKEN) ----
def _require_admin(x_admin_token: Optional[str]):
    if not ADMIN_TOKEN:
        raise HTTPException(403, "Admin endpoints are disabled (set ADMIN_TOKEN).")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Bad admin token.")


@app.get("/api/subscribers")
async def subscribers(x_admin_token: Optional[str] = Header(default=None)):
    _require_admin(x_admin_token)
    return {"subscribers": db.list_subscribers()}


@app.get("/api/sent-log")
async def sent_log(x_admin_token: Optional[str] = Header(default=None)):
    _require_admin(x_admin_token)
    return {"log": db.recent_log()}


class TestEmail(BaseModel):
    to: EmailStr
    subject: str = "Outage Concierge test"
    body: str = "This is a test from your Outage Concierge server. If you got this, email is working."


@app.post("/api/send-test")
async def send_test(req: TestEmail, x_admin_token: Optional[str] = Header(default=None)):
    """Send a real test email to confirm SMTP is live. Requires the admin token."""
    _require_admin(x_admin_token)
    if not EMAIL_LIVE:
        raise HTTPException(503, "Email is not configured (SMTP_* missing), so this would only be a demo.")
    try:
        send_email(req.to, req.subject, req.body)
    except Exception as e:
        raise HTTPException(502, f"Send failed: {e}")
    return {"ok": True, "sent_to": req.to}


class Broadcast(BaseModel):
    subject: str = "Outage alert"
    message: str


@app.post("/api/broadcast")
async def broadcast(b: Broadcast, x_admin_token: Optional[str] = Header(default=None)):
    """Email a message to every active subscriber."""
    _require_admin(x_admin_token)
    sent = {"email": 0, "errors": 0}
    for s in db.list_subscribers():
        try:
            if s["email"] and send_email(s["email"], b.subject, b.message):
                sent["email"] += 1
        except Exception:
            sent["errors"] += 1
    return {"ok": True, "sent": sent}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "chat": bool(ANTHROPIC_API_KEY),
        "email": EMAIL_LIVE,
    }


# ---- Serve the static app (keep this mount last so /api/* wins) ----
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "outage-concierge")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
