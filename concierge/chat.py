"""Conversational layer for the Outage Concierge.

A customer types a natural-language question ("Will I lose power?",
"How long until it's back?"). We:

  1. Classify intent with a lightweight keyword/regex router (no model needed).
  2. Answer using the SAME engine the structured UI uses, so the chat reflects
     the current scenario and never invents numbers.
  3. Optionally let an LLM rephrase the grounded answer when OPENAI_API_KEY is
     set. The LLM only ever sees already-computed, privacy-filtered facts; it
     is instructed not to change any number.

This keeps the assistant conversational while staying transparent and
responsible-use compliant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import config
from .explain import redact, explain_risk, explain_restoration
from .risk_model import RiskResult
from .restoration_model import RestorationResult
from .recommendations import GuidanceResult
from .alerts import AlertDecision


# --------------------------------------------------------------------------
# Scenario snapshot passed in from the app (already computed)
# --------------------------------------------------------------------------
@dataclass
class ChatContext:
    circuit: str
    risk: RiskResult
    restoration: RestorationResult
    guidance: GuidanceResult
    alert: AlertDecision


# --------------------------------------------------------------------------
# Intent routing
# --------------------------------------------------------------------------
INTENTS = {
    "risk": [
        r"\blose power\b", r"\bgo out\b", r"\boutage\b", r"\brisk\b",
        r"\blikely\b", r"\bchance\b", r"\bwill i\b", r"\bgoing to\b",
    ],
    "restoration": [
        r"\bhow long\b", r"\bwhen\b", r"\brestor", r"\bback on\b",
        r"\bback up\b", r"\bfixed\b", r"\bduration\b", r"\bestimate\b",
    ],
    "prepare": [
        r"\bprepare\b", r"\bprep\b", r"\bwhat should i do\b", r"\bshould i\b",
        r"\bsafe", r"\bkit\b", r"\bgenerator\b", r"\bready\b", r"\bprotect\b",
    ],
    "alerts": [
        r"\balert", r"\bnotif", r"\btext me\b", r"\bqualif", r"\bsign up\b",
        r"\bwarn", r"\bupdate", r"\bcontact me\b",
    ],
    "greeting": [r"\bhi\b", r"\bhello\b", r"\bhey\b", r"\bgood (morning|evening|afternoon)\b"],
    "help": [r"\bhelp\b", r"\bwhat can you\b", r"\boptions\b", r"\bask\b"],
}


def classify(question: str) -> str:
    q = question.lower().strip()
    if not q:
        return "help"
    # Meta-intents win when clearly matched (so "what can you do" -> help,
    # not prepare).
    if any(re.search(p, q) for p in INTENTS["help"]):
        return "help"
    # A pure greeting (short, no topical keywords) -> greeting.
    if any(re.search(p, q) for p in INTENTS["greeting"]) and len(q.split()) <= 3:
        return "greeting"
    # Score the topical intents by number of pattern hits; pick the best.
    topical = ("risk", "restoration", "prepare", "alerts")
    best, best_score = "fallback", 0
    for intent in topical:
        score = sum(1 for p in INTENTS[intent] if re.search(p, q))
        if score > best_score:
            best, best_score = intent, score
    return best if best_score > 0 else "fallback"


# --------------------------------------------------------------------------
# Grounded answer builders (deterministic, use real engine output)
# --------------------------------------------------------------------------
def _answer_risk(ctx: ChatContext) -> str:
    e = explain_risk(ctx.risk)
    lines = [
        f"Your outage risk right now is **{ctx.risk.band}** "
        f"(confidence: {ctx.risk.confidence}).",
        f"The biggest factor is {e['primary_driver'].lower()}.",
        e["uncertainty"],
    ]
    return "\n\n".join(lines)


def _answer_restoration(ctx: ChatContext) -> str:
    e = explain_restoration(ctx.restoration)
    lines = [
        f"If an outage happens, the estimated restoration window is "
        f"**{ctx.restoration.pretty_range()}**.",
        e["basis"],
        e["caveat"],
    ]
    return "\n\n".join(x for x in lines if x)


def _answer_prepare(ctx: ChatContext) -> str:
    top = ctx.guidance.sorted_items()[:4]
    bullets = "\n".join(f"- {r.text}" for r in top)
    return ("Here's what I'd focus on for your situation:\n\n" + bullets)


def _answer_alerts(ctx: ChatContext) -> str:
    a = ctx.alert
    if a.eligible:
        head = "Yes, your scenario qualifies for proactive outage alerts."
        detail = (f"Suggested timing: {a.timing}\n\n"
                  f"Channels: {', '.join(a.channels)}.")
        return head + "\n\n" + detail
    return ("Based on the current scenario, you don't currently meet the "
            "alert threshold, but you can still opt in to AEP outage "
            "notifications any time.")


def _answer_greeting(_: ChatContext) -> str:
    return ("Hi! I'm your outage concierge. Ask me things like "
            "\u201cWill I lose power?\u201d, \u201cHow long could restoration "
            "take?\u201d, \u201cWhat should I do to prepare?\u201d, or "
            "\u201cDo I qualify for alerts?\u201d")


def _answer_help(_: ChatContext) -> str:
    return ("I can help with four things, grounded in your current scenario:\n\n"
            "- **Outage risk** \u2014 \u201cWill I lose power?\u201d\n"
            "- **Restoration time** \u2014 \u201cHow long until it\u2019s back?\u201d\n"
            "- **Preparation** \u2014 \u201cWhat should I do?\u201d\n"
            "- **Alerts** \u2014 \u201cDo I qualify for notifications?\u201d")


def _answer_fallback(ctx: ChatContext) -> str:
    return ("I'm not sure I caught that. " + _answer_help(ctx))


_ROUTER = {
    "risk": _answer_risk,
    "restoration": _answer_restoration,
    "prepare": _answer_prepare,
    "alerts": _answer_alerts,
    "greeting": _answer_greeting,
    "help": _answer_help,
    "fallback": _answer_fallback,
}


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def respond(question: str, ctx: ChatContext) -> tuple[str, str]:
    """Return (intent, answer_text) for a customer question.

    The answer is always grounded in the engine. If an LLM key is set, the
    grounded answer is rephrased (numbers preserved); otherwise it is returned
    as-is. Output is privacy-filtered regardless.
    """
    intent = classify(question)
    grounded = _ROUTER[intent](ctx)
    grounded = redact(grounded)

    if config.llm_enabled() and intent not in ("greeting", "help"):
        grounded = _llm_rephrase(question, grounded)
    return intent, grounded


def _llm_rephrase(question: str, grounded_answer: str) -> str:
    """Rephrase a grounded answer conversationally. Numbers must not change.

    Only the already-computed, privacy-filtered answer is sent to the model.
    On any failure we fall back to the deterministic grounded answer.
    """
    try:
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a utility outage concierge. Rephrase the "
                        "GROUNDED ANSWER to directly address the customer's "
                        "question in a warm, plain, reassuring tone under 90 "
                        "words. Rules: do NOT add, remove, or change any "
                        "number, time range, risk level, or confidence value. "
                        "Do not invent facts, names, or addresses. If the "
                        "grounded answer expresses uncertainty, keep it."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"CUSTOMER QUESTION: {question}\n\n"
                        f"GROUNDED ANSWER:\n{grounded_answer}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=220,
        )
        out = resp.choices[0].message.content.strip()
        return redact(out) or grounded_answer
    except Exception:
        return grounded_answer
