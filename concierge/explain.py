"""Explainability + privacy filtering.

Two jobs:
  1. Turn model internals (risk factors, restoration drivers) into plain,
     non-technical explanations a customer can understand.
  2. Enforce a privacy filter so no raw personal, crew, or precise
     sensitive-facility data can ever appear in customer-facing output.

This module backs the "Explainability and Responsible Use" requirement.
"""
from __future__ import annotations

import re

from . import config
from .risk_model import RiskResult
from .restoration_model import RestorationResult


# --------------------------------------------------------------------------
# Privacy filter
# --------------------------------------------------------------------------
_BLOCK_PATTERNS = [re.compile(re.escape(f), re.IGNORECASE) for f in config.PRIVACY_BLOCKLIST]


def redact(text: str) -> str:
    """Remove any blocklisted field tokens that slipped into a string."""
    if not text:
        return text
    cleaned = text
    for pat in _BLOCK_PATTERNS:
        cleaned = pat.sub("[redacted]", cleaned)
    return cleaned


def safe_context(raw: dict) -> dict:
    """Strip blocklisted keys from a context dict before it reaches the UI."""
    return {k: v for k, v in raw.items() if k not in config.PRIVACY_BLOCKLIST}


# --------------------------------------------------------------------------
# Risk explanation
# --------------------------------------------------------------------------
def explain_risk(result: RiskResult) -> dict:
    """Return a structured, plain-language explanation of a risk result."""
    top = result.factors[0] if result.factors else None
    headline = (
        f"Outage risk is **{result.band}** "
        f"(confidence: {result.confidence})."
    )

    bullets = []
    for f in result.factors:
        strength = _describe_strength(f["signal"])
        share = f["contribution"] / (result.score or 1.0)
        bullets.append(
            f"{f['label']}: {strength} "
            f"(about {share*100:.0f}% of the estimate)."
        )

    uncertainty = _risk_uncertainty_note(result)

    return {
        "headline": headline,
        "primary_driver": top["label"] if top else "storm severity",
        "bullets": bullets,
        "uncertainty": uncertainty,
        "assumptions": result.assumptions,
    }


def _describe_strength(signal: float) -> str:
    if signal >= 0.75:
        return "a strong contributor"
    if signal >= 0.5:
        return "a moderate contributor"
    if signal >= 0.25:
        return "a mild contributor"
    return "a minor contributor"


def _risk_uncertainty_note(result: RiskResult) -> str:
    if result.confidence == "High":
        return ("This estimate is backed by a solid amount of historical data "
                "for this circuit.")
    if result.confidence == "Medium":
        return ("This estimate has a moderate amount of supporting history; "
                "treat it as a guide, not a guarantee.")
    return ("Limited history backs this estimate, so actual conditions could "
            "differ meaningfully. We'll update it as real information arrives.")


# --------------------------------------------------------------------------
# Restoration explanation
# --------------------------------------------------------------------------
def explain_restoration(result: RestorationResult) -> dict:
    basis_note = {
        "empirical": "This range is calibrated on real past repair times for "
                     "similar damage.",
        "baseline": "Because similar past repairs were scarce, this range "
                    "uses a representative industry baseline (clearly an "
                    "assumption, not a confirmed time).",
        "blended": "This range blends historical repair times with a baseline.",
    }.get(result.basis, "")

    return {
        "headline": f"Estimated restoration window: **{result.pretty_range()}**",
        "basis": basis_note,
        "drivers": [f"{d['label']}: {d['detail']}" for d in result.drivers],
        "assumptions": result.assumptions,
        "caveat": ("This is a model estimate, not a confirmed utility "
                   "restoration time. Confirmed updates always take "
                   "precedence."),
    }


# --------------------------------------------------------------------------
# Fallback when the model cannot make a reliable prediction
# --------------------------------------------------------------------------
def low_confidence_fallback(what: str) -> str:
    return (
        f"We don't have enough reliable information to give a confident "
        f"{what} right now. Please watch for official AEP updates, and treat "
        f"any estimate shown as preliminary."
    )
