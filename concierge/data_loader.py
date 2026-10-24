"""Load and normalize the challenge GIS data.

Responsibilities:
  * Read each shapefile with geopandas (if present).
  * Normalize CRS to a common lat/long frame for display.
  * Derive clean, analysis-ready frames:
        - damage records with a computed restoration duration (hours)
        - a per-circuit risk profile (historical damage density, asset mix)
        - a customer lookup keyed by anonymized circuit
        - priority-facility points (used only for proximity, never named)

All heavy work is cached so the Streamlit app loads data once.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

from . import config

try:
    import geopandas as gpd  # noqa: F401
    HAVE_GEOPANDAS = True
except Exception:  # pragma: no cover - environment dependent
    HAVE_GEOPANDAS = False


# --------------------------------------------------------------------------
# Container for everything the app needs
# --------------------------------------------------------------------------
@dataclass
class ConciergeData:
    damage: pd.DataFrame = field(default_factory=pd.DataFrame)
    customers: pd.DataFrame = field(default_factory=pd.DataFrame)
    circuit_profile: pd.DataFrame = field(default_factory=pd.DataFrame)
    priority_points: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return not self.damage.empty or not self.customers.empty

    def circuits(self) -> list[str]:
        if self.circuit_profile.empty:
            return []
        return sorted(self.circuit_profile["circuit"].dropna().unique().tolist())


# --------------------------------------------------------------------------
# Low-level readers
# --------------------------------------------------------------------------
def _read_layer(path, columns: Optional[list[str]] = None) -> Optional["gpd.GeoDataFrame"]:
    """Read a shapefile to a GeoDataFrame in display CRS, or None if missing.

    If ``columns`` is given, only those attribute columns are read (geometry is
    always included). Reading a subset of columns dramatically speeds up load
    time for the large tables (e.g. Customer_Clip has ~117k rows), since the
    rest of the .dbf never has to be parsed.
    """
    if not HAVE_GEOPANDAS or not path.exists():
        return None
    read_kwargs = {}
    if columns is not None:
        # geopandas (pyogrio engine) supports reading a column subset.
        read_kwargs["columns"] = columns
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            gdf = gpd.read_file(path, **read_kwargs)
        except Exception:
            # Fall back to a full read if the subset read is unsupported.
            gdf = gpd.read_file(path)
    if gdf.crs is not None:
        try:
            gdf = gdf.to_crs(config.DISPLAY_CRS)
        except Exception:
            pass
    return gdf


def _ensure_latlon(gdf: "gpd.GeoDataFrame") -> pd.DataFrame:
    """Return a plain DataFrame with lon/lat columns derived from geometry."""
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name, errors="ignore"))
    try:
        pts = gdf.geometry.representative_point()
        df["lon"] = pts.x.values
        df["lat"] = pts.y.values
    except Exception:
        df["lon"] = np.nan
        df["lat"] = np.nan
    return df


# --------------------------------------------------------------------------
# Damage frame: the analytical backbone
# --------------------------------------------------------------------------
def _build_damage(gdf: "gpd.GeoDataFrame") -> pd.DataFrame:
    c = config.DAMAGE_COLS
    df = _ensure_latlon(gdf)

    def col(key, default=np.nan):
        name = c[key]
        return df[name] if name in df.columns else pd.Series([default] * len(df))

    out = pd.DataFrame({
        "outage_num": col("outage_num"),
        "circuit": col("circuit").astype("string"),
        "substation": col("substation").astype("string"),
        "district": col("district").astype("string"),
        "asset_type": col("asset_type").astype("string").str.upper().str.strip(),
        "status": col("status").astype("string"),
        "repaired": col("repaired").astype("string"),
        "access_issue": col("access_issue").astype("string"),
        "special_equip": col("special_equip").astype("string"),
        "near_dist": pd.to_numeric(col("near_dist"), errors="coerce"),
        "report_time": pd.to_datetime(col("report_time"), errors="coerce"),
        "repaired_date": pd.to_datetime(col("repaired_date"), errors="coerce"),
    })
    if "lon" in df.columns:
        out["lon"] = df["lon"]
        out["lat"] = df["lat"]

    # Restoration duration (hours) from real report -> repaired timestamps.
    delta = (out["repaired_date"] - out["report_time"]).dt.total_seconds() / 3600.0
    # Guard against bad/negative/implausible values.
    out["restore_hours"] = delta.where((delta > 0) & (delta < 24 * 21))
    return out


def _normalize_asset(asset: str) -> str:
    """Map raw asset text to a coarse category used by the restoration model."""
    if not isinstance(asset, str):
        return "DEFAULT"
    a = asset.upper()
    for key in config.BASELINE_REPAIR_HOURS:
        if key != "DEFAULT" and key in a:
            return key
    return "DEFAULT"


# --------------------------------------------------------------------------
# Per-circuit risk profile
# --------------------------------------------------------------------------
def _build_circuit_profile(damage: pd.DataFrame) -> pd.DataFrame:
    if damage.empty:
        return pd.DataFrame()

    damage = damage.copy()
    damage["asset_cat"] = damage["asset_type"].map(_normalize_asset)

    grp = damage.groupby("circuit", dropna=True)
    profile = grp.agg(
        damage_count=("outage_num", "size"),
        median_restore_hours=("restore_hours", "median"),
        mean_near_dist=("near_dist", "mean"),
        access_issue_rate=("access_issue", lambda s: _yes_rate(s)),
        special_equip_rate=("special_equip", lambda s: _yes_rate(s)),
    ).reset_index()

    # Overhead/tree-exposed asset mix proxy: share of pole/conductor damage.
    exposed = (
        damage.assign(is_exposed=damage["asset_cat"].isin(["POLE", "CONDUCTOR"]))
        .groupby("circuit")["is_exposed"].mean()
        .rename("exposed_asset_share")
        .reset_index()
    )
    profile = profile.merge(exposed, on="circuit", how="left")

    # Normalized 0..1 historical-density score (robust to outliers via rank).
    if profile["damage_count"].max() > 0:
        profile["density_score"] = (
            profile["damage_count"].rank(pct=True)
        )
    else:
        profile["density_score"] = 0.0

    profile["exposed_asset_share"] = profile["exposed_asset_share"].fillna(0.0)
    return profile


def _yes_rate(series: pd.Series) -> float:
    """Fraction of records whose value looks like an affirmative flag."""
    s = series.dropna().astype(str).str.upper().str.strip()
    if len(s) == 0:
        return 0.0
    yes = s.isin({"Y", "YES", "TRUE", "1"})
    # Also treat any non-empty, non-"N" string as a present issue.
    present = yes | (~s.isin({"N", "NO", "FALSE", "0", "", "NONE", "NAN"}))
    return float(present.mean())


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
def _build_customers(gdf: "gpd.GeoDataFrame") -> pd.DataFrame:
    c = config.CUSTOMER_COLS
    df = _ensure_latlon(gdf)

    def col(key, default=np.nan):
        name = c[key]
        return df[name] if name in df.columns else pd.Series([default] * len(df))

    out = pd.DataFrame({
        "customer_id": col("id"),
        "critical": col("critical").astype("string"),
        "special_op": col("special_op").astype("string"),
        "circuit_id": col("circuit_id").astype("string"),
        "service_kind": col("service_kind").astype("string"),
        "city": col("city").astype("string"),
    })
    if "lon" in df.columns:
        out["lon"] = df["lon"]
        out["lat"] = df["lat"]
    return out


# --------------------------------------------------------------------------
# Priority facilities (used only as aggregate proximity signal)
# --------------------------------------------------------------------------
def _collect_priority_points() -> pd.DataFrame:
    frames = []
    for kind in ("hospitals", "fire", "ambulance"):
        gdf = _read_layer(config.LAYERS[kind])
        if gdf is None or len(gdf) == 0:
            continue
        pts = _ensure_latlon(gdf)[["lon", "lat"]].copy()
        pts["kind"] = kind
        frames.append(pts)
    if not frames:
        return pd.DataFrame(columns=["lon", "lat", "kind"])
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_data() -> ConciergeData:
    data = ConciergeData()

    if not HAVE_GEOPANDAS:
        data.warnings.append(
            "geopandas is not installed; shapefiles cannot be read. "
            "Install requirements.txt to enable real-data mode."
        )
        return data

    dmg_gdf = _read_layer(config.LAYERS["damage"], columns=config.DAMAGE_READ_COLS)
    if dmg_gdf is not None and len(dmg_gdf):
        data.damage = _build_damage(dmg_gdf)
        data.circuit_profile = _build_circuit_profile(data.damage)
    else:
        data.warnings.append("Damage layer missing or empty.")

    cust_gdf = _read_layer(config.LAYERS["customers"], columns=config.CUSTOMER_READ_COLS)
    if cust_gdf is not None and len(cust_gdf):
        data.customers = _build_customers(cust_gdf)
    else:
        data.warnings.append("Customer layer missing or empty.")

    data.priority_points = _collect_priority_points()
    return data


# --------------------------------------------------------------------------
# Location resolution: click-to-locate and address geocoding
# --------------------------------------------------------------------------
@dataclass
class LocatedPoint:
    lat: float
    lon: float
    circuit: str | None
    distance_m: float | None
    source: str                 # "map_click" | "address" | "fallback"
    label: str = ""             # human-readable description (no PII)
    critical: bool = False
    medical: bool = False


def _haversine_m(lat1, lon1, lat2, lon2):
    """Vectorized great-circle distance in meters."""
    r = 6371000.0
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = (np.sin(dphi / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2)
    return 2 * r * np.arcsin(np.sqrt(a))


def locate_nearest(data: "ConciergeData", lat: float, lon: float,
                   source: str = "map_click") -> LocatedPoint:
    """Find the nearest customer premise to a point and return its circuit.

    Falls back to the nearest damage record (and its circuit) if customers
    have no coordinates. Never exposes any identifying customer field.
    """
    # Prefer customers (what a real premise lookup would hit).
    cust = data.customers
    if not cust.empty and {"lat", "lon"}.issubset(cust.columns):
        sub = cust.dropna(subset=["lat", "lon"])
        if len(sub):
            d = _haversine_m(lat, lon, sub["lat"].values, sub["lon"].values)
            i = int(np.argmin(d))
            row = sub.iloc[i]
            circuit = _circuit_from_customer(data, row.get("circuit_id"))
            return LocatedPoint(
                lat=lat, lon=lon, circuit=circuit,
                distance_m=float(d[i]), source=source,
                label="Nearest premise on an anonymized circuit",
                critical=_truthy(row.get("critical")),
                medical=_has_medical(row.get("special_op")),
            )

    # Fallback: nearest damage point.
    dmg = data.damage
    if not dmg.empty and {"lat", "lon"}.issubset(dmg.columns):
        sub = dmg.dropna(subset=["lat", "lon"])
        if len(sub):
            d = _haversine_m(lat, lon, sub["lat"].values, sub["lon"].values)
            i = int(np.argmin(d))
            row = sub.iloc[i]
            return LocatedPoint(
                lat=lat, lon=lon, circuit=row.get("circuit"),
                distance_m=float(d[i]), source=source,
                label="Nearest historical damage point")

    return LocatedPoint(lat=lat, lon=lon, circuit=None, distance_m=None,
                        source="fallback",
                        label="No nearby records found")


def _circuit_from_customer(data: "ConciergeData", customer_circuit_id):
    """Map a customer circuit_id to a damage-space circuit (CIRn) if possible.

    The customer file uses codes like '0000012' while the damage file uses
    'CIR12'. They are not guaranteed to align, so we try a best-effort numeric
    match and otherwise return the customer code as-is (the risk model
    degrades gracefully for unknown circuits).
    """
    if customer_circuit_id is None:
        return None
    s = str(customer_circuit_id).strip()
    # Extract trailing digits and try the matching CIR<n> code.
    digits = "".join(ch for ch in s if ch.isdigit()).lstrip("0")
    if digits:
        candidate = f"CIR{digits}"
        if data.circuit_profile is not None and not data.circuit_profile.empty:
            if (data.circuit_profile["circuit"] == candidate).any():
                return candidate
    return s or None


def _truthy(v) -> bool:
    if v is None:
        return False
    s = str(v).strip().upper()
    return s not in ("", "N", "NO", "0", "FALSE", "NONE", "NAN", "<NA>")


def _has_medical(special_op) -> bool:
    if special_op is None:
        return False
    s = str(special_op).upper()
    return any(k in s for k in ("MED", "LIFE", "OXYGEN", "CRITICAL"))


def geocode_address(address: str) -> tuple[float, float] | None:
    """Best-effort address -> (lat, lon) via OpenStreetMap Nominatim.

    Requires internet. Returns None on any failure so callers can fall back
    to map-click. No API key needed; we send a descriptive User-Agent per
    Nominatim usage policy.
    """
    if not address or not address.strip():
        return None
    try:
        import requests

        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "AEP-Outage-Concierge/0.1 (hackathon demo)"},
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        return None
    return None


def data_center(data: "ConciergeData") -> tuple[float, float]:
    """A reasonable default map center (median of damage points)."""
    dmg = data.damage
    if not dmg.empty and {"lat", "lon"}.issubset(dmg.columns):
        sub = dmg.dropna(subset=["lat", "lon"])
        if len(sub):
            return float(sub["lat"].median()), float(sub["lon"].median())
    # Roanoke, VA fallback.
    return 37.27, -79.94
