"""Outage-risk prediction.

Approach: a transparent weighted composite score in [0, 1] combining four
signals, each individually explainable:

    storm_severity     - the incoming storm category (scenario input)
    historical_density - how often this circuit has been damaged historically
    asset_exposure     - share of tree/overhead-exposed assets on the circuit
    proximity          - closeness to historically damaged equipment

The score maps to a risk band (Low / Moderate / Elevated / High). Confidence
reflects how much real evidence backs the estimate (sample size on the
circuit). This design satisfies the "show the most important contributing
factors and communicate uncertainty" requirement directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config


@dataclass
class RiskResult:
    score: float                      # composite 0..1
    band: str                         # Low / Moderate / Elevated / High
    confidence: str                   # Low / Medium / High
    contributions: dict[str, float]   # normalized weight * signal, per factor
    signals: dict[str, float]         # raw 0..1 signal values
    factors: list[dict] = field(default_factory=list)  # ranked, UI-ready
    sample_size: int = 0
    assumptions: list[str] = field(default_factory=list)


def _band_for(score: float) -> str:
    for lo, hi, name in config.RISK_BANDS:
        if lo <= score < hi:
            return name
    return "High"


def _normalized_weights() -> dict[str, float]:
    w = config.RISK_WEIGHTS
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def predict_risk(
    circuit: str,
    storm_severity: float,
    circuit_profile: pd.DataFrame,
) -> RiskResult:
    """Compute the outage-risk band for a circuit under a storm scenario.

    Parameters
    ----------
    circuit : anonymized circuit id (e.g. "CIR12")
    storm_severity : 0..1 scenario severity (see config.STORM_LEVELS)
    circuit_profile : per-circuit historical profile from the data loader
    """
    assumptions: list[str] = []
    row = None
    if circuit_profile is not None and not circuit_profile.empty:
        match = circuit_profile[circuit_profile["circuit"] == circuit]
        if len(match):
            row = match.iloc[0]

    if row is None:
        # No historical record for this circuit: fall back to storm-only,
        # and clearly flag the reduced confidence.
        assumptions.append(
            "No historical damage records matched this circuit; the estimate "
            "is driven by storm severity alone and should be treated as low "
            "confidence."
        )
        density = 0.0
        exposed = 0.0
        proximity = 0.0
        sample_size = 0
    else:
        density = float(row.get("density_score", 0.0) or 0.0)
        exposed = float(row.get("exposed_asset_share", 0.0) or 0.0)
        sample_size = int(row.get("damage_count", 0) or 0)
        # Proximity signal: closer historical damage (small NEAR_DIST) => higher
        # risk. Convert mean distance to a 0..1 closeness score with a soft knee.
        mean_dist = row.get("mean_near_dist", np.nan)
        if pd.isna(mean_dist):
            proximity = 0.0
            assumptions.append("Proximity-to-damage distance was unavailable for this circuit.")
        else:
            proximity = float(np.exp(-max(mean_dist, 0.0) / 200.0))  # ~200ft scale

    signals = {
        "storm_severity": float(np.clip(storm_severity, 0.0, 1.0)),
        "historical_density": float(np.clip(density, 0.0, 1.0)),
        "asset_exposure": float(np.clip(exposed, 0.0, 1.0)),
        "proximity": float(np.clip(proximity, 0.0, 1.0)),
    }

    weights = _normalized_weights()
    contributions = {k: weights[k] * signals[k] for k in signals}
    score = float(np.clip(sum(contributions.values()), 0.0, 1.0))
    band = _band_for(score)

    # Confidence from evidence volume.
    if sample_size >= 20:
        confidence = "High"
    elif sample_size >= config.MIN_SAMPLES_FOR_EMPIRICAL:
        confidence = "Medium"
    else:
        confidence = "Low"

    # Rank factors for the UI (most influential first).
    label = {
        "storm_severity": "Incoming storm severity",
        "historical_density": "Historical damage on this circuit",
        "asset_exposure": "Tree/overhead-exposed equipment",
        "proximity": "Nearness to past damage points",
    }
    factors = sorted(
        (
            {
                "key": k,
                "label": label[k],
                "signal": signals[k],
                "weight": weights[k],
                "contribution": contributions[k],
            }
            for k in signals
        ),
        key=lambda d: d["contribution"],
        reverse=True,
    )

    return RiskResult(
        score=score,
        band=band,
        confidence=confidence,
        contributions=contributions,
        signals=signals,
        factors=factors,
        sample_size=sample_size,
        assumptions=assumptions,
    )
