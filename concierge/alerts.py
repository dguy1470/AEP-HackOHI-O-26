"""Alert eligibility + proactive communication.

Decides whether a scenario qualifies for outage alerts using documented rules
(config thresholds), recommends notification timing / channel / escalation,
and generates concise plain-language messages for the four lifecycle stages:
pre-storm, outage-confirmed, estimate-updated, restoration-complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config

_BAND_ORDER = ["Low", "Moderate", "Elevated", "High"]


@dataclass
class AlertDecision:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    timing: str = ""
    channels: list[str] = field(default_factory=list)
    escalation: list[str] = field(default_factory=list)


def _band_ge(band: str, threshold: str) -> bool:
    try:
        return _BAND_ORDER.index(band) >= _BAND_ORDER.index(threshold)
    except ValueError:
        return False


def evaluate_alert(
    *,
    risk_band: str,
    is_critical: bool,
    has_medical_need: bool,
    near_priority_facility: bool = False,
) -> AlertDecision:
    reasons: list[str] = []
    eligible = False

    if _band_ge(risk_band, config.ALERT_RISK_THRESHOLD):
        eligible = True
        reasons.append(
            f"Risk band '{risk_band}' is at or above the alert threshold "
            f"('{config.ALERT_RISK_THRESHOLD}')."
        )
    if has_medical_need:
        eligible = True
        reasons.append("Declared medical/life-support need warrants proactive contact.")
    if is_critical:
        eligible = True
        reasons.append("Location is flagged critical.")
    if near_priority_facility:
        reasons.append("Scenario is near a priority facility area.")

    if not eligible:
        reasons.append(
            f"Risk band '{risk_band}' is below the alert threshold and no "
            f"priority flags are set."
        )
        return AlertDecision(eligible=False, reasons=reasons)

    # Timing / channel / escalation recommendations.
    if risk_band == "High" or has_medical_need:
        timing = "Immediately, and again ~6-12 hours before storm onset."
        channels = ["SMS", "Push notification", "Voice call (medical/critical)"]
    else:
        timing = "12-24 hours before expected storm onset."
        channels = ["SMS", "Push notification", "Email"]

    escalation = []
    if has_medical_need:
        escalation.append(
            "Escalate to a live agent / medical-priority queue if no "
            "acknowledgement within 30 minutes.")
    if is_critical:
        escalation.append(
            "Notify operations of critical-location exposure for restoration "
            "sequencing.")
    if risk_band == "High":
        escalation.append(
            "Trigger a follow-up update when outage is confirmed or the "
            "estimate changes materially.")

    return AlertDecision(
        eligible=True,
        reasons=reasons,
        timing=timing,
        channels=channels,
        escalation=escalation,
    )
