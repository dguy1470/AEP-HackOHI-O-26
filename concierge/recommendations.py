"""Personalized preparation & safety guidance.

Recommendations are tailored to:
  * predicted risk band
  * expected restoration duration
  * declared customer needs (critical / medical / special operating condition)

Safety-critical messages are clearly labeled. We adapt to *declared* needs
only and never infer sensitive attributes, per the challenge's responsible-use
requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Recommendation:
    text: str
    kind: str = "prep"   # "prep" | "safety" | "emergency"
    priority: int = 3     # 1 = highest


@dataclass
class GuidanceResult:
    items: list[Recommendation] = field(default_factory=list)

    def sorted_items(self) -> list[Recommendation]:
        return sorted(self.items, key=lambda r: r.priority)


def build_guidance(
    *,
    risk_band: str,
    restore_hours: float,
    is_critical: bool,
    has_medical_need: bool,
) -> GuidanceResult:
    items: list[Recommendation] = []

    # --- Universal baseline prep ---
    items.append(Recommendation(
        "Charge phones and any backup batteries now.", "prep", 3))
    items.append(Recommendation(
        "Keep flashlights handy; avoid candles to reduce fire risk.",
        "safety", 2))

    # --- Risk-scaled prep ---
    if risk_band in ("Elevated", "High"):
        items.append(Recommendation(
            "Outages are more likely in your area. Prepare a small kit: "
            "water, non-perishable food, medications, and a battery radio.",
            "prep", 2))
        items.append(Recommendation(
            "Move vehicles out from under trees and secure loose outdoor "
            "items.", "prep", 3))
    if risk_band == "High":
        items.append(Recommendation(
            "Treat a multi-hour outage as likely. Pre-cool your home and "
            "refrigerator and fill water containers.", "prep", 1))

    # --- Duration-scaled prep ---
    if restore_hours >= 24:
        items.append(Recommendation(
            "Restoration could extend beyond a day. Plan for food safety "
            "(keep the fridge closed) and identify a backup location if you "
            "rely on power for comfort or health.", "prep", 1))
    elif restore_hours >= 8:
        items.append(Recommendation(
            "Restoration may take most of a day. Plan meals that don't need "
            "power and keep the refrigerator closed.", "prep", 2))

    # --- Always-on safety ---
    items.append(Recommendation(
        "Stay far away from any downed power line and assume it is live. "
        "Call 911 and AEP immediately.", "emergency", 1))
    items.append(Recommendation(
        "If you use a generator, run it outdoors only, never in a garage or "
        "near windows, to avoid carbon-monoxide poisoning.", "safety", 2))

    # --- Declared-need tailoring (no inference) ---
    if has_medical_need:
        items.append(Recommendation(
            "You've indicated a medical/life-support need. Have a backup "
            "power plan and a go-bag ready, and know the nearest facility "
            "that can support your equipment. Contact AEP to confirm your "
            "medical-priority status.", "emergency", 1))
    if is_critical:
        items.append(Recommendation(
            "Your location is flagged as critical. You may receive "
            "prioritized restoration; keep your contact details current with "
            "AEP so crews can reach you.", "prep", 1))

    return GuidanceResult(items=items)
