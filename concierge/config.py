"""Central configuration: paths, schema maps, thresholds, and constants.

Everything that a judge or developer might want to tweak lives here so the
rest of the code stays declarative and the assumptions are transparent.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA1 = DATA_DIR / "Data1"
DATA2 = DATA_DIR / "Data2"

# Shapefile stems (without extension). If a file is missing the loader
# degrades gracefully rather than crashing.
LAYERS = {
    "damage": DATA1 / "arcos_prod_damage_09_28_Clip.shp",
    "customers": DATA1 / "Customer_Clip.shp",
    "primary": DATA1 / "Primary_ExportFeatures.shp",
    "poles": DATA1 / "Pole_Clip.shp",
    "fuses": DATA1 / "Fuse_Bank_Clip.shp",
    "hospitals": DATA1 / "Hospitals_Medic_Pairwis_Clip.shp",
    "fire": DATA1 / "Fire_Stations_E_Pairwis_Clip.shp",
    "ambulance": DATA1 / "Ambulance_Servi_Pairwis_Clip.shp",
    "transformers": DATA2 / "Transformer_Clip.shp",
    "switches": DATA2 / "Switch_Clip.shp",
    "reclosers": DATA2 / "Recloser_Bank_Clip.shp",
    "secondary": DATA2 / "Secondary_ExportFeatures.shp",
}

# A common working CRS. Source files are a mix of EPSG:4326 (lat/long) and
# EPSG:3857 (web mercator, meters); we normalize everything to 4326 for
# display and reproject to a metric CRS only for distance math.
DISPLAY_CRS = "EPSG:4326"
METRIC_CRS = "EPSG:3857"  # meters, good enough for local proximity in Roanoke, VA

# --------------------------------------------------------------------------
# Damage-file column aliases (shapefile truncates names to 10 chars)
# --------------------------------------------------------------------------
DAMAGE_COLS = {
    "outage_num": "Outage_Num",
    "incident": "Incident_W",
    "event": "Event",
    "region": "Region",
    "district": "District",
    "crew_area": "Crew_Area",
    "substation": "Substation",
    "circuit": "Circuit",
    "report_time": "Report_Tim",
    "status": "Status",
    "repaired": "Repaired",
    "repaired_date": "Repaired_D",
    "asset_type": "Asset_Type",
    "access_issue": "Access_Iss",
    "special_equip": "Special_Eq",
    "lon": "Longitude",
    "lat": "Latitude",
    "near_dist": "NEAR_DIST",
}

CUSTOMER_COLS = {
    "id": "id",
    "critical": "critical_c",
    "special_op": "special_op",
    "location_num": "aep_locati",
    "transformer": "aep_xfmr_l",
    "circuit_id": "circuit_id",
    "service_kind": "kind_of_se",
    "premise": "premise_nu",
    "city": "city",
}

# --------------------------------------------------------------------------
# Risk model configuration (transparent, tunable weights)
# --------------------------------------------------------------------------
# Storm severity categories exposed to the UI as a simple slider/choice.
STORM_LEVELS = {
    "Calm / clear": 0.0,
    "Light wind & rain": 0.25,
    "Strong thunderstorm": 0.55,
    "Severe storm / high wind": 0.8,
    "Major storm (historic)": 1.0,
}

# Relative weights for the risk score contributors. They are normalized at
# runtime, so the absolute numbers just express relative importance.
RISK_WEIGHTS = {
    "storm_severity": 0.40,      # how bad the incoming storm is
    "historical_density": 0.30,  # how often this circuit has been damaged
    "asset_exposure": 0.15,      # overhead / tree-exposed asset mix
    "proximity": 0.15,           # closeness to historically damaged equipment
}

# Risk band cut points on the 0..1 composite score.
RISK_BANDS = [
    (0.00, 0.25, "Low"),
    (0.25, 0.50, "Moderate"),
    (0.50, 0.75, "Elevated"),
    (0.75, 1.01, "High"),
]

# --------------------------------------------------------------------------
# Restoration model configuration
# --------------------------------------------------------------------------
# Fallback baseline repair hours by asset type, used only when the historical
# data has too few samples for a given asset type to be reliable. These are
# representative placeholders (STORMS-style labor references), clearly labeled
# as assumptions in the UI.
BASELINE_REPAIR_HOURS = {
    "TRANSFORMER": 4.0,
    "POLE": 8.0,
    "FUSE": 2.0,
    "RECLOSER": 3.0,
    "SWITCH": 3.0,
    "CONDUCTOR": 5.0,
    "SERVICE": 2.5,
    "DEFAULT": 4.0,
}

# Operational multipliers applied on top of base repair hours.
ACCESS_ISSUE_MULTIPLIER = 1.4      # blocked/difficult access
SPECIAL_EQUIP_MULTIPLIER = 1.3     # special equipment required
TRAVEL_NONPRODUCTIVE_MULTIPLIER = 1.25  # travel + non-productive time baseline

# Minimum sample count before we trust an empirical (data-driven) duration.
MIN_SAMPLES_FOR_EMPIRICAL = 5

# --------------------------------------------------------------------------
# Alert configuration
# --------------------------------------------------------------------------
# A scenario qualifies for proactive alerts when risk band is at/above this,
# OR the customer is flagged critical/medical.
ALERT_RISK_THRESHOLD = "Elevated"
PRIORITY_PROXIMITY_METERS = 500   # "near a priority facility" distance

# --------------------------------------------------------------------------
# Privacy: fields that must never surface in customer-facing output.
# --------------------------------------------------------------------------
PRIVACY_BLOCKLIST = {
    # customer PII (already nulled upstream, enforced again here)
    "address", "road_name", "address_nu", "macss_mete", "premise_nu",
    "aep_locati", "aep_xfmr_l", "anno_strin",
    # facility PII from priority layers
    "NAME", "ADDRESS", "ADDRESSBUI", "GNIS_ID", "FOOT_ID", "COMPLEX_ID",
    # operational / crew fields
    "Reporting_", "Reporting1", "TASK_Crew", "TASK_Job_I", "Repaired_B",
}


def llm_enabled() -> bool:
    """LLM polish is opt-in and only active when an API key is present."""
    return bool(os.environ.get("OPENAI_API_KEY"))
