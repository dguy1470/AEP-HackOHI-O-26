"""Natural-language message generation.

Default mode is deterministic template generation (no external dependency,
fully explainable, zero cost) -- safe for a live demo. An optional LLM "polish"
pass is available only when OPENAI_API_KEY is set; it rephrases the already
privacy-filtered template text and never sees raw sensitive data.

Messages cover the four lifecycle stages the challenge calls out:
    pre_storm, outage_confirmed, estimate_updated, restoration_complete
"""
from __future__ import annotations

from . import config
from .explain import redact


def _separator_block(confirmed: list[str], estimates: list[str], actions: list[str]) -> str:
    """Render the challenge's recommended 3-part separation of fact types."""
    lines = []
    if confirmed:
        lines.append("Confirmed facts:")
        lines += [f"  - {c}" for c in confirmed]
    if estimates:
        lines.append("Model estimates:")
        lines += [f"  - {e}" for e in estimates]
    if actions:
        lines.append("What you can do:")
        lines += [f"  - {a}" for a in actions]
    return "\n".join(lines)


def compose_messages(
    *,
    risk_band: str,
    confidence: str,
    restore_range: str,
    primary_driver: str,
    top_actions: list[str],
) -> dict[str, str]:
    """Build the four lifecycle messages as privacy-filtered template text."""
    actions = top_actions[:3]

    pre_storm = _separator_block(
        confirmed=[
            "A storm is approaching your area.",
        ],
        estimates=[
            f"Outage risk for your location is {risk_band} "
            f"(confidence: {confidence}).",
            f"Biggest factor: {primary_driver.lower()}.",
        ],
        actions=actions,
    )

    outage_confirmed = _separator_block(
        confirmed=["An outage affecting your location has been confirmed."],
        estimates=[
            f"Estimated restoration window: {restore_range} "
            f"(model estimate, not a guarantee).",
        ],
        actions=[
            "Report any downed lines to 911 and AEP.",
            "Keep your refrigerator closed to protect food.",
        ],
    )

    estimate_updated = _separator_block(
        confirmed=["Crew and damage information has been updated."],
        estimates=[
            f"Revised restoration window: {restore_range}.",
            "This replaces the previous estimate.",
        ],
        actions=["Check back for the next confirmed update."],
    )

    restoration_complete = _separator_block(
        confirmed=["Power has been restored to your location."],
        estimates=[],
        actions=[
            "If your power is still out, report it so we can investigate "
            "a separate issue.",
        ],
    )

    messages = {
        "pre_storm": pre_storm,
        "outage_confirmed": outage_confirmed,
        "estimate_updated": estimate_updated,
        "restoration_complete": restoration_complete,
    }
    # Enforce privacy filter on every outgoing message.
    messages = {k: redact(v) for k, v in messages.items()}

    if config.llm_enabled():
        messages = {k: _llm_polish(v) for k, v in messages.items()}
    return messages


def _llm_polish(text: str) -> str:
    """Optionally rephrase template text with an LLM (opt-in, best-effort).

    Only the already privacy-filtered template text is sent. Any failure
    silently falls back to the deterministic template.
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
                        "Rewrite the utility outage message to be warm, clear, "
                        "and plain-language for a general customer. Keep it "
                        "under 80 words. Do not invent facts, times, names, or "
                        "addresses. Preserve the separation of confirmed facts, "
                        "estimates, and actions."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        polished = resp.choices[0].message.content.strip()
        return redact(polished) or text
    except Exception:
        return text
