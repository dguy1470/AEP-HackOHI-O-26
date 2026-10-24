"""
Outage Concierge backend.

A small FastAPI service that:
  - proxies chat requests to Anthropic's Claude using a SERVER-SIDE key
    (so the static site no longer needs a bring-your-own-key field),
  - proxies address lookups to OpenStreetMap Nominatim with a proper User-Agent,
  - accepts notification sign-ups (email via SMTP, SMS via an optional Twilio hook),
  - serves the static app in outage-concierge/.

Nothing secret is committed: all credentials come from environment variables
(see .env.example). The service runs fine with no credentials — email/SMS then
log what they *would* send instead of failing, which keeps local demos simple.
"""
import os
import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

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

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_FROM_NUMBER", "")

# Allowed browser origins. "*" is fine for a local prototype; lock this down in prod.
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

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


# ---- Models ----
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    situation: str = ""


class Subscription(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    channels: list[str] = Field(default_factory=list)  # any of: email, sms
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


# ---- Notification sign-up ----
def send_email(to: str, subject: str, body: str) -> bool:
    if not SMTP_HOST:
        log.info("[email disabled] would send to %s: %s", to, subject)
        return False
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return True


def send_sms(to: str, body: str) -> bool:
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        log.info("[sms disabled] would text %s: %s", to, body)
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    with httpx.Client(timeout=15) as client:
        r = client.post(
            url,
            data={"From": TWILIO_FROM, "To": to, "Body": body},
            auth=(TWILIO_SID, TWILIO_TOKEN),
        )
    return r.status_code in (200, 201)


@app.post("/api/subscribe")
async def subscribe(sub: Subscription):
    if not sub.channels:
        raise HTTPException(400, "Pick at least one channel (email or sms).")
    welcome = (
        "You're signed up for Outage Concierge alerts"
        + (f" for {sub.area}" if sub.area else "")
        + ". We'll let you know before and during any outage. Reply STOP to opt out."
    )
    results = {}
    if "email" in sub.channels:
        if not sub.email:
            raise HTTPException(400, "Email channel selected but no email provided.")
        results["email"] = send_email(sub.email, "Outage alerts are on", welcome)
    if "sms" in sub.channels:
        if not sub.phone:
            raise HTTPException(400, "SMS channel selected but no phone provided.")
        results["sms"] = send_sms(sub.phone, "⚡ " + welcome)
    # "delivered" reflects whether a real provider was configured and accepted it.
    return {"ok": True, "delivered": results,
            "note": "Channels showing false are not configured on this server (demo mode)."}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "chat": bool(ANTHROPIC_API_KEY),
        "email": bool(SMTP_HOST),
        "sms": bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM),
    }


# ---- Serve the static app (keep this mount last so /api/* wins) ----
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "outage-concierge")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
