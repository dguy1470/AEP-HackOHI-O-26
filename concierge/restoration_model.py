"""Restoration-time estimation.

Produces a *range* (not a false-precision promise), calibrated on the real
observed durations in the damage data (Repaired_D - Report_Tim) when enough
samples exist, and falling back to representative STORMS-style baselines
otherwise. Operational multipliers (access issues, special equipment, travel /
non-productive time) widen/shift the estimate, mirroring the challenge's
"operational realism" expectation.

The estimate updates whenever its inputs change, so judges can move a slider
(e.g. damage count or access difficulty) and watch the range respond.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config


@dataclass
class RestorationResult:
    low_hours: float
    high_hours: float
    point_hours: float
    basis: str                       # "empirical" | "baseline" | "blended"
    drivers: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    sample_size: int = 0

    def pretty_range(self) -> str:
        return f"{_fmt(self.low_hours)} \u2013 {_fmt(self.high_hours)}"


def _fmt(hours: float) -> str:
    if hours < 1:
        return f"{int(round(hours * 60))} min"
    if hours < 24:
        return f"{hours:.1f} hr"
    days = hours / 24.0
    return f"{days:.1f} days"


def _empirical_duration(
    damage: pd.DataFrame, circuit: str, asset_cat: str
) -> tuple[float | None, float | None, float | None, int]:
    """Return (p25, p50, p75, n) of observed restore hours for the context."""
    if damage is None or damage.empty or "restore_hours" not in damage.columns:
        return None, None, None, 0

    df = damage.dropna(subset=["restore_hours"])
    # Prefer same-circuit + same-asset; relax to circuit, then asset, then all.
    for mask in (
        (df["circuit"] == circuit) & (df["asset_type"].str.contains(asset_cat, na=False)),
        (df["circuit"] == circuit),
        (df["asset_type"].str.contains(asset_cat, na=False)),
    ):
        subset = df[mask]
        if len(subset) >= config.MIN_SAMPLES_FOR_EMPIRICAL:
            q = subset["restore_hours"].quantile([0.25, 0.5, 0.75])
            return float(q.loc[0.25]), float(q.loc[0.5]), float(q.loc[0.75]), len(subset)
    return None, None, None, 0


def estimate_restoration(
    *,
    circuit: str,
    asset_type: str,
    damage_quantity: int,
    access_issue: bool,
    special_equipment: bool,
    damage: pd.DataFrame | None = None,
) -> RestorationResult:
    """Estimate a restoration-time range for a scenario.

    damage_quantity scales effort (more broken assets => longer), access and
    special-equipment flags apply operational multipliers.
    """
    asset_cat = _asset_category(asset_type)
    assumptions: list[str] = []
    drivers: list[dict] = []

    p25, p50, p75, n = _empirical_duration(damage, circuit, asset_cat)

    if p50 is not None:
        basis = "empirical"
        base_low, base_point, base_high = p25, p50, p75
        drivers.append({
            "label": "Historical repair durations for similar damage",
            "detail": f"Calibrated on {n} past repairs "
                      f"(median {_fmt(p50)}).",
        })
    else:
        basis = "baseline"
        base_point = config.BASELINE_REPAIR_HOURS.get(
            asset_cat, config.BASELINE_REPAIR_HOURS["DEFAULT"]
        )
        base_low = base_point * 0.7
        base_high = base_point * 1.5
        assumptions.append(
            f"Too few historical repairs for this context; using a "
            f"representative baseline for {asset_cat.title()} "
            f"({_fmt(base_point)})."
        )

    # Damage quantity scales effort (sub-linear: crews parallelize somewhat).
    qty = max(int(damage_quantity), 1)
    qty_factor = qty ** 0.75
    if qty > 1:
        drivers.append({
            "label": "Amount of damage",
            "detail": f"{qty} damaged assets scale the effort "
                      f"(\u00d7{qty_factor:.1f}).",
        })

    low = base_low * qty_factor
    point = base_point * qty_factor
    high = base_high * qty_factor

    # Travel + non-productive time baseline multiplier (always applied).
    tnp = config.TRAVEL_NONPRODUCTIVE_MULTIPLIER
    low *= tnp
    point *= tnp
    high *= tnp
    drivers.append({
        "label": "Travel & non-productive time",
        "detail": f"Standard operational overhead (\u00d7{tnp}).",
    })

    if access_issue:
        m = config.ACCESS_ISSUE_MULTIPLIER
        low *= m; point *= m; high *= m
        drivers.append({
            "label": "Access difficulty",
            "detail": f"Blocked/limited access extends the work (\u00d7{m}).",
        })
    if special_equipment:
        m = config.SPECIAL_EQUIP_MULTIPLIER
        low *= m; point *= m; high *= m
        drivers.append({
            "label": "Special equipment required",
            "detail": f"Specialized resources add time (\u00d7{m}).",
        })

    return RestorationResult(
        low_hours=round(low, 1),
        high_hours=round(high, 1),
        point_hours=round(point, 1),
        basis=basis,
        drivers=drivers,
        assumptions=assumptions,
        sample_size=n,
    )


def _asset_category(asset_type: str) -> str:
    if not isinstance(asset_type, str):
        return "DEFAULT"
    a = asset_type.upper()
    for key in config.BASELINE_REPAIR_HOURS:
        if key != "DEFAULT" and key in a:
            return key
    return "DEFAULT"
